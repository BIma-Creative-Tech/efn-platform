import { TaxGroup } from '../odoo-rpc/odoo-online.types';

/**
 * Status platform order (docs/04 §10.6.1). G20 §12.2 (W-30/E-45): `awaiting_counter_payment` = order bayar di kasir
 * (tanpa payment intent) menunggu pelunasan di POS; event `paid` Odoo → `paid` → `fulfilling`.
 */
export type OrderStatus =
  | 'created'
  | 'awaiting_payment'
  | 'awaiting_counter_payment'
  | 'paid'
  | 'fulfilling'
  | 'completed'
  | 'cancelled'
  | 'expired'
  | 'failed'
  | 'review';
/**
 * G20 §12.2 no. 2: metode bayar pesanan online (`qris` = gateway, `counter` = bayar di kasir, satu PT saja).
 * G26 §17.2 (L-95/L-96): + `bank_transfer` (transfer ke rekening PT penjual + bukti, diverifikasi staf Odoo; satu PT,
 * tanpa payment intent). `counter` dibekukan untuk pesanan baru (hanya dengan `ONLINE_COUNTER_LEGACY`).
 */
export type PaymentMethod = 'qris' | 'counter' | 'bank_transfer';
export const PAYMENT_METHODS: readonly PaymentMethod[] = ['qris', 'counter', 'bank_transfer'];
export function isPaymentMethod(v: unknown): v is PaymentMethod {
  return v === 'qris' || v === 'counter' || v === 'bank_transfer';
}
/** Metode yang dilunasi lewat jalur INTERNAL Odoo (kasir POS / Verifikator), tanpa payment intent gateway. */
export function isInternalPaid(m: PaymentMethod): boolean {
  return m === 'counter' || m === 'bank_transfer';
}
/** G26 §17.3: sub-status pembayaran transfer bank selama order `awaiting_payment`. */
export type TransferStatus = 'awaiting_proof' | 'awaiting_verification' | 'rejected';
/** G26 §17.5: alasan tolak Verifikator (allowlist; sama dengan Odoo `REJECT_REASONS`). */
export const TRANSFER_REJECT_REASONS = ['amount_mismatch', 'unreadable', 'wrong_account', 'not_received', 'duplicate_proof'] as const;
export type TransferRejectReason = (typeof TRANSFER_REJECT_REASONS)[number];
/** G26 §17.3: rekening tujuan (milik PT penjual, bukan PII pelanggan) dari hasil place Odoo. */
export interface TransferAccount {
  bank_name: string;
  account_number: string;
  account_holder: string;
}
/** G26 §17.8: refund pesanan transfer bank selalu manual (Finance mentransfer balik). */
export const TRANSFER_REFUND_REASON = 'transfer_refund_manual';
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
/** Order belum dibayar yang masih terbuka (batas order terbuka, erasure, batal pelanggan, kedaluwarsa). */
export const UNPAID_OPEN: readonly OrderStatus[] = ['created', 'awaiting_payment', 'awaiting_counter_payment'];
export const FINAL_PART: ReadonlySet<PartStatus> = new Set(['handed_over', 'cancelled', 'place_failed']);
export const FINAL_REFUND: ReadonlySet<RefundStatus> = new Set(['settled', 'failed', 'manual_required']);
/** L-4: bagian yang menahan penghapusan akun (`409 open_online_orders`). */
export const OPEN_PAID_PARTS: readonly PartStatus[] = ['paying', 'confirmed', 'ready', 'review'];

/**
 * G20 §12.2 no. 8: refund order bayar di kasir tidak lewat gateway → `manual_required` dengan alasan ini (CS melakukan
 * refund order POS pelunasan ke metode asal lalu `online-refund-settle --provider-ref <ref refund POS>`).
 */
export const COUNTER_REFUND_REASON = 'counter_refund_via_pos';
/** L-60: refund counter disetujui approver, otorisasi Odoo `efn_online_counter_refund_authorize` belum berhasil. */
export const COUNTER_REFUND_AUTH_PENDING = 'counter_refund_auth_pending';
/** L-60: Odoo menolak otorisasi refund POS — tetap `manual_required`, CS memutuskan. */
export const COUNTER_REFUND_AUTH_REJECTED = 'counter_refund_auth_rejected';

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

