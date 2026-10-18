/**
 * DELETE /api/me  { confirm: "HAPUS", acknowledge_points: true, confirm_open_orders?: true }
 *   -> BFF DELETE /v1/me[?confirm_open_orders=true] (docs/04 §8.4, §8.13; PDP_DSAR_DESIGN §3/§5) dengan token step-up
 *      dari cookie `efn_stepup`.
 *   -> 202 { request_id, scheduled_at, points_forfeited, open_online_orders, erasure_deadline, status_available }
 *   -> 409 { error: { code: "open_online_orders", message, open_parts, max_defer_days } } (G18 W-18; step-up tetap berlaku,
 *      UI menampilkan penjelasan lalu mengulang dengan `confirm_open_orders: true`).
 *
 * - Step-up wajib: tanpa cookie step-up -> 403 `step_up_required` (UI mengulang re-OTP).
 * - SEC-40 (v3.6): token step-up SEKALI PAKAI untuk DELETE. BFF menjawab `403 step_up_invalid` bila dipakai ulang
 *   -> cookie step-up dihapus, UI meminta re-OTP.
 * - ASUMSI: token step-up dikirim ke BFF di header `X-Step-Up-Token` (DELETE tanpa body).
 * - Konfirmasi ketik-ulang & pengakuan poin hangus dicek di sini juga, bukan hanya di client.
 * - R6: `receipt_token` dari BFF disimpan di cookie HttpOnly `efn_dsar_receipt` (Path=/api/dsar, SameSite=Strict,
 *   <= 30 hari) dan TIDAK disertakan di respons ke browser maupun log. `status_available` memberi tahu UI apakah
 *   halaman status `/akun/penghapusan/<id>` dapat dipakai.
 * - Sukses: cookie step-up dihapus; cookie sesi juga dihapus (PDP_DSAR_DESIGN §5). Pembatalan dalam masa
 *   tenggang: masuk kembali -> /akun/penghapusan/<id> atau /akun/data-saya.
 */
import { NextResponse, type NextRequest } from 'next/server';
import { bffFetch } from '@/lib/bff';
import {
  isDeleteConfirmed,
  MAX_DEFER_DAYS_CAP,
  OPEN_ORDERS_CONFIRM_PARAM,
  openOrdersExplanation,
  parseDeleteAccepted,
  parseOpenOrdersConflict,
} from '@/lib/dsar';
import { isPlausibleReceipt, receiptMaxAge } from '@/lib/dsar-receipt';
import { mapBffFailure, unauthenticated } from '@/lib/me-route';
import { isSameOrigin, jsonError } from '@/lib/request-guard';
import {
  clearSessionCookie,
  clearStepUpCookie,
  getSessionTokenFromRequest,
  getStepUpTokenFromRequest,
  setDsarReceiptCookie,
} from '@/lib/session';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

function stepUpRequired(reused = false): Response {
  const res = NextResponse.json(
    {
      error: {
        code: 'step_up_required',
        message: reused
          ? 'Kode verifikasi hanya berlaku untuk satu kali penghapusan. Minta kode baru lalu ulangi.'
          : 'Verifikasi ulang diperlukan (kode berlaku 10 menit). Minta kode baru lalu ulangi.',
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

  // G18 W-18: konfirmasi eksplisit setelah 409 open_online_orders. Hanya boolean `true` persis yang dihitung; query
  // dibangun di sini (browser tidak memilih path BFF). Token step-up SAMA: BFF menjalankan pra-cek 409 sebelum jti
  // ditandai terpakai, jadi cookie step-up TIDAK dihapus pada 409.
  const confirmOpenOrders = (body as { confirm_open_orders?: unknown }).confirm_open_orders === true;
  const path = confirmOpenOrders ? `/v1/me?${OPEN_ORDERS_CONFIRM_PARAM}=true` : '/v1/me';

  const r = await bffFetch<unknown>(path, {
    method: 'DELETE',
    token,
    headers: { 'x-step-up-token': stepUp },
    incoming: req.headers,
  });

  if (r.ok) {
    const accepted = parseDeleteAccepted(r.data);
    if (!accepted) return jsonError(502, 'upstream_error', 'Permintaan belum dapat diproses. Silakan coba lagi.');
    const receipt =
      r.data && typeof r.data === 'object' ? (r.data as { receipt_token?: unknown }).receipt_token : undefined;
    const now = Date.now();
    const storable = isPlausibleReceipt(receipt) && receiptMaxAge(receipt, now) !== null;
    // Respons dibangun dari field allowlist; receipt_token tidak pernah ikut ke browser.
    const res = NextResponse.json(
      { ...accepted, status_available: storable },
      { status: 202, headers: { 'cache-control': 'no-store' } },
    );
    if (storable) setDsarReceiptCookie(res, receipt, now);
    clearStepUpCookie(res);
    clearSessionCookie(res);
    return res;
  }
  // SEC-40: step-up sekali pakai -> 403 step_up_invalid; token kedaluwarsa -> step_up_required.
  if (r.code === 'step_up_invalid') return stepUpRequired(true);
  if (r.code === 'step_up_required' || r.status === 403) return stepUpRequired();
  if (r.status === 409 && r.code === 'open_online_orders') {
    // Allowlist: hanya angka + teks Indonesia buatan frontend (draf, menunggu Legal); pesan BFF tidak diteruskan.
    const c = parseOpenOrdersConflict(r.body) ?? { open_parts: 0, max_defer_days: MAX_DEFER_DAYS_CAP };
    return Response.json(
      {
        error: {
          code: 'open_online_orders',
          message: openOrdersExplanation(c),
          open_parts: c.open_parts,
          max_defer_days: c.max_defer_days,
        },
      },
      { status: 409, headers: { 'cache-control': 'no-store' } },
    );
  }
  return mapBffFailure(r, 'Permintaan hapus akun belum dapat diproses. Silakan coba lagi.', [409, 429]);
}
