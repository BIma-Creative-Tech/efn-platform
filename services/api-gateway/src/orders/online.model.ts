import { TaxGroup } from '../odoo-rpc/odoo-online.types';

/** Status platform order (docs/04 §10.6.1). */
export type OrderStatus = 'created' | 'awaiting_payment' | 'paid' | 'fulfilling' | 'completed' | 'cancelled' | 'expired' | 'failed' | 'review';
/** Status bagian per SO (docs/04 §10.6.2). */
export type PartStatus =
  | 'placing'
  | 'placed'
  | 'place_failed'
  | 'paying'
  | 'confirmed'
  | 'stock_short'
  | 'review'
  | 'ready'
  | 'handed_over'
  | 'cancelled';
export type CloseReason = 'payment_expired' | 'customer' | 'place_failed' | 'stock_short' | 'ops' | 'review';
export type PartCancelReason = CloseReason | 'late_payment';
export type RefundKind = 'full' | 'lines' | 'late_payment';
export type RefundReason = 'stock_short' | 'customer_cs' | 'ops_cancelled' | 'review' | 'late_payment';
/**
 * `approval_required` (SEC-95/L-16): refund inisiatif Odoo menunggu persetujuan CS (CLI). `gateway_in_flight` (SEC-97):
 * klaim eksklusif sebelum memanggil gateway.
 */
export type RefundStatus =
  | 'requested'
  | 'odoo_done'
  | 'approval_required'
  | 'gateway_in_flight'
  | 'gateway_pending'
  | 'gateway_done'
  | 'settled'
  | 'manual_required'
  | 'failed';
/** SEC-97: refund yang dananya sudah/sedang keluar — ikut dihitung dalam batas porsi. */
export const MONEY_OUT_REFUND: readonly RefundStatus[] = ['gateway_in_flight', 'gateway_pending', 'gateway_done', 'settled'];

export const FINAL_ORDER: ReadonlySet<OrderStatus> = new Set(['completed', 'cancelled', 'expired', 'failed']);
export const UNPAID_OPEN: readonly OrderStatus[] = ['created', 'awaiting_payment'];
export const FINAL_PART: ReadonlySet<PartStatus> = new Set(['handed_over', 'cancelled', 'place_failed']);
export const FINAL_REFUND: ReadonlySet<RefundStatus> = new Set(['settled', 'failed', 'manual_required']);
/** L-4: bagian yang menahan penghapusan akun (`409 open_online_orders`). */
export const OPEN_PAID_PARTS: readonly PartStatus[] = ['paying', 'confirmed', 'ready', 'review'];

/** Peringkat monoton §10.6.2. */
export const PART_RANK: Readonly<Record<PartStatus, number>> = {
  placing: 0,
  placed: 1,
  paying: 2,
  confirmed: 3,
  stock_short: 3,
  review: 3,
  ready: 4,
  handed_over: 5,
  cancelled: 5,
  place_failed: 5,
};

/**
 * Transisi bagian yang diizinkan: naik peringkat, dengan dua pengecualian:
 * - `review → confirmed|cancelled` (diselesaikan ops);
 * - L-14 (docs/04 §10.17, G17-QA-03): `confirmed|ready → review` dari Odoo SELALU diterapkan (mis. kode ambil salah 5×)
 *   walau peringkatnya turun — kode ambil ditahan selama pemeriksaan.
 */
export function canTransitionPart(from: PartStatus, to: PartStatus): boolean {
  if (from === to) return false;
  if (to === 'review' && (from === 'confirmed' || from === 'ready')) return true;
  if (from === 'review' && (to === 'confirmed' || to === 'cancelled')) return true;
  return PART_RANK[to] > PART_RANK[from];
}

/** Pemetaan state `efn.online.order` Odoo → status bagian (§10.6.2). `null` = tidak mengubah status. */
export function partStatusFromOdoo(state: string): PartStatus | null {
  switch (state) {
    case 'placed':
      return 'placed';
    case 'paid':
      return 'paying';
    case 'confirmed':
      return 'confirmed';
    case 'stock_short':
      return 'stock_short';
    case 'ready':
      return 'ready';
    case 'picked_up':
      return 'handed_over';
    case 'cancelled':
      return 'cancelled';
    case 'review':
      return 'review';
    default:
      return null;
  }
}

/**
 * Setelah `paid`, status order DITURUNKAN dari bagian (§10.6.1): semua final → `completed` (≥ 1 diserahkan) atau
 * `cancelled`; ≥ 1 bagian melewati `paying` → `fulfilling`. Status lain (termasuk `review`) tidak diubah di sini.
 */
export function deriveOrderStatus(current: OrderStatus, parts: readonly PartStatus[]): OrderStatus {
  if (current !== 'paid' && current !== 'fulfilling') return current;
  if (parts.length > 0 && parts.every((s) => FINAL_PART.has(s))) {
    return parts.some((s) => s === 'handed_over') ? 'completed' : 'cancelled';
  }
  if (parts.some((s) => PART_RANK[s] >= 3)) return 'fulfilling';
  return current;
}

