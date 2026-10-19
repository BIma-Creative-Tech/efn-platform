/**
 * Tipe & jalur 12 metode `/json/2` untuk BFF (docs/04 §10.4.3; + L-60 `counterRefundAuthorize`; + G21 §13.5 `setInvoiceParty`). Hanya path di `ODOO_ONLINE_PATHS` yang boleh dipanggil
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
  /** L-60 (SEC-145 r13): otorisasi refund POS atas pelunasan counter (fail-closed di Odoo tanpa ini). */
  counterRefundAuthorize: 'efn.online.order/efn_online_counter_refund_authorize',
  /**
   * G21 §13.5 (W-06/E-39, L-63): pasang partner faktur pembeli TERLINDUNGI (efn_pii) pada SO — path gateway ke-12.
   * Argumen berisi PII (profil pembeli): dikirim sekali (TLS + mTLS), tidak pernah di-log/disimpan BFF.
   */
  setInvoiceParty: 'efn.online.order/efn_online_set_invoice_party',
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
  /** G21 §13.5: `efn_pii` tidak aktif / kanal bukan PT PPN → profil pembeli tidak boleh masuk Odoo (L-63). */
  'invoice_not_available',
  /** G22 §14.C.3: porsi Poin EFN ditolak kebijakan PT/global Odoo (fail-closed) → BFF `409 exceeds_max_pct` (L-79: tanpa fallback). */
  'points_invalid',
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
  /** G20 §12.3 (W-33): `product.weight` (kg) untuk batas berat pesanan; `null` = belum diisi di Odoo. */
  weight_kg: number | null;
  /** G22 §14.B B-2 / L-78: atribut `no_variant` (format A.6); `[]` untuk ritel / tanpa atribut. */
  attributes?: ItemAttribute[];
}
/** G22 A.6 / B-2: atribut menu (`required` = `display_type != "multi"` di Odoo). */
export interface ItemAttribute {
  attribute_id: number;
  name: string;
  display_type: 'radio' | 'select' | 'multi' | 'pills';
  required: boolean;
  values: { id: number; name: string; price_extra_excl: number }[];
}
/** G20 §12.2 no. 1: `efn.online.channel.payment_modes`. */
export type ChannelPaymentMode = 'qris' | 'counter';
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
  /**
   * G20 §12.2 no. 1 / L-52: metode bayar yang diizinkan kanal. `null` = Odoo tidak mengirim (modul pra-G20) → BFF memakai
   * `ONLINE_PAYMENT_METHODS` (config, L-55). Opsional di tipe agar `channel_data` lama tetap terbaca.
   */
  payment_modes?: ChannelPaymentMode[] | null;
  /** G22 §14.B B-2: `retail` | `fnb` (absen = `retail`). */
  business_type?: 'retail' | 'fnb';
  /** G22 §14.C.1 no. 5 / B-2: flag kanal Odoo `loyalty_earn` / `loyalty_redeem` (absen = mati). */
  loyalty?: { earn: boolean; redeem: boolean };
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
  /** G22 B-4 / L-78: PTAV `no_variant` (urut naik, ≤ 10). Absen/kosong = baris tanpa atribut (payload lama sama). */
  attribute_value_ids?: number[];
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
  /** G22 L-78: identitas baris = (product_id, attribute_value_ids). Parser selalu mengisi (absen di fixture lama = []). */
  attribute_value_ids?: number[];
  /** G22 L-78: teks atribut dari Odoo (`""`/null tanpa atribut). */
  attributes_text?: string | null;
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
  /**
   * G20 §12.2 no. 3: hanya dikirim bila `counter` (mode `qris` = field tidak ada, payload & `payload_sha256` Odoo sama
   * dengan pra-G20).
   */
  payment_mode?: 'counter';
  /**
   * L-56: kode BAYAR di kasir dibuat BFF (pola `pickup_code`), hanya bila `payment_mode=counter`. Rahasia: dikirim sekali
   * ke Odoo (TLS + mTLS; Odoo hanya menyimpan hash), tidak pernah di-log. Odoo TIDAK mengembalikannya.
   */
  counter_code?: string;
  /** G22 §14.C.3: porsi Poin EFN bagian (hanya bila ada; masuk `payload_sha256` Odoo). */
  points_payment?: { points: number; value_idr: number };
}
export interface PlaceResult {
  order_state: string;
  sale_order_name: string | null;
  amount_total: number;
  amount_payable: number;
  expires_at: Date | null;
  /** G22 §14.C.3: `amount_payable − value_idr` (tanpa poin = amount_payable). */
  amount_due: number;
}
export interface MarkPaidInput {
  payment_ref: string;
  /** = porsi TUNAI (G22). */
  amount_paid: number;
  paid_at: string;
  idempotency_key: string;
  /** G22 §14.C.3: nilai Poin EFN bagian (hanya bila ada). */
  points_value?: number;
}
/** G22 §14.C.4: rincian poin pada refund/batal Odoo. */
export interface RefundPoints {
  points_value: number;
  cash_amount: number | null;
  eligible_amount_refunded: number | null;
}
export interface MarkPaidResult {
  order_state: string;
  short_lines: unknown[] | null;
  refund_required: string | null;
}
export type CancelReason = 'payment_expired' | 'customer' | 'place_failed' | 'stock_short' | 'ops' | 'review';
/** Blok `refund` hasil batal/status Odoo (Odoo B): bruto + rincian poin opsional. */
export interface CancelRefund extends Partial<RefundPoints> {
  refund_ref: string;
  amount: number;
}
export interface CancelResult {
  order_state: string;
  refund: CancelRefund | null;
}
export type RefundReason = 'stock_short' | 'customer_cs' | 'ops_cancelled' | 'review';
export interface RefundInput {
  idempotency_key: string;
  lines?: QuoteLineIn[];
  full?: boolean;
  restock: boolean;
  reason: RefundReason;
}
export interface RefundResult extends Partial<RefundPoints> {
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
/**
  * L-60: argumen ke-3 `efn_online_counter_refund_authorize` (nama argumen `authorization`, terverifikasi Odoo E-45; pola `refund` /
 * `settlement`). `refund_ref` = `odoo_refund_ref` (null hanya untuk pesanan batal tanpa faktur), `amount` = nominal persis.
 */
export interface CounterRefundAuthorizeInput {
  idempotency_key: string;
  refund_id: string;
  refund_ref: string | null;
  amount: number;
}
export interface CounterRefundAuthorizeResult {
  /** `true` hanya bila Odoo menjawab `state: "authorized"` (bentuk E-45 `{ state, amount }`) dan tidak `authorized: false`. */
  authorized: boolean;
  state: string | null;
  /** Nominal yang diotorisasi Odoo (Rupiah bulat); harus sama dengan nominal refund BFF. */
  amount: number | null;
}
/**
 * G21 §13.5: profil pembeli untuk faktur pajak atas nama (`invoice_request`). PII — hanya hidup di memori selama
 * checkout, diteruskan SEKALI ke Odoo; tidak masuk DB/outbox/Redis/log BFF (K-36-7, L-63).
 */
export interface InvoiceBuyerProfile {
  buyer_type: 'npwp' | 'nik' | 'passport';
  tax_id: string;
  name: string;
  address: string;
  email: string;
}
/** Hasil `efn_online_set_invoice_party` — TANPA PII (kontrak §13.5); hanya status opsional. */
export interface SetInvoicePartyResult {
  state: string | null;
}

export interface StatusRow {
  platform_order_ref: string;
  state: string;
  seq: number | null;
  ready_at: string | null;
  picked_up_at: string | null;
  /** G22 §14.C.4: eligible earn dibekukan saat serah terima (null sebelum `picked_up`). */
  eligible_amount?: number | null;
}

/** Klien 11 metode E-28 (+ L-60). Implementasi: `Json2OdooOnlineClient` (mTLS) atau `MockOdooOnline` (dev/test saja). */
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
  /** L-60: hanya setelah refund counter disetujui approver CS; idempoten per `idempotency_key`. */
  counterRefundAuthorize(channel: string, platformOrderRef: string, authorization: CounterRefundAuthorizeInput): Promise<CounterRefundAuthorizeResult>;
  /** G21 §13.5: setelah place berhasil; idempoten per `idempotencyKey`. Gagal tidak membatalkan order (kecuali invoice_not_available). */
  setInvoiceParty(channel: string, platformOrderRef: string, profile: InvoiceBuyerProfile, idempotencyKey: string): Promise<SetInvoicePartyResult>;
  /** true bila circuit kanal sedang terbuka (quote/checkout langsung 503, katalog tetap dari salinan). */
  isCircuitOpen(channel: string): boolean;
}

export const ODOO_ONLINE = Symbol('ODOO_ONLINE');
