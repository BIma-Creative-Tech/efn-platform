import { Inject, Injectable, Logger, OnModuleDestroy, OnModuleInit } from '@nestjs/common';
import { AppConfig } from '../config/app-config';
import { DB, Db, Queryable } from '../common/db/db';
import { OutboxService } from '../common/outbox/outbox.service';
import { isCompanyCode } from '../loyalty/loyalty.service';
import { BEHAVIOR_EVENT_QUEUE, BehaviorEventMessage, BehaviorQueueEvent, behaviorIdempotencyKey } from './behavior-message';
import { MAX_EVENTS_PER_BATCH } from './dto/behavior.dto';
import { deterministicUuid, memberSubjectKey } from './subject-key';

export const PURCHASE_JOB_LOCK_KEY = 0x45464e50; // 'EFNP'
export const PURCHASE_INTERVAL_MS = 300_000;
/** Bagian yang dikonfirmasi lunas dalam jendela ini diproses (cadangan bila job sempat mati). */
const LOOKBACK_HOURS = 48;
const PART_BATCH = 200;
/** Status bagian sesudah lunas dikonfirmasi Odoo (bukan `paying`/`stock_short`/`review`/`cancelled`). */
const PAID_PART_STATUSES = ['confirmed', 'ready', 'handed_over'];
const CHANNEL_CODE_RE = /^[A-Z0-9-]{1,8}$/;
const OUTLET_CODE_RE = /^[A-Za-z0-9_-]{1,16}$/;

interface PaidPartRow {
  part_id: string;
  member_id: string;
  channel_code: string;
  company_code: string;
  outlet_code: string | null;
  confirmed_at: Date | string;
  lines: unknown;
}

/**
 * Event server `purchase` (QA G50 #7, keputusan lead): bagian order online yang lunas & dikonfirmasi Odoo
 * (`order.paid` sudah diproses saga → `online_order_part.confirmed_at`) → satu event per produk untuk subject MEMBER,
 * HANYA bila consent `tracking` akun aktif saat diproses. Lewat jalur behavior yang sama (outbox → `behavior.event` →
 * behavior-writer → staging), bukan tulis langsung `event_raw`. Dipakai mart/segmen "viewed-not-bought".
 *
 * Tanpa menyentuh `src/orders`: job membaca tabel order (sumber kebenaran pembayaran) setiap 5 menit, satu replika
 * (advisory lock). Idempoten: `behavior.purchase_emitted(part_id)` + outbox dalam SATU transaksi; `event_id`/`batch_id`
 * deterministik (UUID v8) sehingga dedup staging menahan duplikat bila tetap terkirim dua kali.
 *
 * `channel` (web|app) = channel event terakhir subject di staging; tanpa jejak → `app` (ASUMSI: order online tidak
 * menyimpan asal web/app; usulan kontrak: kolom asal di `online_order`). POS belum tercakup.
 */
@Injectable()
export class PurchaseEventsService implements OnModuleInit, OnModuleDestroy {
  private readonly logger = new Logger(PurchaseEventsService.name);
  private timer: NodeJS.Timeout | null = null;
  private running = false;
  now: () => number = () => Date.now();

  constructor(
    private readonly config: AppConfig,
    @Inject(DB) private readonly db: Db,
    private readonly outbox: OutboxService,
  ) {}

  onModuleInit(): void {
    if (!this.config.behaviorTrackingEnabled) return;
    this.timer = setInterval(() => void this.runOnce(), PURCHASE_INTERVAL_MS);
    this.timer.unref();
  }

  onModuleDestroy(): void {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
  }

  /** @returns jumlah bagian yang menerbitkan event. Tidak pernah melempar. */
  async runOnce(): Promise<number> {
    if (this.running || !this.config.behaviorTrackingEnabled) return 0;
    this.running = true;
    try {
      const pepper = this.config.behaviorSubjectPepper;
      return await this.db.tx(async (q) => {
        const lock = await q.query<{ ok: boolean }>('SELECT pg_try_advisory_xact_lock($1) AS ok', [PURCHASE_JOB_LOCK_KEY]);
        if (!lock.rows[0]?.ok) return 0;
        const parts = await q.query<PaidPartRow>(
          `SELECT p.id::text AS part_id, o.member_id::text AS member_id, p.channel_code, p.company_code, p.outlet_code,
                  p.confirmed_at, p.lines
             FROM platform.online_order_part p
             JOIN platform.online_order o ON o.id = p.order_id
             JOIN platform.member m ON m.id = o.member_id AND m.deleted_at IS NULL
            WHERE p.status = ANY($1::text[]) AND p.confirmed_at IS NOT NULL
              AND p.confirmed_at > now() - make_interval(hours => $2::int)
              AND NOT EXISTS (SELECT 1 FROM behavior.purchase_emitted e WHERE e.part_id = p.id)
              AND (SELECT c.granted FROM platform.consent c WHERE c.member_id = o.member_id AND c.purpose = 'tracking'
                    ORDER BY c.created_at DESC, c.id DESC LIMIT 1) IS TRUE
            ORDER BY p.confirmed_at
            LIMIT $3`,
          [PAID_PART_STATUSES, LOOKBACK_HOURS, PART_BATCH],
        );
        let n = 0;
        for (const part of parts.rows) if (await this.emit(q, pepper, part)) n += 1;
        if (n > 0) this.logger.log(`Event purchase: ${n} bagian order diterbitkan`);
        return n;
      });
    } catch (err) {
      this.logger.warn(`Job event purchase gagal: ${err instanceof Error ? err.message : 'db'}`);
      return 0;
    } finally {
      this.running = false;
    }
  }

