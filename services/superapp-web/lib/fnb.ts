/**
 * Pesan dari meja / QR order (docs/04 §14.A, `/v1/fnb/*`; ADR-032; L-70..L-72) — tipe, parser (allowlist), peta error,
 * keranjang meja, helper idempotensi. Modul MURNI (client, Route Handler, test).
 *
 * Keamanan token sesi meja (§14.A.2):
 * - Token `fts1.<sid>.<exp>.<sig>` datang di FRAGMENT URL (`/meja#t=...`) sehingga tidak ikut dikirim ke server/access log.
 *   Klien memindahkannya ke MEMORI, menghapus fragment dari address bar, lalu mengirimnya sebagai header
 *   `X-Table-Session` ke Route Handler origin sendiri (`/api/fnb/*`) yang meneruskannya ke BFF.
 * - Token TIDAK PERNAH ke localStorage/sessionStorage, log, analytics, atau query string (keputusan lead G22 — meralat
 *   kalimat "disimpan di sessionStorage" pada §14.A.2). Konsekuensi: memuat ulang halaman = pindai ulang QR.
 * - Validasi bentuk token di sini hanya anti-sampah; verifikasi tanda tangan HANYA di BFF.
 *
 * Catatan pesanan: teks catatan (≤ 140) diteruskan ke BFF/Odoo dan TIDAK disimpan di klien setelah terkirim.
 */
import { extraExcl, normalizeAttributeIds, parseAttributes, type ProductAttribute } from './attributes';
import { isImageSha, isUuid } from './catalog';

// ---------- token sesi meja ----------

export const TABLE_SESSION_HEADER = 'x-table-session';
/** `fts1.<b64url(session_id)>.<exp unix>.<b64url(HMAC)>` — rentang panjang longgar; BFF yang memverifikasi tanda tangan. */
export const TABLE_TOKEN_RE = /^fts1\.[A-Za-z0-9_-]{16,64}\.[0-9]{9,12}\.[A-Za-z0-9_-]{32,96}$/;
export const TABLE_TOKEN_MAX_LEN = 256;

export function isTableToken(v: unknown): v is string {
  return typeof v === 'string' && v.length <= TABLE_TOKEN_MAX_LEN && TABLE_TOKEN_RE.test(v);
}

/**
 * Ambil token dari fragment `#t=<token>`. Hanya kunci `t` yang dikenal. Token yang bentuknya salah -> null
 * (tidak ada percent-decoding: alfabet token sudah aman untuk URL).
 */
export function tableTokenFromHash(hash: string): string | null {
  const raw = hash.startsWith('#') ? hash.slice(1) : hash;
  if (raw.length === 0 || raw.length > 400) return null;
  for (const part of raw.split('&')) {
    const i = part.indexOf('=');
    if (i > 0 && part.slice(0, i) === 't') {
      const t = part.slice(i + 1);
      return isTableToken(t) ? t : null;
    }
  }
  return null;
}

// ---------- catatan item ----------

export const NOTE_MAX = 140;
/** Deret ≥ 8 digit (mirip nomor HP) — §14.A.3 `note_invalid`. */
const NOTE_PHONE_RE = /\d[\d\s.-]{6,}\d/;

export type NoteReason = 'too_long' | 'looks_like_phone' | 'has_at';
export type NoteCheck = { ok: true; value: string } | { ok: false; reason: NoteReason };

/** Trim, ganti karakter kontrol/baris baru dengan spasi, rapikan spasi ganda. */
export function normalizeNote(raw: string): string {
  // eslint-disable-next-line no-control-regex -- karakter kontrol sengaja dibuang
  return raw.replace(/[\u0000-\u001f\u007f-\u009f]/g, ' ').replace(/\s+/g, ' ').trim();
}

/** Validasi UX yang meniru BFF (`note_invalid`). BFF tetap validator utama. */
export function validateNote(raw: string): NoteCheck {
  const value = normalizeNote(raw);
  if (Array.from(value).length > NOTE_MAX) return { ok: false, reason: 'too_long' };
  if (value.includes('@')) return { ok: false, reason: 'has_at' };
  if (NOTE_PHONE_RE.test(value)) return { ok: false, reason: 'looks_like_phone' };
  return { ok: true, value };
}

