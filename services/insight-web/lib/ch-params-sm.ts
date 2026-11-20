/**
 * Serialisasi parameter ClickHouse HTTP untuk katalog supermarket + perakitan query string (docs/04 §28.13.6).
 * Pola sama dengan lib/ch-params.ts; tipe tambahan: Array(UInt32), Float64, UInt32. Nilai diperiksa ganda di sini
 * walau sudah divalidasi lib/sm-filters.ts.
 */
import 'server-only';
import { CH_FIXED_SETTINGS, ParamError } from './ch-params';
import { isIsoDate } from './dates';
import { SM_PARAM_TYPES, SM_QUERIES, type SmChType, type SmParamName, type SmQueryName } from './queries-sm';
import type { ScopeSettingsSm } from './scope';

export type SmParamValue = string | number | readonly number[];
export type SmParamBag = Partial<Record<SmParamName, SmParamValue>>;

const MAX_ITEMS = 300;
const U32_MAX = 4_294_967_295;
const BAD_CHARS = /[\u0000-\u001f\u007f\\']/;

export function serializeSmParam(type: SmChType, value: SmParamValue): string {
  switch (type) {
    case 'Date': {
      if (!isIsoDate(value)) throw new ParamError('tanggal tidak valid');
      return value;
    }
    case 'UInt32': {
      if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < 0 || value > U32_MAX) throw new ParamError('UInt32 tidak valid');
      return String(value);
    }
    case 'Float64': {
      if (typeof value !== 'number' || !Number.isFinite(value)) throw new ParamError('Float64 tidak valid');
      return String(value);
    }
    case 'String': {
      if (typeof value !== 'string' || value.length > 64 || BAD_CHARS.test(value)) throw new ParamError('nilai parameter tidak valid');
      return value;
    }
    case 'Array(UInt32)': {
      if (typeof value === 'string' || typeof value === 'number' || value.length > MAX_ITEMS) throw new ParamError('array tidak valid');
      for (const n of value) {
        if (!Number.isSafeInteger(n) || n < 0 || n > U32_MAX) throw new ParamError('array tidak valid');
      }
      return `[${value.join(',')}]`;
    }
  }
}

/**
 * Query string request: pengaturan tetap + SQL_efn_* (cakupan dari sesi) + param_* HANYA untuk parameter yang
 * dideklarasikan katalog (berlebih diabaikan; wajib hilang -> ParamError).
 */
export function buildSmSearchParams(name: SmQueryName, scope: ScopeSettingsSm, bag: SmParamBag): URLSearchParams {
  const def = SM_QUERIES[name];
  const sp = new URLSearchParams(CH_FIXED_SETTINGS);
  sp.set('SQL_efn_cids', scope.SQL_efn_cids);
  sp.set('SQL_efn_codes', scope.SQL_efn_codes);
  sp.set('SQL_efn_wids', scope.SQL_efn_wids);
  sp.set('SQL_efn_personas', scope.SQL_efn_personas);
  sp.set('SQL_efn_cats', scope.SQL_efn_cats);
  sp.set('SQL_efn_sig2', scope.SQL_efn_sig2);
  for (const p of def.params) {
    const v = bag[p];
    if (v === undefined) throw new ParamError(`parameter wajib hilang: ${p}`);
    sp.set(`param_${p}`, serializeSmParam(SM_PARAM_TYPES[p], v));
  }
  return sp;
}
