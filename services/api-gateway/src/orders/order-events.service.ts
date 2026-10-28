import { Inject, Injectable, Logger, Optional } from '@nestjs/common';
import { ONLINE_POINTS, OnlinePointsPort } from './online-points';
import { ApiError, Errors } from '../common/errors/api-error';
import { isUuid } from '../common/validation';
import { OnlineMetrics } from '../odoo-rpc/online-metrics';
import { PartStatus, TRANSFER_REJECT_REASONS, TransferRejectReason, isInternalPaid, orderHasPayment, partStatusFromOdoo } from './online.model';
import { ONLINE_REPO, OnlineRepo } from './online.repository';
import { OnlineSagaService } from './online-saga.service';
import { createHash, randomUUID } from 'node:crypto';

export const ORDER_EVENTS_CONSUMER = 'online.order-events';
/** L-58: `refund_settled` = kasir sudah me-refund order POS pelunasan (pesanan counter). `refunded` = refund DIMINTA saja. */
export const KNOWN_EVENTS = [
  'placed',
  'paid',
  'confirmed',
  'stock_short',
  'ready',
  'picked_up',
  'cancelled',
  'refunded',
  'review',
  'refund_settled',
  // G22 §14.B B-7(b): tiket dapur pertama in_progress → kitchen_started_at, TANPA transisi status
  'preparing',
  // G26 §17.5 (L-100): Verifikator menolak bukti transfer; bagian tetap `placed`, pelanggan unggah ulang
  'transfer_rejected',
] as const;
export const EVENT_BODY_LIMIT = 8 * 1024;
/** Order yang sudah ditutup tanpa pembayaran BFF (L-57: hanya di sini `cancelled → review` boleh untuk counter). */
const FINAL_UNPAID: readonly string[] = ['cancelled', 'expired', 'failed'];

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
  refund: {
    refund_ref: string;
    amount: number;
    kind: 'full' | 'lines';
    initiator: 'bff' | 'odoo';
    initiated_by: string | null;
    /** G22 §14.C.4 (opsional): porsi poin, tunai, eligible earn yang direfund. */
    points_value?: number;
    cash_amount?: number;
    eligible_amount_refunded?: number;
  } | null;
  /** G22 §14.C.4: `payment.points_value` pada `paid` counter (null = tidak dikirim). */
  payment_points_value?: number | null;
  /** G22 B-7(a): `ready_source` (`kds`|`manual`) — informasi saja. */
  ready_source?: 'kds' | 'manual' | null;
  /** G22 B-7(b): `kitchen_started_at` event `preparing`. */
  kitchen_started_at?: Date | null;
  /** G22 §14.C.4: `picked_up.loyalty.eligible_amount` (hanya bila kanal loyalty_earn). */
  loyalty_eligible_amount?: number | null;
  /**
   * G20 §12.2 no. 7: `payment: { method }` pada event `paid` (tanpa identitas kasir). `null` = tidak dikirim (event
   * lain / Odoo pra-G20). Field lain di objek `payment` diabaikan (minimisasi: tidak disimpan).
   */
  payment_method: 'qris' | 'counter' | 'bank_transfer' | null;
  /** G26 §17.5: alasan `transfer_rejected` (allowlist; nilai lain → 400). */
  transfer_reason?: TransferRejectReason | null;
  /**
   * L-57: alasan event `review` dari Odoo E-45. Hanya nilai di `REVIEW_REASONS` yang dipakai (nilai lain → null,
   * event tetap diproses seperti review biasa). `counter_paid_after_*` = kasir melunasi pesanan counter yang sudah
   * batal/kedaluwarsa (uang sudah diterima di POS).
   */
  review_reason: ReviewReason | null;
  /**
   * L-58: payload event `refund_settled` — `{ refund: { refund_ref, amount, settled_at, provider_refund_ref: "pos:<uuid>" } }`.
   * Hanya terisi untuk event itu (field `refund` event lain tetap di `refund`).
   */
  settlement: CounterSettlement | null;
}

export interface CounterSettlement {
  refund_ref: string;
  amount: number;
  settled_at: Date | null;
  provider_refund_ref: string;
}