export const NOTE_ERROR_MESSAGES: Record<NoteReason, string> = {
  too_long: `Catatan maksimal ${NOTE_MAX} karakter.`,
  looks_like_phone: 'Catatan tidak boleh berisi nomor telepon.',
  has_at: 'Catatan tidak boleh berisi alamat email atau tanda @.',
};

// ---------- pesan error ----------

export const FNB_ERROR_MESSAGES: Record<string, string> = {
  table_session_invalid: 'Kode QR meja tidak valid atau sudah berakhir. Pindai ulang QR di meja Anda, atau minta bantuan pelayan.',
  table_session_closed: 'Sesi meja ini sudah ditutup (tagihan dibayar atau meja ditutup). Minta pelayan bila Anda ingin memesan lagi.',
  ordering_unavailable: 'Pemesanan dari ponsel sedang tidak tersedia. Silakan pesan ke pelayan.',
  invalid_request: 'Permintaan tidak valid. Periksa pesanan Anda lalu coba lagi.',
  idempotency_key_required: 'Permintaan tidak valid. Muat ulang daftar pesanan lalu coba lagi.',
  note_invalid: 'Catatan tidak valid. Hindari nomor telepon dan alamat email, dan maksimal 140 karakter.',
  captcha_required: 'Verifikasi keamanan diperlukan. Centang "Saya bukan robot", lalu kirim pesanan lagi.',
  idempotency_conflict: 'Pesanan ini sudah pernah dikirim dengan isi yang berbeda. Periksa daftar pesanan terkirim, lalu kirim ulang bila perlu.',
  item_unavailable: 'Sebagian menu sedang tidak tersedia. Hapus menu yang ditandai, lalu kirim lagi.',
  session_limit_reached: 'Batas pesanan untuk meja ini sudah tercapai. Silakan pesan ke pelayan.',
  rate_limited: 'Terlalu banyak permintaan. Tunggu sebentar lalu coba lagi.',
  not_found: 'Data tidak ditemukan.',
  points_already_claimed: 'Poin untuk meja ini sudah diklaim oleh akun lain.',
  account_deleting: 'Akun Anda sedang dalam proses penghapusan sehingga tidak dapat mengklaim poin.',
  points_unavailable: 'Poin untuk pesanan meja belum tersedia di restoran ini.',
  unauthenticated: 'Sesi masuk berakhir. Pesanan tetap bisa dilanjutkan sebagai tamu.',
  forbidden: 'Permintaan ditolak.',
  upstream_error: 'Terjadi kendala. Silakan coba lagi beberapa saat lagi.',
};

export const GENERIC_FNB_ERROR = 'Terjadi kendala. Silakan coba lagi beberapa saat lagi.';

export function fnbErrorMessage(code: string | null | undefined): string {
  if (code && Object.prototype.hasOwnProperty.call(FNB_ERROR_MESSAGES, code)) return FNB_ERROR_MESSAGES[code]!;
  return GENERIC_FNB_ERROR;
}

/** Status HTTP BFF yang kodenya diteruskan ke browser (pesan tetap dari peta di atas, bukan pesan BFF). */
export const FNB_PASSTHROUGH_STATUSES: readonly number[] = [400, 404, 409, 422, 429];

export type FailurePhase = { kind: 'invalid' } | { kind: 'closed' } | { kind: 'unavailable' } | { kind: 'error'; text: string };

/** Peta galat muat sesi/menu/daftar -> keadaan layar `/meja`. */
export function classifyFnbFailure(code: string, message: string): FailurePhase {
  if (code === 'table_session_invalid') return { kind: 'invalid' };
  if (code === 'table_session_closed') return { kind: 'closed' };
  if (code === 'ordering_unavailable') return { kind: 'unavailable' };
  return { kind: 'error', text: message || GENERIC_FNB_ERROR };
}

// ---------- status kiriman ----------

