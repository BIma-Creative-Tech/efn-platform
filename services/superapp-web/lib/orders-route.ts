/**
 * Helper Route Handler belanja online (`/api/cart/**`, `/api/orders/**`, dipakai juga `/api/catalog/**`).
 * SERVER-SIDE SAJA. Kontrak: docs/04_SERVICE_CONTRACTS.md §10.3; ADR-034 / ADR-019 (Proposed).
 *
 * - Bearer selalu dari cookie HttpOnly `efn_session` (lib/session.ts); token tidak pernah ke browser.
 * - Route tulis wajib `isSameOrigin` (lib/request-guard.ts); GET data member wajib `isNotCrossSite`.
 * - Semua respons member `Cache-Control: no-store`.
 * - Error BFF dipetakan ke pesan Bahasa Indonesia (K-ON5, lib/orders.ts) — pesan/body BFF TIDAK diteruskan.
 * - Tidak me-log body, kode ambil, `qr_string`, atau token (§10.11).
 */
import 'server-only';
import { createHash } from 'node:crypto';
import type { NextRequest } from 'next/server';
import type { BffResult } from './bff';
import { unauthenticated } from './me-route';
import { GENERIC_ONLINE_ERROR, ONLINE_ERROR_MESSAGES, onlineErrorMessage, PASSTHROUGH_STATUSES } from './orders';
import { isNotCrossSite, isSameOrigin } from './request-guard';
import { getSessionTokenFromRequest } from './session';

export const NO_STORE = { 'cache-control': 'no-store' } as const;

export function noStoreJson(data: unknown, status = 200): Response {
  return Response.json(data, { status, headers: NO_STORE });
}

export function onlineError(status: number, code: string, message = onlineErrorMessage(code)): Response {
  return noStoreJson({ error: { code, message } }, status);
}

type BffFailure = Extract<BffResult<unknown>, { ok: false }>;

/** Kode error klien sendiri dari lib/bff.ts (BFF tidak terjangkau / salah konfigurasi). */
const TRANSPORT_CODES = new Set(['bff_unavailable', 'bff_timeout', 'bff_misconfigured']);

/**
 * Peta kegagalan BFF -> respons browser (K-ON5 §10.3.7).
 * - 401 -> sesi dihapus (`unauthenticated`), kecuali `publicRoute` (katalog tidak memakai sesi).
 * - 4xx kontrak -> status sama, pesan dari peta Indonesia; kode tak dikenal -> `invalid_request`.
 * - 503 `online_unavailable` (kill switch `ONLINE_ENABLED=false`) -> 503 + pesan ramah.
 * - 502/503 `payment_provider_*` -> status sama.
 * - BFF tidak terjangkau -> 503 `service_unavailable`; 5xx lain -> 502 `upstream_error` (tanpa detail internal).
 */
export function mapOnlineFailure(r: BffFailure, opts: { publicRoute?: boolean } = {}): Response {
  if (r.status === 401 && !opts.publicRoute) return unauthenticated();
  if (PASSTHROUGH_STATUSES.includes(r.status)) {
    const known = Object.prototype.hasOwnProperty.call(ONLINE_ERROR_MESSAGES, r.code);
    const code = known ? r.code : r.status === 404 ? 'not_found' : r.status === 429 ? 'rate_limited' : 'invalid_request';
    return onlineError(r.status, code);
  }
  if (r.code === 'online_unavailable' && r.status === 503) return onlineError(503, 'online_unavailable');
  if (r.code === 'payment_provider_error' || r.code === 'payment_provider_unavailable') {
    return onlineError(r.status === 502 ? 502 : 503, r.code);
  }
  if (TRANSPORT_CODES.has(r.code)) {
    return onlineError(503, 'service_unavailable', 'Layanan sedang tidak tersedia. Silakan coba lagi.');
  }
  return onlineError(502, 'upstream_error', GENERIC_ONLINE_ERROR);
}

export type MemberGuard = { ok: true; token: string } | { ok: false; response: Response };

/** Guard route member. `write` = metode pengubah state (Origin wajib sama). */
export function requireMember(req: NextRequest, write: boolean): MemberGuard {
  const allowed = write ? isSameOrigin(req) : isNotCrossSite(req);
  if (!allowed) return { ok: false, response: onlineError(403, 'forbidden', 'Permintaan ditolak.') };
  const token = getSessionTokenFromRequest(req);
  if (!token) return { ok: false, response: unauthenticated() };
  return { ok: true, token };
}

export type JsonBody = { ok: true; value: unknown } | { ok: false; response: Response };

/** Baca body JSON dengan batas ukuran (kontrak: body ≤ 16 KB). */
export async function readJsonBody(req: Request, maxBytes = 16 * 1024): Promise<JsonBody> {
  const declared = Number(req.headers.get('content-length') ?? '0');
  if (Number.isFinite(declared) && declared > maxBytes) {
    return { ok: false, response: onlineError(413, 'payload_too_large', 'Data terlalu besar.') };
  }
  let text: string;
  try {
    text = await req.text();
  } catch {
    return { ok: false, response: onlineError(400, 'invalid_request') };
  }
  if (new TextEncoder().encode(text).byteLength > maxBytes) {
    return { ok: false, response: onlineError(413, 'payload_too_large', 'Data terlalu besar.') };
  }
  if (!text) return { ok: true, value: {} };
  try {
    return { ok: true, value: JSON.parse(text) as unknown };
  } catch {
    return { ok: false, response: onlineError(400, 'invalid_request') };
  }
}

/**
 * Idempotency-Key checkout (§10.3.5) DIBUAT SERVER-SIDE dan DETERMINISTIK dari `quote_id`:
 * `chk1_` + base64url(SHA-256("efn:checkout:v1:" + quote_id)) = 48 karakter `[A-Za-z0-9_-]` (kontrak 16–64).
 *
 * Alasan (keputusan lead G17 "key dibuat di route handler"): klik ganda, reload, atau retry jaringan atas quote yang
 * sama selalu menghasilkan key yang sama -> BFF mengembalikan order yang sama (satu order/SO), tanpa perlu
 * `sessionStorage` di browser. Quote bersifat sekali pakai per member (dihapus setelah checkout sukses), sehingga
 * percobaan baru = quote baru = key baru. Key tidak memuat data pribadi dan tidak dapat dipakai untuk apa pun selain
 * replay checkout milik member yang sama (BFF mengikat key ke `member_id`).
 */
export function checkoutIdempotencyKey(quoteId: string): string {
  const digest = createHash('sha256').update(`efn:checkout:v1:${quoteId.toLowerCase()}`).digest('base64url');
  return `chk1_${digest}`;
}

export const IDEMPOTENCY_KEY_RE = /^[A-Za-z0-9_-]{16,64}$/;
