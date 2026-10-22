/**
 * Helper Route Handler reservasi meja (`/api/booking/**` -> BFF `/v1/booking/**`, docs/04 §15.5/§15.7). SERVER-SIDE SAJA.
 * Pola sama dengan lib/orders-route.ts: Bearer dari cookie HttpOnly, same-origin untuk tulis, `no-store` untuk data member,
 * pesan error dari peta Indonesia (lib/booking.ts) — pesan/body BFF TIDAK diteruskan. Nama panggilan & catatan tamu
 * tidak pernah di-log.
 */
import 'server-only';
import type { BffResult } from './bff';
import { BOOKING_ERROR_MESSAGES, bookingErrorMessage } from './booking';
import { unauthenticated } from './me-route';
import { noStoreJson } from './orders-route';

type BffFailure = Extract<BffResult<unknown>, { ok: false }>;

const TRANSPORT_CODES = new Set(['bff_unavailable', 'bff_timeout', 'bff_misconfigured']);
const PASSTHROUGH = [400, 404, 409, 413, 422, 429];

export function bookingError(status: number, code: string): Response {
  return noStoreJson({ error: { code, message: bookingErrorMessage(code) } }, status);
}

/**
 * Peta kegagalan BFF -> respons browser.
 * - 401 (rute member) -> sesi dihapus. Rute publik -> 503 generik (tidak ada sesi yang perlu dihapus).
 * - 4xx kontrak -> status sama; kode tak dikenal -> generik per status.
 * - 503 `booking_unavailable` (kill switch) -> 503 kode sama (halaman menampilkan "belum tersedia").
 * - BFF tak terjangkau -> 503 `service_unavailable`; 5xx lain -> 502 `upstream_error`.
 */
export function mapBookingFailure(r: BffFailure, opts: { publicRoute?: boolean } = {}): Response {
  if (r.status === 401 && !opts.publicRoute) return unauthenticated();
  if (PASSTHROUGH.includes(r.status)) {
    const known = Object.prototype.hasOwnProperty.call(BOOKING_ERROR_MESSAGES, r.code);
    const code = known ? r.code : r.status === 404 ? 'not_found' : r.status === 429 ? 'rate_limited' : r.status === 413 ? 'payload_too_large' : 'invalid_request';
    return bookingError(r.status, code);
  }
  if (r.status === 503 && r.code === 'booking_unavailable') return bookingError(503, 'booking_unavailable');
  if (TRANSPORT_CODES.has(r.code) || r.status === 503) return bookingError(503, 'service_unavailable');
  return bookingError(502, 'upstream_error');
}
