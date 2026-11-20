import { createHmac, randomUUID, timingSafeEqual } from 'node:crypto';
import { isUuid } from '../../common/validation';

/**
 * Token berhenti berlangganan email (docs/04 §28.9, ADR-048 §3.5). Format:
 *
 *     eu1.<base64url(JSON {v:1, m:<member_id>, j:<jti uuid>, e:<exp epoch detik>})>.<base64url(HMAC-SHA256(key, "eu1." + payload))>
 *
 * - `m` = `platform.member.id` (UUID opak, bukan nomor HP/email).
 * - Sekali pakai: `jti` dicatat di `platform.email_unsubscribe_used` dalam transaksi yang sama dengan pencabutan consent.
 * - Kunci `/run/secrets/email_unsubscribe_key` (terpisah dari JWT/OTP; domain separation lewat prefiks `eu1.`).
 * - Token ditaruh di FRAGMEN URL halaman web (`#t=`) agar tidak masuk access log; halaman mem-POST ke
 *   `POST /v1/email/unsubscribe` dengan body JSON.
 */
export const UNSUBSCRIBE_TOKEN_PREFIX = 'eu1';
export const UNSUBSCRIBE_TOKEN_MAX_LENGTH = 512;

export interface UnsubscribeClaims {
  memberId: string;
  jti: string;
  exp: number;
}

function sign(key: Buffer, payload: string): Buffer {
  return createHmac('sha256', key).update(`${UNSUBSCRIBE_TOKEN_PREFIX}.${payload}`, 'utf8').digest();
}

export function signUnsubscribeToken(key: Buffer, memberId: string, nowMs: number, ttlSeconds: number, jti: string = randomUUID()): string {
  if (!isUuid(memberId) || !isUuid(jti)) throw new Error('memberId/jti harus UUID');
  const exp = Math.floor(nowMs / 1000) + ttlSeconds;
  const payload = Buffer.from(JSON.stringify({ v: 1, m: memberId.toLowerCase(), j: jti.toLowerCase(), e: exp }), 'utf8').toString('base64url');
  return `${UNSUBSCRIBE_TOKEN_PREFIX}.${payload}.${sign(key, payload).toString('base64url')}`;
}

/** Klaim bila tanda tangan valid dan belum kedaluwarsa; selain itu `null` (tidak membedakan alasan). */
export function verifyUnsubscribeToken(key: Buffer, token: unknown, nowMs: number): UnsubscribeClaims | null {
  if (typeof token !== 'string' || token.length === 0 || token.length > UNSUBSCRIBE_TOKEN_MAX_LENGTH) return null;
  const parts = token.split('.');
  if (parts.length !== 3 || parts[0] !== UNSUBSCRIBE_TOKEN_PREFIX) return null;
  const [, payload, sigRaw] = parts;
  if (!/^[A-Za-z0-9_-]+$/.test(payload) || !/^[A-Za-z0-9_-]{43}$/.test(sigRaw)) return null;
  const expected = sign(key, payload);
  const given = Buffer.from(sigRaw, 'base64url');
  if (given.length !== expected.length || !timingSafeEqual(given, expected)) return null;
  let obj: unknown;
  try {
    obj = JSON.parse(Buffer.from(payload, 'base64url').toString('utf8'));
  } catch {
    return null;
  }
  if (typeof obj !== 'object' || obj === null) return null;
  const o = obj as Record<string, unknown>;
  if (o.v !== 1 || !isUuid(o.m) || !isUuid(o.j) || typeof o.e !== 'number' || !Number.isSafeInteger(o.e)) return null;
  if (o.e <= Math.floor(nowMs / 1000)) return null;
  return { memberId: o.m.toLowerCase(), jti: o.j.toLowerCase(), exp: o.e };
}
