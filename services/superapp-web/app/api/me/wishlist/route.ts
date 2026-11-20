/**
 * GET /api/me/wishlist[?channel=GL] -> BFF GET /v1/me/wishlist[?channel=] (docs/04 §28.8). Bearer member.
 * Respons dinormalisasi: `{ items: [{ channel_code, product_id, added_at }] }`.
 */
import type { NextRequest } from 'next/server';
import { bffFetch } from '@/lib/bff';
import { isChannelCode } from '@/lib/catalog';
import { parseWishlist } from '@/lib/engage';
import { engageError, engageFailure } from '@/lib/engage-route';
import { noStoreJson, requireMember } from '@/lib/orders-route';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function GET(req: NextRequest) {
  const g = requireMember(req, false);
  if (!g.ok) return g.response;
  const channel = req.nextUrl.searchParams.get('channel');
  if (channel !== null && !isChannelCode(channel)) return engageError(400, 'invalid_request');
  const path = channel ? `/v1/me/wishlist?${new URLSearchParams({ channel })}` : '/v1/me/wishlist';
  const r = await bffFetch<unknown>(path, { token: g.token, incoming: req.headers });
  if (!r.ok) return engageFailure(r);
  return noStoreJson({ items: parseWishlist(r.data) });
}
