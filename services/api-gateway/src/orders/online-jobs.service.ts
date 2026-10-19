import { Inject, Injectable, Logger, OnModuleDestroy, OnModuleInit, Optional } from '@nestjs/common';
import { ONLINE_POINTS, OnlinePointsPort } from './online-points';
import { AppConfig } from '../config/app-config';
import { QUEUES } from '../common/amqp/queues';
import { ODOO_ONLINE, OdooOnlineApi } from '../odoo-rpc/odoo-online.types';
import { OnlineMetrics, StuckKind } from '../odoo-rpc/online-metrics';
import { ONLINE_PAYMENTS, OnlinePaymentsPort } from '../payments/online-payments';
import {
  COUNTER_REFUND_AUTH_PENDING,
  PartStatus,
  UNPAID_OPEN,
  compensateMessage,
  orderPaidMessage,
  partStatusFromOdoo,
  refundRequestedMessage,
  unpaidClosedMessage,
} from './online.model';
import { ONLINE_REPO, OnlineRepo } from './online.repository';
import { OnlineSagaService } from './online-saga.service';

export const LOCK = { EXPIRE: 0x45464f45, RECON: 0x45464f52, STATUS: 0x45464f53, RETENTION: 0x45464f54 } as const; // 'EFO?'
const BATCH = 100;
const MIN = 60_000;
/** State `efn.online.order` yang berarti SO sudah dilunasi (G20 §12.2 no. 6: pelunasan POS → confirmed/… ). */
const COUNTER_PAID_STATES: ReadonlySet<string> = new Set(['paid', 'confirmed', 'stock_short', 'ready', 'picked_up', 'review']);

/**
 * Job saga online (docs/04 §10.6.5, §10.8.3) — satu replika per job per siklus (advisory lock xact):
 * - kedaluwarsa (30 s): `awaiting_payment` lewat `expires_at + 30 s` / `created` > 5 menit → batalkan intent di gateway,
 *   lalu (terkunci) `expired` + outbox `order.unpaid_closed`; intent ternyata `paid` → dilewati (webhook menang);
 * - rekonsiliasi (5 menit): terbitkan ulang `order.paid` (bagian `paying` > 5 m), `order.refund.requested` (refund belum
 *   final > 15 m), `order.compensate` (`stock_short` > 5 m), `order.unpaid_closed` (order tutup belum bayar dengan bagian
 *   terbuka); cek Σ porsi = intent & Σ refund ≤ porsi; gauge `efn_online_stuck_parts`; heartbeat `EFN_ONLINE_RECON`;
 * - status (15 menit): bagian `confirmed|ready` → `efn_online_status` per kanal (≤ 100) → terapkan state tertinggal;
 * - retensi (1 jam): kode ambil, QR online, tautan member, riwayat event.
 */
@Injectable()
export class OnlineJobsService implements OnModuleInit, OnModuleDestroy {
  private readonly logger = new Logger('OnlineJobs');
  private readonly timers: NodeJS.Timeout[] = [];
  private readonly running = new Set<string>();
  now: () => number = () => Date.now();

  constructor(
    private readonly config: AppConfig,
    @Inject(ONLINE_REPO) private readonly repo: OnlineRepo,
    @Inject(ODOO_ONLINE) private readonly odoo: OdooOnlineApi,
    @Inject(ONLINE_PAYMENTS) private readonly payments: OnlinePaymentsPort,
    private readonly saga: OnlineSagaService,
    private readonly metrics: OnlineMetrics,
    @Optional() @Inject(ONLINE_POINTS) private readonly points: OnlinePointsPort | null = null,
  ) {}

  onModuleInit(): void {
    const every = (ms: number, name: string, fn: () => Promise<unknown>): void => {
      if (ms <= 0) return;
      const t = setInterval(() => void this.guarded(name, fn), ms);
      t.unref();
      this.timers.push(t);
    };
    every(this.config.onlineSweepIntervalMs, 'expire', () => this.expireOnce());
    every(this.config.onlineReconIntervalMs, 'recon', () => this.reconOnce());
    every(this.config.onlineStatusReconIntervalMs, 'status', () => this.statusOnce());
    every(this.config.onlineRetentionIntervalMs, 'retention', () => this.retentionOnce());
  }

  onModuleDestroy(): void {
    for (const t of this.timers) clearInterval(t);
    this.timers.length = 0;
  }

