import { Inject, Injectable, Logger } from '@nestjs/common';
import { ApiError, Errors } from '../common/errors/api-error';
import { isUuid } from '../common/validation';
import { OnlineMetrics } from '../odoo-rpc/online-metrics';
import { PartStatus, partStatusFromOdoo } from './online.model';
import { ONLINE_REPO, OnlineRepo } from './online.repository';
import { OnlineSagaService } from './online-saga.service';
import { createHash, randomUUID } from 'node:crypto';

export const ORDER_EVENTS_CONSUMER = 'online.order-events';
export const KNOWN_EVENTS = ['placed', 'paid', 'confirmed', 'stock_short', 'ready', 'picked_up', 'cancelled', 'refunded', 'review'] as const;
export const EVENT_BODY_LIMIT = 8 * 1024;

export interface OdooOrderEvent {
  /** Kunci dari Odoo (format lama `online:<id lokal Odoo>:<event>:<seq>`) — hanya untuk kompatibilitas & audit. */
  idempotency_key: string;
  /** G18-QA-01: uuid event dari Odoo (opsional, usulan kontrak) — stabil lintas restore DB Odoo. */
  event_id: string | null;
  seq: number;
  platform_order_ref: string;
  checkout_ref: string;
  channel_code: string;
  company_code: string | null;
  event: string;
  state: string;
  occurred_at: Date | null;
  refund: { refund_ref: string; amount: number; kind: 'full' | 'lines'; initiator: 'bff' | 'odoo'; initiated_by: string | null } | null;
}

export type EventOutcome = 'applied' | 'duplicate' | 'ignored_stale' | 'ignored_unknown_event';

/** Format lama (id lokal) atau baru (platform_order_ref uuid). */
const KEY_RE = /^online:(\d{1,12}|[0-9a-fA-F-]{36}):[a-z_]{1,32}:\d{1,9}$/;

/**
 * G18-QA-01: kunci idempotensi BFF yang STABIL lintas restore/rebuild DB Odoo — tidak memakai id lokal Odoo:
 * `odoo-ev:<platform_order_ref>:<event>:<seq>[:<event_id>]`. `platform_order_ref` = id bagian milik BFF (uuid).
 */
export function stableEventKey(ev: Pick<OdooOrderEvent, 'platform_order_ref' | 'event' | 'seq' | 'event_id'>): string {
  const base = `odoo-ev:${ev.platform_order_ref}:${ev.event}:${ev.seq}`;
  return ev.event_id ? `${base}:${ev.event_id}` : base;
}

/**
 * Sidik isi event (tanpa `occurred_at`, `event_id`, kunci Odoo) — kunci sama dengan isi berbeda = konflik, bukan
 * duplikat (G18-QA-01: jangan diabaikan diam-diam).
 */
export function eventFingerprint(ev: OdooOrderEvent): string {
  const rf = ev.refund ? [ev.refund.refund_ref, ev.refund.amount, ev.refund.kind, ev.refund.initiator] : null;
  const canonical = JSON.stringify([ev.platform_order_ref, ev.checkout_ref, ev.channel_code, ev.company_code, ev.event, ev.state, ev.seq, rf]);
  return createHash('sha256').update(canonical, 'utf8').digest('hex');
}

