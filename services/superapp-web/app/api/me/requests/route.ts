/**
 * GET /api/me/requests -> BFF GET /v1/me/requests (docs/04 §8.4) — riwayat permintaan DSAR milik pemanggil.
 * Respons dinormalisasi (lib/dsar.ts); tautan unduh hanya diteruskan bila https.
 */
import type { NextRequest } from 'next/server';
import { bffFetch } from '@/lib/bff';
import { parseDsarRequests } from '@/lib/dsar';
import { mapBffFailure, unauthenticated } from '@/lib/me-route';
import { isNotCrossSite, jsonError } from '@/lib/request-guard';
import { getSessionTokenFromRequest } from '@/lib/session';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function GET(req: NextRequest) {
  if (!isNotCrossSite(req)) return jsonError(403, 'forbidden', 'Permintaan ditolak.');
  const token = getSessionTokenFromRequest(req);
  if (!token) return unauthenticated();

  const r = await bffFetch<unknown>('/v1/me/requests', { token, incoming: req.headers });
  if (!r.ok) return mapBffFailure(r, 'Riwayat permintaan belum dapat dimuat.');
  return Response.json(parseDsarRequests(r.data), { headers: { 'cache-control': 'no-store' } });
}
