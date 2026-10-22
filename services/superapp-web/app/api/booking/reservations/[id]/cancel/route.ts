/**
 * POST /api/booking/reservations/:id/cancel -> BFF (docs/04 §15.5). Idempoten di BFF; sudah mulai/selesai ->
 * 409 `booking_not_cancellable`.
 */
import type { NextRequest } from 'next/server';
import { bffFetch } from '@/lib/bff';
import { bookingError, mapBookingFailure } from '@/lib/booking-route';
import { isUuid } from '@/lib/catalog';
import { noStoreJson, requireMember } from '@/lib/orders-route';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function POST(req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  const g = requireMember(req, true);
  if (!g.ok) return g.response;
  if (!isUuid(id)) return bookingError(404, 'not_found');
  const r = await bffFetch<unknown>(`/v1/booking/reservations/${id.toLowerCase()}/cancel`, {
    method: 'POST',
    token: g.token,
    json: {},
    incoming: req.headers,
  });
  if (!r.ok) return mapBookingFailure(r);
  return noStoreJson({ status: 'cancelled' });
}