export type SubmissionStatus =
  | 'submitting'
  | 'accepted'
  | 'pending_confirmation'
  | 'preparing'
  | 'ready'
  | 'served'
  | 'rejected'
  | 'cancelled'
  | 'failed';

export const SUBMISSION_STATUS_LABEL: Record<SubmissionStatus, string> = {
  submitting: 'Sedang dikirim',
  accepted: 'Diterima dapur',
  pending_confirmation: 'Menunggu konfirmasi pelayan',
  preparing: 'Sedang disiapkan',
  ready: 'Siap diantar',
  served: 'Sudah diantar',
  rejected: 'Ditolak pelayan',
  cancelled: 'Dibatalkan',
  failed: 'Gagal dikirim',
};

export const FINAL_SUBMISSION_STATUSES: readonly string[] = ['served', 'rejected', 'cancelled', 'failed'];

export function submissionStatusLabel(s: string): string {
  return Object.prototype.hasOwnProperty.call(SUBMISSION_STATUS_LABEL, s) ? SUBMISSION_STATUS_LABEL[s as SubmissionStatus] : 'Status tidak dikenal';
}

export function isFinalSubmission(s: string): boolean {
  return FINAL_SUBMISSION_STATUSES.includes(s);
}

/** Penjelasan singkat setelah kiriman berhasil (status awal dari 202). */
export function submitAcceptedMessage(status: string): string {
  if (status === 'pending_confirmation') return 'Pesanan terkirim. Menunggu konfirmasi pelayan sebelum masuk dapur.';
  return 'Pesanan terkirim dan diterima. Pelayan akan mengantar ke meja Anda.';
}

/**
 * Jeda polling daftar kiriman: 8 dtk selama ada kiriman yang belum final, 30 dtk bila tidak (kiriman dari ponsel lain
 * di meja yang sama tetap muncul). Gagal berturut-turut -> backoff eksponensial sampai 2 menit. Batas BFF 30/menit/sesi
 * dibagi semua ponsel di meja itu.
 */
export function fnbPollDelayMs(statuses: readonly string[], failures: number): number {
  const base = statuses.some((s) => !isFinalSubmission(s)) ? 8_000 : 30_000;
  return Math.min(120_000, base * 2 ** Math.min(Math.max(0, failures), 4));
}

// ---------- parser respons (allowlist) ----------

function isObj(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v);
}
function str(v: unknown, max: number): string | null {
  return typeof v === 'string' ? v.slice(0, max) : null;
}
function posInt(v: unknown): number | null {
  return typeof v === 'number' && Number.isSafeInteger(v) && v > 0 ? v : null;
}
function nonNegNum(v: unknown): number | null {
  return typeof v === 'number' && Number.isFinite(v) && v >= 0 ? v : null;
}
function statusCode(v: unknown): string {
  return typeof v === 'string' && /^[a-z_]{1,32}$/.test(v) ? v : 'unknown';
}

export interface FnbSession {
  outlet_name: string;
  table_label: string;
  company_code: string;
  brand_code: string | null;
  expires_at: string | null;
  confirm_mode: 'auto' | 'waiter';
  limits: { max_lines: number; max_line_qty: number; submissions_left: number | null };
  points: { claimable: boolean; claimed: boolean; claimed_by_me: boolean };
}

export const DEFAULT_MAX_LINES = 30;
export const DEFAULT_MAX_LINE_QTY = 20;

export function parseFnbSession(v: unknown): FnbSession | null {
  if (!isObj(v)) return null;
  const table = str(v.table_label, 64);
  if (!table) return null;
  const limits = isObj(v.limits) ? v.limits : {};
  const points = isObj(v.points) ? v.points : {};
  const left = typeof limits.submissions_left === 'number' && Number.isSafeInteger(limits.submissions_left) && limits.submissions_left >= 0 ? limits.submissions_left : null;
  return {
    outlet_name: str(v.outlet_name, 120) ?? '',
    table_label: table,
    company_code: str(v.company_code, 16) ?? '',
    brand_code: str(v.brand_code, 16),
    expires_at: str(v.expires_at, 40),
    // L-71: default aman = konfirmasi pelayan.
    confirm_mode: v.confirm_mode === 'auto' ? 'auto' : 'waiter',
    limits: {
      max_lines: Math.min(posInt(limits.max_lines) ?? DEFAULT_MAX_LINES, 100),
      max_line_qty: Math.min(posInt(limits.max_line_qty) ?? DEFAULT_MAX_LINE_QTY, 99),
      submissions_left: left,
    },
    points: {
      claimable: points.claimable === true,
      claimed: points.claimed === true,
      claimed_by_me: points.claimed_by_me === true,
    },
  };
}