/** Validasi skema body event (docs/04 §10.5). Gagal → 400 `invalid_request` (final di Odoo). */
export function parseOdooEvent(raw: Buffer | undefined): OdooOrderEvent {
  if (!raw || raw.length === 0 || raw.length > EVENT_BODY_LIMIT) throw Errors.invalidRequest();
  let o: Record<string, unknown>;
  try {
    const v: unknown = JSON.parse(raw.toString('utf8'));
    if (typeof v !== 'object' || v === null || Array.isArray(v)) throw new Error('bukan objek');
    o = v as Record<string, unknown>;
  } catch {
    throw Errors.invalidRequest();
  }
  const str = (k: string, re: RegExp): string => {
    const v = o[k];
    if (typeof v !== 'string' || !re.test(v)) throw Errors.invalidRequest();
    return v;
  };
  if (o.schema_version !== 1) throw Errors.invalidRequest();
  const seq = o.seq;
  if (typeof seq !== 'number' || !Number.isSafeInteger(seq) || seq < 0) throw Errors.invalidRequest();
  const key = str('idempotency_key', KEY_RE);
  let eventId: string | null = null;
  if (o.event_id !== undefined && o.event_id !== null) {
    const v = str('event_id', /^[0-9a-fA-F-]{36}$/).toLowerCase();
    if (!isUuid(v)) throw Errors.invalidRequest();
    eventId = v;
  }
  const ref = str('platform_order_ref', /^[0-9a-fA-F-]{36}$/).toLowerCase();
  const checkout = str('checkout_ref', /^[0-9a-fA-F-]{36}$/).toLowerCase();
  if (!isUuid(ref) || !isUuid(checkout)) throw Errors.invalidRequest();
  const company = o.company_code === null || o.company_code === undefined ? null : str('company_code', /^[A-Z]{2,8}$/);
  const occurredRaw = o.occurred_at;
  const occurred = typeof occurredRaw === 'string' && !Number.isNaN(Date.parse(occurredRaw)) ? new Date(occurredRaw) : null;
  let refund: OdooOrderEvent['refund'] = null;
  if (o.refund !== null && o.refund !== undefined && o.refund !== false) {
    const rf = o.refund as Record<string, unknown>;
    if (typeof rf !== 'object' || typeof rf.refund_ref !== 'string' || rf.refund_ref.length < 1 || rf.refund_ref.length > 128) throw Errors.invalidRequest();
    if (typeof rf.amount !== 'number' || !Number.isSafeInteger(rf.amount) || rf.amount < 0) throw Errors.invalidRequest();
    if (rf.kind !== 'full' && rf.kind !== 'lines') throw Errors.invalidRequest();
    if (rf.initiator !== 'bff' && rf.initiator !== 'odoo') throw Errors.invalidRequest();
    // SEC-95: identitas user Odoo pemicu (uid / login non-email) — opsional; bentuk lain diabaikan (tidak ditolak)
    let by: string | null = null;
    if (typeof rf.initiator_uid === 'number' && Number.isSafeInteger(rf.initiator_uid) && rf.initiator_uid > 0) by = `odoo-uid:${rf.initiator_uid}`;
    else if (typeof rf.initiator_login === 'string' && /^[A-Za-z0-9._-]{1,64}$/.test(rf.initiator_login)) by = `odoo-login:${rf.initiator_login.toLowerCase()}`;
    refund = { refund_ref: rf.refund_ref, amount: rf.amount, kind: rf.kind, initiator: rf.initiator, initiated_by: by };
  }
  return {
    idempotency_key: key,
    event_id: eventId,
    seq,
    platform_order_ref: ref,
    checkout_ref: checkout,
    channel_code: str('channel_code', /^[A-Z][A-Z0-9-]{1,15}$/),
    company_code: company,
    event: str('event', /^[a-z_]{1,32}$/),
    state: str('state', /^[a-z_]{1,32}$/),
    occurred_at: occurred,
    refund,
  };
}

/**
 * Event Odoo → BFF (docs/04 §10.5): service key + HMAC + replay window dicek guard; di sini satu transaksi:
 * G18-QA-01: kunci = `stableEventKey` (bukan id lokal Odoo); kunci sama + sidik isi sama → `duplicate`; isi berbeda →
 * 409 `event_conflict` + anomali; kunci Odoo lama milik bagian lain (restore DB Odoo) → diproses + anomali.
 * `claim('online.order-events', key)` (duplikat → `duplicate`) → cari bagian (id = platform_order_ref, order_id =
 * checkout_ref; tidak ada → 404; kanal/PT beda → 409 `company_mismatch`) → `seq ≤ last_seq` → `ignored_stale` →
 * transisi monoton → riwayat `online_order_event` → efek samping via outbox (notifikasi `ready`; refund inisiatif Odoo).
 * 404/409 dilempar SEBELUM commit sehingga klaim ikut rollback (Odoo menandai final).
 */
@Injectable()
export class OrderEventsService {
  private readonly logger = new Logger('OnlineEvents');

  constructor(
    @Inject(ONLINE_REPO) private readonly repo: OnlineRepo,
    private readonly saga: OnlineSagaService,
    private readonly metrics: OnlineMetrics,
  ) {}

