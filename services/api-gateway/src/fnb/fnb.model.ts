import { createHash } from 'node:crypto';
import { ApiError, Errors } from '../common/errors/api-error';
import type { FnbCompanyCode } from '../config/app-config';

/**
 * Model & validasi QR meja F&B (docs/04 §14.A, ADR-032 Accepted B, L-70/L-71). Modul murni (tanpa I/O).
 */

export type SessionStatus = 'open' | 'closed' | 'expired';
export type CloseReason = 'paid' | 'cancelled' | 'closed_by_staff' | 'reissued' | 'expired' | 'ops';
/**
 * `pos_member` (r15 K2/SEC-165): order sudah diberi member di kasir (OTP) → POS yang meng-earn, BFF tidak meng-earn
 * klaim QR untuk order itu.
 */
export type EarnStatus = 'none' | 'earned' | 'skipped' | 'limited' | 'conflict' | 'pos_member';
/** A.3: status kiriman. `submitting` = sedang/terputus saat dikirim ke Odoo (direkonsiliasi `efn_qr_status`). */
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
export type OrderState = 'draft' | 'paid' | 'cancelled';

export interface TableSessionRow {
  id: string;
  company_code: FnbCompanyCode;
  config_id: number;
  table_id: number;
  table_label: string;
  /** Odoo E-27 tidak mengirimnya saat buka sesi → umumnya null (L-80: order dari event close). */
  order_uuid: string | null;
  open_idempotency_key: string;
  open_request_sha256: string;
  print_seq: number;
  status: SessionStatus;
  close_reason: CloseReason | null;
  opened_at: Date;
  expires_at: Date;
  closed_at: Date | null;
  member_id: string | null;
  member_claimed_at: Date | null;
  earn_status: EarnStatus;
  submissions_count: number;
  items_count: number;
  last_seq: number;
  /**
   * r15 K3: waktu event `session_closed` sesi ini SELESAI diproses BFF (earn di transaksi yang sama). NULL = close belum
   * diterima → reverse `earn_channel:"qr"` untuk order sesi ini dijawab `409 earn_pending`.
   */
  close_event_at: Date | null;
}

/** Baris kiriman tersimpan — TANPA teks catatan (D-22-6), hanya `has_note`. */
export interface StoredLine {
  product_id: number;
  qty: number;
  attribute_value_ids: number[];
  has_note: boolean;
}
export interface DisplayLine {
  product_id: number;
  name: string;
  qty: number;
  /** L-78: PTAV terurut naik + teks atribut dari Odoo (mis. "Gula: Tanpa gula, Ekstra: Shot espresso"). */
  attribute_value_ids: number[];
  attributes_text: string | null;
}
export interface SubmissionRow {
  id: string;
  session_id: string;
  idempotency_key: string;
  request_sha256: string;
  lines: StoredLine[];
  items_count: number;
  display: { lines: DisplayLine[]; indicative_subtotal_excl: number | null } | null;
  status: SubmissionStatus;
  odoo_state: string | null;
  order_uuid: string | null;
  order_state: OrderState | null;
  eligible_amount: number | null;
  earn_status: EarnStatus;
  points_earned: number;
  created_at: Date;
  updated_at: Date;
}

/** Peringkat monoton status kiriman (A.3). `rejected|cancelled|failed` final. */
export const SUBMISSION_RANK: Readonly<Record<SubmissionStatus, number>> = {
  submitting: 0,
  pending_confirmation: 1,
  accepted: 2,
  preparing: 3,
  ready: 4,
  served: 5,
  rejected: 6,
  cancelled: 6,
  failed: 6,
};
export const FINAL_SUBMISSION: ReadonlySet<SubmissionStatus> = new Set(['rejected', 'cancelled', 'failed']);

/**
 * Transisi kiriman dari Odoo: hanya naik peringkat; status final tidak berubah; `served` boleh `cancelled` (dibatalkan
 * di POS setelah disajikan = jarang, tetap diterapkan agar tampilan sesuai kasir).
 */
