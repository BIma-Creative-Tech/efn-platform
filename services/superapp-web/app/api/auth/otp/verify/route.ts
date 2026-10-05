/**
 * POST /api/auth/otp/verify  { challenge_id, code }  ->  BFF POST /v1/auth/otp/verify (docs/04 §3.2)
 * Sukses: access_token disimpan di cookie efn_session (HttpOnly, Secure, SameSite=Lax);
 * token TIDAK dikembalikan ke browser.
 */
import { NextResponse, type NextRequest } from 'next/server';
import { bffFetch, type BffErrorBody, type OtpTokenResponse } from '@/lib/bff';
import { setSessionCookie } from '@/lib/session';
import { isSameOrigin, jsonError } from '@/lib/request-guard';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

const CHALLENGE_RE = /^[0-9a-fA-F-]{8,64}$/;
const CODE_RE = /^\d{6}$/;

export async function POST(req: NextRequest) {
  if (!isSameOrigin(req)) return jsonError(403, 'forbidden', 'Permintaan ditolak.');

  let body: { challenge_id?: unknown; code?: unknown } | null;
  try {
    body = (await req.json()) as typeof body;
  } catch {
    return jsonError(400, 'invalid_request', 'Permintaan tidak valid.');
  }
  const challengeId = typeof body?.challenge_id === 'string' ? body.challenge_id : '';
  const code = typeof body?.code === 'string' ? body.code.trim() : '';
  if (!CHALLENGE_RE.test(challengeId) || !CODE_RE.test(code)) {
    return Response.json({ ok: false, reason: 'invalid' }, { status: 400 });
  }

  const result = await bffFetch<OtpTokenResponse>('/v1/auth/otp/verify', {
    method: 'POST',
    json: { challenge_id: challengeId, code },
    incoming: req.headers,
  });

  if (result.ok && typeof result.data?.access_token === 'string') {
    const res = NextResponse.json({ ok: true }, { status: 200, headers: { 'cache-control': 'no-store' } });
    setSessionCookie(res, result.data.access_token, result.data.expires_in);
    return res;
  }

  if (!result.ok && result.status === 400) {
    // Kontrak §3.1/§3.2: { ok:false, reason: invalid|expired|locked, attempts_left }
    const b: BffErrorBody = result.body ?? {};
    const reason = b.reason === 'expired' || b.reason === 'locked' ? b.reason : 'invalid';
    const attemptsLeft = typeof b.attempts_left === 'number' ? b.attempts_left : undefined;
    return Response.json({ ok: false, reason, attempts_left: attemptsLeft }, { status: 400 });
  }
  if (!result.ok && result.status === 429) {
    return jsonError(429, 'rate_limited', 'Terlalu banyak percobaan. Coba lagi beberapa menit lagi.');
  }
  // docs/04 §8.11: akun sedang diproses hapus (DSAR `processing`) -> 409 account_deleting. Hanya terlihat oleh
  // pemilik nomor yang lolos OTP, jadi aman diteruskan apa adanya (G6 QA).
  if (!result.ok && result.status === 409 && result.code === 'account_deleting') {
    return jsonError(409, 'account_deleting', 'Akun Anda sedang dalam proses penghapusan dan tidak dapat digunakan lagi.');
  }
  return jsonError(502, 'otp_unavailable', 'Verifikasi belum dapat diproses. Silakan coba lagi.');
}
