/**
 * Klaim poin untuk sesi meja (docs/04 §14.A.3, §14.A.4; earn terjadi di BFF saat kasir menutup tagihan).
 * - POST   /api/fnb/points-claim -> BFF POST /v1/fnb/session/points-claim `{}`; 200 `{ claimed: true }` (idempoten).
 * - DELETE /api/fnb/points-claim -> BFF DELETE /v1/fnb/session/points-claim; 204.
 * Login (cookie HttpOnly) WAJIB; tanpa cookie -> 401 `unauthenticated` tanpa memanggil BFF. Tamu tidak bisa klaim.
 * Poin hanya terikat ke member pada sesi ini — tidak ada nominal/saldo di respons.
 */
import type { NextRequest } from 'next/server';
import { bffFetch } from '@/lib/bff';
import { fnbGuard, mapFnbFailure, tableHeaders } from '@/lib/fnb-route';
import { noStoreJson } from '@/lib/orders-route';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function POST(req: NextRequest) {
  const g = fnbGuard(req, true, { requireBearer: true });
  if (!g.ok) return g.response;
  const r = await bffFetch<unknown>('/v1/fnb/session/points-claim', {
    method: 'POST',
    token: g.bearer ?? undefined,
    json: {},
    headers: tableHeaders(g.table),
    incoming: req.headers,
  });
  if (!r.ok) return mapFnbFailure(r, true);
  return noStoreJson({ claimed: true });
}

export async function DELETE(req: NextRequest) {
  const g = fnbGuard(req, true, { requireBearer: true });
  if (!g.ok) return g.response;
  const r = await bffFetch<unknown>('/v1/fnb/session/points-claim', {
    method: 'DELETE',
    token: g.bearer ?? undefined,
    headers: tableHeaders(g.table),
    incoming: req.headers,
  });
  if (!r.ok) return mapFnbFailure(r, true);
  return new Response(null, { status: 204, headers: { 'cache-control': 'no-store' } });
}
