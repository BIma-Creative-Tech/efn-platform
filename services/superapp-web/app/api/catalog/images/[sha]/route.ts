/**
 * GET /api/catalog/images/:sha?size=512|1024 -> BFF GET /v1/catalog/images/:sha (docs/04 §10.3.2). Publik.
 * Gambar disajikan dari origin sendiri (CSP `img-src 'self'`); tidak pernah URL Odoo/object storage.
 */
import type { NextRequest } from 'next/server';
import { isImageSha, parseImageSize } from '@/lib/catalog';
import { proxyCatalogImage } from '@/lib/catalog-route';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function GET(req: NextRequest, ctx: { params: Promise<{ sha: string }> }) {
  const { sha } = await ctx.params;
  if (!isImageSha(sha)) return new Response(null, { status: 404, headers: { 'cache-control': 'no-store' } });
  return proxyCatalogImage(sha, parseImageSize(req.nextUrl.searchParams.get('size')), req.headers);
}
