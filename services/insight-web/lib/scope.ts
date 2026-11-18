/**
 * Setting cakupan ClickHouse (docs/04 §26.7, pola text2sql §23.3, kunci BERBEDA = insight_scope_key):
 *   SQL_efn_cids = cids urut naik dipisah koma (boleh kosong untuk all_brands tanpa company)
 *   SQL_efn_codes = kode PT urut naik dipisah koma
 *   SQL_efn_sig  = hex huruf kecil SHA256(key + "|" + cids + "|" + codes); key = byte ASCII string hex (bukan hex-decode)
 * Dihitung dari cookie sesi terverifikasi; tidak pernah dari input browser.
 */
import { ESB_CODES } from './claims';
import { sha256Hex } from './hmac';

export interface ScopeSettings {
  SQL_efn_cids: string;
  SQL_efn_codes: string;
  SQL_efn_sig: string;
}

export function scopeSettings(cids: readonly number[], codes: readonly string[], key: string): ScopeSettings {
  const allowed: ReadonlySet<string> = new Set(ESB_CODES);
  for (const c of codes) {
    if (!allowed.has(c)) throw new Error('scope: kode PT tidak dikenal');
  }
  for (const id of cids) {
    if (!Number.isSafeInteger(id) || id <= 0) throw new Error('scope: company id tidak valid');
  }
  const cidsStr = Array.from(new Set(cids)).sort((a, b) => a - b).join(',');
  const codesStr = Array.from(new Set(codes)).sort().join(',');
  return {
    SQL_efn_cids: cidsStr,
    SQL_efn_codes: codesStr,
    SQL_efn_sig: sha256Hex(`${key}|${cidsStr}|${codesStr}`),
  };
}
