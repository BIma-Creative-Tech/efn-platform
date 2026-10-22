/**
 * Reservasi meja (docs/04 §15.5 BFF, §15.7 web; G23) — tipe kontrak + validator + parser MURNI (client, Route Handler, test).
 *
 * ADR terkait: ADR-013 (booking dibuka G23 sebagai modul custom `efn_table_booking`), ADR-029 (data tamu minimal).
 * Prinsip:
 *  - Ketersediaan, meja, dan bentrok dihitung Odoo (lewat BFF); klien hanya menampilkan & memilih.
 *  - Data tamu minimal (L-86): `guest_label` = nama panggilan ≤ 40, `note` ≤ 140. Keduanya TIDAK disimpan di storage
 *    browser, tidak di-log, dan tidak masuk URL. Draft yang dibawa melewati login hanya gerai/tanggal/jumlah/jam.
 *  - Semua respons BFF di-allowlist (field lain dibuang).
 *  - Kill switch BFF `BOOKING_ENABLED=false` -> 503 `booking_unavailable` -> halaman "Reservasi belum tersedia".
 */
import { COMPANIES, type Brand, type CompanyCode } from './brands';
import { isOutletCode, isUuid, OUTLET_RE } from './catalog';

export { OUTLET_RE };

// ---------- kelayakan brand ----------

/** Brand yang punya PT F&B (PB, BCF) -> reservasi & makan di tempat relevan. GrandLucky (ritel saja) -> tidak. */
export function brandHasFnb(brand: Pick<Brand, 'companies'>): boolean {
  return brand.companies.some((c) => COMPANIES[c as CompanyCode]?.businessType === 'fnb');
}

// ---------- batas & pola ----------

export const GUEST_LABEL_MAX = 40;
export const NOTE_MAX = 140;
export const MAX_PARTY = 50;
export const MAX_SLOTS = 96;
export const MAX_HORIZON_DAYS = 60;
export const BOOKING_BODY_MAX_BYTES = 4 * 1024;
/** Kontrak §15.5: Idempotency-Key 16–64. UUID v4 (36) lolos. */
export const BOOKING_IDEMPOTENCY_KEY_RE = /^[A-Za-z0-9_-]{16,64}$/;
export const DATE_RE = /^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$/;
/** ISO-8601 dengan zona (UTC `Z` atau offset). */
export const ISO_INSTANT_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(?:Z|[+-]\d{2}:\d{2})$/;
export const DEFAULT_TIMEZONE = 'Asia/Jakarta';

export function isIsoDate(v: unknown): v is string {
  if (typeof v !== 'string' || !DATE_RE.test(v)) return false;
  const d = new Date(`${v}T00:00:00Z`);
  return !Number.isNaN(d.getTime()) && d.toISOString().slice(0, 10) === v;
}

export function isIsoInstant(v: unknown): v is string {
  return typeof v === 'string' && v.length <= 40 && ISO_INSTANT_RE.test(v) && !Number.isNaN(Date.parse(v));
}

/** Zona IANA yang dikenali runtime; selain itu `Asia/Jakarta` (zona outlet default). */
export function safeTimeZone(v: unknown): string {
  if (typeof v !== 'string' || !/^[A-Za-z_]+(?:\/[A-Za-z0-9_+-]+){0,2}$/.test(v) || v.length > 64) return DEFAULT_TIMEZONE;
  try {
    new Intl.DateTimeFormat('en-US', { timeZone: v });
    return v;
  } catch {
    return DEFAULT_TIMEZONE;
  }
}

export function parsePartySize(v: unknown): number | null {
  const n = typeof v === 'number' ? v : typeof v === 'string' && /^\d{1,2}$/.test(v) ? Number(v) : NaN;
  return Number.isInteger(n) && n >= 1 && n <= MAX_PARTY ? n : null;
}

// ---------- tanggal (zona outlet) ----------

/** Tanggal kalender `YYYY-MM-DD` di zona `tz` untuk instan `nowMs`. */
export function todayIn(tz: string, nowMs: number): string {
  // en-CA memformat tanggal sebagai YYYY-MM-DD.
  return new Intl.DateTimeFormat('en-CA', { timeZone: safeTimeZone(tz), year: 'numeric', month: '2-digit', day: '2-digit' }).format(nowMs);
}

