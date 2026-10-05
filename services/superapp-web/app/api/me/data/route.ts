/**
 * GET /api/me/data -> BFF GET /v1/me/data (docs/04 §8.4; PDP_DSAR_DESIGN §3) — export data milik pemanggil.
 *
 * - 200: JSON export diteruskan sebagai lampiran (`Content-Disposition: attachment`), tidak di-cache.
 * - 202: export besar diproses async -> `{ request_id, download_url?, expires_at? }`; tautan (presigned MinIO,
 *   TTL 24 jam) hanya diteruskan bila https. Tautan juga muncul di riwayat permintaan saat siap (ASUMSI).
 * Isi export tidak di-log.
 */
import { NextResponse, type NextRequest } from 'next/server';
import { bffFetch } from '@/lib/bff';
import { exportFilename, parseExportAccepted } from '@/lib/dsar';
import { mapBffFailure, unauthenticated } from '@/lib/me-route';
import { isNotCrossSite, jsonError } from '@/lib/request-guard';
import { clearStepUpCookie, getSessionTokenFromRequest, getStepUpTokenFromRequest } from '@/lib/session';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

function stepUpRequired(): Response {
  const res = NextResponse.json(
    {
      error: {
        code: 'step_up_required',
        message: 'Untuk keamanan, verifikasi dengan kode yang dikirim ke nomor akun Anda sebelum mengunduh data.',
      },
    },
    { status: 403, headers: { 'cache-control': 'no-store' } },
  );
  clearStepUpCookie(res);
  return res;
}

export async function GET(req: NextRequest) {
  if (!isNotCrossSite(req)) return jsonError(403, 'forbidden', 'Permintaan ditolak.');
  const token = getSessionTokenFromRequest(req);
  if (!token) return unauthenticated();

  // docs/04 §8.9: GET /v1/me/data juga wajib `X-Step-Up-Token` (G6 QA — sebelumnya tidak dikirim sehingga BFF
  // selalu 403 dan UI menampilkan 502). Tanpa cookie step-up -> 403 step_up_required; UI menjalankan re-OTP lalu ulang.
  const stepUp = getStepUpTokenFromRequest(req);
  if (!stepUp) return stepUpRequired();
  const r = await bffFetch<unknown>('/v1/me/data', {
    token,
    headers: { 'x-step-up-token': stepUp },
    incoming: req.headers,
    timeoutMs: 30_000,
  });
  if (!r.ok && (r.code === 'step_up_required' || r.code === 'step_up_invalid' || r.status === 403)) return stepUpRequired();
  if (!r.ok) return mapBffFailure(r, 'Data belum dapat diunduh. Silakan coba lagi.', [429]);

  if (r.status === 202) {
    const accepted = parseExportAccepted(r.data);
    if (!accepted) return jsonError(502, 'upstream_error', 'Data belum dapat diunduh. Silakan coba lagi.');
    return Response.json(accepted, { status: 202, headers: { 'cache-control': 'no-store' } });
  }

  if (r.data === null || typeof r.data !== 'object') {
    return jsonError(502, 'upstream_error', 'Data belum dapat diunduh. Silakan coba lagi.');
  }
  return new Response(JSON.stringify(r.data, null, 2), {
    status: 200,
    headers: {
      'content-type': 'application/json; charset=utf-8',
      'content-disposition': `attachment; filename="${exportFilename()}"`,
      'cache-control': 'no-store',
      'x-content-type-options': 'nosniff',
    },
  });
}