export const LATE_COUNTER_REVIEW_REASONS = [
  'counter_paid_after_cancel',
  'counter_paid_after_expiry',
  // G26 §17.5 no. 3: Verifikator menyetujui transfer setelah pesanan batal/kedaluwarsa (dana sudah masuk rekening PT)
  'transfer_paid_after_cancel',
  'transfer_paid_after_expiry',
] as const;
export type ReviewReason = (typeof LATE_COUNTER_REVIEW_REASONS)[number];

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
  const fields: unknown[] = [ev.platform_order_ref, ev.checkout_ref, ev.channel_code, ev.company_code, ev.event, ev.state, ev.seq, rf];
  // G20: metode bayar ikut sidik HANYA bila ada → sidik event pra-G20 tidak berubah (dedup lintas versi tetap cocok)
  if (ev.payment_method !== null) fields.push(['payment', ev.payment_method]);
  if (ev.review_reason !== null) fields.push(['review_reason', ev.review_reason]);
  if (ev.transfer_reason !== undefined && ev.transfer_reason !== null) fields.push(['transfer', ev.transfer_reason]);
  if (ev.settlement !== null) {
    const s = ev.settlement;
    fields.push(['settlement', s.refund_ref, s.amount, s.provider_refund_ref, s.settled_at ? s.settled_at.toISOString() : null]);
  }
  // G22: kunci baru ikut sidik HANYA bila ada (sidik event pra-G22 tidak berubah)
  if (ev.refund && (ev.refund.points_value !== undefined || ev.refund.cash_amount !== undefined || ev.refund.eligible_amount_refunded !== undefined)) {
    fields.push(['refund_points', ev.refund.points_value ?? null, ev.refund.cash_amount ?? null, ev.refund.eligible_amount_refunded ?? null]);
  }
  if (ev.payment_points_value !== undefined && ev.payment_points_value !== null) fields.push(['payment_points', ev.payment_points_value]);
  if (ev.loyalty_eligible_amount !== undefined && ev.loyalty_eligible_amount !== null) fields.push(['loyalty', ev.loyalty_eligible_amount]);
  const canonical = JSON.stringify(fields);
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
  const eventName = str('event', /^[a-z_]{1,32}$/);
  let refund: OdooOrderEvent['refund'] = null;
  let settlement: CounterSettlement | null = null;
  if (eventName === 'refund_settled') {
    // L-58: bentuk khusus; wajib lengkap (selain itu 400, final di Odoo)
    const rf = o.refund as Record<string, unknown> | null | undefined;
    if (typeof rf !== 'object' || rf === null || Array.isArray(rf)) throw Errors.invalidRequest();
    if (typeof rf.refund_ref !== 'string' || rf.refund_ref.length < 1 || rf.refund_ref.length > 128) throw Errors.invalidRequest();
    if (typeof rf.amount !== 'number' || !Number.isSafeInteger(rf.amount) || rf.amount < 1) throw Errors.invalidRequest();
    if (typeof rf.provider_refund_ref !== 'string' || !/^pos:[0-9a-fA-F-]{36}$/.test(rf.provider_refund_ref)) throw Errors.invalidRequest();
    const at = typeof rf.settled_at === 'string' && !Number.isNaN(Date.parse(rf.settled_at)) ? new Date(rf.settled_at) : null;
    settlement = { refund_ref: rf.refund_ref, amount: rf.amount, settled_at: at, provider_refund_ref: rf.provider_refund_ref.toLowerCase() };
  } else if (o.refund !== null && o.refund !== undefined && o.refund !== false) {
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
    // G22 §14.C.4: rincian poin (opsional; bila ada wajib integer Rupiah ≥ 0, points_value ≤ amount)
    const idr = (k: string): number | undefined => {
      const v = rf[k];
      if (v === undefined || v === null || v === false) return undefined;
      if (typeof v !== 'number' || !Number.isFinite(v) || v < 0 || v > 1e12) throw Errors.invalidRequest();
      return Math.floor(v + 1e-9);
    };
    const pv = idr('points_value');
    const cash = idr('cash_amount');
    const elig = idr('eligible_amount_refunded');
    if (pv !== undefined && pv > rf.amount) throw Errors.invalidRequest();
    if (pv !== undefined) refund.points_value = pv;
    if (cash !== undefined) refund.cash_amount = cash;
    if (elig !== undefined) refund.eligible_amount_refunded = elig;
  }
  let paymentMethod: OdooOrderEvent['payment_method'] = null;
  let paymentPoints: number | null = null;
  if (o.payment !== null && o.payment !== undefined && o.payment !== false) {
    const p = o.payment as Record<string, unknown>;
    if (typeof p !== 'object' || Array.isArray(p) || (p.method !== 'counter' && p.method !== 'qris' && p.method !== 'bank_transfer')) throw Errors.invalidRequest();
    paymentMethod = p.method;
    if (p.points_value !== undefined && p.points_value !== null && p.points_value !== false) {
      if (typeof p.points_value !== 'number' || !Number.isSafeInteger(p.points_value) || p.points_value < 0) throw Errors.invalidRequest();
      paymentPoints = p.points_value;
    }
  }
  // G22 B-7 / §14.C.4
  const readySource = o.ready_source === 'kds' || o.ready_source === 'manual' ? o.ready_source : null;
  const ksRaw = o.kitchen_started_at;
  const kitchenStarted = typeof ksRaw === 'string' && !Number.isNaN(Date.parse(ksRaw)) ? new Date(ksRaw) : null;
  let loyaltyEligible: number | null = null;
  if (o.loyalty !== undefined && o.loyalty !== null && o.loyalty !== false) {
    const l = o.loyalty as Record<string, unknown>;
    if (typeof l !== 'object' || Array.isArray(l)) throw Errors.invalidRequest();
    const e = l.eligible_amount;
    if (typeof e !== 'number' || !Number.isFinite(e) || e < 0 || e > 1e12) throw Errors.invalidRequest();
    loyaltyEligible = Math.floor(e + 1e-9);
  }
  let transferReason: TransferRejectReason | null = null;
  if (eventName === 'transfer_rejected') {
    const t = o.transfer as Record<string, unknown> | null | undefined;
    if (typeof t !== 'object' || t === null || Array.isArray(t)) throw Errors.invalidRequest();
    if (typeof t.reason !== 'string' || !(TRANSFER_REJECT_REASONS as readonly string[]).includes(t.reason)) throw Errors.invalidRequest();
    transferReason = t.reason as TransferRejectReason;
  }
  const rr = o.review_reason;
  const reviewReason: ReviewReason | null =
    typeof rr === 'string' && (LATE_COUNTER_REVIEW_REASONS as readonly string[]).includes(rr) ? (rr as ReviewReason) : null;
  return {
    idempotency_key: key,
    event_id: eventId,
    seq,
    platform_order_ref: ref,
    checkout_ref: checkout,
    channel_code: str('channel_code', /^[A-Z][A-Z0-9-]{1,15}$/),
    company_code: company,
    event: eventName,
    state: str('state', /^[a-z_]{1,32}$/),
    occurred_at: occurred,
    refund,
    payment_method: paymentMethod,
    transfer_reason: transferReason,
    review_reason: reviewReason,
    settlement,
    payment_points_value: paymentPoints,
    ready_source: readySource,
    kitchen_started_at: kitchenStarted,
    loyalty_eligible_amount: loyaltyEligible,
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
    /** G22 §14.C: earn/clawback poin online (opsional di test lama). */
    @Optional() @Inject(ONLINE_POINTS) private readonly points: OnlinePointsPort | null = null,
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

        // ── G20 §12.2 no. 7: bayar di kasir ──
        const order = await r.getOrder(part.order_id, true);
        if (!order) throw Errors.notFound();
        // G26: `counterOrder` = pelunasan lewat jalur internal Odoo (kasir POS ATAU Verifikator transfer bank)
        const counterOrder = isInternalPaid(order.payment_method);
        if ((ev.payment_method === 'counter' || ev.payment_method === 'bank_transfer') && ev.payment_method !== order.payment_method) {
          // Pelunasan internal untuk order metode lain = bug/serangan: tolak (409 final di Odoo) + alert, tidak mengubah status
          this.metrics.anomaly('payment_method_mismatch', 'critical', { order_id: order.id, part_id: part.id, state: ev.event, code: `${ev.payment_method}_on_${order.payment_method}` });
          throw Errors.conflict('payment_method_mismatch', 'Metode bayar tidak cocok dengan pesanan.');
        }
        if (counterOrder && ev.event === 'paid' && ev.payment_method !== order.payment_method) {
          this.metrics.anomaly('payment_method_mismatch', 'critical', { order_id: order.id, part_id: part.id, state: ev.event, code: `qris_on_${order.payment_method}` });
          throw Errors.conflict('payment_method_mismatch', 'Metode bayar tidak cocok dengan pesanan.');
        }
        // G26 §17.5 (L-100): bukti ditolak Verifikator → sub-status `rejected` + alasan; bagian tetap `placed` (unggah ulang)
        if (ev.event === 'transfer_rejected') {
          await r.updatePart(part.id, { last_seq: ev.seq, odoo_state: ev.state.slice(0, 32) });
          if (order.payment_method !== 'bank_transfer') {
            this.metrics.anomaly('payment_method_mismatch', 'critical', { order_id: order.id, part_id: part.id, state: ev.event, code: 'transfer_rejected_on_other' });
            throw Errors.conflict('payment_method_mismatch', 'Metode bayar tidak cocok dengan pesanan.');
          }
          if (order.status !== 'awaiting_payment' || part.status !== 'placed' || order.transfer_status !== 'awaiting_verification') return record('ignored_stale');
          await r.updateOrder(order.id, { transfer_status: 'rejected', transfer_reject_reason: ev.transfer_reason ?? null });
          await r.closeTransferProofs(order.id, 'rejected', ev.transfer_reason ?? null);
          await this.saga.notify(r, order, part, 'online_transfer_rejected');
          return record('applied');
        }
        // G22 B-7(b): `preparing` = tiket pertama in_progress → kitchen_started_at, TANPA transisi (peringkat §10.6.2 tetap)
        if (ev.event === 'preparing') {
          const at = ev.kitchen_started_at ?? ev.occurred_at ?? null;
          await r.updatePart(part.id, {
            last_seq: ev.seq,
            odoo_state: ev.state.slice(0, 32),
            ...(part.kitchen_started_at ? {} : { kitchen_started_at: at && at.getTime() <= Date.now() ? at : new Date() }),
          });
          return record('applied');
        }
        // G22 §14.C.4: nilai Poin EFN pelunasan kasir harus = porsi poin bagian (Odoo sendiri me-review bila beda)
        if (ev.event === 'paid' && ev.payment_points_value !== undefined && ev.payment_points_value !== null && ev.payment_points_value !== (part.points_value_idr ?? 0)) {
          this.metrics.anomaly('points_value_mismatch', 'critical', { order_id: order.id, part_id: part.id, expected: part.points_value_idr ?? 0, actual: ev.payment_points_value });
        }
        // L-58: kasir sudah me-refund order POS pelunasan → settle refund counter yang cocok (ref + order + nominal)
        if (ev.event === 'refund_settled') {
          await r.updatePart(part.id, { last_seq: ev.seq, odoo_state: ev.state.slice(0, 32) });
          const outcome = ev.settlement ? await this.saga.settleCounterRefund(r, order, part, ev.settlement) : 'invalid';
          if (outcome === 'settled') return record('applied');
          // L-60: `unapproved` (refund POS tanpa persetujuan/otorisasi) juga critical
          this.metrics.anomaly('refund_settled_rejected', outcome === 'qris' || outcome === 'mismatch' || outcome === 'unapproved' ? 'critical' : 'warning', {
            order_id: order.id,
            part_id: part.id,
            code: outcome,
          });
          return record('ignored_stale');
        }
        // L-57: kasir melunasi pesanan counter yang sudah batal/kedaluwarsa → `cancelled → review` (pengecualian monoton,
        // khusus counter + review_reason ini). Tanpa refund otomatis (CS memutuskan); order tetap ditutup.
        if (ev.event === 'review' && ev.review_reason !== null && counterOrder && FINAL_UNPAID.includes(order.status)) {
          this.metrics.anomaly('late_counter_payment', 'critical', { order_id: order.id, part_id: part.id, state: ev.state, code: ev.review_reason });
          const applied = await this.saga.applyPart(r, part, 'review', { source: 'odoo', odooState: ev.state, seq: ev.seq, lateCounterReview: true });
          return record(applied ? 'applied' : 'ignored_stale');
        }
        if (counterOrder && (ev.event === 'paid' || ev.event === 'confirmed' || ev.event === 'stock_short')) {
          const state = ev.event === 'paid' ? ev.state : ev.event;
          const applied = await this.saga.applyCounterPaid(r, order, part, { source: 'odoo', odooState: state, seq: ev.seq, paidAt: ev.occurred_at });
          if (!applied) {
            await r.updatePart(part.id, { last_seq: Math.max(part.last_seq, ev.seq), odoo_state: ev.state.slice(0, 32) });
            return record('ignored_stale');
          }
          return record('applied');
        }

        const target: PartStatus | null = ev.event === 'refunded' ? null : partStatusFromOdoo(ev.event === 'picked_up' ? 'picked_up' : ev.event);
        if (target) {
          // L-14: transisi yang tidak diterapkan (mundur/sama) dicatat `ignored_stale`, bukan `applied` (seq tetap maju)
          const applied = await this.saga.applyPart(r, part, target, { source: 'odoo', odooState: ev.state, seq: ev.seq, cancelReason: 'ops' });
          if (!applied) return record('ignored_stale');
          // G22 §14.C.3: earn saat diambil (eligible dari Odoo, hanya bila kanal loyalty_earn) — transaksi event yang sama
          if (ev.event === 'picked_up' && ev.loyalty_eligible_amount !== undefined && ev.loyalty_eligible_amount !== null && this.points) {
            await this.points.earnOnPickup(r, order, part, ev.loyalty_eligible_amount);
          }
        } else {
          await r.updatePart(part.id, { last_seq: ev.seq, odoo_state: ev.state });
        }
        if (ev.event === 'review') {
          this.metrics.anomaly('odoo_review', 'warning', { part_id: part.id, order_id: part.order_id, state: ev.state });
        }
        // L-58: `refunded`/`cancelled` = refund DIMINTA (nota kredit), tidak pernah menandai settled
        if (ev.refund && ev.refund.initiator === 'odoo' && (ev.event === 'cancelled' || ev.event === 'refunded')) {
          // G20: order bayar di kasir yang sudah lunas juga punya dana (refund tanpa intent, lewat POS)
          if (!orderHasPayment(order)) {
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
              // G22 §14.C.4
              points_value: ev.refund.points_value ?? 0,
              cash_amount: ev.refund.cash_amount ?? null,
            });
            if (rf.status === 'approval_required') {
              this.metrics.refund('approval_required');
              this.metrics.anomaly('odoo_refund_needs_approval', 'warning', { refund_id: rf.id, part_id: part.id, actual: ev.refund.amount });
            }
          }
        }
        // G22 §14.C.3 / D-22-11: clawback earn saat refund DIMINTA (`refunded`), proporsional eligible yang direfund
        if (ev.event === 'refunded' && ev.refund && (ev.refund.eligible_amount_refunded ?? 0) > 0 && this.points) {
          const fresh = await r.getPart(part.id, true);
          const row = (await r.refundsOfPart(part.id)).find((x) => x.odoo_refund_ref === ev.refund?.refund_ref);
          const key = row
            ? `online_reverse:${row.id}`
            : `online_reverse:${part.id}:${createHash('sha256').update(ev.refund.refund_ref, 'utf8').digest('hex').slice(0, 16)}`;
          if (fresh) await this.points.clawbackOnRefund(r, fresh, key, ev.refund.eligible_amount_refunded ?? 0);
        }
        await this.saga.refreshOrder(r, part.order_id);
        return record('applied');
      });
    } catch (err) {
      if (err instanceof ApiError) {
        const code = (err.body as { error?: { code?: unknown } }).error?.code;
        // G20: 409 metode bayar tidak cocok diberi label sendiri (bukan company_mismatch)
        this.metrics.event(ev.event, err.getStatus() === 404 ? 'not_found' : code === 'payment_method_mismatch' ? 'payment_method_mismatch' : 'company_mismatch');
      }
      throw err;
    }
    this.metrics.event(ev.event, outcome);
    this.logger.log(`Event ${ev.event} bagian ${ev.platform_order_ref} seq ${ev.seq}: ${outcome}`);
    return { ok: true, status: outcome };
  }
}
