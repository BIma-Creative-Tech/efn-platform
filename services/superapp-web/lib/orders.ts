/**
 * Pesanan online (docs/04 §10.3.5–§10.3.7, §10.6) — tipe, label status, peta error K-ON5, hitung mundur.
 * Modul MURNI (client + server + test).
 *
 * Kode ambil (`pickup_code`) adalah RAHASIA: tidak ada di tipe order (kontrak tidak mengirimnya di status),
 * hanya di `PickupCodeItem` yang dipakai layar "Tampilkan kode ambil" — tidak pernah ke URL/log/analytics.
 */
import { parseQuoteLines, type QuoteLine, type TaxGroup } from './cart';
import { CURSOR_RE, isChannelCode, isOutletCode, isUuid } from './catalog';
import { formatCounterCode } from './payment';

export type OrderStatus =
  | 'created'
  | 'awaiting_payment'
  /** §12.2: pesanan "Bayar di kasir" menunggu pelunasan di kasir (kode bayar, bukan kode ambil). */
  | 'awaiting_counter_payment'
  | 'paid'
  | 'fulfilling'
  | 'review'
  | 'completed'
  | 'cancelled'
  | 'expired'
  | 'failed';

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

export const FINAL_ORDER_STATUSES: readonly OrderStatus[] = ['completed', 'cancelled', 'expired', 'failed'];
export const CANCELLABLE_STATUSES: readonly OrderStatus[] = ['created', 'awaiting_payment', 'awaiting_counter_payment'];
export const PICKUP_CODE_PART_STATUSES: readonly PartStatus[] = ['confirmed', 'ready'];

/** Polling klien §10.3.6: 3 dtk saat menunggu bayar, 30 dtk setelahnya, berhenti di status final. */
export function pollIntervalMs(status: OrderStatus): number | null {
  if (FINAL_ORDER_STATUSES.includes(status)) return null;
  if (status === 'awaiting_counter_payment') return 10_000; // kasir bisa lama; 10 dtk × 360 = 60 menit (= batas bayar)
  return status === 'awaiting_payment' || status === 'created' ? 3_000 : 30_000;
}

export const ORDER_STATUS_LABEL: Record<OrderStatus, string> = {
  created: 'Sedang dibuat',
  awaiting_payment: 'Menunggu pembayaran',
  awaiting_counter_payment: 'Menunggu pembayaran di kasir',
  paid: 'Dibayar, menunggu konfirmasi toko',
  fulfilling: 'Diproses toko',
  review: 'Sedang diperiksa tim kami',
  completed: 'Selesai',
  cancelled: 'Dibatalkan',
  expired: 'Kedaluwarsa (tidak dibayar)',
  failed: 'Gagal dibuat',
};

export const PART_STATUS_LABEL: Record<PartStatus, string> = {
  placing: 'Sedang dibuat',
  placed: 'Menunggu pembayaran',
  place_failed: 'Gagal dibuat',
  paying: 'Pembayaran dikonfirmasi ke toko',
  confirmed: 'Dikonfirmasi, sedang disiapkan',
  stock_short: 'Stok kurang — dibatalkan & dana dikembalikan',
  review: 'Sedang diperiksa tim kami',
  ready: 'Siap diambil',
  handed_over: 'Sudah diambil',
  cancelled: 'Dibatalkan',
};

const ORDER_STATUSES = Object.keys(ORDER_STATUS_LABEL) as OrderStatus[];
const PART_STATUSES = Object.keys(PART_STATUS_LABEL) as PartStatus[];

export function isOrderStatus(v: unknown): v is OrderStatus {
  return typeof v === 'string' && (ORDER_STATUSES as string[]).includes(v);
}

export function isPartStatus(v: unknown): v is PartStatus {
  return typeof v === 'string' && (PART_STATUSES as string[]).includes(v);
}

export function orderStatusLabel(s: string): string {
  return isOrderStatus(s) ? ORDER_STATUS_LABEL[s] : 'Status tidak dikenal';
}

export function partStatusLabel(s: string): string {
  return isPartStatus(s) ? PART_STATUS_LABEL[s] : 'Status tidak dikenal';
}