export interface PartLine {
  product_id: number;
  name: string;
  qty: number;
  price_unit_incl: number;
  subtotal_incl: number;
  tax_labels: string[];
}

export interface OrderRow {
  id: string;
  member_id: string | null;
  idempotency_key: string;
  request_sha256: string;
  quote_id: string;
  status: OrderStatus;
  amount_payable: number;
  currency: 'IDR';
  payment_intent_id: string | null;
  expires_at: Date;
  paid_at: Date | null;
  closed_at: Date | null;
  close_reason: CloseReason | null;
  created_at: Date;
  updated_at: Date;
}

export interface PartRow {
  id: string;
  order_id: string;
  channel_code: string;
  company_code: string;
  outlet_code: string;
  fulfilment: 'pickup' | 'delivery';
  status: PartStatus;
  lines: PartLine[];
  tax_groups: TaxGroup[];
  amount_total: number;
  amount_payable: number;
  quote_hash: string;
  sale_order_name: string | null;
  odoo_state: string | null;
  last_seq: number;
  pickup_code_enc: Buffer | null;
  refunded_amount: number;
  confirmed_at: Date | null;
  ready_at: Date | null;
  handed_over_at: Date | null;
  cancelled_at: Date | null;
  cancel_reason: PartCancelReason | null;
  created_at: Date;
  updated_at: Date;
}

export interface RefundRow {
  id: string;
  part_id: string;
  payment_intent_id: string;
  kind: RefundKind;
  reason: RefundReason;
  lines: { product_id: number; qty: number }[] | null;
  amount: number | null;
  odoo_refund_ref: string | null;
  provider_refund_ref: string | null;
  status: RefundStatus;
  /** SEC-98: alasan kegagalan (mis. `refund_exceeds_portion`) — settle manual ditolak untuk alasan ini. */
  failure_reason: string | null;
  requested_by: 'system' | 'cs';
  created_at: Date;
  updated_at: Date;
  settled_at: Date | null;
}

export interface PortionRow {
  payment_intent_id: string;
  part_id: string;
  channel_code: string;
  company_code: string;
  amount: number;
}

/** Kolom bagian yang boleh di-patch (nama kolom = kunci; dicek whitelist di repo PG). */
export type PartPatch = Partial<
  Pick<
    PartRow,
    | 'status'
    | 'sale_order_name'
    | 'odoo_state'
    | 'last_seq'
    | 'pickup_code_enc'
    | 'refunded_amount'
    | 'confirmed_at'
    | 'ready_at'
    | 'handed_over_at'
    | 'cancelled_at'
    | 'cancel_reason'
    | 'amount_total'
    | 'amount_payable'
  >
>;
export type OrderPatch = Partial<
  Pick<OrderRow, 'status' | 'payment_intent_id' | 'paid_at' | 'closed_at' | 'close_reason' | 'amount_payable' | 'expires_at'>
>;
export type RefundPatch = Partial<Pick<RefundRow, 'status' | 'amount' | 'odoo_refund_ref' | 'provider_refund_ref' | 'settled_at' | 'failure_reason'>>;

/** Pesan RabbitMQ saga (§10.9), `schema_version: 1`, tanpa PII. */
export interface OrderPaidMessage {
  schema_version: 1;
  idempotency_key: string;
  order_id: string;
  payment_id: string;
}
export interface UnpaidClosedMessage {
  schema_version: 1;
  idempotency_key: string;
  order_id: string;
  reason: 'payment_expired' | 'customer' | 'place_failed';
}
export interface CompensateMessage {
  schema_version: 1;
  idempotency_key: string;
  part_id: string;
  reason: 'stock_short' | 'late_payment' | 'review';
}
export interface RefundRequestedMessage {
  schema_version: 1;
  idempotency_key: string;
  refund_id: string;
}

export function orderPaidMessage(orderId: string, paymentId: string): OrderPaidMessage {
  return { schema_version: 1, idempotency_key: `order.paid:${orderId}:${paymentId}`, order_id: orderId, payment_id: paymentId };
}
export function unpaidClosedMessage(orderId: string, reason: UnpaidClosedMessage['reason']): UnpaidClosedMessage {
  return { schema_version: 1, idempotency_key: `order.unpaid_closed:${orderId}`, order_id: orderId, reason };
}
export function compensateMessage(partId: string, reason: CompensateMessage['reason']): CompensateMessage {
  return { schema_version: 1, idempotency_key: `order.compensate:${partId}:${reason}`, part_id: partId, reason };
}
export function refundRequestedMessage(refundId: string): RefundRequestedMessage {
  return { schema_version: 1, idempotency_key: `order.refund.requested:${refundId}`, refund_id: refundId };
}

export const NOTIFY_TEMPLATES_ONLINE = ['online_order_ready', 'online_order_cancelled', 'online_order_refunded'] as const;
export type OnlineNotifyTemplate = (typeof NOTIFY_TEMPLATES_ONLINE)[number];
