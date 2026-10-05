/**
 * GET /api/auth/challenge  ->  BFF GET /v1/auth/challenge (docs/04 §8.1, ALTCHA self-hosted).
 * Dipanggil widget ALTCHA (atribut `challengeurl`) dari origin sendiri -> CSP `connect-src 'self'` cukup.
 * Hanya field challenge yang dikenal diteruskan; tidak pernah di-cache.
 */
import type { NextRequest } from 'next/server';
import { sanitizeAltchaChallenge } from '@/lib/altcha';
import { bffFetch } from '@/lib/bff';
import { isNotCrossSite, jsonError } from '@/lib/request-guard';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function GET(req: NextRequest) {
  if (!isNotCrossSite(req)) return jsonError(403, 'forbidden', 'Permintaan ditolak.');

  const result = await bffFetch<unknown>('/v1/auth/challenge', { method: 'GET', incoming: req.headers });
  if (result.ok) {
    const challenge = sanitizeAltchaChallenge(result.data);
    if (challenge) return Response.json(challenge, { status: 200, headers: { 'cache-control': 'no-store' } });
    console.error('[altcha] challenge BFF tidak sesuai bentuk yang diharapkan');
  } else if (result.status === 429) {
    return jsonError(429, 'rate_limited', 'Terlalu banyak permintaan. Coba lagi beberapa menit lagi.');
  }
  return jsonError(502, 'challenge_unavailable', 'Verifikasi keamanan belum dapat dimuat. Silakan coba lagi.');
}
