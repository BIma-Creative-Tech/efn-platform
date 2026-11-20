/**
 * Consent AKUN purpose `tracking` (G50; docs/04 §28.6, ADR-047 §3.1 no. 1).
 *   GET /api/me/tracking-consent            -> BFF GET /v1/me/consents (diambil purpose `tracking`)
 *   PUT /api/me/tracking-consent { granted, version } -> BFF PUT /v1/me/consents/tracking { granted, version }
 * Dipisah dari /api/me/consents agar kontrak purpose `location|marketing` yang sudah teruji tidak berubah.
 * Aksi EKSPLISIT member (tombol); tidak pernah dipanggil otomatis. Pemberian wajib atas versi teks terbaru.
 */
import type { NextRequest } from 'next/server';
import { bffFetch } from '@/lib/bff';
import { mapBffFailure, unauthenticated } from '@/lib/me-route';
import { isNotCrossSite, isSameOrigin, jsonError } from '@/lib/request-guard';
import { getSessionTokenFromRequest } from '@/lib/session';
import { pickTracking, TRACKING_TEXT } from '@/lib/tracking';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

const NO_STORE = { 'cache-control': 'no-store' } as const;

export async function GET(req: NextRequest) {
  if (!isNotCrossSite(req)) return jsonError(403, 'forbidden', 'Permintaan ditolak.');
  const token = getSessionTokenFromRequest(req);
  if (!token) return unauthenticated();
  const result = await bffFetch<unknown>('/v1/me/consents', { token, incoming: req.headers });
  if (!result.ok) return mapBffFailure(result, 'Pengaturan pelacakan belum dapat dimuat.');
  return Response.json(pickTracking(result.data), { headers: NO_STORE });
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
  const b = body && typeof body === 'object' ? (body as Record<string, unknown>) : null;
  if (!b || typeof b.granted !== 'boolean' || typeof b.version !== 'string') {
    return jsonError(400, 'invalid_request', 'Permintaan tidak valid.');
  }
  if (b.granted && b.version !== TRACKING_TEXT.version) {
    return jsonError(409, 'stale_text', 'Teks persetujuan telah diperbarui. Muat ulang halaman lalu tinjau kembali.');
  }
  const version = TRACKING_TEXT.version; // penarikan dicatat dengan versi terbaru (pola consent.ts)
  const result = await bffFetch<unknown>('/v1/me/consents/tracking', {
    method: 'PUT',
    token,
    json: { granted: b.granted, version },
    incoming: req.headers,
  });
  if (!result.ok && result.status === 503 && result.code === 'consent_unavailable') {
    return jsonError(503, 'consent_unavailable', 'Pengaturan persetujuan belum tersedia. Coba lagi nanti.');
  }
  if (!result.ok) return mapBffFailure(result, 'Perubahan belum tersimpan. Silakan coba lagi.');
  return Response.json({ granted: b.granted, version, updated_at: new Date().toISOString() }, { headers: NO_STORE });
}
