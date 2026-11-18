import { describe, expect, it } from 'vitest';
import { buildChSearchParams, CH_FIXED_SETTINGS, ParamError, serializeParam } from '@/lib/ch-params';
import { PARAM_TYPES, QUERIES, type QueryName } from '@/lib/queries';
import { scopeSettings } from '@/lib/scope';

const names = Object.keys(QUERIES) as QueryName[];
const PLACEHOLDER = /\{(\w+):([A-Za-z0-9()]+)\}/g;

describe('katalog query', () => {
  it('memuat query kanonik docs/04 §26.8', () => {
    for (const n of [
      'exec_kpi', 'exec_trend_daily', 'exec_branch_rank', 'flow_summary', 'flow_by_branch', 'flow_by_product', 'flow_outliers',
      'sales_by_weekday', 'sales_by_category', 'sales_channel_split', 'sales_top_menu', 'menu_mix_by_branch',
      'pay_by_method', 'pay_trend', 'dist_ck_delivery', 'dist_transfer',
      'dq_freshness', 'dq_last_run', 'dq_alias_unmapped', 'dq_non_ea', 'dq_store_no_sales',
      'list_branches', 'list_categories', 'list_products',
    ]) {
      expect(names).toContain(n);
    }
  });

  it.each(names)('%s: placeholder = params terdeklarasi, tipe sesuai, filter PT lapis 1, LIMIT literal', (n) => {
    const def = QUERIES[n];
    const found = new Map<string, string>();
    for (const m of def.sql.matchAll(PLACEHOLDER)) found.set(m[1], m[2]);
    expect(Array.from(found.keys()).sort()).toEqual([...def.params].sort());
    for (const [p, t] of found) expect(PARAM_TYPES[p as keyof typeof PARAM_TYPES]).toBe(t);
    expect(def.sql).toContain('has({pt:Array(String)}, company_code)');
    expect(def.sql).not.toContain('${');
    expect(def.sql).not.toMatch(/LIMIT\s*\{/);
    // Agregat tunggal (tanpa GROUP BY/DISTINCT) = 1 baris; selebihnya wajib LIMIT literal.
    if (/GROUP BY|DISTINCT/.test(def.sql)) expect(def.sql).toMatch(/LIMIT \d+\s*$/);
    expect(def.sql.trim().endsWith(';')).toBe(false);
  });

  it('semua query membaca hanya view efn_insight.v_esb_*', () => {
    for (const n of names) {
      const tables = Array.from(QUERIES[n].sql.matchAll(/FROM\s+([\w.]+)/g)).map((m) => m[1]);
      for (const t of tables) expect(t).toMatch(/^efn_insight\.v_esb_[a-z_]+$/);
    }
  });

  it('KPI transaksi memakai v_esb_sales_branch_daily (Q-INS2)', () => {
    expect(QUERIES.exec_kpi.sql).toContain('v_esb_sales_branch_daily');
    expect(QUERIES.exec_trend_daily.sql).toContain('v_esb_sales_branch_daily');
  });
});

describe('serializeParam', () => {
  it('Array(String): kutip dan escape', () => {
    expect(serializeParam('Array(String)', ['PB', 'WTZ'])).toBe("['PB','WTZ']");
    expect(serializeParam('Array(String)', [])).toBe('[]');
    expect(serializeParam('Array(String)', ["Chef's"])).toBe("['Chef\\'s']");
  });
  it('menolak backslash, kontrol, terlalu panjang, bukan array', () => {
    expect(() => serializeParam('Array(String)', ['a\\b'])).toThrow(ParamError);
    expect(() => serializeParam('Array(String)', ['a\nb'])).toThrow(ParamError);
    expect(() => serializeParam('Array(String)', ['a'.repeat(129)])).toThrow(ParamError);
    expect(() => serializeParam('Array(String)', 'PB')).toThrow(ParamError);
  });
  it('Date / UInt8 / String', () => {
    expect(serializeParam('Date', '2026-11-17')).toBe('2026-11-17');
    expect(() => serializeParam('Date', "2026-11-17'; DROP")).toThrow(ParamError);
    expect(() => serializeParam('Date', '2026-02-31')).toThrow(ParamError);
    expect(serializeParam('UInt8', 1)).toBe('1');
    expect(() => serializeParam('UInt8', 2)).toThrow(ParamError);
    expect(serializeParam('String', 'net')).toBe('net');
    expect(() => serializeParam('String', "a'b")).toThrow(ParamError);
  });
});

describe('buildChSearchParams', () => {
  const scope = scopeSettings([1], ['PB'], 'ab'.repeat(32));
  const bag = { from: '2026-10-18', to: '2026-11-16', pt: ['PB'], branch: [], extra: 'x' } as never;

  it('pengaturan tetap + SQL_efn_* + hanya param terdeklarasi', () => {
    const sp = buildChSearchParams('exec_branch_rank', scope, bag);
    for (const [k, v] of Object.entries(CH_FIXED_SETTINGS)) expect(sp.get(k)).toBe(v);
    expect(sp.get('database')).toBe('efn_insight');
    expect(sp.get('output_format_json_quote_64bit_integers')).toBe('0');
    expect(sp.get('SQL_efn_cids')).toBe('1');
    expect(sp.get('SQL_efn_codes')).toBe('PB');
    expect(sp.get('SQL_efn_sig')).toBe(scope.SQL_efn_sig);
    expect(sp.get('param_from')).toBe('2026-10-18');
    expect(sp.get('param_pt')).toBe("['PB']");
    expect(sp.get('param_branch')).toBe('[]');
    expect(sp.has('param_extra')).toBe(false);
    expect(sp.has('param_cat')).toBe(false);
  });

  it('parameter wajib hilang -> ParamError', () => {
    expect(() => buildChSearchParams('exec_branch_rank', scope, { from: '2026-10-18' })).toThrow(ParamError);
  });
});
