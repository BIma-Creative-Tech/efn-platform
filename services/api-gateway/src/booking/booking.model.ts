import { ApiError, Errors } from '../common/errors/api-error';
import { isUuid } from '../common/validation';

/**
 * Model & parser reservasi meja (docs/04 §15.2–§15.5, L-85..L-90). Klien tidak bisa mengirim meja, PT, kanal, member,
 * atau durasi; semua diturunkan server. `guest_label`/`note` hanya diteruskan ke Odoo (tidak disimpan BFF, L-86).
 */

export const BOOKING_STATUSES = ['pending', 'requested', 'confirmed', 'seated', 'done', 'cancelled', 'no_show', 'failed'] as const;
export type BookingStatus = (typeof BOOKING_STATUSES)[number];
export const ACTIVE_STATUSES: readonly BookingStatus[] = ['pending', 'requested', 'confirmed'];
export const ODOO_STATES = ['requested', 'confirmed', 'seated', 'done', 'cancelled', 'no_show'] as const;
export type OdooBookingState = (typeof ODOO_STATES)[number];
export const BOOKING_EVENTS = ['confirmed', 'seated', 'done', 'cancelled', 'no_show'] as const;
export type BookingEvent = (typeof BOOKING_EVENTS)[number];

/** Urutan maju status (event lama/terlambat tidak boleh memundurkan status). */
export const STATUS_RANK: Record<BookingStatus, number> = {
  pending: 0,
  failed: 0,
  requested: 1,
  confirmed: 2,
  seated: 3,
  done: 4,
  cancelled: 4,
  no_show: 4,
};

export const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
export const ISO_UTC_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:00Z$/;
export const OUTLET_CODE_RE = /^[A-Z0-9][A-Z0-9_-]{0,31}$/;
export const IDEMPOTENCY_KEY_RE = /^[A-Za-z0-9_-]{16,64}$/;
export const GUEST_LABEL_MAX = 40;
export const NOTE_MAX = 140;
export const BOOKING_BODY_LIMIT = 4 * 1024;

export const BookingErrors = {
  unavailable: (): ApiError => ApiError.of(503, 'booking_unavailable', 'Reservasi meja sedang tidak tersedia. Silakan coba lagi nanti.'),
  outletUnavailable: (): ApiError => ApiError.of(409, 'booking_unavailable', 'Reservasi di gerai ini sedang tidak tersedia.'),
  slotUnavailable: (): ApiError => ApiError.of(409, 'slot_unavailable', 'Jam yang dipilih sudah penuh. Pilih jam lain.'),
  outsideHours: (): ApiError => ApiError.of(409, 'outside_hours', 'Jam yang dipilih di luar jam reservasi gerai.'),
  tooLate: (): ApiError => ApiError.of(409, 'too_late', 'Jam yang dipilih terlalu dekat. Pilih jam yang lebih lambat.'),
  tooFar: (): ApiError => ApiError.of(409, 'too_far', 'Tanggal yang dipilih terlalu jauh ke depan.'),
  partySize: (): ApiError => ApiError.of(409, 'party_size', 'Jumlah tamu di luar batas reservasi gerai ini.'),
  tooManyActive: (): ApiError =>
    ApiError.of(409, 'booking_limit', 'Anda sudah punya reservasi aktif maksimal. Batalkan salah satu untuk membuat yang baru.'),
  notCancellable: (): ApiError => ApiError.of(409, 'booking_not_cancellable', 'Reservasi ini sudah tidak bisa dibatalkan.'),
  idempotencyKeyRequired: (): ApiError => ApiError.of(400, 'idempotency_key_required', 'Header Idempotency-Key wajib diisi.'),
  idempotencyConflict: (): ApiError =>
    ApiError.of(409, 'idempotency_conflict', 'Permintaan dengan kunci yang sama sudah dipakai untuk reservasi lain.'),
  textInvalid: (): ApiError =>
    ApiError.of(400, 'note_invalid', 'Nama/catatan tidak boleh memuat nomor telepon, email, atau baris baru.'),
};

const FORMAT_RE = /[\p{Cf}]/gu;
const CONTROL_RE = /[\p{Cc}]/gu;
const DECIMAL_DIGIT_RE = /\p{Nd}/gu;
const PHONE_LIKE_RE = /(\+?\p{Nd}[\p{Nd}\s.()-]{6,}\p{Nd})/u;

/** SEC-168 (pola QR): tanpa '@', tanpa deretan angka mirip nomor HP, tanpa baris baru; kosong → null. */
export function sanitizeGuestText(v: unknown, max: number): string | null {
  if (v === undefined || v === null) return null;
  if (typeof v !== 'string') throw Errors.invalidRequest();
  if (/[\r\n]/.test(v)) throw BookingErrors.textInvalid();
  const s = v.normalize('NFKC').replace(FORMAT_RE, '').replace(CONTROL_RE, ' ').replace(/\s+/g, ' ').trim();
  if (s.length === 0) return null;
  if ([...s].length > max || s.includes('@')) throw BookingErrors.textInvalid();
  if ((s.match(DECIMAL_DIGIT_RE) ?? []).length >= 8 || PHONE_LIKE_RE.test(s)) throw BookingErrors.textInvalid();
  return s;
}

export interface CreateBookingInput {
  outlet_code: string;
  start_at: string;
  party_size: number;
  guest_label: string | null;
  note: string | null;
}

