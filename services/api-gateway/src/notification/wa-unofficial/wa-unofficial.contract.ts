import { createHmac } from 'node:crypto';

/**
 * Kontrak queue WA unofficial (docs/04 §28.7, ADR-044 §3, L-158/L-159). Salinan sisi service ada di
 * `services/wa-unofficial/src/contract.ts` — UBAH KEDUANYA BERSAMAAN (test kontrak di kedua sisi memakai vektor sama).
 *
 * `to_e164` = digit E.164 TANPA `+` (format `member_ref`, `^62[0-9]{8,13}$`); `to_hash = hex(HMAC-SHA256(kunci
 * wa_unofficial_hash_key, to_e164))`. (Klarifikasi kontrak: §28.7 tidak menyebut ada/tidaknya `+`.)
 */
export const WA_QUEUE = 'notify.wa_unofficial';
export const WA_RESULT_QUEUE = 'notify.wa_unofficial.result';
export const WA_RESULT_CONSUMER = 'bff.wa-unofficial-result';
/** Publisher sah `notify.wa_unofficial.result` (user RMQ service terisolasi). */
export const WA_RESULT_PUBLISHERS: readonly string[] = ['efn-wa-unofficial'];

export const WA_CATEGORIES = ['promo', 'notify_restock', 'price_alert'] as const;
export type WaCategory = (typeof WA_CATEGORIES)[number];

export function isWaCategory(v: unknown): v is WaCategory {
  return typeof v === 'string' && (WA_CATEGORIES as readonly string[]).includes(v);
}

/** Template per kategori + parameter (teks dirender di service; BFF hanya memvalidasi allowlist). */
export const WA_TEMPLATES: Readonly<Record<string, { category: WaCategory; params: readonly string[]; required: readonly string[] }>> = {
  promo_generic: { category: 'promo', params: ['title', 'body', 'valid_to', 'voucher_code'], required: ['title', 'body'] },
  markdown_near_expiry: { category: 'promo', params: ['product_name', 'outlet_name', 'valid_to'], required: ['product_name', 'outlet_name'] },
  restock_available: { category: 'notify_restock', params: ['product_name', 'outlet_name'], required: ['product_name', 'outlet_name'] },
  price_drop: { category: 'price_alert', params: ['product_name', 'new_price_idr'], required: ['product_name', 'new_price_idr'] },
};

export const WA_RESULT_STATUSES = ['sent', 'failed', 'rate_limited', 'banned', 'disconnected'] as const;
export type WaResultStatus = (typeof WA_RESULT_STATUSES)[number];

/** Batas `expiration` AMQP (§28.7: ≤ 24 jam). */
export const WA_MAX_EXPIRATION_MS = 24 * 3600 * 1000;

export interface NotifyWaMessage {
  schema_version: 1;
  idempotency_key: string;
  to_e164: string;
  to_hash: string;
  category: WaCategory;
  template: string;
  params: Record<string, string>;
  expires_at: string;
}

export interface NotifyWaResultMessage {
  schema_version: 1;
  ref_idempotency_key: string | null;
  kind: 'result' | 'opt_out';
  status?: WaResultStatus;
  /** Tambahan G50-B (usulan kontrak): kode alasan singkat, mis. `expired`, `invalid_category`, `not_on_whatsapp`. */
  reason?: string;
  to_hash: string;
  at: string;
}

export function waToHash(key: Buffer, toE164: string): string {
  return createHmac('sha256', key).update(toE164, 'utf8').digest('hex');
}

export function waIdempotencyKey(source: string, sourceRef: string, toHash: string): string {
  return `wa:${source}:${sourceRef}:${toHash}`;
}

export const WA_SOURCE_RE = /^[a-z_]{1,32}$/;
export const WA_SOURCE_REF_RE = /^[A-Za-z0-9:_.-]{1,128}$/;
const PARAM_MAX = 300;

export class WaContractError extends Error {}

/** Normalisasi parameter (string/angka, tanpa karakter kontrol, dipotong); kunci di luar template ditolak. */
export function normalizeWaParams(template: string, params: Readonly<Record<string, unknown>>): Record<string, string> {
  if (!Object.prototype.hasOwnProperty.call(WA_TEMPLATES, template)) throw new WaContractError('template WA tidak dikenal');
  const def = WA_TEMPLATES[template];
  for (const k of Object.keys(params)) if (!def.params.includes(k)) throw new WaContractError(`parameter WA tidak dikenal: ${k}`);
  const out: Record<string, string> = {};
  for (const k of def.params) {
    const v = params[k];
    if (v === undefined || v === null || v === '') continue;
    if (typeof v !== 'string' && (typeof v !== 'number' || !Number.isFinite(v))) throw new WaContractError(`parameter WA tidak valid: ${k}`);
    // eslint-disable-next-line no-control-regex
    const s = String(v).replace(/[\u0000-\u001f\u007f]+/g, ' ').replace(/ {2,}/g, ' ').trim();
    if (s.length === 0) continue;
    out[k] = s.length > PARAM_MAX ? `${s.slice(0, PARAM_MAX - 1)}…` : s;
  }
  for (const k of def.required) if (!(k in out)) throw new WaContractError(`parameter WA wajib: ${k}`);
  return out;
}
