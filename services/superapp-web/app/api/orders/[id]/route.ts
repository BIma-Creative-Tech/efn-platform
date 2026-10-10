/**
 * GET /api/orders/:id -> BFF GET /v1/orders/:id (docs/04 §10.3.6). Polling klien 3 dtk / 30 dtk.
 * Id bukan UUID -> 404 tanpa memanggil BFF; order milik orang lain -> BFF 404 (anti-enumeration).
 * `qr_string` diteruskan hanya saat `awaiting_payment` (dirender lokal jadi QR), tidak pernah di-log.
 */
import type { NextRequest } from 'next/server';
import { bffFetch } from '@/lib/bff';
import { isUuid } from '@/lib/catalog';
import { parseOrderDetail } from '@/lib/orders';
import { mapOnlineFailure, noStoreJson, onlineError, requireMember } from '@/lib/orders-route';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function GET(req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  const g = requireMember(req, false);
  if (!g.ok) return g.response;
  if (!isUuid(id)) return onlineError(404, 'not_found');
  const r = await bffFetch<unknown>(`/v1/orders/${id.toLowerCase()}`, { token: g.token, incoming: req.headers });
  if (!r.ok) return mapOnlineFailure(r);
  const order = parseOrderDetail(r.data);
  if (!order) return onlineError(502, 'upstream_error');
  return noStoreJson(order);
}