export interface FnbCategory {
  category_id: number;
  name: string;
  sequence: number;
  image_sha: string | null;
}

export interface FnbProduct {
  product_id: number;
  name: string;
  category_id: number | null;
  image_sha: string | null;
  /** Harga SEBELUM pajak & layanan (indikatif). */
  price_excl: number;
  tax_labels: string[];
  price_note: string;
  available: boolean;
  attributes: ProductAttribute[];
}

export interface FnbMenu {
  categories: FnbCategory[];
  products: FnbProduct[];
  menu_synced_at: string | null;
}

export const DEFAULT_PRICE_NOTE = 'belum termasuk pajak & layanan';

export function parseFnbMenu(v: unknown): FnbMenu | null {
  if (!isObj(v) || !Array.isArray(v.products)) return null;
  const categories: FnbCategory[] = [];
  if (Array.isArray(v.categories)) {
    for (const c of v.categories) {
      if (!isObj(c)) continue;
      const id = posInt(c.category_id);
      const name = str(c.name, 120);
      if (id === null || !name) continue;
      categories.push({ category_id: id, name, sequence: typeof c.sequence === 'number' && Number.isFinite(c.sequence) ? c.sequence : 0, image_sha: isImageSha(c.image_sha) ? c.image_sha : null });
    }
    categories.sort((a, b) => a.sequence - b.sequence || a.name.localeCompare(b.name, 'id'));
  }
  const products: FnbProduct[] = [];
  const seen = new Set<number>();
  for (const p of v.products.slice(0, 500)) {
    if (!isObj(p)) continue;
    const id = posInt(p.product_id);
    const name = str(p.name, 200);
    const price = nonNegNum(p.price_excl);
    if (id === null || !name || price === null || seen.has(id)) continue;
    seen.add(id);
    products.push({
      product_id: id,
      name,
      category_id: posInt(p.category_id),
      image_sha: isImageSha(p.image_sha) ? p.image_sha : null,
      price_excl: price,
      tax_labels: Array.isArray(p.tax_labels) ? p.tax_labels.filter((x): x is string => typeof x === 'string').map((x) => x.slice(0, 40)).slice(0, 8) : [],
      price_note: str(p.price_note, 80) || DEFAULT_PRICE_NOTE,
      available: p.available !== false,
      attributes: parseAttributes(p.attributes),
    });
  }
  return { categories, products, menu_synced_at: str(v.menu_synced_at, 40) };
}

/** Baris kiriman. `attributes_text` = ringkasan pilihan dari BFF ('' bila tidak ada); id atribut hanya untuk identitas. */
export interface FnbSubmissionLine {
  name: string;
  qty: number;
  attributes_text: string;
  attribute_value_ids: number[];
}

export interface FnbSubmission {
  submission_id: string;
  created_at: string | null;
  status: string;
  lines: FnbSubmissionLine[];
}

function parseSubmissionLines(v: unknown): FnbSubmissionLine[] {
  if (!Array.isArray(v)) return [];
  const out: FnbSubmissionLine[] = [];
  for (const l of v.slice(0, 100)) {
    if (!isObj(l)) continue;
    const qty = posInt(l.qty);
    const name = str(l.name, 200);
    if (qty !== null && name) {
      out.push({
        name,
        qty,
        attributes_text: normalizeNote(str(l.attributes_text, 300) ?? ''),
        attribute_value_ids: normalizeAttributeIds(l.attribute_value_ids) ?? [],
      });
    }
  }
  return out;
}

