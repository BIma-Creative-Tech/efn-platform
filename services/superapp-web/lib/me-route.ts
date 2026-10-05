/**
 * Helper Route Handler untuk endpoint akun (`/api/me/*` -> BFF `/v1/me/*`). SERVER-SIDE SAJA.
 * - Bearer selalu dari cookie HttpOnly `efn_session`; token tidak pernah dikembalikan ke browser.
 * - 401 dari BFF -> cookie sesi dihapus (sesi berakhir/dicabut, mis. setelah permintaan hapus akun).
 * - Error lain dipetakan ke pesan generik; pesan 4xx BFF (sudah generik, kontrak §3) boleh diteruskan.
 */
import 'server-only';
import { NextResponse } from 'next/server';
import type { BffResult } from './bff';
import { clearSessionCookie } from './session';

export function unauthenticated(): Response {
  const res = NextResponse.json(
    { error: { code: 'unauthenticated', message: 'Sesi berakhir. Silakan masuk kembali.' } },
    { status: 401, headers: { 'cache-control': 'no-store' } },
  );
  clearSessionCookie(res);
  return res;
}

/** Petakan hasil gagal BFF ke respons browser. `passthrough` = status 4xx yang pesannya diteruskan. */
export function mapBffFailure(
  result: Extract<BffResult<unknown>, { ok: false }>,
  fallbackMessage: string,
  passthrough: number[] = [400, 403, 404, 409, 422, 429],
): Response {
  if (result.status === 401) return unauthenticated();
  if (passthrough.includes(result.status)) {
    return Response.json(
      { error: { code: result.code, message: result.message } },
      { status: result.status, headers: { 'cache-control': 'no-store' } },
    );
  }
  return Response.json(
    { error: { code: 'upstream_error', message: fallbackMessage } },
    { status: 502, headers: { 'cache-control': 'no-store' } },
  );
}
