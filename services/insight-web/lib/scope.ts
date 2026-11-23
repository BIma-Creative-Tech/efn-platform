/**
 * Setting cakupan ClickHouse (docs/04 §26.7, pola text2sql §23.3, kunci BERBEDA = insight_scope_key):
 *   SQL_efn_cids = cids urut naik dipisah koma (boleh kosong untuk all_brands tanpa company)
 *   SQL_efn_codes = kode PT urut naik dipisah koma
 *   key = byte ASCII string hex (bukan hex-decode). Tanda tangan view ESB: lihat `scopeSettings` (sig3, §28.15.6).
 * Dihitung dari cookie sesi terverifikasi; tidak pernah dari input browser.
 */
import { ALL_CODES, ESB_CODES, PERSONAS, SM_CODES, parseBrs, personasOf, smCodesOf } from './claims';
import { sha256Hex } from './hmac';
import type { Session } from './session';

/**
 * Setting cakupan view SM (docs/04 §28.13.6). Semua nilai dihitung dari sesi eis2 terverifikasi:
 *   SQL_efn_cids     = cids sesi (boleh memuat id company ESB; aman karena wids hanya warehouse SM)
 *   SQL_efn_codes    = codes ∩ SM_CODES            (tidak kosong)
 *   SQL_efn_wids     = wids sesi                   (tidak kosong; 0 = baris tingkat PT)
 *   SQL_efn_personas = roles ∩ PERSONAS, urut naik (tidak kosong)
 *   SQL_efn_cats     = cats sesi                   (boleh string kosong)
 *   SQL_efn_sig2     = lower(hex(SHA256(KEY|cids|codes|wids|personas|cats)))  KEY = byte ASCII string hex insight_scope_key
 */
export interface ScopeSettingsSm {
  SQL_efn_cids: string;
  SQL_efn_codes: string;
  SQL_efn_wids: string;
  SQL_efn_personas: string;
  SQL_efn_cats: string;
  SQL_efn_sig2: string;
}

const isUint = (n: number): boolean => Number.isSafeInteger(n) && n >= 0 && n <= 4_294_967_295;

export function scopeSettingsSm(
  s: Pick<Session, 'cids' | 'codes' | 'roles' | 'wids' | 'cats'>,
  key: string,
): ScopeSettingsSm {
  const codes = Array.from(new Set(smCodesOf(s.codes))).sort();
  const personas = Array.from(new Set(personasOf(s.roles))).sort();
  if (codes.length === 0 || codes.some((c) => !(SM_CODES as readonly string[]).includes(c))) throw new Error('scope: tanpa kode SM');
  if (personas.length === 0 || personas.some((p) => !(PERSONAS as readonly string[]).includes(p))) throw new Error('scope: tanpa persona');
  if (s.wids.length === 0) throw new Error('scope: wids kosong');
  if (s.cids.length === 0 || !s.cids.every((n) => isUint(n) && n > 0)) throw new Error('scope: company id tidak valid');
  if (!s.wids.every(isUint) || !s.cats.every((n) => isUint(n) && n > 0)) throw new Error('scope: id tidak valid');
  const asc = (a: readonly number[]): string => Array.from(new Set(a)).sort((x, y) => x - y).join(',');
  const cids = asc(s.cids);
  const wids = asc(s.wids);
  const cats = asc(s.cats);
  const codesStr = codes.join(',');
  const personasStr = personas.join(',');
  return {
    SQL_efn_cids: cids,
    SQL_efn_codes: codesStr,
    SQL_efn_wids: wids,
    SQL_efn_personas: personasStr,
    SQL_efn_cats: cats,
    SQL_efn_sig2: sha256Hex(`${key}|${cids}|${codesStr}|${wids}|${personasStr}|${cats}`),
  };
}

/**
 * Setting cakupan view ESB `efn_insight.v_esb_*` (docs/04 §28.15.6, G52; menggantikan `SQL_efn_sig` v1 untuk Insight):
 *   SQL_efn_brs  = `brs` sesi digabung koma, urutan token (boleh string kosong)
 *   SQL_efn_sig3 = lower(hex(SHA256(KEY + "|" + cids + "|" + codes + "|" + brs)))   KEY = byte ASCII string hex
 * `codes` = codes ∩ ESB_CODES. Format `brs` dijaga ulang di sini (regex yang sama dengan row policy; urut naik ASCII).
 */
/**
 * Setting cakupan view Pengunjung `efn_insight.v_vis_*` (V-01, 23 Nov 2026; policy access/insight-visitor-policy.sh):
 *   SQL_efn_vcodes = kode PT pengunjung sesi (lib/visitor.ts visitorCodes), urut naik dipisah koma, tidak kosong
 *   SQL_efn_vsig   = lower(hex(SHA256(KEY + "|visitor|" + vcodes)))   KEY = byte ASCII string hex insight_scope_key
 * Domain tanda tangan sendiri (label "visitor"), jadi tidak bisa dipakai untuk view ESB/SM dan sebaliknya.
 */
export interface ScopeSettingsVisitor {
  SQL_efn_vcodes: string;
  SQL_efn_vsig: string;
}

export function scopeSettingsVisitor(codes: readonly string[], key: string): ScopeSettingsVisitor {
  const allowed: ReadonlySet<string> = new Set(ALL_CODES);
  const uniq = Array.from(new Set(codes)).sort();
  if (uniq.length === 0 || uniq.some((c) => !allowed.has(c))) throw new Error('scope: kode PT pengunjung tidak valid');
  const vcodes = uniq.join(',');
  return { SQL_efn_vcodes: vcodes, SQL_efn_vsig: sha256Hex(`${key}|visitor|${vcodes}`) };
}

export interface ScopeSettings {
  SQL_efn_cids: string;
  SQL_efn_codes: string;
  SQL_efn_brs: string;
  SQL_efn_sig3: string;
}

export function scopeSettings(cids: readonly number[], codes: readonly string[], brs: readonly string[], key: string): ScopeSettings {
  const allowed: ReadonlySet<string> = new Set(ESB_CODES);
  for (const c of codes) {
    if (!allowed.has(c)) throw new Error('scope: kode PT tidak dikenal');
  }
  for (const id of cids) {
    if (!Number.isSafeInteger(id) || id <= 0) throw new Error('scope: company id tidak valid');
  }
  if (parseBrs(brs) === null) throw new Error('scope: cabang tidak valid');
  const cidsStr = Array.from(new Set(cids)).sort((a, b) => a - b).join(',');
  const codesStr = Array.from(new Set(codes)).sort().join(',');
  const brsStr = brs.join(',');
  return {
    SQL_efn_cids: cidsStr,
    SQL_efn_codes: codesStr,
    SQL_efn_brs: brsStr,
    SQL_efn_sig3: sha256Hex(`${key}|${cidsStr}|${codesStr}|${brsStr}`),
  };
}
