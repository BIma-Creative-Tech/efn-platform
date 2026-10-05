/**
 * DELETE /api/me  { confirm: "HAPUS", acknowledge_points: true }
 *   -> BFF DELETE /v1/me (docs/04 §8.4; PDP_DSAR_DESIGN §3/§5) dengan token step-up dari cookie `efn_stepup`.
 *   -> 202 { request_id, scheduled_at, points_forfeited }
 *
 * - Step-up wajib: tanpa cookie step-up -> 403 `step_up_required` (UI mengulang re-OTP).
 * - ASUMSI: token step-up dikirim ke BFF di header `X-Step-Up-Token` (DELETE tanpa body).
 * - Konfirmasi ketik-ulang & pengakuan poin hangus dicek di sini juga, bukan hanya di client.
 * - Sukses: cookie step-up dihapus; cookie sesi juga dihapus karena BFF mencabut sesi saat permintaan hapus
 *   (PDP_DSAR_DESIGN §5). Pembatalan dalam masa tenggang: masuk kembali -> /akun/data-saya.
 */
import { NextResponse, type NextRequest } from 'next/server';
import { bffFetch } from '@/lib/bff';
import { isDeleteConfirmed, parseDeleteAccepted } from '@/lib/dsar';
import { mapBffFailure, unauthenticated } from '@/lib/me-route';
import { isSameOrigin, jsonError } from '@/lib/request-guard';
import {
  clearSessionCookie,
  clearStepUpCookie,
  getSessionTokenFromRequest,
  getStepUpTokenFromRequest,
} from '@/lib/session';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

function stepUpRequired(): Response {
  const res = NextResponse.json(
    {
      error: {
        code: 'step_up_required',
        message: 'Verifikasi ulang diperlukan (kode berlaku 10 menit). Minta kode baru lalu ulangi.',
      },
    },
    { status: 403, headers: { 'cache-control': 'no-store' } },
  );
  clearStepUpCookie(res);
  return res;
}

export async function DELETE(req: NextRequest) {
  if (!isSameOrigin(req)) return jsonError(403, 'forbidden', 'Permintaan ditolak.');
  const token = getSessionTokenFromRequest(req);
  if (!token) return unauthenticated();
  const stepUp = getStepUpTokenFromRequest(req);
  if (!stepUp) return stepUpRequired();

  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return jsonError(400, 'invalid_request', 'Permintaan tidak valid.');
  }
  if (!isDeleteConfirmed(body)) {
    return jsonError(400, 'confirmation_required', 'Konfirmasi penghapusan belum lengkap.');
  }

  const r = await bffFetch<unknown>('/v1/me', {
    method: 'DELETE',
    token,
    headers: { 'x-step-up-token': stepUp },
    incoming: req.headers,
  });

  if (r.ok) {
    const accepted = parseDeleteAccepted(r.data);
    if (!accepted) return jsonError(502, 'upstream_error', 'Permintaan belum dapat diproses. Silakan coba lagi.');
    const res = NextResponse.json(accepted, { status: 202, headers: { 'cache-control': 'no-store' } });
    clearStepUpCookie(res);
    clearSessionCookie(res);
    return res;
  }
  // Token step-up kedaluwarsa/tidak valid (BFF dapat menjawab 401 khusus step-up atau 403).
  if (r.code === 'step_up_required' || r.code === 'step_up_invalid' || r.status === 403) return stepUpRequired();
  return mapBffFailure(r, 'Permintaan hapus akun belum dapat diproses. Silakan coba lagi.', [409, 429]);
}
