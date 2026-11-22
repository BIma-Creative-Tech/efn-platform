/**
 * Validator klaim bersama untuk token SSO `eit1` dan cookie sesi `eis1` (docs/04 §26.3-§26.5).
 * Modul murni: tanpa I/O, aman dipakai di proxy.ts (runtime Node) maupun test.
 */

/**
 * Kode PT ber-data ESB (docs/04 §26). PT Supermarket Demo `SMD` (G44, §12.1) SENGAJA tidak di `ESB_CODES`: bukan brand ESB,
 * dan scope Insight supermarket memakai token `eit2`/`eis2` + `wids` (§28.13, G47) — kontrak `eit1` tidak berubah, jadi
 * `parseCodes` (eit1/eis1) tetap menolak `SMD`.
 *
 * Konstanta di bawah IDENTIK dengan Odoo `insight_token.py` (§28.13.3). Menambah PT supermarket = revisi kontrak +
 * konstanta di dua sisi + test, bukan konfigurasi.
 */
export const ESB_CODES = ['BCF', 'BCR', 'CUP', 'IMD', 'PB', 'WTZ'] as const;
export type EsbCode = (typeof ESB_CODES)[number];
export const SM_CODES = ['SMD'] as const;
export type SmCode = (typeof SM_CODES)[number];
export type Code = EsbCode | SmCode;
/** Urut ASCII (BCF,BCR,CUP,IMD,PB,SMD,WTZ) = urutan wajib `codes` di token. */
export const ALL_CODES: readonly Code[] = [...ESB_CODES, ...SM_CODES].sort();
export const PERSONAS = ['category', 'dc', 'exec', 'marketing', 'store'] as const;
export type Persona = (typeof PERSONAS)[number];
export type BaseRole = 'user' | 'all_brands';
export type Role = BaseRole | Persona;

const CODE_SET: ReadonlySet<string> = new Set(ESB_CODES);
const ALL_CODE_SET: ReadonlySet<string> = new Set(ALL_CODES);
const SM_CODE_SET: ReadonlySet<string> = new Set(SM_CODES);
const PERSONA_SET: ReadonlySet<string> = new Set(PERSONAS);
/** Persona yang mendapat baris tingkat PT (`wids` memuat 0), §28.13.3. */
const PT_LEVEL_PERSONAS: ReadonlySet<string> = new Set(['category', 'dc', 'exec', 'marketing']);

export const isEsbCode = (c: string): c is EsbCode => CODE_SET.has(c);
export const isSmCode = (c: string): c is SmCode => SM_CODE_SET.has(c);
export const isPersona = (r: string): r is Persona => PERSONA_SET.has(r);
export const esbCodesOf = (codes: readonly string[]): EsbCode[] => codes.filter(isEsbCode);
export const smCodesOf = (codes: readonly string[]): SmCode[] => codes.filter(isSmCode);
export const personasOf = (roles: readonly string[]): Persona[] => roles.filter(isPersona);

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

/**
 * roles=["user"] wajib punya >= 1 company; all_brands boleh 0 (IMD/CUP belum jadi company, L-120) selama tidak
 * memuat persona (§28.13.4: `cids` >= 1 kecuali `roles` memuat `all_brands` dan tidak memuat persona).
 */
export function cidsMatchRole(cids: readonly number[], roles: readonly Role[]): boolean {
  if (cids.length >= 1) return true;
  return roles.includes('all_brands') && personasOf(roles).length === 0;
}

// ---------------------------------------------------------------- v2 (eit2/eis2, docs/04 §28.13.4)

/** codes v2: string ⊆ ESB_CODES ∪ SM_CODES, unik, urut naik, 1..7 butir. */
export function parseCodesV2(v: unknown): Code[] | null {
  if (!Array.isArray(v) || v.length < 1 || v.length > 7) return null;
  if (!v.every((x) => typeof x === 'string' && ALL_CODE_SET.has(x))) return null;
  const a = v as Code[];
  return strictlyAscending(a) ? a : null;
}

/**
 * roles v2: urut string ASCII naik, unik; tepat satu dari `user`/`all_brands` + 0..5 persona ⊆ PERSONAS
 * (urutan: all_brands < category < dc < exec < marketing < store < user).
 */
export function parseRolesV2(v: unknown): Role[] | null {
  if (!Array.isArray(v) || v.length < 1 || v.length > 6) return null;
  if (!v.every((x) => typeof x === 'string')) return null;
  const a = v as string[];
  if (!strictlyAscending(a)) return null;
  let base = 0;
  for (const r of a) {
    if (r === 'user' || r === 'all_brands') base += 1;
    else if (!isPersona(r)) return null;
  }
  return base === 1 ? (a as Role[]) : null;
}