  /** Heartbeat dead-man EfnOnlineReconDeadman: kunci pertama WAJIB `status` (selector Loki berjangkar). */
  heartbeat(status: 'ok' | 'error', fields: Record<string, string | number>): void {
    const line = `EFN_ONLINE_RECON ${JSON.stringify({ status, ...fields })}`;
    if (status === 'ok') this.logger.log(line);
    else this.logger.error(line);
  }

  private async guarded(name: string, fn: () => Promise<unknown>): Promise<void> {
    if (this.running.has(name)) return;
    this.running.add(name);
    try {
      await fn();
    } catch (err) {
      this.logger.error(`Job online ${name} gagal: ${err instanceof Error ? err.message : 'error'}`);
      if (name === 'recon' || name === 'status') this.heartbeat('error', { job: name === 'recon' ? 'saga' : 'status', error: err instanceof Error ? err.name : 'error' });
    } finally {
      this.running.delete(name);
    }
  }

  // ───────────── kedaluwarsa ─────────────
  async expireOnce(): Promise<{ expired: number; skipped: number }> {
    const now = this.now();
    const candidates = await this.repo.expireCandidates(new Date(now - 30_000), new Date(now - 5 * MIN), BATCH);
    let expired = 0;
    let skipped = 0;
    for (const c of candidates) {
      // G20 §12.2: order bayar di kasir — Odoo sumber kebenaran pelunasan. Sebelum kedaluwarsa, tanya status SO; bila
      // sudah lunas di POS (event belum sampai) → terapkan pelunasan, jangan batalkan.
      if (c.payment_method === 'counter' && c.status === 'awaiting_counter_payment') {
        const outcome = await this.counterPaidBeforeExpiry(c.id, now);
        if (outcome === 'paid' || outcome === 'retry') {
          skipped += 1;
          continue;
        }
      }
      if (c.payment_intent_id) {
        try {
          await this.payments.cancelIntent(c.payment_intent_id);
        } catch (err) {
          this.logger.warn(`Cancel intent order ${c.id} gagal (lanjut lokal): ${err instanceof Error ? err.message : 'error'}`);
        }
      }
      const done = await this.repo.tx(async (r) => {
        if (!(await r.tryJobLock(LOCK.EXPIRE))) return 'locked' as const;
        const o = await r.getOrder(c.id, true);
        if (!o || !UNPAID_OPEN.includes(o.status)) return 'skip' as const;
        if (o.payment_intent_id) {
          const intent = await this.payments.getIntent(o.payment_intent_id);
          if (intent?.status === 'paid') return 'skip' as const; // webhook menang
        }
        await r.transitionOrder(o.id, [o.status], 'expired', { close_reason: 'payment_expired', closed_at: new Date(this.now()) });
        await this.points?.releaseOrder(r, o.id, await r.getParts(o.id)); // G22 C.3: release di transaksi transisi
        const m = unpaidClosedMessage(o.id, 'payment_expired');
        await r.enqueue(QUEUES.ORDER_UNPAID_CLOSED, { ...m }, m.idempotency_key);
        return 'expired' as const;
      });
      if (done === 'locked') break;
      if (done === 'expired') {
        expired += 1;
        this.metrics.order('expired');
      } else skipped += 1;
    }
    return { expired, skipped };
  }

  /** Batas toleransi Odoo tak terjangkau sebelum order bayar di kasir tetap dikedaluwarsakan (bayar terlambat → anomali). */
  static readonly COUNTER_EXPIRY_GRACE_MS = 10 * MIN;

