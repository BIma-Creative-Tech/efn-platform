/**
 * GET /api/fnb/orders/:id -> BFF GET /v1/fnb/orders/:id (docs/04 §14.A.3). Id bukan UUID -> 404 tanpa memanggil BFF;
 * kiriman milik sesi lain -> BFF 404 (BOLA, §14.A.10).
 */
import type { NextRequest } from 'next/server';
import { bffFetch } from '@/lib/bff';
import { isUuid } from '@/lib/catalog';
import { parseSubmissionState } from '@/lib/fnb';
import { fnbError, fnbGuard, mapFnbFailure, tableHeaders } from '@/lib/fnb-route';
import { noStoreJson } from '@/lib/orders-route';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function GET(req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  const g = fnbGuard(req, false);
  if (!g.ok) return g.response;
  if (!isUuid(id)) return fnbError(404, 'not_found');
  const r = await bffFetch<unknown>(`/v1/fnb/orders/${id.toLowerCase()}`, {
    token: g.bearer ?? undefined,
    headers: tableHeaders(g.table),
    incoming: req.headers,
  });
  if (!r.ok) return mapFnbFailure(r, g.bearer !== null);
  const state = parseSubmissionState(r.data);
  if (!state) return fnbError(502, 'upstream_error');
  return noStoreJson(state);
}