/** wids: int >= 0, unik, urut naik, 0..200 butir (0 = baris tingkat PT). */
export function parseWids(v: unknown): number[] | null {
  if (!Array.isArray(v) || v.length > 200) return null;
  if (!v.every((x) => isInt(x) && x >= 0 && x <= 4_294_967_295)) return null;
  const a = v as number[];
  return strictlyAscending(a) ? a : null;
}

/** cats: int > 0, unik, urut naik, 0..50 butir. */
export function parseCats(v: unknown): number[] | null {
  if (!Array.isArray(v) || v.length > 50) return null;
  if (!v.every((x) => isInt(x) && x > 0 && x <= 4_294_967_295)) return null;
  const a = v as number[];
  return strictlyAscending(a) ? a : null;
}

/**
 * Aturan konsistensi §28.13.4 (diperiksa pembuat DAN penerima; gagal = `bad_claims`):
 *  1. wids tidak kosong <=> ada persona di roles <=> codes memuat kode SM;
 *  2. 0 ∈ wids <=> roles memuat salah satu {exec, category, dc, marketing};
 *  3. cats tidak kosong => "category" ∈ roles.
 */
export function scopeConsistent(c: {
  codes: readonly string[];
  roles: readonly string[];
  wids: readonly number[];
  cats: readonly number[];
}): boolean {
  const hasPersona = personasOf(c.roles).length > 0;
  const hasSm = smCodesOf(c.codes).length > 0;
  const hasWids = c.wids.length > 0;
  if (hasWids !== hasPersona || hasPersona !== hasSm) return false;
  const wantZero = c.roles.some((r) => PT_LEVEL_PERSONAS.has(r));
  if (c.wids.includes(0) !== wantZero) return false;
  if (c.cats.length > 0 && !c.roles.includes('category')) return false;
  return true;
}

// ---------------------------------------------------------------- v3 (eit3/eis3, docs/04 §28.15.4-§28.15.5, G52)

/** Persona yang punya halaman F&B (§28.15.4). IDENTIK dengan Odoo `insight_token.py`. */
export const FNB_PERSONAS = ['exec', 'marketing', 'store'] as const;
/** Akun ESB yang sah di klaim `brs` (= KNOWN_BRANDS esb-extract). */
export const ESB_BRANDS = ['bacha', 'cup', 'imd', 'pb', 'wtz'] as const;
export const MAX_BRS = 50;
/** `<esb_brand>:<branch_id>`; branch_id 1..4294967295 (batas atas diperiksa terpisah). */
export const BRS_ITEM_RE = /^(bacha|cup|imd|pb|wtz):[1-9][0-9]{0,9}$/;

/** brs: string unik, urut naik ASCII, 0..50 butir, tiap butir cocok BRS_ITEM_RE dan branch_id <= 4294967295. */
export function parseBrs(v: unknown): string[] | null {
  if (!Array.isArray(v) || v.length > MAX_BRS) return null;
  for (const x of v) {
    if (typeof x !== 'string' || !BRS_ITEM_RE.test(x)) return null;
    if (Number(x.slice(x.indexOf(':') + 1)) > 4_294_967_295) return null;
  }
  const a = v as string[];
  return strictlyAscending(a) ? a : null;
}

/**
 * Aturan konsistensi v3 T1..T7 (§28.15.5; diperiksa pembuat DAN penerima; gagal = `bad_claims`). T8 (ukuran payload) dijamin
 * regex token/cookie. `P = roles ∩ PERSONAS`, `ESB = codes ∩ ESB_CODES`, `SM = codes ∩ SM_CODES`.
 */
export function scopeConsistentV3(c: {
  codes: readonly string[];
  roles: readonly string[];
  wids: readonly number[];
  cats: readonly number[];
  brs: readonly string[];
}): boolean {
  const p = new Set(personasOf(c.roles));
  const hasP = p.size > 0;
  const hasSm = smCodesOf(c.codes).length > 0;
  const hasEsb = esbCodesOf(c.codes).length > 0;
  // T1
  if ((c.wids.length > 0) !== hasSm) return false;
  if (hasSm && !hasP) return false;
  // T2
  if (hasSm && c.wids.includes(0) !== c.roles.some((r) => PT_LEVEL_PERSONAS.has(r))) return false;
  // T3
  if ((p.has('category') || p.has('dc')) && !hasSm) return false;
  // T4
  if (hasP && !hasSm && !hasEsb) return false;
  // T5 (L-245)
  if (p.has('store') && Array.from(p).some((r) => r !== 'exec' && r !== 'store')) return false;
  // T6
  const branchScoped = p.has('store') && !p.has('exec') && !c.roles.includes('all_brands') && hasEsb;
  if ((c.brs.length > 0) !== branchScoped) return false;
  // T7
  if (c.cats.length > 0 && !p.has('category')) return false;
  return true;
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