/** Jam (WIB) untuk daftar kiriman; tanggal rusak/kosong -> ''. */
export function formatClock(iso: string | null): string {
  if (!iso) return '';
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return '';
  return new Intl.DateTimeFormat('id-ID', { timeStyle: 'short', timeZone: 'Asia/Jakarta' }).format(t);
}

/** Semua kiriman sesi (meja berbagi token), terbaru di atas. */
export function parseFnbOrders(v: unknown): FnbSubmission[] | null {
  if (!isObj(v) || !Array.isArray(v.items)) return null;
  const out: FnbSubmission[] = [];
  for (const o of v.items.slice(0, 200)) {
    if (!isObj(o) || !isUuid(o.submission_id)) continue;
    out.push({ submission_id: o.submission_id, created_at: str(o.created_at, 40), status: statusCode(o.status), lines: parseSubmissionLines(o.lines) });
  }
  const t = (s: FnbSubmission) => {
    const n = s.created_at ? Date.parse(s.created_at) : NaN;
    return Number.isNaN(n) ? 0 : n;
  };
  return out.sort((a, b) => t(b) - t(a));
}

export interface SubmitAccepted {
  submission_id: string;
  status: string;
  lines: FnbSubmissionLine[];
  indicative_subtotal_excl: number | null;
}

export function parseSubmitAccepted(v: unknown): SubmitAccepted | null {
  if (!isObj(v) || !isUuid(v.submission_id)) return null;
  return {
    submission_id: v.submission_id,
    status: statusCode(v.status),
    lines: parseSubmissionLines(v.lines),
    indicative_subtotal_excl: nonNegNum(v.indicative_subtotal_excl),
  };
}

export interface SubmissionState {
  submission_id: string;
  status: string;
  updated_at: string | null;
}

export function parseSubmissionState(v: unknown): SubmissionState | null {
  if (!isObj(v) || !isUuid(v.submission_id)) return null;
  return { submission_id: v.submission_id, status: statusCode(v.status), updated_at: str(v.updated_at, 40) };
}

/** `product_ids` pada 409 `item_unavailable` (id valid saja, ≤ 100). */
export function parseUnavailableIds(body: unknown): number[] {
  if (!isObj(body)) return [];
  const raw = body.product_ids;
  const src = Array.isArray(raw) ? raw : isObj(body.error) && Array.isArray(body.error.product_ids) ? body.error.product_ids : [];
  const out: number[] = [];
  for (const x of src.slice(0, 100)) {
    const id = posInt(x);
    if (id !== null && !out.includes(id)) out.push(id);
  }
  return out;
}

export const ITEM_UNAVAILABLE_GENERIC = 'Sebagian menu sedang tidak tersedia. Periksa pesanan Anda, hapus menu yang tidak tersedia, lalu kirim lagi.';

/**
 * Umpan balik `item_unavailable`: hanya id yang benar-benar ada di keranjang yang ditandai. Teks "Hapus menu yang ditandai"
 * HANYA dipakai bila ada baris yang ditandai; bila `product_ids` kosong/tidak cocok -> pesan umum tanpa klaim penandaan.
 */
export function itemUnavailableFeedback(ids: readonly number[], lines: readonly Pick<TableLine, 'product_id'>[]): { marked: number[]; text: string } {
  const inCart = new Set(lines.map((l) => l.product_id));
  const marked = ids.filter((id) => inCart.has(id));
  return { marked, text: marked.length > 0 ? FNB_ERROR_MESSAGES.item_unavailable! : ITEM_UNAVAILABLE_GENERIC };
}

// ---------- keranjang meja (memori) ----------

export interface TableLine {
  /** Identitas baris: produk + atribut + catatan. */
  key: string;
  product_id: number;
  name: string;
  qty: number;
  attribute_value_ids: number[];
  /** Ringkasan pilihan (tampilan). */
  options_text: string;
  note: string;
  /** Harga satuan indikatif sebelum pajak (menu + tambahan atribut) — tampilan saja. */
  unit_excl: number;
}

export interface TableLimits {
  max_lines: number;
  max_line_qty: number;
}