// ---------- Peta error pelanggan K-ON5 (§10.3.7) -> pesan Bahasa Indonesia ----------

export const ONLINE_ERROR_MESSAGES: Record<string, string> = {
  price_changed: 'Harga berubah sejak ringkasan dibuat. Kami sudah menghitung ulang — periksa total baru sebelum membayar.',
  out_of_stock: 'Sebagian barang habis. Kurangi atau hapus barang yang ditandai, lalu coba lagi.',
  item_unavailable: 'Sebagian barang atau toko sedang tidak tersedia. Periksa kembali keranjang Anda.',
  order_not_cancellable: 'Pesanan ini sudah dibayar atau diproses sehingga tidak bisa dibatalkan di aplikasi. Hubungi layanan pelanggan.',
  invalid_state: 'Status pesanan sudah berubah. Muat ulang halaman.',
  online_unavailable: 'Belanja online sedang tidak tersedia. Anda tetap bisa berbelanja langsung di toko. Silakan coba lagi nanti.',
  payment_provider_error: 'Pembayaran belum dapat diproses. Silakan coba lagi.',
  payment_provider_unavailable: 'Layanan pembayaran sedang tidak tersedia. Silakan coba lagi beberapa saat lagi.',
  invalid_request: 'Permintaan tidak valid. Muat ulang halaman lalu coba lagi.',
  not_found: 'Data tidak ditemukan.',
  quote_expired: 'Ringkasan harga sudah kedaluwarsa. Kami menghitung ulang total Anda.',
  cart_changed: 'Keranjang berubah setelah ringkasan dibuat. Kami menghitung ulang total Anda.',
  cart_has_issues: 'Ada barang yang tidak bisa dipesan. Perbaiki barang yang ditandai sebelum membayar.',
  cart_version_conflict: 'Keranjang Anda baru saja diubah di perangkat lain. Kami memuat versi terbaru.',
  idempotency_conflict: 'Pembayaran untuk ringkasan ini sedang diproses. Buka daftar pesanan Anda.',
  idempotency_key_required: 'Permintaan tidak valid. Muat ulang halaman lalu coba lagi.',
  too_many_open_orders: 'Anda masih punya beberapa pesanan yang belum dibayar. Selesaikan atau batalkan dulu sebelum membuat pesanan baru.',
  fulfilment_unavailable: 'Pengantaran belum tersedia. Silakan pilih ambil di toko.',
  pickup_code_unavailable: 'Kode ambil tersedia setelah pesanan dikonfirmasi toko.',
  // L-14 (G17-QA-03): bagian pesanan sedang diperiksa; kode ambil ditahan
  order_in_review: 'Pesanan Anda sedang diperiksa tim kami, jadi kode ambil ditahan sementara. Hubungi layanan pelanggan bila perlu bantuan.',
  // §12.2 (bayar di kasir)
  counter_payment_single_part_only:
    'Bayar di kasir hanya untuk pesanan dari satu PT. Pisahkan pesanan per PT, atau pilih pembayaran QRIS.',
  payment_method_unavailable: 'Metode pembayaran ini belum tersedia untuk pesanan Anda. Pilih metode lain atau coba lagi nanti.',
  // §13.5 / L-63: PT F&B (PB1) atau efn_pii belum aktif di Odoo
  invoice_not_available: 'Faktur atas nama belum tersedia untuk pesanan ini',
  rate_limited: 'Terlalu banyak permintaan. Tunggu sebentar lalu coba lagi.',
  // §14.C.2 (G22): poin sebagai alat bayar per PT
  insufficient_points: 'Saldo poin tidak cukup. Saldo Anda mungkin berubah; kami memperbarui ringkasan, silakan kurangi poin lalu coba lagi.',
  below_minimum: 'Jumlah poin di bawah minimum penukaran untuk PT ini. Ubah jumlah poin lalu coba lagi.',
  exceeds_max_pct: 'Jumlah poin melebihi batas penukaran untuk pesanan ini. Kurangi poin lalu coba lagi.',
  points_unavailable: 'Penukaran poin sedang tidak tersedia untuk pesanan ini. Anda tetap bisa membayar tanpa poin.',
  points_cover_full_amount: 'Poin tidak boleh menutup seluruh pembayaran. Sisakan minimal Rp1 untuk dibayar tunai, lalu kurangi poin.',
  // §11.2: satu pesan untuk semua penyebab kode promo tidak berlaku (anti-enumerasi)
  promo_invalid: 'Kode promo tidak dapat digunakan',
  unauthenticated: 'Sesi berakhir. Silakan masuk kembali.',
};

