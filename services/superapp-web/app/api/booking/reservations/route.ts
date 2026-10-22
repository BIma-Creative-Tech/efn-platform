/**
 * Reservasi member (docs/04 §15.5).
 * - GET  /api/booking/reservations -> BFF GET /v1/booking/reservations (≤ 20, milik member).
 * - POST /api/booking/reservations `{ outlet_code, start_at, party_size, guest_label?, note? }` + header
 *   `Idempotency-Key` (16–64, dibuat browser per isi permintaan agar retry tidak membuat reservasi ganda) -> BFF.
 *   Body di-allowlist; nama panggilan/catatan diteruskan SEKALI ke BFF dan tidak di-log/disimpan di sini.
 * Semua respons `no-store`.
 */
import type { NextRequest } from 'next/server';
import { bffFetch } from '@/lib/bff';
import { BOOKING_BODY_MAX_BYTES, BOOKING_IDEMPOTENCY_KEY_RE, parseReservation, parseReservationList, sanitizeCreateBooking } from '@/lib/booking';
import { bookingError, mapBookingFailure } from '@/lib/booking-route';
import { noStoreJson, readJsonBody, requireMember } from '@/lib/orders-route';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function GET(req: NextRequest) {
  const g = requireMember(req, false);
  if (!g.ok) return g.response;
  const r = await bffFetch<unknown>('/v1/booking/reservations', { token: g.token, incoming: req.headers });
  if (!r.ok) return mapBookingFailure(r);
  const items = parseReservationList(r.data);
  if (!items) return bookingError(502, 'upstream_error');
  return noStoreJson({ items });
}

export async function POST(req: NextRequest) {
  const g = requireMember(req, true);
  if (!g.ok) return g.response;
  const key = (req.headers.get('idempotency-key') ?? '').trim();
  if (!BOOKING_IDEMPOTENCY_KEY_RE.test(key)) return bookingError(400, 'idempotency_key_required');
  const body = await readJsonBody(req, BOOKING_BODY_MAX_BYTES);
  if (!body.ok) return body.response;
  const clean = sanitizeCreateBooking(body.value);
  if (!clean) return bookingError(400, 'invalid_request');
  const r = await bffFetch<unknown>('/v1/booking/reservations', {
    method: 'POST',
    token: g.token,
    json: clean,
    headers: { 'idempotency-key': key },
    incoming: req.headers,
    timeoutMs: 20_000,
  });
  if (!r.ok) return mapBookingFailure(r);
  const created = parseReservation(r.data);
  if (!created) return bookingError(502, 'upstream_error');
  return noStoreJson(created, 201);
}
