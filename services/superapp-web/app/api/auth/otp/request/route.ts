/**
 * POST /api/auth/otp/request  { phone, altcha }  ->  BFF POST /v1/auth/otp/request (docs/04 §3.2 + §8.1)
 * `altcha` = payload base64 dari widget ALTCHA (proof-of-work). Tanpa/tidak valid -> `400 captcha_required`
 * (dicek bentuknya di sini, diverifikasi kriptografis & sekali-pakai oleh BFF).
 * Respons selalu generik (tidak membocorkan apakah nomor terdaftar). Nomor & payload tidak di-log.
 */
import type { NextRequest } from 'next/server';
import { isPlausibleAltchaPayload } from '@/lib/altcha';
import { bffFetch, type OtpChallenge } from '@/lib/bff';
import { normalizeIndonesianPhone } from '@/lib/phone';
import { isSameOrigin, jsonError } from '@/lib/request-guard';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

const CAPTCHA_MESSAGE = 'Verifikasi keamanan belum selesai atau kedaluwarsa. Ulangi verifikasi lalu kirim lagi.';

export async function POST(req: NextRequest) {
  if (!isSameOrigin(req)) return jsonError(403, 'forbidden', 'Permintaan ditolak.');

  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return jsonError(400, 'invalid_request', 'Permintaan tidak valid.');
  }
  const b = (body ?? {}) as { phone?: unknown; altcha?: unknown };
  const phone = typeof b.phone === 'string' ? normalizeIndonesianPhone(b.phone) : null;
  if (!phone) return jsonError(400, 'invalid_phone', 'Nomor HP tidak valid. Gunakan format 08xx atau 628xx.');
  if (!isPlausibleAltchaPayload(b.altcha)) return jsonError(400, 'captcha_required', CAPTCHA_MESSAGE);

  const result = await bffFetch<OtpChallenge>('/v1/auth/otp/request', {
    method: 'POST',
    json: { phone, altcha: b.altcha },
    incoming: req.headers,
  });

  if (result.ok && typeof result.data?.challenge_id === 'string') {
    return Response.json(
      { challenge_id: result.data.challenge_id, expires_in: result.data.expires_in ?? 180 },
      { status: 202, headers: { 'cache-control': 'no-store' } },
    );
  }
  if (!result.ok && result.status === 429) {
    return jsonError(429, 'rate_limited', 'Terlalu banyak permintaan. Coba lagi beberapa menit lagi.');
  }
  // BFF = validator utama: `400 invalid_phone` (SEC-06/SEC-07) dan `400 captcha_required` (§8.1) diteruskan
  // dengan kode yang sama agar UI bisa bereaksi (mis. me-reset widget), bukan 502 generik.
  if (!result.ok && result.status === 400) {
    if (result.code === 'captcha_required') return jsonError(400, 'captcha_required', CAPTCHA_MESSAGE);
    if (result.code === 'invalid_phone') {
      return jsonError(400, 'invalid_phone', 'Nomor HP tidak valid. Gunakan nomor Indonesia (08xx atau 628xx).');
    }
    return jsonError(400, 'invalid_request', 'Permintaan tidak valid.');
  }
  return jsonError(502, 'otp_unavailable', 'Kode belum dapat dikirim. Silakan coba lagi.');
}