export function addDays(date: string, days: number): string {
  const d = new Date(`${date}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

/** `n` tanggal berurutan mulai `start` (n dibatasi 1..60). */
export function dateOptions(start: string, n: number): string[] {
  if (!isIsoDate(start)) return [];
  const count = Math.max(1, Math.min(MAX_HORIZON_DAYS, Math.floor(Number.isFinite(n) ? n : 1)));
  return Array.from({ length: count }, (_, i) => addDays(start, i));
}

const WEEKDAY = new Intl.DateTimeFormat('id-ID', { weekday: 'short', timeZone: 'UTC' });
const DAY_MONTH = new Intl.DateTimeFormat('id-ID', { day: 'numeric', month: 'short', timeZone: 'UTC' });
const LONG_DATE = new Intl.DateTimeFormat('id-ID', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric', timeZone: 'UTC' });

/** Label chip tanggal: `{ weekday: 'Kam', day: '22 Okt' }` (tanggal kalender, bukan instan). */
export function dateChipLabel(date: string): { weekday: string; day: string } {
  const t = Date.parse(`${date}T00:00:00Z`);
  if (Number.isNaN(t)) return { weekday: '', day: date };
  return { weekday: WEEKDAY.format(t), day: DAY_MONTH.format(t) };
}

export function longDateLabel(date: string): string {
  const t = Date.parse(`${date}T00:00:00Z`);
  return Number.isNaN(t) ? date : LONG_DATE.format(t);
}

/** Jam lokal outlet, mis. `18.30`. */
export function formatClockIn(iso: string | null | undefined, tz: string): string {
  const t = iso ? Date.parse(iso) : NaN;
  if (Number.isNaN(t)) return '';
  return new Intl.DateTimeFormat('id-ID', { hour: '2-digit', minute: '2-digit', hour12: false, timeZone: safeTimeZone(tz) }).format(t);
}

/** Tanggal + jam lokal outlet, mis. `Kamis, 22 Oktober 2026 pukul 18.30`. */
export function formatDateTimeIn(iso: string | null | undefined, tz: string): string {
  const t = iso ? Date.parse(iso) : NaN;
  if (Number.isNaN(t)) return '';
  return new Intl.DateTimeFormat('id-ID', { dateStyle: 'full', timeStyle: 'short', timeZone: safeTimeZone(tz) }).format(t);
}

// ---------- kontrak respons ----------

export interface BookingOutlet {
  outlet_code: string;
  company_code: string;
  channel_code: string;
  public_name: string;
}

export interface BookingSlot {
  start_at: string;
  available: boolean;
}

export interface Availability {
  outlet_code: string;
  date: string;
  timezone: string;
  slot_minutes: number;
  duration_minutes: number;
  min_party: number;
  max_party: number;
  horizon_days: number;
  slots: BookingSlot[];
}

export type BookingStatus = 'requested' | 'confirmed' | 'seated' | 'done' | 'cancelled' | 'no_show' | 'failed';

export interface Reservation {
  booking_id: string;
  status: BookingStatus | 'unknown';
  outlet_code: string;
  outlet_name: string;
  start_at: string | null;
  end_at: string | null;
  party_size: number;
  table_label: string | null;
}

function isObj(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v);
}
function str(v: unknown, max: number): string | null {
  return typeof v === 'string' ? v.slice(0, max) : null;
}
function int(v: unknown, min: number, max: number): number | null {
  return typeof v === 'number' && Number.isInteger(v) && v >= min && v <= max ? v : null;
}

export function parseBookingOutlets(v: unknown): BookingOutlet[] | null {
  if (!isObj(v) || !Array.isArray(v.items)) return null;
  const out: BookingOutlet[] = [];
  for (const o of v.items) {
    if (!isObj(o) || !isOutletCode(o.outlet_code)) continue;
    const company = str(o.company_code, 16);
    if (!company || !/^[A-Z][A-Z0-9]{1,15}$/.test(company)) continue;
    out.push({
      outlet_code: o.outlet_code,
      company_code: company,
      channel_code: typeof o.channel_code === 'string' && /^[A-Z][A-Z0-9-]{1,15}$/.test(o.channel_code) ? o.channel_code : company,
      public_name: str(o.public_name, 120) || o.outlet_code,
    });
    if (out.length >= 100) break;
  }
  return out;
}

/** Gerai reservasi yang boleh tampil di brand ini (PT brand). Holding (`efn`) = semua PT-nya. */
export function outletsForBrand(outlets: BookingOutlet[], companies: readonly string[]): BookingOutlet[] {
  return outlets.filter((o) => companies.includes(o.company_code));
}

export function parseAvailability(v: unknown): Availability | null {
  if (!isObj(v) || !isOutletCode(v.outlet_code) || !isIsoDate(v.date) || !Array.isArray(v.slots)) return null;
  const minParty = int(v.min_party, 1, MAX_PARTY) ?? 1;
  const maxParty = Math.max(minParty, int(v.max_party, 1, MAX_PARTY) ?? minParty);
  const slots: BookingSlot[] = [];
  const seen = new Set<string>();
  for (const s of v.slots) {
    if (!isObj(s) || !isIsoInstant(s.start_at) || seen.has(s.start_at)) continue;
    seen.add(s.start_at);
    slots.push({ start_at: s.start_at, available: s.available === true });
    if (slots.length >= MAX_SLOTS) break;
  }
  slots.sort((a, b) => Date.parse(a.start_at) - Date.parse(b.start_at));
  return {
    outlet_code: v.outlet_code,
    date: v.date,
    timezone: safeTimeZone(v.timezone),
    slot_minutes: int(v.slot_minutes, 5, 240) ?? 30,
    duration_minutes: int(v.duration_minutes, 15, 720) ?? 90,
    min_party: minParty,
    max_party: maxParty,
    horizon_days: int(v.horizon_days, 1, MAX_HORIZON_DAYS) ?? 1,
    slots,
  };
}

const STATUSES: readonly BookingStatus[] = ['requested', 'confirmed', 'seated', 'done', 'cancelled', 'no_show', 'failed'];

export function isBookingStatus(v: unknown): v is BookingStatus {
  return typeof v === 'string' && (STATUSES as readonly string[]).includes(v);
}

export function parseReservation(v: unknown): Reservation | null {
  if (!isObj(v) || !isUuid(v.booking_id)) return null;
  return {
    booking_id: v.booking_id.toLowerCase(),
    status: isBookingStatus(v.status) ? v.status : 'unknown',
    outlet_code: isOutletCode(v.outlet_code) ? v.outlet_code : '',
    outlet_name: str(v.outlet_name, 120) ?? '',
    start_at: isIsoInstant(v.start_at) ? v.start_at : null,
    end_at: isIsoInstant(v.end_at) ? v.end_at : null,
    party_size: int(v.party_size, 1, MAX_PARTY) ?? 0,
    table_label: str(v.table_label, 60),
  };
}

/** `{ items: [...] }` (atau array langsung) -> daftar reservasi valid (≤ 20). */
export function parseReservationList(v: unknown): Reservation[] | null {
  const items = Array.isArray(v) ? v : isObj(v) && Array.isArray(v.items) ? v.items : null;
  if (!items) return null;
  return items
    .map(parseReservation)
    .filter((r): r is Reservation => r !== null)
    .slice(0, 20);
}

export const BOOKING_STATUS_LABEL: Record<BookingStatus | 'unknown', string> = {
  requested: 'Menunggu konfirmasi gerai',
  confirmed: 'Dikonfirmasi',
  seated: 'Sudah datang',
  done: 'Selesai',
  cancelled: 'Dibatalkan',
  no_show: 'Tidak datang',
  failed: 'Gagal dibuat',
  unknown: 'Status tidak dikenal',
};

/** Batal hanya untuk `requested|confirmed` yang belum mulai (§15.3 RPC 18). Server tetap memutuskan. */
export function canCancelReservation(r: Pick<Reservation, 'status' | 'start_at'>, nowMs: number): boolean {
  if (r.status !== 'requested' && r.status !== 'confirmed') return false;
  const t = r.start_at ? Date.parse(r.start_at) : NaN;
  return !Number.isNaN(t) && t > nowMs;
}

// ---------- input pelanggan ----------

export interface CreateBookingBody {
  outlet_code: string;
  start_at: string;
  party_size: number;
  guest_label?: string;
  note?: string;
}

/** Bersihkan teks bebas: karakter kontrol -> spasi, spasi beruntun dipadatkan, trim. */
export function cleanText(v: unknown): string {
  if (typeof v !== 'string') return '';
  return v.replace(/[\u0000-\u001f\u007f-\u009f]/g, ' ').replace(/\s+/g, ' ').trim();
}

/** Deretan ≥ 8 digit (boleh dipisah spasi/tanda hubung/titik) = kemungkinan nomor HP -> ditolak (minimasi data, ADR-029). */
export function looksLikePhone(v: string): boolean {
  return /(?:\+?\d[\s.-]?){8,}/.test(v);
}

export type GuestInputError = 'guest_label_too_long' | 'note_too_long' | 'guest_label_phone' | 'note_phone';

export const GUEST_INPUT_MESSAGES: Record<GuestInputError, string> = {
  guest_label_too_long: `Nama panggilan maksimal ${GUEST_LABEL_MAX} karakter.`,
  note_too_long: `Catatan maksimal ${NOTE_MAX} karakter.`,
  guest_label_phone: 'Isi nama panggilan saja, jangan nomor HP.',
  note_phone: 'Jangan tulis nomor HP di catatan; gerai menghubungi Anda lewat aplikasi.',
};

/** Validasi UX nama panggilan & catatan (server tetap memvalidasi + filter SEC-168). */
export function validateGuestInput(label: string, note: string): Partial<Record<'guest_label' | 'note', GuestInputError>> {
  const errs: Partial<Record<'guest_label' | 'note', GuestInputError>> = {};
  const l = cleanText(label);
  const n = cleanText(note);
  if (Array.from(l).length > GUEST_LABEL_MAX) errs.guest_label = 'guest_label_too_long';
  else if (l && looksLikePhone(l)) errs.guest_label = 'guest_label_phone';
  if (Array.from(n).length > NOTE_MAX) errs.note = 'note_too_long';
  else if (n && looksLikePhone(n)) errs.note = 'note_phone';
  return errs;
}

/**
 * Allowlist body POST reservasi (Route Handler). Field lain dibuang; bentuk salah -> null.
 * `guest_label`/`note` kosong dihilangkan.
 */
export function sanitizeCreateBooking(v: unknown): CreateBookingBody | null {
  if (!isObj(v) || !isOutletCode(v.outlet_code) || !isIsoInstant(v.start_at)) return null;
  const party = parsePartySize(v.party_size);
  if (party === null) return null;
  const body: CreateBookingBody = { outlet_code: v.outlet_code, start_at: v.start_at, party_size: party };
  if (v.guest_label !== undefined && v.guest_label !== null) {
    if (typeof v.guest_label !== 'string') return null;
    const l = cleanText(v.guest_label);
    if (Array.from(l).length > GUEST_LABEL_MAX) return null;
    if (l) body.guest_label = l;
  }
  if (v.note !== undefined && v.note !== null) {
    if (typeof v.note !== 'string') return null;
    const n = cleanText(v.note);
    if (Array.from(n).length > NOTE_MAX) return null;
    if (n) body.note = n;
  }
  return body;
}

// ---------- draft lintas login (sessionStorage) & idempotency ----------

/** Pilihan yang dibawa melewati halaman masuk. SENGAJA tanpa nama panggilan/catatan (data tamu tidak disimpan). */
export interface BookingDraft {
  outlet_code: string;
  date: string;
  party_size: number;
  start_at: string | null;
}

export const BOOKING_DRAFT_KEY = 'efn_booking_draft_v1';
export const BOOKING_ATTEMPT_KEY = 'efn_booking_attempt_v1';

export function parseBookingDraft(raw: string | null): BookingDraft | null {
  if (!raw || raw.length > 512) return null;
  try {
    const v: unknown = JSON.parse(raw);
    if (!isObj(v) || !isOutletCode(v.outlet_code) || !isIsoDate(v.date)) return null;
    const party = parsePartySize(v.party_size);
    if (party === null) return null;
    return { outlet_code: v.outlet_code, date: v.date, party_size: party, start_at: isIsoInstant(v.start_at) ? v.start_at : null };
  } catch {
    return null;
  }
}

export function serializeBookingDraft(d: BookingDraft): string {
  return JSON.stringify({ outlet_code: d.outlet_code, date: d.date, party_size: d.party_size, start_at: d.start_at });
}

export interface BookingAttempt {
  key: string;
  fp: string;
}

export function parseBookingAttempt(raw: string | null): BookingAttempt | null {
  if (!raw || raw.length > 300) return null;
  try {
    const v: unknown = JSON.parse(raw);
    if (isObj(v) && typeof v.key === 'string' && BOOKING_IDEMPOTENCY_KEY_RE.test(v.key) && typeof v.fp === 'string' && v.fp.length <= 64) {
      return { key: v.key, fp: v.fp };
    }
  } catch {
    // diabaikan
  }
  return null;
}

/** cyrb53 — sidik non-kriptografis isi permintaan (memutuskan pemakaian ulang key; bukan keamanan). */
export function bookingFingerprint(body: CreateBookingBody): string {
  const text = JSON.stringify([body.outlet_code, body.start_at, body.party_size, body.guest_label ?? '', body.note ?? '']);
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

/** Isi sama -> key percobaan sebelumnya dipakai ulang (retry tidak membuat reservasi ganda); isi beda -> key baru. */
export function resolveBookingAttempt(stored: BookingAttempt | null, fp: string, generate: () => string): BookingAttempt {
  if (stored && stored.fp === fp && BOOKING_IDEMPOTENCY_KEY_RE.test(stored.key)) return stored;
  return { key: generate(), fp };
}

// ---------- peta error -> pesan Indonesia ----------

export const BOOKING_ERROR_MESSAGES: Record<string, string> = {
  slot_unavailable: 'Jam ini baru saja penuh. Pilih jam lain.',
  outside_hours: 'Jam yang dipilih di luar jam buka gerai. Pilih jam lain.',
  too_late: 'Jam ini sudah terlalu dekat untuk dipesan online. Pilih jam yang lebih lambat.',
  too_far: 'Tanggal ini belum dibuka untuk reservasi. Pilih tanggal yang lebih dekat.',
  party_size: 'Jumlah tamu di luar batas reservasi online gerai ini. Untuk rombongan besar, hubungi gerai.',
  booking_unavailable: 'Reservasi belum tersedia di gerai ini. Silakan coba lagi nanti.',
  booking_not_cancellable: 'Reservasi ini tidak bisa dibatalkan lagi (sudah dimulai, selesai, atau sudah dibatalkan).',
  idempotency_conflict: 'Permintaan reservasi ini sedang diproses. Buka "Reservasi saya" untuk melihat statusnya.',
  idempotency_key_required: 'Permintaan tidak valid. Muat ulang halaman lalu coba lagi.',
  too_many_active_bookings: 'Anda sudah punya batas maksimum reservasi aktif. Batalkan salah satu atau tunggu sampai selesai.',
  // Kode BFF G23 (src/booking/booking.model.ts) untuk batas reservasi aktif per member.
  booking_limit: 'Anda sudah punya batas maksimum reservasi aktif. Batalkan salah satu atau tunggu sampai selesai.',
  note_invalid: 'Nama/catatan tidak boleh memuat nomor telepon, email, atau baris baru.',
  rate_limited: 'Terlalu banyak permintaan. Tunggu sebentar lalu coba lagi.',
  invalid_request: 'Permintaan tidak valid. Muat ulang halaman lalu coba lagi.',
  not_found: 'Reservasi tidak ditemukan.',
  unauthenticated: 'Sesi berakhir. Silakan masuk kembali.',
  payload_too_large: 'Data terlalu besar.',
  forbidden: 'Permintaan ditolak.',
  service_unavailable: 'Layanan sedang tidak tersedia. Silakan coba lagi.',
};

export const GENERIC_BOOKING_ERROR = 'Terjadi kendala. Silakan coba lagi beberapa saat lagi.';

export function bookingErrorMessage(code: string | null | undefined): string {
  if (code && Object.prototype.hasOwnProperty.call(BOOKING_ERROR_MESSAGES, code)) return BOOKING_ERROR_MESSAGES[code]!;
  return GENERIC_BOOKING_ERROR;
}

/** Kode yang berarti "pilih jam lain" -> klien memuat ulang ketersediaan. */
export const BOOKING_RELOAD_CODES: readonly string[] = ['slot_unavailable', 'outside_hours', 'too_late', 'too_far'];