export function tableLineKey(productId: number, attrs: readonly number[], note: string): string {
  return `${productId}|${[...attrs].sort((a, b) => a - b).join('.')}|${note}`;
}

export type TableAddResult = { ok: true; lines: TableLine[] } | { ok: false; reason: 'too_many_lines' | 'invalid' };

export interface TableAddInput {
  product: Pick<FnbProduct, 'product_id' | 'name' | 'price_excl' | 'attributes'>;
  qty: number;
  attribute_value_ids: number[];
  options_text: string;
  note: string;
}

export function addTableLine(lines: readonly TableLine[], input: TableAddInput, limits: TableLimits): TableAddResult {
  const attrs = normalizeAttributeIds(input.attribute_value_ids);
  if (attrs === null || !Number.isInteger(input.qty) || input.qty < 1) return { ok: false, reason: 'invalid' };
  const note = normalizeNote(input.note);
  const key = tableLineKey(input.product.product_id, attrs, note);
  const existing = lines.find((l) => l.key === key);
  if (existing) {
    return {
      ok: true,
      lines: lines.map((l) => (l.key === key ? { ...l, qty: Math.min(l.qty + input.qty, limits.max_line_qty) } : l)),
    };
  }
  if (lines.length >= limits.max_lines) return { ok: false, reason: 'too_many_lines' };
  const line: TableLine = {
    key,
    product_id: input.product.product_id,
    name: input.product.name,
    qty: Math.min(input.qty, limits.max_line_qty),
    attribute_value_ids: attrs,
    options_text: input.options_text,
    note,
    unit_excl: input.product.price_excl + extraExcl(input.product.attributes, attrs),
  };
  return { ok: true, lines: [...lines, line] };
}

export function setTableLineQty(lines: readonly TableLine[], key: string, qty: number, maxQty: number): TableLine[] {
  if (!Number.isFinite(qty) || qty <= 0) return lines.filter((l) => l.key !== key);
  return lines.map((l) => (l.key === key ? { ...l, qty: Math.min(Math.floor(qty), maxQty) } : l));
}

export function tableItemCount(lines: readonly TableLine[]): number {
  return lines.reduce((n, l) => n + l.qty, 0);
}

/** Perkiraan subtotal sebelum pajak & layanan (tampilan saja; tagihan final dari kasir). */
export function tableEstimateExcl(lines: readonly TableLine[]): number {
  return lines.reduce((n, l) => n + l.unit_excl * l.qty, 0);
}

/** Badan `POST /v1/fnb/orders`: klien tidak pernah mengirim harga/meja/config/member (§14.A.10 API3). */
export interface SubmitBody {
  lines: { product_id: number; qty: number; attribute_value_ids: number[]; note?: string }[];
  altcha?: string;
}

export function buildSubmitBody(lines: readonly TableLine[], altcha?: string | null): SubmitBody {
  return {
    lines: lines.map((l) => ({
      product_id: l.product_id,
      qty: l.qty,
      attribute_value_ids: l.attribute_value_ids,
      ...(l.note ? { note: l.note } : {}),
    })),
    ...(altcha ? { altcha } : {}),
  };
}

const ALTCHA_PAYLOAD_RE = /^[A-Za-z0-9+/_=-]{1,8192}$/;
export const MAX_SUBMIT_LINES = 100;
const MAX_SUBMIT_QTY = 99;

export type SubmitBodyResult = { ok: true; body: SubmitBody } | { ok: false; code: 'invalid_request' | 'note_invalid' };