/**
 * Order sudah dibayar (dana diterima): QRIS = punya intent; bayar di kasir = `paid_at` terisi (event `paid` Odoo, §12.2
 * no. 7). Dipakai untuk refund (CS/Odoo/kompensasi) — order `counter` tidak pernah punya intent gateway.
 */
export function orderHasPayment(o: Pick<OrderRow, 'payment_intent_id' | 'payment_method' | 'paid_at'>): boolean {
  return o.payment_intent_id !== null || (isInternalPaid(o.payment_method) && o.paid_at !== null);
}

export interface PartLine {
  product_id: number;
  name: string;
  qty: number;
  price_unit_incl: number;
  subtotal_incl: number;
  tax_labels: string[];
  /**
   * G19 §11.2 no. 3: baris reward promo/kupon dari Odoo (harga ≤ 0). Hanya ada bila `true` (baris produk tidak membawa
   * field ini). Tidak pernah dikirim ke `efn_online_place` dan tidak dihitung sebagai baris refund.
   */
  reward?: true;
  /** G22 B-4 / L-78: hanya untuk baris beratribut. */
  attribute_value_ids?: number[];
  attributes_text?: string | null;
}

/** G22 §14.C.6: status earn poin online per bagian. */
export type PartEarnStatus = 'none' | 'pending' | 'earned' | 'skipped' | 'limited';

export interface OrderRow {
  id: string;
  member_id: string | null;
  idempotency_key: string;
  request_sha256: string;
  quote_id: string;
  status: OrderStatus;
  amount_payable: number;
  currency: 'IDR';
  /** G20 §12.2: `qris` (default) | `counter` (tanpa intent; `payment_intent_id` selalu NULL). */
  payment_method: PaymentMethod;
  payment_intent_id: string | null;
  expires_at: Date;
  paid_at: Date | null;
  closed_at: Date | null;
  close_reason: CloseReason | null;
  created_at: Date;
  updated_at: Date;
  /** G22 §14.C.6: Σ nilai Poin EFN (Rupiah) semua bagian; absen/0 = tanpa poin. */
  amount_points_value?: number;
  /** G22: Σ porsi tunai (= amount_payable − amount_points_value); null = order pra-G22/tanpa poin. */
  amount_cash?: number | null;
  // ── G26 §17.3 (hanya order `bank_transfer`; null/0 untuk metode lain) ──
  transfer_status?: TransferStatus | null;
  transfer_ref?: string | null;
  transfer_account?: TransferAccount | null;
  transfer_uploads?: number;
  transfer_reject_reason?: TransferRejectReason | null;
}

/** Porsi TUNAI bagian (G22): `amount_cash` bila terisi, selain itu `amount_payable` (tanpa poin). */
export function partCash(p: Pick<PartRow, 'amount_payable' | 'amount_cash'>): number {
  return p.amount_cash !== undefined && p.amount_cash !== null ? p.amount_cash : p.amount_payable;
}
export function orderCash(o: Pick<OrderRow, 'amount_payable' | 'amount_cash'>): number {
  return o.amount_cash !== undefined && o.amount_cash !== null ? o.amount_cash : o.amount_payable;
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
  /**
   * G20 §12.2 no. 3: kode BAYAR di kasir dari Odoo (≠ kode ambil), AES-256-GCM AAD
   * `efn:online_order_part.counter_code_enc:v2:<part_id>`. Hanya terisi selama bagian `placed` pada order
   * `awaiting_counter_payment`; di-NULL-kan saat bagian berpindah status / order ditutup.
   */
  counter_code_enc: Buffer | null;
  refunded_amount: number;
  confirmed_at: Date | null;
  ready_at: Date | null;
  handed_over_at: Date | null;
  cancelled_at: Date | null;
  cancel_reason: PartCancelReason | null;
  created_at: Date;
  updated_at: Date;
  // ── G22 §14.C.6 (opsional di tipe: fixture/data pra-G22) ──
  points_redeemed?: number;
  points_value_idr?: number;
  amount_cash?: number | null;
  loyalty_reservation_id?: string | null;
  eligible_amount?: number | null;
  earn_status?: PartEarnStatus;
  points_earned?: number;
  /** Nilai Rupiah poin yang sudah dikembalikan (restore). */
  points_restored_value?: number;
  /** B-7: tiket dapur pertama `in_progress` (event `preparing`). */
  kitchen_started_at?: Date | null;
  /** G23 §15.4: jadwal ambil pre-order (null = secepatnya). */
  pickup_slot?: Date | null;
}

