/**
 * POST /api/orders/:id/cancel -> BFF POST /v1/orders/:id/cancel (docs/04 §10.3.6). Hanya sebelum bayar
 * (`created|awaiting_payment`); sudah dibayar -> 409 `order_not_cancellable` (pesan: hubungi CS). Idempoten di BFF.
 */
import type { NextRequest } from 'next/server';
import { bffFetch } from '@/lib/bff';
import { isUuid } from '@/lib/catalog';
import { mapOnlineFailure, noStoreJson, onlineError, requireMember } from '@/lib/orders-route';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function POST(req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  const g = requireMember(req, true);
  if (!g.ok) return g.response;
  if (!isUuid(id)) return onlineError(404, 'not_found');
  const r = await bffFetch<unknown>(`/v1/orders/${id.toLowerCase()}/cancel`, {
    method: 'POST',
    token: g.token,
    json: {},
    incoming: req.headers,
  });
  if (!r.ok) return mapOnlineFailure(r);
  return noStoreJson({ status: 'cancelled' });
}
