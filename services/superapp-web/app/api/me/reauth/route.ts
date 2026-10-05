/**
 * POST /api/me/reauth — step-up OTP untuk aksi sensitif DSAR (docs/04 §8.4, PDP_DSAR_DESIGN §3/§5).
 *
 *   `{}`                      -> BFF POST /v1/me/reauth `{}`                      -> 202 { challenge_id, expires_in }
 *   `{ challenge_id, code }`  -> BFF POST /v1/me/reauth `{ challenge_id, code }`  -> 200 { step_up_token, expires_in }
 *
 * Bentuk request/response di atas = ASUMSI (kontrak hanya menyebut "token step-up purpose: dsar, 10 menit").
 * Token step-up disimpan di cookie HttpOnly `efn_stepup` (Path=/api/me, SameSite=Strict), tidak dikembalikan.
 * OTP dikirim ke nomor akun sendiri (dari Bearer), jadi tidak ada input nomor & tidak perlu captcha.
 */
import { NextResponse, type NextRequest } from 'next/server';
import { bffFetch, type BffErrorBody, type ReauthChallenge, type ReauthToken } from '@/lib/bff';
import { isValidChallengeId, isValidOtpCode } from '@/lib/dsar';
import { mapBffFailure, unauthenticated } from '@/lib/me-route';
import { isSameOrigin, jsonError } from '@/lib/request-guard';
import { getSessionTokenFromRequest, setStepUpCookie } from '@/lib/session';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function POST(req: NextRequest) {
  if (!isSameOrigin(req)) return jsonError(403, 'forbidden', 'Permintaan ditolak.');
  const token = getSessionTokenFromRequest(req);
  if (!token) return unauthenticated();

  let body: { challenge_id?: unknown; code?: unknown } | null;
  try {
    body = (await req.json()) as typeof body;
  } catch {
    return jsonError(400, 'invalid_request', 'Permintaan tidak valid.');
  }

  // Mode 1: minta OTP step-up.
  if (!body || (body.challenge_id === undefined && body.code === undefined)) {
    const r = await bffFetch<ReauthChallenge>('/v1/me/reauth', {
      method: 'POST',
      token,
      json: {},
      incoming: req.headers,
    });
    if (r.ok && typeof r.data?.challenge_id === 'string') {
      return Response.json(
        { challenge_id: r.data.challenge_id, expires_in: r.data.expires_in ?? 180 },
        { status: 202, headers: { 'cache-control': 'no-store' } },
      );
    }
    if (!r.ok) return mapBffFailure(r, 'Kode verifikasi belum dapat dikirim. Silakan coba lagi.', [429]);
    return jsonError(502, 'upstream_error', 'Kode verifikasi belum dapat dikirim. Silakan coba lagi.');
  }

  // Mode 2: verifikasi OTP step-up.
  const code = typeof body.code === 'string' ? body.code.trim() : '';
  if (!isValidChallengeId(body.challenge_id) || !isValidOtpCode(code)) {
    return Response.json({ ok: false, reason: 'invalid' }, { status: 400, headers: { 'cache-control': 'no-store' } });
  }
  const r = await bffFetch<ReauthToken>('/v1/me/reauth', {
    method: 'POST',
    token,
    json: { challenge_id: body.challenge_id, code },
    incoming: req.headers,
  });
  if (r.ok && typeof r.data?.step_up_token === 'string') {
    const expiresIn = typeof r.data.expires_in === 'number' ? r.data.expires_in : 600;
    const res = NextResponse.json(
      { ok: true, expires_in: Math.min(expiresIn, 600) },
      { status: 200, headers: { 'cache-control': 'no-store' } },
    );
    setStepUpCookie(res, r.data.step_up_token, expiresIn);
    return res;
  }
  if (!r.ok && r.status === 400) {
    // Format §3.1: { ok:false, reason, attempts_left }
    const b: BffErrorBody = r.body ?? {};
    const reason = b.reason === 'expired' || b.reason === 'locked' ? b.reason : 'invalid';
    const attemptsLeft = typeof b.attempts_left === 'number' ? b.attempts_left : undefined;
    return Response.json(
      { ok: false, reason, attempts_left: attemptsLeft },
      { status: 400, headers: { 'cache-control': 'no-store' } },
    );
  }
  if (!r.ok) return mapBffFailure(r, 'Verifikasi belum dapat diproses. Silakan coba lagi.', [429]);
  return jsonError(502, 'upstream_error', 'Verifikasi belum dapat diproses. Silakan coba lagi.');
}