  /**
   * `paid` = Odoo melaporkan SO sudah lunas → pelunasan diterapkan; `retry` = Odoo tidak terjangkau dan masih dalam
   * toleransi → coba siklus berikut; `expire` = boleh dikedaluwarsakan (Odoo belum lunas / toleransi habis).
   */
  private async counterPaidBeforeExpiry(orderId: string, now: number): Promise<'paid' | 'retry' | 'expire'> {
    const parts = (await this.repo.getParts(orderId)).filter((p) => p.status === 'placed');
    if (parts.length === 0) return 'expire';
    const order = await this.repo.getOrder(orderId);
    if (!order) return 'expire';
    let rows;
    try {
      rows = await this.odoo.status(parts[0].channel_code, parts.map((p) => p.id));
    } catch (err) {
      this.logger.warn(`Status Odoo order bayar di kasir ${orderId} gagal: ${err instanceof Error ? err.message : 'error'}`);
      return now < order.expires_at.getTime() + OnlineJobsService.COUNTER_EXPIRY_GRACE_MS ? 'retry' : 'expire';
    }
    let paid = false;
    for (const row of rows) {
      if (!COUNTER_PAID_STATES.has(row.state)) continue;
      const applied = await this.repo.tx(async (r) => {
        const o = await r.getOrder(orderId, true);
        const p = await r.getPart(row.platform_order_ref, true);
        if (!o || !p || p.order_id !== orderId) return false;
        return this.saga.applyCounterPaid(r, o, p, { source: 'bff', odooState: row.state, seq: row.seq, paidAt: null });
      });
      paid = paid || applied;
    }
    return paid ? 'paid' : 'expire';
  }

  // ───────────── rekonsiliasi ─────────────
  async reconOnce(): Promise<Record<string, number> | null> {
    const now = new Date(this.now());
    const out = await this.repo.tx(async (r) => {
      if (!(await r.tryJobLock(LOCK.RECON))) return null;
      const stats = { paid: 0, refunds: 0, compensate: 0, unpaid: 0, portion_mismatch: 0, refund_overrun: 0 };
      const minus = (ms: number): Date => new Date(now.getTime() - ms);
      const paying = await r.partsInStatusBefore(['paying'], minus(5 * MIN), BATCH);
      const orders = new Set<string>();
      for (const p of paying) {
        if (orders.has(p.order_id)) continue;
        orders.add(p.order_id);
        const o = await r.getOrder(p.order_id);
        if (!o?.payment_intent_id) continue;
        const m = orderPaidMessage(o.id, o.payment_intent_id);
        await r.enqueue(QUEUES.ORDER_PAID, { ...m }, m.idempotency_key);
        stats.paid += 1;
      }
      for (const rf of await r.refundsInStatusBefore(['requested', 'odoo_done', 'gateway_in_flight', 'gateway_pending', 'gateway_done'], minus(15 * MIN), BATCH)) {
        const m = refundRequestedMessage(rf.id);
        await r.enqueue(QUEUES.ORDER_REFUND_REQUESTED, { ...m }, m.idempotency_key);
        stats.refunds += 1;
      }
      // L-60: otorisasi refund POS counter yang tertunda (galat transien Odoo) → terbitkan ulang (idempoten)
      for (const rf of await r.refundsInStatusBefore(['manual_required'], minus(15 * MIN), BATCH)) {
        if (rf.payment_intent_id !== null || rf.failure_reason !== COUNTER_REFUND_AUTH_PENDING) continue;
        const m = refundRequestedMessage(rf.id);
        await r.enqueue(QUEUES.ORDER_REFUND_REQUESTED, { ...m }, m.idempotency_key);
        stats.refunds += 1;
      }
      for (const p of await r.partsInStatusBefore(['stock_short'], minus(5 * MIN), BATCH)) {
        const m = compensateMessage(p.id, 'stock_short');
        await r.enqueue(QUEUES.ORDER_COMPENSATE, { ...m }, m.idempotency_key);
        stats.compensate += 1;
      }
      for (const o of await r.closedOrdersWithOpenParts(minus(5 * MIN), BATCH)) {
        const reason = o.close_reason === 'customer' ? 'customer' : o.close_reason === 'place_failed' ? 'place_failed' : 'payment_expired';
        const m = unpaidClosedMessage(o.id, reason);
        await r.enqueue(QUEUES.ORDER_UNPAID_CLOSED, { ...m }, m.idempotency_key);
        stats.unpaid += 1;
      }
      for (const x of await r.portionMismatches(20)) {
        stats.portion_mismatch += 1;
        this.metrics.mismatch('portion');
        this.metrics.anomaly('portion_mismatch', 'critical', { payment_id: x.payment_id, expected: x.expected, actual: x.actual });
      }
      for (const x of await r.refundOverruns(20)) {
        stats.refund_overrun += 1;
        this.metrics.anomaly('refund_exceeds_portion', 'critical', { part_id: x.part_id, expected: x.portion, actual: x.refunded });
      }
      const stuck = await r.stuckCounts(now);
      for (const [k, v] of Object.entries(stuck)) this.metrics.setStuck(k as StuckKind, v);
      const open: Record<string, number> = {};
      for (const rf of await r.refundsInStatusBefore(['requested', 'odoo_done', 'approval_required', 'gateway_in_flight', 'gateway_pending', 'gateway_done', 'manual_required', 'failed'], new Date(now.getTime() + 1), 10_000)) {
        open[rf.status] = (open[rf.status] ?? 0) + 1;
      }
      this.metrics.setRefundsOpen(open);
      return stats;
    });
    // Heartbeat §10.11 (promtail: `[Online…] EFN_ONLINE_RECON {"status":"ok|error",…}`) — tanpa PII
    this.heartbeat('ok', { job: 'saga', ...(out ?? { skipped: 1 }) });
    return out;
  }

