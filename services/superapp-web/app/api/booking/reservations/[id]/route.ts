/** GET /api/booking/reservations/:id -> BFF GET /v1/booking/reservations/:id (milik member; lain -> 404). */
import type { NextRequest } from 'next/server';
import { bffFetch } from '@/lib/bff';
import { parseReservation } from '@/lib/booking';
import { bookingError, mapBookingFailure } from '@/lib/booking-route';
import { isUuid } from '@/lib/catalog';
import { noStoreJson, requireMember } from '@/lib/orders-route';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function GET(req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  const g = requireMember(req, false);
  if (!g.ok) return g.response;
  if (!isUuid(id)) return bookingError(404, 'not_found');
  const r = await bffFetch<unknown>(`/v1/booking/reservations/${id.toLowerCase()}`, { token: g.token, incoming: req.headers });
  if (!r.ok) return mapBookingFailure(r);
  const data = parseReservation(r.data);
  if (!data) return bookingError(502, 'upstream_error');
  return noStoreJson(data);
}
