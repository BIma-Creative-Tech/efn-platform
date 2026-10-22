/**
 * GET /api/booking/availability?outlet=&date=&party= -> BFF GET /v1/booking/availability (docs/04 §15.5). Publik,
 * cache publik 30 dtk (BFF juga cache 30 dtk + rate limit per IP).
 */
import type { NextRequest } from 'next/server';
import { bffFetch } from '@/lib/bff';
import { isIsoDate, parseAvailability, parsePartySize } from '@/lib/booking';
import { bookingError, mapBookingFailure } from '@/lib/booking-route';
import { isOutletCode } from '@/lib/catalog';
import { isNotCrossSite } from '@/lib/request-guard';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function GET(req: NextRequest) {
  if (!isNotCrossSite(req)) return bookingError(403, 'forbidden');
  const sp = req.nextUrl.searchParams;
  const outlet = sp.get('outlet');
  const date = sp.get('date');
  const party = parsePartySize(sp.get('party'));
  if (!isOutletCode(outlet) || !isIsoDate(date) || party === null) return bookingError(400, 'invalid_request');
  const q = new URLSearchParams({ outlet, date, party: String(party) });
  const r = await bffFetch<unknown>(`/v1/booking/availability?${q}`, { incoming: req.headers });
  if (!r.ok) return mapBookingFailure(r, { publicRoute: true });
  const data = parseAvailability(r.data);
  if (!data) return bookingError(502, 'upstream_error');
  return Response.json(data, { headers: { 'cache-control': 'public, max-age=30' } });
}