  // ───────────── rekonsiliasi pull status Odoo ─────────────
  async statusOnce(): Promise<number> {
    const applied = await this.statusPull();
    this.heartbeat('ok', { job: 'status', applied });
    return applied;
  }

  private async statusPull(): Promise<number> {
    const parts = await this.repo.partsInStatusBefore(['confirmed', 'ready', 'review'], new Date(this.now()), 500);
    // G20 §12.2: bagian bayar-di-kasir yang masih menunggu — event `paid` yang hilang dipulihkan dari status SO
    const counterParts = await this.repo.counterAwaitingParts(200);
    const counterIds = new Set(counterParts.map((p) => p.id));
    const byChannel = new Map<string, string[]>();
    for (const p of [...parts, ...counterParts]) byChannel.set(p.channel_code, [...(byChannel.get(p.channel_code) ?? []), p.id]);
    let applied = 0;
    for (const [channel, ids] of byChannel) {
      for (let i = 0; i < ids.length; i += 100) {
        let rows;
        try {
          rows = await this.odoo.status(channel, ids.slice(i, i + 100));
        } catch (err) {
          this.logger.warn(`efn_online_status kanal ${channel} gagal: ${err instanceof Error ? err.message : 'error'}`);
          break;
        }
        for (const row of rows) {
          const target: PartStatus | null = partStatusFromOdoo(row.state);
          if (!target) continue;
          const ok = await this.repo.tx(async (r) => {
            const p = await r.getPart(row.platform_order_ref, true);
            if (!p || p.channel_code !== channel) return false;
            if (row.seq !== null && row.seq <= p.last_seq) return false;
            let changed: boolean;
            if (counterIds.has(p.id) && COUNTER_PAID_STATES.has(row.state)) {
              const o = await r.getOrder(p.order_id, true);
              changed = o ? await this.saga.applyCounterPaid(r, o, p, { source: 'bff', odooState: row.state, seq: row.seq, paidAt: null }) : false;
            } else if (counterIds.has(p.id) && target !== 'cancelled') {
              return false; // `placed` (belum dibayar) / state lain: tidak ada perubahan
            } else {
              changed = await this.saga.applyPart(r, p, target, { source: 'bff', odooState: row.state, seq: row.seq, cancelReason: 'ops' });
            }
            if (changed) {
              await r.insertEvent({
                part_id: p.id,
                source: 'bff',
                event: row.state.slice(0, 32),
                seq: row.seq,
                idempotency_key: `recon:${p.id}:${row.state}:${row.seq ?? 'x'}`,
                outcome: 'applied',
                occurred_at: null,
              });
              await this.saga.refreshOrder(r, p.order_id);
            }
            return changed;
          });
          if (ok) applied += 1;
        }
      }
    }
    return applied;
  }

  // ───────────── retensi ─────────────
  async retentionOnce(): Promise<unknown> {
    return this.repo.tx(async (r) => {
      if (!(await r.tryJobLock(LOCK.RETENTION))) return null;
      const res = await r.retention(new Date(this.now()), {
        pickupCodeMaxDays: this.config.onlinePickupCodeMaxDays,
        memberLinkDays: this.config.onlineOrderMemberLinkDays,
        eventRetentionDays: this.config.onlineEventRetentionDays,
        adminAuditRetentionDays: this.config.onlineAdminAuditRetentionDays,
      });
      this.metrics.retention(res);
      const total = res.pickupNulled + res.counterNulled + res.qrNulled + res.unlinked + res.eventsPurged + res.adminAuditPurged;
      if (total > 0) this.logger.log(`Retensi online: ${JSON.stringify(res)}`);
      return res;
    });
  }
}
