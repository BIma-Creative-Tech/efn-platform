/** DELETE /api/me/wishlist/items/:channel/:product -> BFF DELETE /v1/me/wishlist/items/:channel_code/:product_id -> 204 (404 = sudah tidak ada -> 204). */
import type { NextRequest } from 'next/server';
import { bffFetch } from '@/lib/bff';
import { isChannelCode, parsePositiveInt } from '@/lib/catalog';
import { engageError, engageFailure, noContent } from '@/lib/engage-route';
import { requireMember } from '@/lib/orders-route';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function DELETE(req: NextRequest, ctx: { params: Promise<{ channel: string; product: string }> }) {
  const g = requireMember(req, true);
  if (!g.ok) return g.response;
  const { channel, product } = await ctx.params;
  const pid = parsePositiveInt(product);
  if (!isChannelCode(channel) || pid === null) return engageError(400, 'invalid_request');
  const r = await bffFetch<unknown>(`/v1/me/wishlist/items/${encodeURIComponent(channel)}/${pid}`, {
    method: 'DELETE',
    token: g.token,
    incoming: req.headers,
  });
  if (!r.ok && r.status !== 404) return engageFailure(r);
  return noContent();
}