  async emit(q: Queryable, pepper: string, part: PaidPartRow): Promise<boolean> {
    const claim = await q.query(`INSERT INTO behavior.purchase_emitted (part_id) VALUES ($1::uuid) ON CONFLICT DO NOTHING RETURNING part_id`, [part.part_id]);
    if ((claim.rowCount ?? 0) === 0) return false;
    // Bagian yang tidak bisa dipetakan ke kontrak staging ditandai selesai tanpa event (tidak dicoba tiap siklus)
    if (!CHANNEL_CODE_RE.test(part.channel_code) || !isCompanyCode(part.company_code)) return false;
    const events = purchaseEvents(part);
    if (events.length === 0) return false;
    const subject = memberSubjectKey(pepper, part.member_id);
    await q.query(`INSERT INTO behavior.subject_member (subject_key, member_id) VALUES ($1, $2) ON CONFLICT DO NOTHING`, [subject, part.member_id]);
    const last = await q.query<{ channel: string }>(
      `SELECT channel FROM behavior.event_raw WHERE subject_key = $1 AND channel IN ('web', 'app') ORDER BY received_at DESC LIMIT 1`,
      [subject],
    );
    const channel: 'web' | 'app' = last.rows[0]?.channel === 'web' ? 'web' : 'app';
    const receivedAt = new Date(this.now()).toISOString();
    for (let i = 0; i < events.length; i += MAX_EVENTS_PER_BATCH) {
      const batchId = deterministicUuid(`efn:purchase:batch:${part.part_id}:${i / MAX_EVENTS_PER_BATCH}`);
      const msg: BehaviorEventMessage = {
        schema_version: 1,
        batch_id: batchId,
        received_at: receivedAt,
        subject_key: subject,
        subject_kind: 'member',
        consented: true, // V-01: hanya member ber-consent `tracking` (filter query di runOnce)
        channel,
        events: events.slice(i, i + MAX_EVENTS_PER_BATCH),
      };
      await this.outbox.enqueue(q, BEHAVIOR_EVENT_QUEUE, { ...msg }, behaviorIdempotencyKey(batchId));
    }
    return true;
  }
}

/** Baris produk (tanpa baris reward, qty > 0) → satu event per produk (qty dijumlah, harga satuan termahal). */
export function purchaseEvents(part: PaidPartRow): BehaviorQueueEvent[] {
  const lines = Array.isArray(part.lines) ? (part.lines as Record<string, unknown>[]) : [];
  const byProduct = new Map<number, { qty: number; price: number }>();
  for (const l of lines) {
    if (!l || typeof l !== 'object' || l.reward === true) continue;
    const pid = l.product_id;
    const qty = l.qty;
    if (typeof pid !== 'number' || !Number.isSafeInteger(pid) || pid < 1 || typeof qty !== 'number' || !(qty > 0)) continue;
    const price = typeof l.price_unit_incl === 'number' && Number.isFinite(l.price_unit_incl) ? Math.max(0, Math.round(l.price_unit_incl)) : 0;
    const cur = byProduct.get(pid) ?? { qty: 0, price: 0 };
    byProduct.set(pid, { qty: cur.qty + qty, price: Math.max(cur.price, price) });
  }
  const ts = new Date(part.confirmed_at).toISOString();
  const outlet = part.outlet_code && OUTLET_CODE_RE.test(part.outlet_code) ? part.outlet_code : null;
  return [...byProduct.entries()].map(([pid, v]) => ({
    event_id: deterministicUuid(`efn:purchase:${part.part_id}:${pid}`),
    type: 'purchase' as const,
    client_ts: ts,
    channel_code: part.channel_code,
    company_code: part.company_code,
    outlet_code: outlet,
    product_id: pid,
    duration_s: null,
    qty: Math.min(9999, Math.max(0, Math.round(v.qty))),
    query: null,
    result_count: null,
    stock_status: null,
    price_idr: v.price,
    page: null,
  }));
}
