/**
 * Helper Route Handler pesan dari meja (`/api/fnb/**` -> BFF `/v1/fnb/**`, docs/04 §14.A.3). SERVER-SIDE SAJA.
 *
 * - Browser mengirim token sesi meja di header `X-Table-Session` (token disimpan di memori, tidak di storage/URL);
 *   di sini bentuknya diperiksa lalu diteruskan ke BFF di header yang sama. Token TIDAK PERNAH di-log atau dikembalikan.
 * - Bearer (cookie HttpOnly `efn_session`) OPSIONAL: tamu boleh memesan. Bila cookie ada tetapi BFF menjawab 401,
 *   cookie dihapus (`unauthenticated`) dan klien mengulang sebagai tamu (§14.A.3: Bearer tidak valid -> 401).
 * - Route tulis wajib Origin sama (CSRF); GET menolak lintas situs. Semua respons `Cache-Control: no-store`.
 * - Pesan error = peta Indonesia di lib/fnb.ts; pesan/body BFF tidak diteruskan.
 */
import 'server-only';
import type { NextRequest } from 'next/server';
import type { BffResult } from './bff';
import { FNB_ERROR_MESSAGES, FNB_PASSTHROUGH_STATUSES, fnbErrorMessage, isTableToken, parseUnavailableIds, TABLE_SESSION_HEADER } from './fnb';
import { unauthenticated } from './me-route';
import { noStoreJson } from './orders-route';
import { isNotCrossSite, isSameOrigin } from './request-guard';
import { getSessionTokenFromRequest } from './session';

type BffFailure = Extract<BffResult<unknown>, { ok: false }>;

export function fnbError(status: number, code: string, extra?: Record<string, unknown>): Response {
  return noStoreJson({ error: { code, message: fnbErrorMessage(code), ...(extra ?? {}) } }, status);
}

export type FnbGuard = { ok: true; table: string; bearer: string | null } | { ok: false; response: Response };

/**
 * Guard route fnb. `write` = metode pengubah state. `requireBearer` untuk klaim poin (login wajib).
 * Token meja hilang/salah bentuk -> 401 `table_session_invalid` tanpa memanggil BFF.
 */
export function fnbGuard(req: NextRequest, write: boolean, opts: { requireBearer?: boolean } = {}): FnbGuard {
  const allowed = write ? isSameOrigin(req) : isNotCrossSite(req);
  if (!allowed) return { ok: false, response: fnbError(403, 'forbidden') };
  const table = (req.headers.get(TABLE_SESSION_HEADER) ?? '').trim();
  if (!isTableToken(table)) return { ok: false, response: fnbError(401, 'table_session_invalid') };
  const bearer = getSessionTokenFromRequest(req);
  if (opts.requireBearer && !bearer) return { ok: false, response: unauthenticated() };
  return { ok: true, table, bearer };
}

/** Header ke BFF: token meja (+ tambahan seperti Idempotency-Key). */
export function tableHeaders(table: string, extra: Record<string, string> = {}): Record<string, string> {
  return { [TABLE_SESSION_HEADER]: table, ...extra };
}

const TRANSPORT_CODES = new Set(['bff_unavailable', 'bff_timeout', 'bff_misconfigured']);

/**
 * Peta kegagalan BFF -> respons browser.
 * - 401 `table_session_invalid` -> tetap (token meja salah/kedaluwarsa, tanpa membedakan sebab).
 * - 401 lain dengan Bearer terkirim -> `unauthenticated` + cookie sesi dihapus; tanpa Bearer -> `table_session_invalid`.
 * - 400/404/409/422/429 -> status sama, kode dikenal dipertahankan (tak dikenal -> `invalid_request`/`not_found`/`rate_limited`).
 *   `item_unavailable` membawa `product_ids` yang sudah divalidasi.
 * - 503 / BFF tidak terjangkau -> 503 `ordering_unavailable` ("silakan pesan ke pelayan"; tidak diantrekan).
 * - 5xx lain -> 502 `upstream_error` generik.
 */
export function mapFnbFailure(r: BffFailure, hadBearer: boolean): Response {
  if (r.status === 401) {
    if (r.code === 'table_session_invalid') return fnbError(401, 'table_session_invalid');
    return hadBearer ? unauthenticated() : fnbError(401, 'table_session_invalid');
  }
  if (FNB_PASSTHROUGH_STATUSES.includes(r.status)) {
    const known = Object.prototype.hasOwnProperty.call(FNB_ERROR_MESSAGES, r.code);
    const code = known ? r.code : r.status === 404 ? 'not_found' : r.status === 429 ? 'rate_limited' : 'invalid_request';
    return fnbError(r.status, code, code === 'item_unavailable' ? { product_ids: parseUnavailableIds(r.body) } : undefined);
  }
  if (r.status === 503 || TRANSPORT_CODES.has(r.code)) return fnbError(503, 'ordering_unavailable');
  return fnbError(502, 'upstream_error');
}