/** §15.5 body `{ outlet_code, start_at, party_size, guest_label?, note? }` — additionalProperties:false. */
export function parseCreateBooking(body: unknown): CreateBookingInput {
  if (typeof body !== 'object' || body === null || Array.isArray(body)) throw Errors.invalidRequest();
  const o = body as Record<string, unknown>;
  const allowed = new Set(['outlet_code', 'start_at', 'party_size', 'guest_label', 'note']);
  if (Object.keys(o).some((k) => !allowed.has(k))) throw Errors.invalidRequest();
  if (typeof o.outlet_code !== 'string' || !OUTLET_CODE_RE.test(o.outlet_code)) throw Errors.invalidRequest();
  if (typeof o.start_at !== 'string' || !ISO_UTC_RE.test(o.start_at) || Number.isNaN(Date.parse(o.start_at))) throw Errors.invalidRequest();
  if (typeof o.party_size !== 'number' || !Number.isSafeInteger(o.party_size) || o.party_size < 1 || o.party_size > 50) {
    throw Errors.invalidRequest();
  }
  return {
    outlet_code: o.outlet_code,
    start_at: o.start_at,
    party_size: o.party_size,
    guest_label: sanitizeGuestText(o.guest_label, GUEST_LABEL_MAX),
    note: sanitizeGuestText(o.note, NOTE_MAX),
  };
}

export interface AvailabilityQuery {
  outlet_code: string;
  date: string;
  party: number;
}

export function parseAvailabilityQuery(q: Record<string, unknown>): AvailabilityQuery {
  const outlet = q.outlet;
  const date = q.date;
  const party = typeof q.party === 'string' && /^\d{1,2}$/.test(q.party) ? Number(q.party) : NaN;
  if (typeof outlet !== 'string' || !OUTLET_CODE_RE.test(outlet)) throw Errors.invalidRequest();
  if (typeof date !== 'string' || !DATE_RE.test(date) || Number.isNaN(Date.parse(`${date}T00:00:00Z`))) throw Errors.invalidRequest();
  if (!Number.isSafeInteger(party) || party < 1 || party > 50) throw Errors.invalidRequest();
  return { outlet_code: outlet, date, party };
}

export function bookingIdParam(v: unknown): string {
  if (!isUuid(v)) throw Errors.notFound();
  return v.toLowerCase();
}

/** Event Odoo → BFF (§15.5). Validasi ketat (additionalProperties:false). */
export interface BookingEventInput {
  schema_version: 1;
  idempotency_key: string;
  seq: number;
  platform_booking_ref: string;
  company_code: string;
  outlet_code: string;
  event: BookingEvent;
  state: OdooBookingState;
  occurred_at: string;
}

export function parseBookingEvent(raw: Buffer | undefined): BookingEventInput {
  if (!raw || raw.length === 0 || raw.length > BOOKING_BODY_LIMIT) throw Errors.invalidRequest();
  let o: Record<string, unknown>;
  try {
    const parsed: unknown = JSON.parse(raw.toString('utf8'));
    if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) throw new Error('bukan objek');
    o = parsed as Record<string, unknown>;
  } catch {
    throw Errors.invalidRequest();
  }
  const keys = ['schema_version', 'idempotency_key', 'seq', 'platform_booking_ref', 'company_code', 'outlet_code', 'event', 'state', 'occurred_at'];
  if (Object.keys(o).length !== keys.length || keys.some((k) => !(k in o))) throw Errors.invalidRequest();
  const ok =
    o.schema_version === 1 &&
    typeof o.idempotency_key === 'string' &&
    o.idempotency_key.length <= 128 &&
    /^booking:[0-9a-f-]{36}:[a-z_]+:\d+$/.test(o.idempotency_key) &&
    typeof o.seq === 'number' &&
    Number.isSafeInteger(o.seq) &&
    o.seq > 0 &&
    isUuid(o.platform_booking_ref) &&
    (o.company_code === 'PB' || o.company_code === 'BCF') &&
    typeof o.outlet_code === 'string' &&
    OUTLET_CODE_RE.test(o.outlet_code) &&
    (BOOKING_EVENTS as readonly unknown[]).includes(o.event) &&
    (ODOO_STATES as readonly unknown[]).includes(o.state) &&
    typeof o.occurred_at === 'string' &&
    !Number.isNaN(Date.parse(o.occurred_at));
  if (!ok) throw Errors.invalidRequest();
  return o as unknown as BookingEventInput;
}

export interface BookingRow {
  id: string;
  member_id: string | null;
  idempotency_key: string;
  request_sha256: string;
  company_code: string;
  channel_code: string;
  outlet_code: string;
  outlet_name: string | null;
  start_at: Date;
  end_at: Date;
  party_size: number;
  status: BookingStatus;
  odoo_booking_ref: string | null;
  table_label: string | null;
  last_seq: number;
  created_at: Date;
  updated_at: Date;
}

export interface BookingView {
  booking_id: string;
  status: BookingStatus;
  outlet_code: string;
  outlet_name: string | null;
  start_at: string;
  end_at: string;
  party_size: number;
  table_label: string | null;
  cancellable: boolean;
}

export function toView(row: BookingRow, now: number): BookingView {
  return {
    booking_id: row.id,
    status: row.status,
    outlet_code: row.outlet_code,
    outlet_name: row.outlet_name,
    start_at: row.start_at.toISOString().replace('.000Z', 'Z'),
    end_at: row.end_at.toISOString().replace('.000Z', 'Z'),
    party_size: row.party_size,
    table_label: row.table_label,
    cancellable: (row.status === 'requested' || row.status === 'confirmed') && row.start_at.getTime() > now,
  };
}
