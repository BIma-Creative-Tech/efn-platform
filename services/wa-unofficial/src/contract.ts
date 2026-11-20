import { createHmac, timingSafeEqual } from 'node:crypto';

/**
 * Kontrak queue (docs/04 §28.7, ADR-044 §3.1, L-158/L-159). Salinan sisi BFF:
 * `services/api-gateway/src/notification/wa-unofficial/wa-unofficial.contract.ts` — UBAH KEDUANYA BERSAMAAN.
 *
 * `to_e164` = digit E.164 tanpa `+` (format member_ref BFF, hanya nomor Indonesia `^62[0-9]{8,13}$`).
 * `to_hash = hex(HMAC-SHA256(wa_unofficial_hash_key, to_e164))`.
 */
export const WA_QUEUE = 'notify.wa_unofficial';
export const WA_RESULT_QUEUE = 'notify.wa_unofficial.result';
/** Publisher sah `notify.wa_unofficial` (properti AMQP `user_id`). */
export const WA_PUBLISHERS: readonly string[] = ['efn-bff'];

/** ALLOWLIST kategori (ADR-044 §3.1). OTP/pesanan/pembayaran/poin TIDAK PERNAH lewat service ini. */
export const WA_CATEGORIES = ['promo', 'notify_restock', 'price_alert'] as const;
export type WaCategory = (typeof WA_CATEGORIES)[number];

export const WA_TEMPLATES: Readonly<Record<string, { category: WaCategory; params: readonly string[]; required: readonly string[] }>> = {
  promo_generic: { category: 'promo', params: ['title', 'body', 'valid_to', 'voucher_code'], required: ['title', 'body'] },
  markdown_near_expiry: { category: 'promo', params: ['product_name', 'outlet_name', 'valid_to'], required: ['product_name', 'outlet_name'] },
  restock_available: { category: 'notify_restock', params: ['product_name', 'outlet_name'], required: ['product_name', 'outlet_name'] },
  price_drop: { category: 'price_alert', params: ['product_name', 'new_price_idr'], required: ['product_name', 'new_price_idr'] },
};

export type WaResultStatus = 'sent' | 'failed' | 'rate_limited' | 'banned' | 'disconnected';

export interface WaMessage {
  idempotency_key: string;
  to_e164: string;
  to_hash: string;
  category: WaCategory;
  template: string;
  params: Record<string, string>;
  expires_at: number;
}

export interface WaResult {
  schema_version: 1;
  ref_idempotency_key: string | null;
  kind: 'result' | 'opt_out';
  status?: WaResultStatus;
  reason?: string;
  to_hash: string;
  at: string;
}

export const E164_RE = /^62[0-9]{8,13}$/;
const HASH_RE = /^[0-9a-f]{64}$/;
const PARAM_MAX = 300;

export function toHash(key: Buffer, e164: string): string {
  return createHmac('sha256', key).update(e164, 'utf8').digest('hex');
}

/**
 * Hasil parse bertahap: `reportable` = idempotency_key + to_hash valid sehingga kegagalan bisa dilaporkan lewat
 * `notify.wa_unofficial.result` (ack + failed). Selain itu → DLQ.
 */
export type ParseOutcome =
  | { ok: true; msg: WaMessage }
  | { ok: false; reportable: { key: string; toHash: string; reason: string } | null; reason: string };

export function parseWaMessage(payload: unknown, hashKey: Buffer): ParseOutcome {
  if (typeof payload !== 'object' || payload === null || Array.isArray(payload)) return { ok: false, reportable: null, reason: 'invalid_payload' };
  const o = payload as Record<string, unknown>;
  const key = typeof o.idempotency_key === 'string' && o.idempotency_key.startsWith('wa:') && o.idempotency_key.length <= 300 ? o.idempotency_key : null;
  const hash = typeof o.to_hash === 'string' && HASH_RE.test(o.to_hash) ? o.to_hash : null;
  const fail = (reason: string): ParseOutcome => ({ ok: false, reportable: key && hash ? { key, toHash: hash, reason } : null, reason });
  if (!key || !hash) return fail('invalid_payload');
  if (o.schema_version !== 1) return fail('unsupported_schema');
  if (typeof o.category !== 'string' || !(WA_CATEGORIES as readonly string[]).includes(o.category)) return fail('invalid_category');
  if (!key.endsWith(`:${hash}`)) return fail('invalid_payload');
  if (typeof o.to_e164 !== 'string' || !E164_RE.test(o.to_e164)) return fail('invalid_recipient');
  const expected = Buffer.from(toHash(hashKey, o.to_e164), 'hex');
  if (!timingSafeEqual(expected, Buffer.from(hash, 'hex'))) return fail('hash_mismatch');
  if (typeof o.template !== 'string' || !Object.prototype.hasOwnProperty.call(WA_TEMPLATES, o.template)) return fail('invalid_template');
  const def = WA_TEMPLATES[o.template];
  if (def.category !== o.category) return fail('invalid_template');
  const exp = typeof o.expires_at === 'string' ? Date.parse(o.expires_at) : Number.NaN;
  if (!Number.isFinite(exp)) return fail('invalid_payload');
  const raw = typeof o.params === 'object' && o.params !== null && !Array.isArray(o.params) ? (o.params as Record<string, unknown>) : null;
  if (!raw) return fail('invalid_params');
  const params: Record<string, string> = {};
  for (const k of Object.keys(raw)) if (!def.params.includes(k)) return fail('invalid_params');
  for (const k of def.params) {
    const v = raw[k];
    if (v === undefined || v === null || v === '') continue;
    if (typeof v !== 'string' && (typeof v !== 'number' || !Number.isFinite(v))) return fail('invalid_params');
    // eslint-disable-next-line no-control-regex
    const s = String(v).replace(/[\u0000-\u001f\u007f]+/g, ' ').replace(/ {2,}/g, ' ').trim();
    if (s) params[k] = s.length > PARAM_MAX ? `${s.slice(0, PARAM_MAX - 1)}…` : s;
  }
  for (const k of def.required) if (!(k in params)) return fail('invalid_params');
  return {
    ok: true,
    msg: { idempotency_key: key, to_e164: o.to_e164, to_hash: hash, category: o.category as WaCategory, template: o.template, params, expires_at: exp },
  };
}