export function canTransitionSubmission(from: SubmissionStatus, to: SubmissionStatus): boolean {
  if (from === to || FINAL_SUBMISSION.has(from)) return false;
  return SUBMISSION_RANK[to] > SUBMISSION_RANK[from];
}

/** Input baris dari klien (catatan hanya di memori sampai dikirim ke Odoo). */
export interface SubmitLineInput {
  product_id: number;
  qty: number;
  attribute_value_ids: number[];
  note: string | null;
}
export interface SubmitInput {
  lines: SubmitLineInput[];
  altcha: string | null;
}

export const FnbErrors = {
  sessionInvalid: (): ApiError => ApiError.of(401, 'table_session_invalid', 'Kode meja tidak valid atau sudah kedaluwarsa. Pindai ulang QR di meja.'),
  sessionClosed: (): ApiError => ApiError.of(409, 'table_session_closed', 'Sesi meja ini sudah ditutup. Minta QR baru ke pelayan.'),
  orderingUnavailable: (): ApiError =>
    ApiError.of(503, 'ordering_unavailable', 'Pemesanan dari ponsel sedang tidak tersedia. Silakan pesan ke pelayan.'),
  noteInvalid: (): ApiError =>
    ApiError.of(400, 'note_invalid', 'Catatan tidak boleh memuat nomor telepon, email, atau baris baru (maks. 140 karakter).'),
  idempotencyKeyRequired: (): ApiError => ApiError.of(400, 'idempotency_key_required', 'Header Idempotency-Key wajib diisi.'),
  idempotencyConflict: (): ApiError =>
    ApiError.of(409, 'idempotency_conflict', 'Permintaan dengan kunci yang sama sudah dipakai untuk pesanan lain.'),
  itemUnavailable: (productIds: number[] = []): ApiError =>
    new ApiError(409, {
      error: { code: 'item_unavailable', message: 'Sebagian menu sedang tidak tersedia. Perbarui pesanan Anda.' },
      product_ids: productIds,
    }),
  sessionLimitReached: (): ApiError =>
    ApiError.of(422, 'session_limit_reached', 'Batas pesanan untuk meja ini tercapai. Silakan pesan ke pelayan.'),
  pointsAlreadyClaimed: (): ApiError => ApiError.of(409, 'points_already_claimed', 'Poin meja ini sudah diklaim akun lain.'),
  pointsUnavailable: (): ApiError => ApiError.of(422, 'points_unavailable', 'Poin untuk pesanan meja belum tersedia.'),
  accountDeleting: (): ApiError => ApiError.of(409, 'account_deleting', 'Akun sedang dijadwalkan untuk dihapus.'),
  // ── Odoo → BFF (/v1/pos/fnb/*) ──
  tableSessionExists: (): ApiError => ApiError.of(409, 'table_session_exists', 'Meja ini masih punya sesi QR yang terbuka.'),
  qrDisabled: (): ApiError => ApiError.of(422, 'qr_disabled', 'Pemesanan QR tidak aktif untuk outlet ini.'),
  companyMismatch: (): ApiError => Errors.conflict('company_mismatch', 'PT/config tidak cocok dengan sesi.'),
};

// eslint-disable-next-line no-control-regex
const CONTROL_RE = /[\u0000-\u001f\u007f-\u009f\u2028\u2029]/g;
// ↑ CONTROL_RE (langkah 3 di bawah): kontrol C0 U+0000-U+001F, DEL + C1 U+007F-U+009F, U+2028, U+2029 → spasi.

