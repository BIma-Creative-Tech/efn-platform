/** DELETE /api/me/restock-alerts/:id -> BFF DELETE /v1/me/restock-alerts/:id -> 204 (404 = sudah tidak ada -> 204). */
import type { NextRequest } from 'next/server';
import { bffFetch } from '@/lib/bff';
import { engageError, engageFailure, noContent } from '@/lib/engage-route';
import { requireMember } from '@/lib/orders-route';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function DELETE(req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const g = requireMember(req, true);
  if (!g.ok) return g.response;
  const { id } = await ctx.params;
  if (!/^[A-Za-z0-9_-]{1,64}$/.test(id)) return engageError(400, 'invalid_request');
  const r = await bffFetch<unknown>(`/v1/me/restock-alerts/${encodeURIComponent(id)}`, {
    method: 'DELETE',
    token: g.token,
    incoming: req.headers,
  });
  if (!r.ok && r.status !== 404) return engageFailure(r);
  return noContent();
}
