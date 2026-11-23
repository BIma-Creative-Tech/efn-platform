/**
 * GET /api/config -> BFF GET /v1/config (docs/04 §28.6: `features.tracking`, V-01 `features.visit_log`; §28.10 flag
 * wishlist/alert). Publik.
 * Dinormalisasi ke `{ features }` (lib/tracking.ts `parseFeatures`). BFF mati / endpoint tidak ada -> SEMUA fitur mati
 * (200, bukan error): banner & tombol G50 tidak tampil, belanja tetap jalan.
 */
import type { NextRequest } from 'next/server';
import { bffFetch } from '@/lib/bff';
import { NO_FEATURES, parseFeatures } from '@/lib/tracking';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function GET(req: NextRequest) {
  const r = await bffFetch<unknown>('/v1/config', { incoming: req.headers, timeoutMs: 3000 });
  const features = r.ok ? parseFeatures(r.data) : NO_FEATURES;
  return Response.json({ features }, { headers: { 'cache-control': 'no-store' } });
}
