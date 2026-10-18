/**
 * Tipe & jalur 10 metode `/json/2` untuk BFF (docs/04 §10.4.3). Hanya path di `ODOO_ONLINE_PATHS` yang boleh dipanggil
 * (sama dengan allowlist `odoo-rpc-gateway`); klien tidak punya cara memanggil model/metode lain.
 */
export const ODOO_ONLINE_PATHS = {
  catalog: 'efn.online.channel/efn_online_catalog',
  stock: 'efn.online.channel/efn_online_stock',
  image: 'efn.online.channel/efn_online_image',
  quote: 'efn.online.channel/efn_online_quote',
  place: 'efn.online.order/efn_online_place',
  markPaid: 'efn.online.order/efn_online_mark_paid',
  cancel: 'efn.online.order/efn_online_cancel',
  refund: 'efn.online.order/efn_online_refund',
  refundSettled: 'efn.online.order/efn_online_refund_settled',
  status: 'efn.online.order/efn_online_status',
} as const;
export type OdooOnlineMethod = keyof typeof ODOO_ONLINE_PATHS;

/** Kode penolakan bisnis E-28: `UserError("efn_online:<kode>")` persis (§10.4.2). */
export const ODOO_BUSINESS_CODES = [
  'price_changed',
  'out_of_stock',
  'amount_mismatch',
  'invalid_state',
  'idempotency_conflict',
  'not_found',
  'invalid_input',
  'channel_inactive',
] as const;
export type OdooBusinessCode = (typeof ODOO_BUSINESS_CODES)[number];

/**
 * Jenis kegagalan klien json2:
 * - `business`  — UserError E-28 (`code` terisi), tidak di-retry;
 * - `auth`      — 401/403 (key kedaluwarsa/dicabut, atau gateway menolak path) → alert, tidak di-retry;
 * - `not_found` — 404 (metode/route tidak ada: salah konfigurasi);
 * - `transport` — jaringan/timeout/429/5xx (di-retry, dihitung circuit breaker);
 * - `circuit_open` — circuit kanal terbuka;
 * - `invalid_response` — respons tidak sesuai kontrak (bug/konfigurasi);
 * - `busy` — batas konkurensi global BFF → Odoo penuh (SEC-96), tidak dihitung circuit breaker.
 */
export type OdooErrorKind = 'business' | 'auth' | 'not_found' | 'transport' | 'circuit_open' | 'invalid_response' | 'busy';

export class OdooRpcError extends Error {
  constructor(
    readonly kind: OdooErrorKind,
    readonly code: OdooBusinessCode | null = null,
    message?: string,
  ) {
    super(message ?? (code ? `odoo ${kind}: ${code}` : `odoo ${kind}`));
    this.name = 'OdooRpcError';
  }
}

export function isOdooBusiness(err: unknown, ...codes: OdooBusinessCode[]): err is OdooRpcError {
  return err instanceof OdooRpcError && err.kind === 'business' && (codes.length === 0 || (err.code !== null && codes.includes(err.code)));
}

export type Fulfilment = 'pickup' | 'delivery';

export interface CatalogPrice {
  outlet_code: string | null;
  price_excl: number;
  price_incl: number;
  tax_labels: string[];
}
export interface CatalogItem {
  product_id: number;
  tmpl_id: number;
  name: string;
  description: string;
  categ: { id: number; name: string; parent_id: number | null; sequence: number } | null;
  uom: string;
  image_sha: string | null;
  sequence: number;
  prices: CatalogPrice[];
  max_qty: number;
}
/**
 * L-11 (docs/04 §10.17): blok `channel` WAJIB pada hasil `efn_online_catalog` —
 * `{ code, name, company_code, currency, outlets: [{ code, public_name, pickup_enabled, prep_minutes, active }] }`.
 * Tidak ada blok → kanal tidak ditampilkan (fail-closed, tanpa tebakan dari kode kanal).
 */
