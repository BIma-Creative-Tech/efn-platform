/**
 * Validator klaim bersama untuk token SSO `eit1` dan cookie sesi `eis1` (docs/04 §26.3-§26.5).
 * Modul murni: tanpa I/O, aman dipakai di proxy.ts (runtime Node) maupun test.
 */

/**
 * Kode PT ber-data ESB (docs/04 §26). PT Supermarket Demo `SMD` (G44, §12.1) SENGAJA tidak di sini: bukan brand ESB, dan
 * scope Insight supermarket memakai token `eit2`/`eis2` + `wids` (§28.11, L-162, G47) — kontrak `eit1` tidak berubah.
 */
export const ESB_CODES = ['BCF', 'BCR', 'CUP', 'IMD', 'PB', 'WTZ'] as const;
export type EsbCode = (typeof ESB_CODES)[number];
export type Role = 'user' | 'all_brands';

const CODE_SET: ReadonlySet<string> = new Set(ESB_CODES);

export function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v);
}

/** Objek harus memuat TEPAT kunci yang disebut (tidak lebih, tidak kurang). */
export function hasExactKeys(o: Record<string, unknown>, keys: readonly string[]): boolean {
  const own = Object.keys(o);
  return own.length === keys.length && keys.every((k) => Object.prototype.hasOwnProperty.call(o, k));
}

export function isInt(v: unknown): v is number {
  return typeof v === 'number' && Number.isSafeInteger(v);
}

function strictlyAscending<T extends number | string>(a: readonly T[]): boolean {
  for (let i = 1; i < a.length; i += 1) {
    if (!(a[i - 1] < a[i])) return false;
  }
  return true;
}

/** cids: array int > 0, unik, urut naik, 0..32 butir. */
export function parseCids(v: unknown): number[] | null {
  if (!Array.isArray(v) || v.length > 32) return null;
  if (!v.every((x) => isInt(x) && x > 0)) return null;
  const a = v as number[];
  return strictlyAscending(a) ? a : null;
}

/** codes: string ⊆ ESB_CODES, unik, urut naik, 1..6 butir. */
export function parseCodes(v: unknown): EsbCode[] | null {
  if (!Array.isArray(v) || v.length < 1 || v.length > ESB_CODES.length) return null;
  if (!v.every((x) => typeof x === 'string' && CODE_SET.has(x))) return null;
  const a = v as EsbCode[];
  return strictlyAscending(a) ? a : null;
}

/** roles: tepat ["user"] atau ["all_brands"]. */
export function parseRoles(v: unknown): Role[] | null {
  if (!Array.isArray(v) || v.length !== 1) return null;
  if (v[0] === 'user') return ['user'];
  if (v[0] === 'all_brands') return ['all_brands'];
  return null;
}

/** roles=["user"] wajib punya >= 1 company; all_brands boleh 0 (IMD/CUP belum jadi company, L-120). */
export function cidsMatchRole(cids: readonly number[], roles: readonly Role[]): boolean {
  return roles[0] === 'all_brands' || cids.length >= 1;
}

const CONTROL_RE = /[\p{Cc}\p{Cf}\p{Zl}\p{Zp}]/gu;

/**
 * Nama tampilan: string <= 64 code point (Odoo sudah memotong). Karakter kontrol dibuang lagi di sisi
 * penerima (HMAC menjamin integritas; ini hanya pengaman tampilan). Kosong -> "-".
 */
export function parseName(v: unknown): string | null {
  if (typeof v !== 'string') return null;
  if (Array.from(v).length > 64) return null;
  const clean = v.replace(CONTROL_RE, '').replace(/\s+/g, ' ').trim();
  return clean === '' ? '-' : clean;
}

export function isSub(v: unknown): v is number {
  return isInt(v) && v >= 1 && v <= 2 ** 31 - 1;
}
