/**
 * Perakitan query string ClickHouse untuk katalog Pengunjung (V-01). Pola sama dengan lib/ch-params.ts (serializer yang
 * sama; nilai diperiksa ganda) + setting cakupan SQL_efn_vcodes/SQL_efn_vsig dari SESI (lib/scope.ts scopeSettingsVisitor).
 */
import 'server-only';
import { CH_FIXED_SETTINGS, ParamError, serializeParam, type ParamValue } from './ch-params';
import { VIS_PARAM_TYPES, VIS_QUERIES, type VisParamName, type VisQueryName } from './queries-visitor';
import type { ScopeSettingsVisitor } from './scope';

export type VisParamBag = Partial<Record<VisParamName, ParamValue>>;

/** Pengaturan tetap + cakupan + param_* HANYA untuk parameter yang dideklarasikan katalog (wajib hilang -> ParamError). */
export function buildVisSearchParams(name: VisQueryName, scope: ScopeSettingsVisitor, bag: VisParamBag): URLSearchParams {
  const def = VIS_QUERIES[name];
  const sp = new URLSearchParams(CH_FIXED_SETTINGS);
  sp.set('SQL_efn_vcodes', scope.SQL_efn_vcodes);
  sp.set('SQL_efn_vsig', scope.SQL_efn_vsig);
  for (const p of def.params) {
    const v = bag[p];
    if (v === undefined) throw new ParamError(`parameter wajib hilang: ${p}`);
    sp.set(`param_${p}`, serializeParam(VIS_PARAM_TYPES[p], v));
  }
  return sp;
}
