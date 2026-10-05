/**
 * ALTCHA (SEC-06, docs/04 §8.1) — validasi BENTUK challenge & payload. MURNI (server + client, diuji unit).
 *
 * Ini bukan verifikasi kriptografis: proof-of-work & HMAC (`altcha_hmac_key`) hanya diverifikasi BFF,
 * termasuk sekali-pakai (`altcha:used:<sig>`). Tujuan di sini: menolak sampah lebih awal, membatasi ukuran,
 * dan memastikan Route Handler hanya meneruskan field yang dikenal (tanpa field asing dari BFF/klien).
 */

const ALGORITHMS = new Set(['SHA-256', 'SHA-384', 'SHA-512']);
const HEX_RE = /^[0-9a-f]{16,256}$/i;
const BASE64_RE = /^[A-Za-z0-9+/]+={0,2}$/;
/** Payload widget ±200–400 byte; batas longgar namun tegas. */
export const ALTCHA_PAYLOAD_MAX_LEN = 4096;

export interface AltchaChallenge {
  algorithm: string;
  challenge: string;
  salt: string;
  signature: string;
  maxnumber?: number;
}

function isSafeString(v: unknown, max: number): v is string {
  return typeof v === 'string' && v.length > 0 && v.length <= max;
}

/** Ambil hanya field challenge ALTCHA yang dikenal; null bila bentuk tidak sesuai. */
export function sanitizeAltchaChallenge(value: unknown): AltchaChallenge | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const v = value as Record<string, unknown>;
  if (!isSafeString(v.algorithm, 16) || !ALGORITHMS.has(v.algorithm)) return null;
  if (!isSafeString(v.challenge, 256) || !HEX_RE.test(v.challenge)) return null;
  // salt ALTCHA dapat memuat parameter query (mis. `?expires=...`), jadi tidak dibatasi ke hex.
  if (!isSafeString(v.salt, 512)) return null;
  if (!isSafeString(v.signature, 256) || !HEX_RE.test(v.signature)) return null;
  const out: AltchaChallenge = {
    algorithm: v.algorithm,
    challenge: v.challenge,
    salt: v.salt,
    signature: v.signature,
  };
  if (v.maxnumber !== undefined) {
    if (typeof v.maxnumber !== 'number' || !Number.isSafeInteger(v.maxnumber) || v.maxnumber <= 0) return null;
    out.maxnumber = v.maxnumber;
  }
  return out;
}

function decodeBase64(s: string): string | null {
  try {
    return atob(s);
  } catch {
    return null;
  }
}

/**
 * Payload dari widget = base64(JSON { algorithm, challenge, number, salt, signature, took? }).
 * true bila bentuknya masuk akal untuk diteruskan ke BFF.
 */
export function isPlausibleAltchaPayload(value: unknown): value is string {
  if (typeof value !== 'string') return false;
  if (value.length < 16 || value.length > ALTCHA_PAYLOAD_MAX_LEN) return false;
  if (!BASE64_RE.test(value)) return false;
  const decoded = decodeBase64(value);
  if (!decoded) return false;
  let obj: unknown;
  try {
    obj = JSON.parse(decoded);
  } catch {
    return false;
  }
  if (!obj || typeof obj !== 'object' || Array.isArray(obj)) return false;
  const o = obj as Record<string, unknown>;
  return (
    isSafeString(o.algorithm, 16) &&
    ALGORITHMS.has(o.algorithm) &&
    isSafeString(o.challenge, 256) &&
    HEX_RE.test(o.challenge) &&
    typeof o.number === 'number' &&
    Number.isSafeInteger(o.number) &&
    o.number >= 0 &&
    isSafeString(o.salt, 512) &&
    isSafeString(o.signature, 256) &&
    HEX_RE.test(o.signature)
  );
}