// ───────────── Filter catatan (D-22-6, SEC-168 r15) — HARUS identik dengan Odoo `qr_common.py` ─────────────
// Sumber ini sengaja TIDAK memuat karakter non-ASCII literal maupun escape backslash-u untuk karakter format: kelas
// karakter dibangun dari kode titik heksadesimal (pernah ada karakter literal yang merusak kompilasi/tinjauan).
const cp = (n: number): string => String.fromCharCode(n);
const span = (a: number, b: number): string => `${cp(a)}-${cp(b)}`;
/** Langkah 2: karakter format yang DIHAPUS (bukan diganti spasi). */
export const NOTE_FORMAT_RANGES: readonly (readonly [number, number])[] = [
  [0x200b, 0x200f], // zero-width space/non-joiner/joiner, LRM, RLM
  [0x202a, 0x202e], // bidi embedding/override (LRE, RLE, PDF, LRO, RLO)
  [0x2060, 0x2064], // word joiner, invisible operators
  [0x2066, 0x2069], // bidi isolate (LRI, RLI, FSI, PDI)
  [0xfeff, 0xfeff], // BOM / zero-width no-break space
];
const FORMAT_RE = new RegExp(`[${NOTE_FORMAT_RANGES.map(([a, b]) => (a === b ? cp(a) : span(a, b))).join('')}]`, 'g');
/** Langkah 7a: digit desimal Unicode kategori Nd (Python: `ch.isdecimal()`). */
const DECIMAL_DIGIT_RE = /\p{Nd}/gu;
/** Langkah 7b: aturan D-22-6 lama, ASCII saja (Python: flag `re.ASCII`). */
const PHONE_LIKE_RE = /[0-9][0-9\s.-]{6,}[0-9]/;
export const NOTE_MAX = 140;
/** SEC-168: maksimum digit Nd per catatan; 8 atau lebih → ditolak. */
export const NOTE_MAX_DIGITS = 7;

/**
 * A.3 / D-22-6 / SEC-168: catatan baris → teks bersih atau `400 note_invalid`. Hasil hanya diteruskan ke Odoo (tidak
 * disimpan/di-log BFF). ATURAN (urutan wajib; Odoo `qr_common.py` harus sama persis):
 *  0. `undefined`/`null` → null. Bukan string atau panjang mentah > 1000 → tolak.
 *  1. Normalisasi Unicode NFKC (angka fullwidth U+FF10..U+FF19 → 0..9, U+FF20 → '@', spasi lebar → spasi).
 *  2. HAPUS karakter format: U+200B–U+200F, U+202A–U+202E, U+2060–U+2064, U+2066–U+2069, U+FEFF.
 *  3. Ganti kontrol U+0000–U+001F, U+007F–U+009F, U+2028, U+2029 dengan spasi.
 *  4. Runtun whitespace apa pun → satu spasi U+0020; trim.
 *  5. Kosong → null.
 *  6. > 140 CODE POINT (Python `len(s)`; JS `[...s].length`, bukan `.length` UTF-16) → tolak.
 *  7. Tolak bila memuat '@', ATAU (a) jumlah digit desimal Unicode (Nd) di SELURUH catatan ≥ 8, ATAU (b) cocok deret ASCII
 *     `[0-9][0-9\s.-]{6,}[0-9]` (aturan lama; gabungan keduanya = aturan yang lebih ketat).
 * 7a sengaja tanpa "jendela": pemisah apa pun (`0812-3456 / 789`, huruf/simbol di antara digit) tidak lagi menjadi bypass
 * selama total digit ≥ 8. Upaya terbaik: huruf "O"/"l" sebagai angka atau angka ditulis kata tetap lolos (DPIA §13).
 */
export function sanitizeNote(v: unknown): string | null {
  if (v === undefined || v === null) return null;
  if (typeof v !== 'string' || v.length > 1000) throw FnbErrors.noteInvalid();
  const s = v.normalize('NFKC').replace(FORMAT_RE, '').replace(CONTROL_RE, ' ').replace(/\s+/g, ' ').trim();
  if (s.length === 0) return null;
  if ([...s].length > NOTE_MAX || s.includes('@')) throw FnbErrors.noteInvalid();
  if ((s.match(DECIMAL_DIGIT_RE) ?? []).length > NOTE_MAX_DIGITS || PHONE_LIKE_RE.test(s)) throw FnbErrors.noteInvalid();
  return s;
}

function int(v: unknown, min: number, max: number): number {
  if (typeof v !== 'number' || !Number.isSafeInteger(v) || v < min || v > max) throw Errors.invalidRequest();
  return v;
}

