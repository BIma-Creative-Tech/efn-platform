/**
 * GET /api/promo -> BFF GET /v1/promo?surface=web (docs/04 §18.2). Publik, allowlist lib/promo.ts.
 * BFF mati / promo nonaktif -> feed kosong (200) agar beranda tetap menampilkan hero lama.
 */
import { bffFetch } from '@/lib/bff';
import { EMPTY_PROMO, parsePromoFeed } from '@/lib/promo';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function GET(req: Request) {
  const r = await bffFetch<unknown>('/v1/promo?surface=web', { incoming: req.headers });
  const feed = r.ok ? parsePromoFeed(r.data) : null;
  return Response.json(feed ?? EMPTY_PROMO, { headers: { 'cache-control': feed ? 'public, max-age=60' : 'no-store' } });
}
