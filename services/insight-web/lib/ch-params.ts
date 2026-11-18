/**
 * Serialisasi parameter ClickHouse HTTP (`param_<nama>`) dan perakitan query string request.
 * Nilai di-parse ClickHouse sesuai tipe placeholder `{nama:Tipe}` (bukan interpolasi string ke SQL).
 * Defensif ganda: nilai diperiksa lagi di sini (karakter kontrol, backslash, panjang) walau sudah divalidasi filter.
 */
import 'server-only';
import { isIsoDate } from './dates';
import { PARAM_TYPES, QUERIES, type ChType, type ParamName, type QueryName } from './queries';
import type { ScopeSettings } from './scope';

export class ParamError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'ParamError';
  }
}

export type ParamValue = string | number | readonly string[];
export type ParamBag = Partial<Record<ParamName, ParamValue>>;

const BAD_CHARS = /[\u0000-\u001f\u007f\\]/;
const MAX_STR = 128;
const MAX_ITEMS = 100;

function checkStr(s: unknown): string {
  if (typeof s !== 'string' || s.length > MAX_STR || BAD_CHARS.test(s)) {
    throw new ParamError('nilai parameter tidak valid');
  }
  return s;
}

export function serializeParam(type: ChType, value: ParamValue): string {
  switch (type) {
    case 'Date': {
      if (!isIsoDate(value)) throw new ParamError('tanggal tidak valid');
      return value;
    }
    case 'UInt8': {
      if (value !== 0 && value !== 1 && value !== '0' && value !== '1') throw new ParamError('UInt8 tidak valid');
      return String(value);
    }
    case 'String': {
      const s = checkStr(value);
      if (s.includes("'")) throw new ParamError('nilai parameter tidak valid');
      return s;
    }
    case 'Array(String)': {
      if (typeof value === 'string' || typeof value === 'number' || value.length > MAX_ITEMS) {
        throw new ParamError('array tidak valid');
      }
      const items = value.map((v) => `'${checkStr(v).replace(/'/g, "\\'")}'`);
      return `[${items.join(',')}]`;
    }
  }
}

/** Pengaturan HTTP tetap per request (docs/design/insight-web-queries.md §1.6). */
export const CH_FIXED_SETTINGS: Record<string, string> = {
  database: 'efn_insight',
  default_format: 'JSON',
  output_format_json_quote_64bit_integers: '0',
  max_execution_time: '30',
  // Galat di tengah eksekusi menjadi status HTTP error, bukan 200 + teks galat di badan.
  wait_end_of_query: '1',
};

/**
 * Query string request: pengaturan tetap + SQL_efn_* (cakupan dari sesi) + param_* HANYA untuk parameter yang
 * dideklarasikan katalog (parameter berlebih dari pemanggil diabaikan; parameter wajib hilang -> ParamError).
 */
export function buildChSearchParams(name: QueryName, scope: ScopeSettings, bag: ParamBag): URLSearchParams {
  const def = QUERIES[name];
  const sp = new URLSearchParams(CH_FIXED_SETTINGS);
  sp.set('SQL_efn_cids', scope.SQL_efn_cids);
  sp.set('SQL_efn_codes', scope.SQL_efn_codes);
  sp.set('SQL_efn_sig', scope.SQL_efn_sig);
  for (const p of def.params) {
    const v = bag[p];
    if (v === undefined) throw new ParamError(`parameter wajib hilang: ${p}`);
    sp.set(`param_${p}`, serializeParam(PARAM_TYPES[p], v));
  }
  return sp;
}