/**
 * A.3 `POST /v1/fnb/orders` body `{ lines: [{ product_id, qty, attribute_value_ids, note? }], altcha? }` —
 * `additionalProperties:false` (API3: klien tidak bisa mengirim harga, meja, config, member, company).
 */
export function parseSubmitBody(body: unknown, limits: { maxLines: number; maxLineQty: number }): SubmitInput {
  if (typeof body !== 'object' || body === null || Array.isArray(body)) throw Errors.invalidRequest();
  const o = body as Record<string, unknown>;
  if (Object.keys(o).some((k) => k !== 'lines' && k !== 'altcha')) throw Errors.invalidRequest();
  if (!Array.isArray(o.lines) || o.lines.length < 1 || o.lines.length > limits.maxLines) throw Errors.invalidRequest();
  const lines = o.lines.map((raw): SubmitLineInput => {
    if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) throw Errors.invalidRequest();
    const l = raw as Record<string, unknown>;
    if (Object.keys(l).some((k) => !['product_id', 'qty', 'attribute_value_ids', 'note'].includes(k))) throw Errors.invalidRequest();
    const ptav = l.attribute_value_ids === undefined ? [] : l.attribute_value_ids;
    if (!Array.isArray(ptav) || ptav.length > 10) throw Errors.invalidRequest();
    const ids = ptav.map((x) => int(x, 1, 2_147_483_647));
    if (new Set(ids).size !== ids.length) throw Errors.invalidRequest();
    return {
      product_id: int(l.product_id, 1, 2_147_483_647),
      qty: int(l.qty, 1, limits.maxLineQty),
      attribute_value_ids: [...ids].sort((a, b) => a - b),
      note: sanitizeNote(l.note),
    };
  });
  let altcha: string | null = null;
  if (o.altcha !== undefined && o.altcha !== null) {
    if (typeof o.altcha !== 'string' || o.altcha.length === 0 || o.altcha.length > 4096) throw Errors.invalidRequest();
    altcha = o.altcha;
  }
  return { lines, altcha };
}

/** Sidik body untuk idempotensi kiriman (`request_sha256`). Catatan ikut sebagai hash-nya saja (tidak disimpan teks). */
export function submitSha256(input: SubmitInput): string {
  const canonical = input.lines.map((l) => [
    l.product_id,
    l.qty,
    l.attribute_value_ids,
    l.note === null ? null : createHash('sha256').update(l.note, 'utf8').digest('hex'),
  ]);
  return createHash('sha256').update(JSON.stringify(canonical), 'utf8').digest('hex');
}

/** Bentuk baris yang disimpan (tanpa teks catatan). */
export function storedLines(input: SubmitInput): StoredLine[] {
  return input.lines.map((l) => ({ product_id: l.product_id, qty: l.qty, attribute_value_ids: l.attribute_value_ids, has_note: l.note !== null }));
}

export const IDEMPOTENCY_KEY_RE = /^[A-Za-z0-9_-]{16,128}$/;
/**
 * A.4: `fnb_open:<config_id>:<table_id>:<n>` (bentuk Odoo E-27; n = nomor cetak per meja). Bentuk kontrak awal
 * `fnb_open:<order_uuid>:<n>` tetap diterima (group 1 = order_uuid, group 2 = n).
 * SEC-167 r15: n ≤ 99 999 999 (sebelumnya ≤ 9999; kolom `print_seq` integer).
 */
export const OPEN_KEY_RE = /^fnb_open:(?:(\d{1,10}):(\d{1,10})|([A-Za-z0-9-]{1,64})):([1-9]\d{0,7})$/;
/** Label meja (nama meja/area, bukan PII): ≤ 32, tanpa kontrol, tanpa `@`, tanpa deret mirip HP. */
export function validTableLabel(v: unknown): v is string {
  return (
    typeof v === 'string' &&
    v.trim().length >= 1 &&
    v.length <= 32 &&
    !/[\u0000-\u001f\u007f]/.test(v) &&
    !v.includes('@') &&
    !PHONE_LIKE_RE.test(v)
  );
}