  async handle(ev: OdooOrderEvent): Promise<{ ok: true; status: EventOutcome }> {
    let outcome: EventOutcome;
    try {
      outcome = await this.repo.tx(async (r) => {
        // G18-QA-01: dedup atas kunci stabil (platform_order_ref + event + seq [+ event_id]) + sidik isi
        const key = stableEventKey(ev);
        const fp = eventFingerprint(ev);
        const prev = await r.findEvent(key);
        if (prev) {
          if (prev.payload_sha256 === null || prev.payload_sha256 === fp) return 'duplicate' as const;
          this.metrics.anomaly('odoo_event_conflict', 'critical', { part_id: ev.platform_order_ref, state: ev.event, code: 'payload_mismatch' });
          throw Errors.conflict('event_conflict', 'Event dengan kunci sama tetapi isi berbeda.');
        }
        // Kompatibel mundur: event yang sudah diproses SEBELUM perbaikan tercatat dengan kunci Odoo lama. Duplikat hanya
        // bila baris itu milik bagian/event/seq yang sama; milik bagian lain = id lokal Odoo terpakai ulang (restore/DR).
        if (ev.idempotency_key !== key) {
          const legacy = await r.findEvent(ev.idempotency_key);
          if (legacy) {
            if (legacy.part_id === ev.platform_order_ref && legacy.event === ev.event && legacy.seq === ev.seq) return 'duplicate' as const;
            this.metrics.anomaly('odoo_event_key_reused', 'warning', { part_id: ev.platform_order_ref, state: ev.event, code: 'legacy_key_reused' });
          }
        }
        if (!(await r.claim(ORDER_EVENTS_CONSUMER, key))) return 'duplicate' as const;
        const part = await r.getPart(ev.platform_order_ref, true);
        if (!part || part.order_id !== ev.checkout_ref) throw Errors.notFound();
        if (part.channel_code !== ev.channel_code || (ev.company_code !== null && ev.company_code !== part.company_code)) {
          throw Errors.conflict('company_mismatch', 'Kanal/PT tidak cocok.');
        }
        const record = async (o: EventOutcome): Promise<EventOutcome> => {
          await r.insertEvent({
            part_id: part.id,
            source: 'odoo',
            event: ev.event,
            seq: ev.seq,
            idempotency_key: key,
            outcome: o,
            occurred_at: ev.occurred_at,
            payload_sha256: fp,
            source_key: ev.idempotency_key,
          });
          return o;
        };
        if (!(KNOWN_EVENTS as readonly string[]).includes(ev.event)) return record('ignored_unknown_event');
        if (ev.seq <= part.last_seq) return record('ignored_stale');

        const target: PartStatus | null = ev.event === 'refunded' ? null : partStatusFromOdoo(ev.event === 'picked_up' ? 'picked_up' : ev.event);
        if (target) {
          // L-14: transisi yang tidak diterapkan (mundur/sama) dicatat `ignored_stale`, bukan `applied` (seq tetap maju)
          const applied = await this.saga.applyPart(r, part, target, { source: 'odoo', odooState: ev.state, seq: ev.seq, cancelReason: 'ops' });
          if (!applied) return record('ignored_stale');
        } else {
          await r.updatePart(part.id, { last_seq: ev.seq, odoo_state: ev.state });
        }
        if (ev.event === 'review') {
          this.metrics.anomaly('odoo_review', 'warning', { part_id: part.id, order_id: part.order_id, state: ev.state });
        }
        if (ev.refund && ev.refund.initiator === 'odoo' && (ev.event === 'cancelled' || ev.event === 'refunded')) {
          const order = await r.getOrder(part.order_id);
          if (!order?.payment_intent_id) {
            this.metrics.anomaly('odoo_refund_without_payment', 'critical', { part_id: part.id });
          } else if (ev.refund.amount > 0) {
            const rf = await r.insertRefund({
              id: randomUUID(),
              part_id: part.id,
              payment_intent_id: order.payment_intent_id,
              kind: ev.refund.kind,
              reason: 'ops_cancelled',
              lines: null,
              amount: ev.refund.amount,
              odoo_refund_ref: ev.refund.refund_ref,
              // SEC-95 / L-16: TIDAK otomatis — pemisahan tugas; CS menyetujui lewat CLI online-refund-approve
              status: 'approval_required',
              requested_by: 'system',
              initiated_by: ev.refund.initiated_by,
            });
            if (rf.status === 'approval_required') {
              this.metrics.refund('approval_required');
              this.metrics.anomaly('odoo_refund_needs_approval', 'warning', { refund_id: rf.id, part_id: part.id, actual: ev.refund.amount });
            }
          }
        }
        await this.saga.refreshOrder(r, part.order_id);
        return record('applied');
      });
    } catch (err) {
      if (err instanceof ApiError) this.metrics.event(ev.event, err.getStatus() === 404 ? 'not_found' : 'company_mismatch');
      throw err;
    }
    this.metrics.event(ev.event, outcome);
    this.logger.log(`Event ${ev.event} bagian ${ev.platform_order_ref} seq ${ev.seq}: ${outcome}`);
    return { ok: true, status: outcome };
  }
}
