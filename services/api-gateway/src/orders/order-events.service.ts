import { Inject, Injectable, Logger } from '@nestjs/common';
import { ApiError, Errors } from '../common/errors/api-error';
import { isUuid } from '../common/validation';
import { OnlineMetrics } from '../odoo-rpc/online-metrics';
import { PartStatus, partStatusFromOdoo } from './online.model';
import { ONLINE_REPO, OnlineRepo } from './online.repository';
import { OnlineSagaService } from './online-saga.service';
import { randomUUID } from 'node:crypto';

export const ORDER_EVENTS_CONSUMER = 'online.order-events';
export const KNOWN_EVENTS = ['placed', 'paid', 'confirmed', 'stock_short', 'ready', 'picked_up', 'cancelled', 'refunded', 'review'] as const;
export const EVENT_BODY_LIMIT = 8 * 1024;

export interface OdooOrderEvent {
  idempotency_key: string;
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

const KEY_RE = /^online:\d{1,12}:[a-z_]{1,32}:\d{1,9}$/;

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
        if (!(await r.claim(ORDER_EVENTS_CONSUMER, ev.idempotency_key))) return 'duplicate' as const;
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
            idempotency_key: ev.idempotency_key,
            outcome: o,
            occurred_at: ev.occurred_at,
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