export interface RefundRow {
  id: string;
  part_id: string;
  /** NULL = order bayar di kasir (§12.2 no. 8): BFF tidak memanggil gateway; uang keluar lewat refund POS (E-31). */
  payment_intent_id: string | null;
  kind: RefundKind;
  reason: RefundReason;
  lines: { product_id: number; qty: number }[] | null;
  amount: number | null;
  odoo_refund_ref: string | null;
  provider_refund_ref: string | null;
  status: RefundStatus;
  /** SEC-98: alasan kegagalan (mis. `refund_exceeds_portion`) — settle manual ditolak untuk alasan ini. */
  failure_reason: string | null;
  /**
   * SEC-95: pembuat/pemicu refund — aktor CS `<user>/<host>`, `odoo-uid:<n>`, atau `odoo-login:<login>`. `null` = sistem
   * atau data lama (tidak pernah menjadi alasan menolak persetujuan).
   */
  initiated_by: string | null;
  /** SEC-106: token klaim `gateway_in_flight` (CAS penulisan pasca-gateway); NULL di luar klaim. */
  claim_token: string | null;
  requested_by: 'system' | 'cs';
  created_at: Date;
  updated_at: Date;
  settled_at: Date | null;
  /** G22 §14.C.4: porsi Poin EFN (Rupiah) dalam `amount` (dari Odoo / dihitung BFF untuk kompensasi tanpa dokumen). */
  points_value?: number;
  /** G22: porsi tunai ke gateway/POS (= amount − points_value); null = data lama (= amount). */
  cash_amount?: number | null;
}

/** Nominal tunai refund (G22): `cash_amount` bila ada, selain itu `amount`. */
export function refundCash(r: Pick<RefundRow, 'amount' | 'cash_amount'>): number {
  return r.cash_amount !== undefined && r.cash_amount !== null ? r.cash_amount : (r.amount ?? 0);
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
    | 'counter_code_enc'
    | 'refunded_amount'
    | 'confirmed_at'
    | 'ready_at'
    | 'handed_over_at'
    | 'cancelled_at'
    | 'cancel_reason'
    | 'amount_total'
    | 'amount_payable'
    | 'points_redeemed'
    | 'points_value_idr'
    | 'amount_cash'
    | 'loyalty_reservation_id'
    | 'eligible_amount'
    | 'earn_status'
    | 'points_earned'
    | 'points_restored_value'
    | 'kitchen_started_at'
  >
>;
export type OrderPatch = Partial<
  Pick<
    OrderRow,
    | 'status'
    | 'payment_intent_id'
    | 'paid_at'
    | 'closed_at'
    | 'close_reason'
    | 'amount_payable'
    | 'expires_at'
    | 'amount_points_value'
    | 'amount_cash'
    | 'transfer_status'
    | 'transfer_account'
    | 'transfer_uploads'
    | 'transfer_reject_reason'
  >
>;
export type RefundPatch = Partial<
  Pick<
    RefundRow,
    'status' | 'amount' | 'odoo_refund_ref' | 'provider_refund_ref' | 'settled_at' | 'failure_reason' | 'claim_token' | 'points_value' | 'cash_amount'
  >
>;

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

export const NOTIFY_TEMPLATES_ONLINE = ['online_order_ready', 'online_order_cancelled', 'online_order_refunded', 'online_transfer_rejected'] as const;
export type OnlineNotifyTemplate = (typeof NOTIFY_TEMPLATES_ONLINE)[number];
