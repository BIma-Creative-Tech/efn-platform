/**
 * GET /api/promo -> BFF GET /v1/promo?surface=web (docs/04 §18.2). Publik, allowlist lib/promo.ts.
 * BFF mati / promo nonaktif -> feed kosong (200) agar beranda tetap menampilkan hero lama.
 * Hanya promo milik PT tenant ini (`x-efn-brand`, keputusan 23 Nov 2026, lib/promo.ts promoForCompanies).
 */
import { bffFetch } from '@/lib/bff';
import { getBrandById } from '@/lib/brands';
import { EMPTY_PROMO, parsePromoFeed, promoForCompanies } from '@/lib/promo';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function GET(req: Request) {
  const brand = getBrandById(req.headers.get('x-efn-brand'));
  const r = await bffFetch<unknown>('/v1/promo?surface=web', { incoming: req.headers });
  const feed = r.ok ? parsePromoFeed(r.data) : null;
  return Response.json(feed ? promoForCompanies(feed, brand.companies) : EMPTY_PROMO, {
    headers: { 'cache-control': feed ? 'public, max-age=60' : 'no-store' },
  });
}
