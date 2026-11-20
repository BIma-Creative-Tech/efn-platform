/**
 * Helper Route Handler G50 (wishlist, restock alert, price alert, event perilaku). SERVER-SIDE SAJA.
 * - Bearer dari cookie HttpOnly (requireMember di lib/orders-route.ts); route tulis wajib Origin sama.
 * - Pesan error = peta Indonesia (lib/engage.ts); teks BFF tidak diteruskan. Tidak me-log body (UU PDP).
 */
import 'server-only';
import type { BffResult } from './bff';
import { engageMessage, mapEngageCode } from './engage';
import { unauthenticated } from './me-route';
import { NO_STORE } from './orders-route';

type BffFailure = Extract<BffResult<unknown>, { ok: false }>;

const TRANSPORT_CODES = new Set(['bff_unavailable', 'bff_timeout', 'bff_misconfigured']);
const PASS_STATUS = [400, 403, 404, 409, 422, 429];

export function engageError(status: number, code: string, extraHeaders: Record<string, string> = {}): Response {
  return Response.json({ error: { code, message: engageMessage(code) } }, { status, headers: { ...NO_STORE, ...extraHeaders } });
}

/** Kegagalan BFF -> respons browser. 401 menghapus cookie sesi (sesi berakhir). */
export function engageFailure(r: BffFailure): Response {
  if (r.status === 401) return unauthenticated();
  if (TRANSPORT_CODES.has(r.code)) return engageError(503, 'service_unavailable');
  if (PASS_STATUS.includes(r.status)) {
    const code = mapEngageCode(r.status, r.code);
    const headers: Record<string, string> = r.status === 429 && r.retryAfterS ? { 'retry-after': String(r.retryAfterS) } : {};
    return engageError(r.status === 422 ? 400 : r.status, code, headers);
  }
  return engageError(r.status === 503 ? 503 : 502, r.status === 503 ? 'service_unavailable' : 'upstream_error');
}

export function noContent(): Response {
  return new Response(null, { status: 204, headers: NO_STORE });
}
