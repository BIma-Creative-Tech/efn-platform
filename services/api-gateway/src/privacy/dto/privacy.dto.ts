import { Errors } from '../../common/errors/api-error';
import { asRecord, requireInt, requireUuid } from '../../common/validation';
import { ConsentPurpose, SetConsentInput, isConsentPurpose } from '../consent.service';

/**
 * Versi teks consent. `@` diizinkan karena frontend memakai format `<purpose>@<tanggal>` (mis. `location@2026-10-06`,
 * services/superapp-web/lib/consent.ts) — G6 QA. Harus sama dengan validasi `CONSENT_<P>_VERSION` di AppConfig.
 */
export const CONSENT_VERSION_RE = /^[A-Za-z0-9._@-]{1,32}$/;
const VERSION_RE = CONSENT_VERSION_RE;

export function parseConsentPurpose(v: unknown): ConsentPurpose {
  if (!isConsentPurpose(v)) throw Errors.notFound();
  return v;
}

/** `PUT /v1/me/consents/:purpose` `{ granted, version }` (docs/04 §8.4). */
export function parseSetConsent(body: unknown): SetConsentInput {
  const o = asRecord(body);
  if (typeof o.granted !== 'boolean') throw Errors.invalidRequest();
  if (typeof o.version !== 'string' || !VERSION_RE.test(o.version)) throw Errors.invalidRequest();
  return { granted: o.granted, version: o.version };
}

export type ReauthInput = { kind: 'request' } | { kind: 'verify'; challengeId: string; code: string };

/** `POST /v1/me/reauth`: `{}` → kirim OTP; `{ challenge_id, code }` → verifikasi (docs/04 §8.9). */
export function parseReauth(body: unknown): ReauthInput {
  const o = body === undefined || body === null ? {} : asRecord(body);
  if (o.challenge_id === undefined && o.code === undefined) return { kind: 'request' };
  const code = o.code;
  if (typeof code !== 'string' || code.length > 16) throw Errors.invalidRequest();
  return { kind: 'verify', challengeId: requireUuid(o, 'challenge_id'), code: code.trim() };
}

export const ERASURE_HTTP_STORES = ['odoo'] as const;

export function parseErasureStoreQuery(v: unknown): 'odoo' {
  if (v !== 'odoo') throw Errors.invalidRequest('store tidak didukung.');
  return 'odoo';
}

/** `POST /v1/pos/privacy/erasures/:request_id/complete` `{ store: "odoo", records: n }` (n ≥ 0, n=0 tetap dikirim). */
export function parseErasureComplete(body: unknown): { store: 'odoo'; records: number } {
  const o = asRecord(body);
  const store = parseErasureStoreQuery(o.store);
  return { store, records: requireInt(o, 'records', 0, 100_000_000) };
}

export function parseRequestId(v: unknown): string {
  return requireUuid({ v }, 'v');
}

/**
 * W-18 (Q-P18): `DELETE /v1/me?confirm_open_orders=true|1` — pelanggan mengonfirmasi hapus akun walau ada pesanan
 * online terbuka. Tidak ada / `false` / `0` → false; nilai lain → 400 `invalid_request`.
 */
export function parseConfirmOpenOrders(v: unknown): boolean {
  if (v === undefined || v === null || v === '' || v === 'false' || v === '0') return false;
  if (v === 'true' || v === '1') return true;
  throw Errors.invalidRequest();
}
