/**
 * POST /api/me/delete/cancel -> BFF POST /v1/me/delete/cancel (docs/04 §8.4) — batalkan hapus akun
 * dalam masa tenggang. Bearer dari cookie HttpOnly; cek Origin (CSRF lapis kedua).
 */
import type { NextRequest } from 'next/server';
import { bffFetch } from '@/lib/bff';
import { mapBffFailure, unauthenticated } from '@/lib/me-route';
import { isSameOrigin, jsonError } from '@/lib/request-guard';
import { getSessionTokenFromRequest } from '@/lib/session';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function POST(req: NextRequest) {
  if (!isSameOrigin(req)) return jsonError(403, 'forbidden', 'Permintaan ditolak.');
  const token = getSessionTokenFromRequest(req);
  if (!token) return unauthenticated();

  const r = await bffFetch<unknown>('/v1/me/delete/cancel', {
    method: 'POST',
    token,
    json: {},
    incoming: req.headers,
  });
  if (!r.ok) return mapBffFailure(r, 'Pembatalan belum dapat diproses. Silakan coba lagi.', [404, 409, 429]);
  return Response.json({ ok: true }, { status: 200, headers: { 'cache-control': 'no-store' } });
}
