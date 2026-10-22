/**
 * GET /api/booking/outlets -> BFF GET /v1/booking/outlets?brand=<brand_code> (docs/04 §15.5). Publik.
 * Domain brand memfilter gerai PT-nya (pembatasan UI saja; BFF tetap memutuskan). Cache publik 60 dtk.
 */
import type { NextRequest } from 'next/server';
import { bffFetch } from '@/lib/bff';
import { outletsForBrand, parseBookingOutlets } from '@/lib/booking';
import { mapBookingFailure, bookingError } from '@/lib/booking-route';
import { getBrandById } from '@/lib/brands';
import { isNotCrossSite } from '@/lib/request-guard';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function GET(req: NextRequest) {
  if (!isNotCrossSite(req)) return bookingError(403, 'forbidden');
  const brand = getBrandById(req.headers.get('x-efn-brand'));
  const q = brand.brandCode ? `?${new URLSearchParams({ brand: brand.brandCode })}` : '';
  const r = await bffFetch<unknown>(`/v1/booking/outlets${q}`, { incoming: req.headers });
  if (!r.ok) return mapBookingFailure(r, { publicRoute: true });
  const list = parseBookingOutlets(r.data);
  if (!list) return bookingError(502, 'upstream_error');
  return Response.json({ items: outletsForBrand(list, brand.companies) }, { headers: { 'cache-control': 'public, max-age=60' } });
}
