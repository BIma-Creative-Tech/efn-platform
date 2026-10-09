/**
 * Keranjang member (docs/04 §10.3.3): GET/PUT/DELETE /api/cart -> BFF /v1/cart.
 * - Bearer dari cookie HttpOnly; PUT/DELETE wajib Origin sama (CSRF); GET menolak lintas situs.
 * - Body PUT di-allowlist ke `{ version, groups[{ channel_code, outlet_code, fulfilment, lines[{ product_id, qty }] }] }`:
 *   harga/total apa pun dari klien DIBUANG (tidak pernah dipercaya). Body ≤ 16 KB.
 * - Kill switch `ONLINE_ENABLED=false` -> BFF 503 `online_unavailable` -> diteruskan dengan pesan ramah.
 */
import type { NextRequest } from 'next/server';
import { bffFetch } from '@/lib/bff';
import { EMPTY_CART, parseCartPutResult, sanitizeCart } from '@/lib/cart';
import { mapOnlineFailure, noStoreJson, onlineError, readJsonBody, requireMember } from '@/lib/orders-route';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function GET(req: NextRequest) {
  const g = requireMember(req, false);
  if (!g.ok) return g.response;
  const r = await bffFetch<unknown>('/v1/cart', { token: g.token, incoming: req.headers });
  if (!r.ok) return mapOnlineFailure(r);
  const cart = r.data === null ? EMPTY_CART : sanitizeCart(r.data);
  if (!cart) return onlineError(502, 'upstream_error');
  return noStoreJson(cart);
}

export async function PUT(req: NextRequest) {
  const g = requireMember(req, true);
  if (!g.ok) return g.response;
  const body = await readJsonBody(req);
  if (!body.ok) return body.response;
  const cart = sanitizeCart(body.value);
  if (!cart) return onlineError(400, 'invalid_request');
  const r = await bffFetch<unknown>('/v1/cart', { method: 'PUT', token: g.token, json: cart, incoming: req.headers });
  if (!r.ok) return mapOnlineFailure(r);
  const result = parseCartPutResult(r.data);
  if (!result) return onlineError(502, 'upstream_error');
  return noStoreJson(result);
}

export async function DELETE(req: NextRequest) {
  const g = requireMember(req, true);
  if (!g.ok) return g.response;
  const r = await bffFetch<unknown>('/v1/cart', { method: 'DELETE', token: g.token, incoming: req.headers });
  if (!r.ok) return mapOnlineFailure(r);
  return new Response(null, { status: 204, headers: { 'cache-control': 'no-store' } });
}