export interface CatalogChannelInfo {
  code: string;
  company_code: string;
  /** G19 §11.3 (E-37a): `efn.online.channel.brand_code` `^[A-Z][A-Z0-9]{1,9}$`, tidak unik; `null` bila kosong. */
  brand_code: string | null;
  name: string;
  currency: string;
  active: boolean;
  fulfilment: { pickup: boolean; delivery: boolean };
  outlets: { outlet_code: string; public_name: string; pickup_enabled: boolean; prep_minutes: number | null; active: boolean }[];
}
export interface CatalogPage {
  items: CatalogItem[];
  deleted: number[];
  next_cursor: string | null;
  server_time: string;
  channel: CatalogChannelInfo | null;
}
export type StockStatus = 'in_stock' | 'low' | 'out';
export interface StockRow {
  product_id: number;
  status: StockStatus;
}
export interface ImageResult {
  sha: string;
  mimetype: string;
  data: Buffer;
}
export interface QuoteLineIn {
  product_id: number;
  qty: number;
}
export interface QuoteLine {
  product_id: number;
  /** Nama dari Odoo (bila dikirim); BFF memakai salinan katalog sebagai cadangan. */
  name: string | null;
  qty: number;
  price_unit_incl: number;
  subtotal_incl: number;
  tax_labels: string[];
  available: boolean;
  /**
   * G19 §11.2 no. 3: baris reward promo/kupon dari Odoo (`reward: true`; harga ≤ 0, produk reward Odoo). Diteruskan
   * apa adanya ke klien; TIDAK pernah dikirim balik ke Odoo saat place dan tidak pernah diterima dari klien/cart.
   */
  reward: boolean;
}
export interface TaxGroup {
  label: string;
  base: number;
  amount: number;
}
export interface QuoteIssue {
  /** `null` untuk isu tingkat order (mis. `order_limit`). */
  product_id: number | null;
  code: string;
}
export interface QuoteResult {
  company_code: string | null;
  lines: QuoteLine[];
  tax_groups: TaxGroup[];
  amount_total: number;
  amount_payable: number;
  quote_hash: string;
  valid_until: Date | null;
  issues: QuoteIssue[];
  /** G19 §11.2 no. 3: kode yang terpakai di grup (kanal) ini — dinormalisasi huruf besar oleh parser. */
  promo_applied: string[];
  /** Kode yang tidak berlaku di grup ini (apa pun sebabnya — Odoo tidak membedakan, anti-enumerasi). */
  promo_rejected: string[];
}
export interface PlaceInput {
  idempotency_key: string;
  platform_order_ref: string;
  checkout_ref: string;
  outlet_code: string;
  fulfilment: Fulfilment;
  /** Hanya baris produk cart (tanpa baris reward). */
  lines: QuoteLineIn[];
  quote_hash: string;
  /** G19 §11.2 no. 2/5: `promo_applied` grup dari quote tersimpan (bukan dari klien); masuk `payload_sha256` Odoo. */
  promo_codes: string[];
  /** Rahasia: hanya dikirim sekali ke Odoo (TLS + mTLS), tidak pernah di-log (§10.3.6). */
  pickup_code: string;
}
export interface PlaceResult {
  order_state: string;
  sale_order_name: string | null;
  amount_total: number;
  amount_payable: number;
  expires_at: Date | null;
}
export interface MarkPaidInput {
  payment_ref: string;
  amount_paid: number;
  paid_at: string;
  idempotency_key: string;
}
export interface MarkPaidResult {
  order_state: string;
  short_lines: unknown[] | null;
  refund_required: string | null;
}
export type CancelReason = 'payment_expired' | 'customer' | 'place_failed' | 'stock_short' | 'ops' | 'review';
export interface CancelResult {
  order_state: string;
  refund: { refund_ref: string; amount: number } | null;
}
export type RefundReason = 'stock_short' | 'customer_cs' | 'ops_cancelled' | 'review';
export interface RefundInput {
  idempotency_key: string;
  lines?: QuoteLineIn[];
  full?: boolean;
  restock: boolean;
  reason: RefundReason;
}
export interface RefundResult {
  refund_ref: string;
  amount: number;
  credit_note: string | null;
}
export interface RefundSettledInput {
  provider_refund_ref: string;
  settled_at: string;
  ok: boolean;
  idempotency_key: string;
}
export interface StatusRow {
  platform_order_ref: string;
  state: string;
  seq: number | null;
  ready_at: string | null;
  picked_up_at: string | null;
}

/** Klien 10 metode E-28. Implementasi: `Json2OdooOnlineClient` (mTLS) atau `MockOdooOnline` (dev/test saja). */
export interface OdooOnlineApi {
  catalog(channel: string, since: string | null, cursor: string | null, limit: number): Promise<CatalogPage>;
  stock(channel: string, outlet: string, productIds: number[]): Promise<StockRow[]>;
  image(channel: string, tmplId: number, size: 512 | 1024): Promise<ImageResult>;
  /** `promoCodes` = kode ternormalisasi (huruf besar, unik, ≤ 3); dikirim sama ke setiap kanal (§11.2 no. 2). */
  quote(channel: string, outlet: string, fulfilment: Fulfilment, lines: QuoteLineIn[], promoCodes: string[]): Promise<QuoteResult>;
  place(channel: string, order: PlaceInput): Promise<PlaceResult>;
  markPaid(channel: string, platformOrderRef: string, payment: MarkPaidInput): Promise<MarkPaidResult>;
  cancel(channel: string, platformOrderRef: string, reason: CancelReason, idempotencyKey: string): Promise<CancelResult>;
  refund(channel: string, platformOrderRef: string, refund: RefundInput): Promise<RefundResult>;
  refundSettled(channel: string, refundRef: string, settlement: RefundSettledInput): Promise<{ state: string }>;
  status(channel: string, platformOrderRefs: string[]): Promise<StatusRow[]>;
  /** true bila circuit kanal sedang terbuka (quote/checkout langsung 503, katalog tetap dari salinan). */
  isCircuitOpen(channel: string): boolean;
}

export const ODOO_ONLINE = Symbol('ODOO_ONLINE');