/** Validasi + allowlist ulang di Route Handler: field di luar kontrak dibuang, catatan dinormalisasi. */
export function sanitizeSubmitBody(v: unknown): SubmitBodyResult {
  if (!isObj(v) || !Array.isArray(v.lines) || v.lines.length < 1 || v.lines.length > MAX_SUBMIT_LINES) return { ok: false, code: 'invalid_request' };
  const lines: SubmitBody['lines'] = [];
  for (const l of v.lines) {
    if (!isObj(l)) return { ok: false, code: 'invalid_request' };
    const id = posInt(l.product_id);
    const qty = typeof l.qty === 'number' && Number.isInteger(l.qty) && l.qty >= 1 && l.qty <= MAX_SUBMIT_QTY ? l.qty : null;
    const attrs = normalizeAttributeIds(l.attribute_value_ids);
    if (id === null || qty === null || attrs === null) return { ok: false, code: 'invalid_request' };
    const entry: SubmitBody['lines'][number] = { product_id: id, qty, attribute_value_ids: attrs };
    if (l.note !== undefined && l.note !== null) {
      if (typeof l.note !== 'string') return { ok: false, code: 'invalid_request' };
      const check = validateNote(l.note);
      if (!check.ok) return { ok: false, code: 'note_invalid' };
      if (check.value) entry.note = check.value;
    }
    lines.push(entry);
  }
  const body: SubmitBody = { lines };
  if (v.altcha !== undefined && v.altcha !== null) {
    if (typeof v.altcha !== 'string' || !ALTCHA_PAYLOAD_RE.test(v.altcha)) return { ok: false, code: 'invalid_request' };
    body.altcha = v.altcha;
  }
  return { ok: true, body };
}

// ---------- Idempotency-Key kiriman (dibuat klien per percobaan) ----------

export const FNB_IDEMPOTENCY_KEY_RE = /^[A-Za-z0-9_-]{16,128}$/;

/** `crypto.randomUUID()` bila ada, kalau tidak 32 heks dari `getRandomValues`. Cocok dengan `FNB_IDEMPOTENCY_KEY_RE`. */
export function newIdempotencyKey(c: Pick<Crypto, 'randomUUID' | 'getRandomValues'> | undefined = globalThis.crypto): string {
  if (c && typeof c.randomUUID === 'function') return c.randomUUID();
  const bytes = new Uint8Array(16);
  if (!c || typeof c.getRandomValues !== 'function') throw new Error('crypto_unavailable');
  c.getRandomValues(bytes);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
}

/** cyrb53 — sidik non-kriptografis isi pesanan untuk memutuskan pemakaian ulang key (bukan keamanan). */
export function fingerprint(text: string): string {
  let h1 = 0xdeadbeef;
  let h2 = 0x41c6ce57;
  for (let i = 0; i < text.length; i += 1) {
    const ch = text.charCodeAt(i);
    h1 = Math.imul(h1 ^ ch, 2654435761);
    h2 = Math.imul(h2 ^ ch, 1597334677);
  }
  h1 = Math.imul(h1 ^ (h1 >>> 16), 2246822507) ^ Math.imul(h2 ^ (h2 >>> 13), 3266489909);
  h2 = Math.imul(h2 ^ (h2 >>> 16), 2246822507) ^ Math.imul(h1 ^ (h1 >>> 13), 3266489909);
  return `${(4294967296 * (2097151 & h2) + (h1 >>> 0)).toString(36)}.${text.length.toString(36)}`;
}

export interface Attempt {
  key: string;
  fp: string;
}

export function parseStoredAttempt(raw: string | null): Attempt | null {
  if (!raw || raw.length > 400) return null;
  try {
    const v: unknown = JSON.parse(raw);
    if (isObj(v) && typeof v.key === 'string' && FNB_IDEMPOTENCY_KEY_RE.test(v.key) && typeof v.fp === 'string' && v.fp.length <= 64) {
      return { key: v.key, fp: v.fp };
    }
  } catch {
    // diabaikan
  }
  return null;
}

/**
 * Pakai ulang key percobaan sebelumnya bila isi pesanan sama (retry setelah jaringan putus -> BFF menjawab kiriman yang
 * sama, tidak ada pesanan ganda); isi berbeda -> key baru. `altcha` TIDAK ikut sidik (berubah tiap percobaan).
 */
export function resolveAttemptKey(stored: Attempt | null, fp: string, generate: () => string): Attempt {
  if (stored && stored.fp === fp && FNB_IDEMPOTENCY_KEY_RE.test(stored.key)) return stored;
  return { key: generate(), fp };
}

export function submitFingerprint(lines: readonly TableLine[]): string {
  return fingerprint(JSON.stringify(buildSubmitBody(lines).lines));
}
