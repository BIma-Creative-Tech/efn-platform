import { createHash, createHmac, timingSafeEqual } from 'node:crypto';

/**
 * Kunci HMAC (insight_sso_key / insight_session_key / insight_scope_key), docs/04 §26.3:
 * KEY = byte ASCII dari string hex di file secret apa adanya setelah strip() -- BUKAN hasil hex-decode.
 * Validasi: hanya [0-9a-fA-F], panjang >= 64 karakter. Mengembalikan null bila tidak valid (fail closed).
 */
const KEY_RE = /^[0-9a-fA-F]{64,}$/;

export function parseKey(raw: string | null | undefined): string | null {
  if (typeof raw !== 'string') return null;
  const k = raw.trim();
  return KEY_RE.test(k) ? k : null;
}

export function hmacSha256(key: string, message: string): Buffer {
  return createHmac('sha256', Buffer.from(key, 'ascii')).update(message, 'ascii').digest();
}

export function hmacSha256B64Url(key: string, message: string): string {
  return hmacSha256(key, message).toString('base64url');
}

/**
 * Bandingkan tanda tangan base64url (43 karakter = 32 byte) dengan HMAC-SHA256(key, message) memakai
 * timingSafeEqual. Penulisan non-kanonik (bit sisa base64url) ditolak.
 */
export function verifyHmacB64Url(key: string, message: string, sigB64: string): boolean {
  const got = Buffer.from(sigB64, 'base64url');
  if (got.length !== 32 || got.toString('base64url') !== sigB64) return false;
  const want = hmacSha256(key, message);
  return timingSafeEqual(got, want);
}

export function sha256Hex(s: string): string {
  return createHash('sha256').update(s, 'utf8').digest('hex');
}