export const GENERIC_ONLINE_ERROR = 'Terjadi kendala. Silakan coba lagi beberapa saat lagi.';

export function onlineErrorMessage(code: string | null | undefined): string {
  if (code && Object.prototype.hasOwnProperty.call(ONLINE_ERROR_MESSAGES, code)) return ONLINE_ERROR_MESSAGES[code]!;
  return GENERIC_ONLINE_ERROR;
}

/** Kode yang berarti "quote harus dihitung ulang" (klien otomatis memanggil quote lagi). */
export const REQUOTE_CODES: readonly string[] = ['price_changed', 'quote_expired', 'cart_changed', 'out_of_stock', 'item_unavailable'];

/** Status HTTP BFF yang boleh diteruskan ke browser (dengan pesan dari peta di atas, bukan pesan BFF). */
export const PASSTHROUGH_STATUSES: readonly number[] = [400, 404, 409, 422, 429];

// ---------- hitung mundur ----------

/** Sisa detik sampai `expiresAt` (ISO). Tanggal rusak -> 0. `skewMs` = jam server − jam klien. */
export function remainingSeconds(expiresAt: string | null | undefined, nowMs: number, skewMs = 0): number {
  if (!expiresAt) return 0;
  const t = Date.parse(expiresAt);
  if (Number.isNaN(t)) return 0;
  return Math.max(0, Math.floor((t - (nowMs + skewMs)) / 1000));
}

