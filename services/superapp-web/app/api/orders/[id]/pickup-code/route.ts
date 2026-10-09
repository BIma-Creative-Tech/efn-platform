/**
 * GET /api/orders/:id/pickup-code -> BFF GET /v1/orders/:id/pickup-code (docs/04 §10.3.6).
 *
 * KODE AMBIL = RAHASIA: respons `no-store` (+ `Pragma: no-cache`), tidak di-log, tidak di URL/query, tidak ke analytics.
 * Hanya dipanggil saat pengguna menekan "Tampilkan kode ambil". Lintas situs ditolak (Fetch Metadata).
 */
import type { NextRequest } from 'next/server';
import { bffFetch } from '@/lib/bff';
import { isUuid } from '@/lib/catalog';
import { parsePickupCodes } from '@/lib/orders';
import { mapOnlineFailure, onlineError, requireMember } from '@/lib/orders-route';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function GET(req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  const g = requireMember(req, false);
  if (!g.ok) return g.response;
  if (!isUuid(id)) return onlineError(404, 'not_found');
  const r = await bffFetch<unknown>(`/v1/orders/${id.toLowerCase()}/pickup-code`, { token: g.token, incoming: req.headers });
  if (!r.ok) return mapOnlineFailure(r);
  const items = parsePickupCodes(r.data);
  if (!items) return onlineError(502, 'upstream_error');
  return Response.json(
    { items },
    { headers: { 'cache-control': 'no-store, max-age=0', pragma: 'no-cache', 'referrer-policy': 'no-referrer' } },
  );
}
