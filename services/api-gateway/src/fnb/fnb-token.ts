import { createHmac, timingSafeEqual } from 'node:crypto';
import { isUuid } from '../common/validation';

/**
 * Token sesi meja QR `fts1` (docs/04 §14.A.2, R-S03, SEC-73/75/77):
 *
 *   fts1.<b64url(session_id 16 byte)>.<exp unix detik>.<b64url(HMAC-SHA256(K, "fts1|" + session_id_hex + "|" + exp))>
 *
 * K = HKDF-SHA256(secret `fnb_table_token_key`, info "efn:fnb_table_token:v1") — diturunkan di `AppConfig`.
 * - Diterbitkan BFF; dapat dihitung ulang dari (session_id, exp) → BFF TIDAK menyimpan token/hash token.
 * - Diperiksa SEBELUM query DB (murah, anti-DoS); semua kegagalan (format, tanda tangan, kedaluwarsa) → hasil `null` yang
 *   SAMA (respons 401 generik, tidak membedakan sebab).
 * - Rotasi: verifikasi mencoba kunci aktif lalu kunci lama (overlap ≤ 4 jam = TTL maksimum).
 * - Token TIDAK PERNAH di-log, tidak di URL path/query (hanya fragment QR → header `X-Table-Session`).
 */
export const FTS_PREFIX = 'fts1';
export const TABLE_SESSION_HEADER = 'x-table-session';
/** Panjang maksimum header yang diproses (token sah = 4 + 1 + 22 + 1 + 10 + 1 + 43 = 82). */
export const MAX_TOKEN_LENGTH = 128;
const TOKEN_RE = /^fts1\.([A-Za-z0-9_-]{22})\.(\d{10})\.([A-Za-z0-9_-]{43})$/;

function sign(key: Buffer, idHex: string, exp: number): Buffer {
  return createHmac('sha256', key).update(`${FTS_PREFIX}|${idHex}|${exp}`, 'utf8').digest();
}

function uuidToHex(id: string): string {
  return id.replace(/-/g, '').toLowerCase();
}

function hexToUuid(hex: string): string {
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

/** Terbitkan token untuk `sessionId` (UUID) dengan `exp` (unix detik). */
export function issueTableToken(key: Buffer, sessionId: string, exp: number): string {
  if (!isUuid(sessionId)) throw new Error('session_id bukan UUID');
  if (!Number.isSafeInteger(exp) || exp < 1_000_000_000 || exp > 9_999_999_999) throw new Error('exp tidak valid');
  const hex = uuidToHex(sessionId);
  const idB64 = Buffer.from(hex, 'hex').toString('base64url');
  return `${FTS_PREFIX}.${idB64}.${exp}.${sign(key, hex, exp).toString('base64url')}`;
}

/**
 * Verifikasi token → `{ sessionId, exp }` atau `null`. `keys` = [aktif, lama?]. Semua kandidat kunci dibandingkan dengan
 * `timingSafeEqual` tanpa short-circuit. Token kedaluwarsa (`exp ≤ now`) → null.
 */
export function verifyTableToken(token: unknown, keys: readonly Buffer[], nowSeconds: number): { sessionId: string; exp: number } | null {
  if (typeof token !== 'string' || token.length === 0 || token.length > MAX_TOKEN_LENGTH) return null;
  const m = TOKEN_RE.exec(token);
  if (!m) return null;
  const id = Buffer.from(m[1], 'base64url');
  const mac = Buffer.from(m[3], 'base64url');
  if (id.length !== 16 || mac.length !== 32) return null;
  // kanonik: b64url ulang harus identik (tolak varian encoding yang di-decode ke byte sama)
  if (id.toString('base64url') !== m[1] || mac.toString('base64url') !== m[3]) return null;
  const exp = Number(m[2]);
  const hex = id.toString('hex');
  let ok = false;
  for (const k of keys) {
    if (timingSafeEqual(sign(k, hex, exp), mac)) ok = true;
  }
  if (!ok || exp <= nowSeconds) return null;
  const sessionId = hexToUuid(hex);
  return isUuid(sessionId) ? { sessionId, exp } : null;
}
