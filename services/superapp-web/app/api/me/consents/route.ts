/**
 * GET /api/me/consents                         -> BFF GET /v1/me/consents (docs/04 §8.4)
 * PUT /api/me/consents { purpose, granted, version } -> BFF PUT /v1/me/consents/:purpose { granted, version }
 *
 * Bearer dari cookie HttpOnly. PUT: cek Origin (CSRF lapis kedua selain SameSite=Lax) dan versi teks
 * persetujuan harus versi terbaru (`409 stale_text`); penarikan selalu diterima (lib/consent.ts).
 */
import type { NextRequest } from 'next/server';
import { bffFetch } from '@/lib/bff';
import { parseConsents, validateConsentUpdate } from '@/lib/consent';
import { mapBffFailure, unauthenticated } from '@/lib/me-route';
import { isNotCrossSite, isSameOrigin, jsonError } from '@/lib/request-guard';
import { getSessionTokenFromRequest } from '@/lib/session';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function GET(req: NextRequest) {
  if (!isNotCrossSite(req)) return jsonError(403, 'forbidden', 'Permintaan ditolak.');
  const token = getSessionTokenFromRequest(req);
  if (!token) return unauthenticated();

  const result = await bffFetch<unknown>('/v1/me/consents', { token, incoming: req.headers });
  if (!result.ok) return mapBffFailure(result, 'Pengaturan privasi belum dapat dimuat.');
  return Response.json(Object.values(parseConsents(result.data)), { headers: { 'cache-control': 'no-store' } });
}

export async function PUT(req: NextRequest) {
  if (!isSameOrigin(req)) return jsonError(403, 'forbidden', 'Permintaan ditolak.');
  const token = getSessionTokenFromRequest(req);
  if (!token) return unauthenticated();

  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return jsonError(400, 'invalid_request', 'Permintaan tidak valid.');
  }
  const v = validateConsentUpdate(body);
  if (!v.ok) return jsonError(v.status, v.code, v.message);

  const result = await bffFetch<unknown>(`/v1/me/consents/${encodeURIComponent(v.purpose)}`, {
    method: 'PUT',
    token,
    json: { granted: v.granted, version: v.version },
    incoming: req.headers,
  });
  // docs/04 §8.11: teks consent aktif belum dikonfigurasi di BFF -> 503 consent_unavailable (G6 QA: kode diteruskan)
  if (!result.ok && result.status === 503 && result.code === 'consent_unavailable') {
    return jsonError(503, 'consent_unavailable', 'Pengaturan persetujuan belum tersedia. Coba lagi nanti.');
  }
  if (!result.ok) return mapBffFailure(result, 'Perubahan belum tersimpan. Silakan coba lagi.');
  return Response.json(
    { purpose: v.purpose, granted: v.granted, version: v.version },
    { status: 200, headers: { 'cache-control': 'no-store' } },
  );
}
