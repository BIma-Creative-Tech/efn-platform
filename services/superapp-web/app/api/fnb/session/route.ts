/**
 * GET /api/fnb/session -> BFF GET /v1/fnb/session (docs/04 §14.A.3). Header `X-Table-Session` wajib (dari memori klien);
 * Bearer dari cookie HttpOnly opsional (mengisi `points.claimed_by_me`). Respons di-allowlist, `no-store`.
 */
import type { NextRequest } from 'next/server';
import { bffFetch } from '@/lib/bff';
import { parseFnbSession } from '@/lib/fnb';
import { fnbError, fnbGuard, mapFnbFailure, tableHeaders } from '@/lib/fnb-route';
import { noStoreJson } from '@/lib/orders-route';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function GET(req: NextRequest) {
  const g = fnbGuard(req, false);
  if (!g.ok) return g.response;
  const r = await bffFetch<unknown>('/v1/fnb/session', { token: g.bearer ?? undefined, headers: tableHeaders(g.table), incoming: req.headers });
  if (!r.ok) return mapFnbFailure(r, g.bearer !== null);
  const session = parseFnbSession(r.data);
  if (!session) return fnbError(502, 'upstream_error');
  return noStoreJson(session);
}
