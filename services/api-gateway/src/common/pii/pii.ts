import { createHash, createHmac } from 'node:crypto';

/**
 * Utilitas data pribadi (UU PDP 27/2022, R-L01).
 * - `member_ref` = nomor HP E.164 tanpa `+` (kontrak §1). **Hanya nomor Indonesia** `^62[0-9]{8,13}$`
 *   (kontrak §3.1 v2, SEC-06: mencegah WhatsApp authentication pumping ke nomor internasional).
 * - Di log: selalu `maskMsisdn` → `62812****890`.
 * - Di DB/analitik: `memberHash` = sha256(pepper + member_ref) hex (pepper turunan HKDF, lihat AppConfig).
 * - Nilai asli yang perlu dipulihkan (penerima notifikasi) disimpan terenkripsi — lihat `crypto.ts`.
 */
export const MEMBER_REF_RE = /^62[0-9]{8,13}$/;
const MEMBER_HASH_RE = /^[0-9a-f]{64}$/;

/** Normalisasi input nomor HP ke E.164 tanpa `+`. `08xx` → `628xx`. Nomor non-Indonesia → null. */
export function normalizeMsisdn(input: unknown): string | null {
  if (typeof input !== 'string' || input.length > 32) return null;
  let s = input.replace(/[\s\-().]/g, '');
  if (s.startsWith('+')) s = s.slice(1);
  else if (s.startsWith('00')) s = s.slice(2);
  else if (s.startsWith('0')) s = `62${s.slice(1)}`;
  return MEMBER_REF_RE.test(s) ? s : null;
}

export function isMemberRef(value: unknown): value is string {
  return typeof value === 'string' && MEMBER_REF_RE.test(value);
}

export function isMemberHash(value: unknown): value is string {
  return typeof value === 'string' && MEMBER_HASH_RE.test(value);
}

export function maskMsisdn(msisdn: string): string {
  if (typeof msisdn !== 'string' || msisdn.length < 9) return '****';
  return `${msisdn.slice(0, 5)}****${msisdn.slice(-3)}`;
}

export function memberHash(pepper: string, memberRef: string): string {
  return createHash('sha256').update(pepper + memberRef, 'utf8').digest('hex');
}

/**
 * SEC-24: pseudonim untuk NAMA key Redis (IP klien, dsb.) = hex(HMAC-SHA256(redisKeyPepper, value)) dipotong 32 hex
 * (128 bit). `redisKeyPepper` = turunan HKDF `efn:redis_key:v1` (AppConfig) — berbeda dari pepper member_hash,
 * sehingga key Redis tidak bisa di-join dengan data analitik dan nilai mentah tidak tersimpan di AOF Redis.
 */
export function redisKeyHash(pepper: Buffer, value: string): string {
  return createHmac('sha256', pepper).update(value, 'utf8').digest('hex').slice(0, 32);
}

/** Tombstone pengganti member_hash pada baris yang dianonimkan (PDP_DSAR_DESIGN §5): sha256("erased:" || request_id). */
export function erasureTombstone(requestId: string): string {
  return createHash('sha256').update(`erased:${requestId}`, 'utf8').digest('hex');
}
