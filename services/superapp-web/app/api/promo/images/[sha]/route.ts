/**
 * GET /api/promo/images/:sha?w=1600|1024 -> BFF GET /v1/promo/images/:sha (docs/04 §18.2). Publik, origin sendiri.
 */
import type { NextRequest } from 'next/server';
import { proxyBffImage } from '@/lib/catalog-route';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function GET(req: NextRequest, ctx: { params: Promise<{ sha: string }> }) {
  const { sha } = await ctx.params;
  const w = req.nextUrl.searchParams.get('w');
  if (!/^[0-9a-f]{64}$/.test(sha) || (w !== '1600' && w !== '1024')) return new Response(null, { status: 404, headers: { 'cache-control': 'no-store' } });
  return proxyBffImage(`/v1/promo/images/${sha}?w=${w}`, req.headers);
}