export function formatCountdown(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  const m = Math.floor(s / 60);
  return `${String(m).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
}

/** Selisih jam server (header `Date`) − jam klien; header tidak ada/rusak -> 0. */
export function clockSkewMs(dateHeader: string | null, clientNowMs: number): number {
  if (!dateHeader) return 0;
  const t = Date.parse(dateHeader);
  if (Number.isNaN(t)) return 0;
  const skew = t - clientNowMs;
  // Header Date presisi 1 dtk; abaikan selisih kecil, batasi selisih ekstrem (jam rusak) ke ±1 jam.
  if (Math.abs(skew) < 2_000) return 0;
  return Math.max(-3_600_000, Math.min(3_600_000, skew));
}

// ---------- kontrak order ----------

export interface OrderPayment {
  method?: string;
  status: string;
  /** Hanya saat `awaiting_payment`. Dirender jadi QR di perangkat; tidak pernah di-log. */
  qr_string: string | null;
  expires_at: string | null;
  /**
   * Hanya saat `awaiting_counter_payment` (§12.2): kode bayar format `ABCD-EFGH`. Ditunjukkan ke kasir; bukan kode ambil.
   * Tidak pernah ke URL/storage/log/analytics.
   */
  counter_code: string | null;
}

export interface OrderPart {
  channel_code: string;
  company_code: string;
  outlet_code: string;
  outlet_name: string;
  status: PartStatus | string;
  sale_order_name: string | null;
  lines: QuoteLine[];
  tax_groups: TaxGroup[];
  amount_total: number;
  amount_payable: number;
  refunded_amount: number;
  ready_at: string | null;
  handed_over_at: string | null;
  cancel_reason: string | null;
  /** G22 (§14.C.2): poin yang ditukar untuk bagian ini, nilainya (Rp), dan sisa yang dibayar tunai. */
  points_redeemed: number;
  points_value_idr: number;
  /** null = BFF belum mengirim (tampilan memakai `amount_payable`). */
  amount_cash: number | null;
  /** Poin yang diperoleh setelah pesanan diambil, dan nilai poin yang dikembalikan saat refund. */
  points_earned: number;
  points_restored: number;
}

export interface OrderDetail {
  order_id: string;
  status: OrderStatus | string;
  created_at: string | null;
  expires_at: string | null;
  paid_at: string | null;
  amount_payable: number;
  /** G22: Σ nilai poin (Rp) dan Σ porsi tunai. `amount_cash` null = BFF belum mengirim. */
  amount_points_value: number;
  amount_cash: number | null;
  payment: OrderPayment | null;
  parts: OrderPart[];
}

export interface OrderSummary {
  order_id: string;
  status: OrderStatus | string;
  created_at: string | null;
  amount_payable: number;
  parts: { channel_code: string; status: PartStatus | string }[];
}

export interface OrderList {
  items: OrderSummary[];
  next_cursor: string | null;
}

export interface PickupCodeItem {
  channel_code: string;
  outlet_code: string;
  outlet_name: string;
  pickup_code: string;
}

function isObj(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v);
}
function n(v: unknown, fallback = 0): number {
  return typeof v === 'number' && Number.isFinite(v) ? v : fallback;
}
function s(v: unknown, max = 120): string | null {
  return typeof v === 'string' ? v.slice(0, max) : null;
}
function status(v: unknown): string {
  return typeof v === 'string' && /^[a-z_]{1,32}$/.test(v) ? v : 'unknown';
}

function parsePayment(v: unknown): OrderPayment | null {
  if (!isObj(v)) return null;
  return {
    method: s(v.method, 20) ?? undefined,
    status: status(v.status),
    qr_string: typeof v.qr_string === 'string' && v.qr_string.length <= 2048 ? v.qr_string : null,
    expires_at: s(v.expires_at, 40),
    counter_code: formatCounterCode(v.counter_code),
  };
}

function parseTaxGroups(v: unknown): TaxGroup[] {
  if (!Array.isArray(v)) return [];
  return v
    .filter(isObj)
    .filter((t) => typeof t.label === 'string')
    .map((t) => ({ label: String(t.label).slice(0, 40), base: n(t.base), amount: n(t.amount) }));
}

function parsePart(v: unknown): OrderPart | null {
  if (!isObj(v) || !isChannelCode(v.channel_code)) return null;
  return {
    channel_code: v.channel_code,
    company_code: s(v.company_code, 16) ?? v.channel_code,
    outlet_code: isOutletCode(v.outlet_code) ? v.outlet_code : '',
    outlet_name: s(v.outlet_name) ?? '',
    status: status(v.status),
    sale_order_name: s(v.sale_order_name, 40),
    lines: parseQuoteLines(v.lines),
    tax_groups: parseTaxGroups(v.tax_groups),
    amount_total: n(v.amount_total),
    amount_payable: n(v.amount_payable),
    refunded_amount: n(v.refunded_amount),
    ready_at: s(v.ready_at, 40),
    handed_over_at: s(v.handed_over_at, 40),
    cancel_reason: s(v.cancel_reason, 40),
    points_redeemed: nonNeg(v.points_redeemed),
    points_value_idr: nonNeg(v.points_value_idr),
    amount_cash: typeof v.amount_cash === 'number' && Number.isFinite(v.amount_cash) && v.amount_cash >= 0 ? v.amount_cash : null,
    points_earned: nonNeg(v.points_earned),
    points_restored: nonNeg(v.points_restored),
  };
}

function nonNeg(v: unknown): number {
  return typeof v === 'number' && Number.isFinite(v) && v >= 0 ? v : 0;
}

/** Porsi tunai yang harus dibayar (QRIS/kasir): `amount_cash` bila ada, kalau tidak total tagihan. */
export function cashDue(order: Pick<OrderDetail, 'amount_cash' | 'amount_payable'>): number {
  return order.amount_cash ?? order.amount_payable;
}

/** Allowlist respons `GET /v1/orders/:id`. Field lain (termasuk kode ambil bila BFF keliru) dibuang. */
export function parseOrderDetail(v: unknown): OrderDetail | null {
  if (!isObj(v) || !isUuid(v.order_id) || !Array.isArray(v.parts)) return null;
  const st = status(v.status);
  const payment = parsePayment(v.payment);
  // qr hanya relevan saat menunggu bayar (§10.3.6) — buang di status lain.
  if (payment && st !== 'awaiting_payment') payment.qr_string = null;
  // kode bayar hanya relevan saat menunggu bayar di kasir (§12.2) — buang di status lain.
  if (payment && st !== 'awaiting_counter_payment') payment.counter_code = null;
  return {
    order_id: v.order_id,
    status: st,
    created_at: s(v.created_at, 40),
    expires_at: s(v.expires_at, 40),
    paid_at: s(v.paid_at, 40),
    amount_payable: n(v.amount_payable),
    amount_points_value: nonNeg(v.amount_points_value),
    amount_cash: typeof v.amount_cash === 'number' && Number.isFinite(v.amount_cash) && v.amount_cash >= 0 ? v.amount_cash : null,
    payment,
    parts: v.parts.map(parsePart).filter((p): p is OrderPart => p !== null),
  };
}

/** Respons `201 POST /v1/orders` memakai bentuk yang kompatibel (order_id, status, payment, parts). */
export function parseCheckoutResult(v: unknown): { order_id: string; status: string } | null {
  if (!isObj(v) || !isUuid(v.order_id)) return null;
  return { order_id: v.order_id, status: status(v.status) };
}

export function parseOrderList(v: unknown): OrderList | null {
  if (!isObj(v) || !Array.isArray(v.items)) return null;
  const items: OrderSummary[] = [];
  for (const o of v.items) {
    if (!isObj(o) || !isUuid(o.order_id)) continue;
    items.push({
      order_id: o.order_id,
      status: status(o.status),
      created_at: s(o.created_at, 40),
      amount_payable: n(o.amount_payable),
      parts: Array.isArray(o.parts)
        ? o.parts.filter(isObj).filter((p) => isChannelCode(p.channel_code)).map((p) => ({ channel_code: String(p.channel_code), status: status(p.status) }))
        : [],
    });
  }
  const cursor = typeof v.next_cursor === 'string' && CURSOR_RE.test(v.next_cursor) ? v.next_cursor : null;
  return { items, next_cursor: cursor };
}

/** Alfabet kode ambil §10.2 (tanpa I, L, O, 0, 1); tampil `ABCD-EFGH`. */
export const PICKUP_CODE_RE = /^[ABCDEFGHJKMNPQRSTUVWXYZ23456789]{4}-[ABCDEFGHJKMNPQRSTUVWXYZ23456789]{4}$/;

export function parsePickupCodes(v: unknown): PickupCodeItem[] | null {
  if (!isObj(v) || !Array.isArray(v.items)) return null;
  const out: PickupCodeItem[] = [];
  for (const i of v.items) {
    if (!isObj(i) || !isChannelCode(i.channel_code) || typeof i.pickup_code !== 'string') continue;
    if (!PICKUP_CODE_RE.test(i.pickup_code)) continue;
    out.push({
      channel_code: i.channel_code,
      outlet_code: isOutletCode(i.outlet_code) ? i.outlet_code : '',
      outlet_name: s(i.outlet_name) ?? '',
      pickup_code: i.pickup_code,
    });
  }
  return out;
}

/**
 * Salinan order tanpa kode bayar — dipakai render server (RSC) agar kode tidak masuk payload HTML/RSC;
 * kode hanya diambil klien lewat `GET /api/orders/:id` (no-store) setelah halaman terbuka.
 */
export function withoutCounterCode(order: OrderDetail): OrderDetail {
  return order.payment ? { ...order, payment: { ...order.payment, counter_code: null } } : order;
}

/** True bila pesanan "Bayar di kasir" masih menunggu pelunasan. */
export function isAwaitingCounter(order: Pick<OrderDetail, 'status'>): boolean {
  return order.status === 'awaiting_counter_payment';
}

export function canCancel(order: Pick<OrderDetail, 'status'>): boolean {
  return (CANCELLABLE_STATUSES as readonly string[]).includes(order.status);
}

export function hasPickupCode(order: Pick<OrderDetail, 'parts'>): boolean {
  return order.parts.some((p) => (PICKUP_CODE_PART_STATUSES as readonly string[]).includes(p.status));
}

