/** GET /api/catalog/products/:id?channel=RTL -> BFF GET /v1/catalog/products/:id (docs/04 §10.3.2). Publik. */
import type { NextRequest } from 'next/server';
import { isChannelCode, parsePositiveInt, parseProductDetail } from '@/lib/catalog';
import { proxyCatalog } from '@/lib/catalog-route';
import { onlineError } from '@/lib/orders-route';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function GET(req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  const productId = parsePositiveInt(id);
  if (productId === null) return onlineError(404, 'not_found');
  const channel = req.nextUrl.searchParams.get('channel');
  if (!isChannelCode(channel)) return onlineError(400, 'invalid_request');
  return proxyCatalog(`/v1/catalog/products/${productId}?${new URLSearchParams({ channel })}`, req.headers, parseProductDetail);
}
