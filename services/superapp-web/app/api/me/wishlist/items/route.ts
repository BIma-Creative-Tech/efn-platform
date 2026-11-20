/** PUT /api/me/wishlist/items { channel_code, product_id } -> BFF PUT /v1/me/wishlist/items -> 204 (idempoten, maks. 200). */
import type { NextRequest } from 'next/server';
import { bffFetch } from '@/lib/bff';
import { validateWishlistRef } from '@/lib/engage';
import { engageError, engageFailure, noContent } from '@/lib/engage-route';
import { readJsonBody, requireMember } from '@/lib/orders-route';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function PUT(req: NextRequest) {
  const g = requireMember(req, true);
  if (!g.ok) return g.response;
  const body = await readJsonBody(req, 1024);
  if (!body.ok) return body.response;
  const v = validateWishlistRef(body.value);
  if (!v.ok) return engageError(400, v.code);
  const r = await bffFetch<unknown>('/v1/me/wishlist/items', { method: 'PUT', token: g.token, json: v.value, incoming: req.headers });
  if (!r.ok) return engageFailure(r);
  return noContent();
}
