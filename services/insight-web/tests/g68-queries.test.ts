/**
 * Katalog query G68 (docs/04 §32.10): 15 query bernama (`pnl_*`, `waste_*`, `fr_*`), pemeriksaan statis (parameter terdeklarasi,
 * view yang dibaca = §32.6/§32.7, tanpa view internal, filter PT lapis 1). Tidak menjalankan ClickHouse: SQL belum dieksekusi
 * terhadap ClickHouse 24.8 dan view baru dibangun paralel oleh paket W-A (lihat README dan laporan).
 */
import { describe, expect, it } from 'vitest';
import { buildChSearchParams } from '@/lib/ch-params';
import { plKeySql } from '@/lib/pnl';
import { PARAM_TYPES, QUERIES, type QueryName } from '@/lib/queries';
import { scopeSettings } from '@/lib/scope';
import { KEY } from './helpers-v3';

const PNL = ['pnl_kpi', 'pnl_trend_monthly', 'pnl_statement', 'pnl_branch_rank', 'pnl_accounts'] as const;
const WASTE = ['waste_kpi', 'waste_trend', 'waste_by_purpose', 'waste_branch_rank', 'waste_top_products'] as const;
const FR = ['fr_kpi', 'fr_trend_weekly', 'fr_by_outlet', 'fr_by_supplier', 'fr_suppliers', 'fr_top_short_products'] as const;
const ALL = [...PNL, ...WASTE, ...FR] as const;

const PLACEHOLDER = /\{(\w+):([A-Za-z0-9()]+)\}/g;
const sqlOf = (n: QueryName): string => QUERIES[n].sql;
const viewsOf = (n: QueryName): string[] => Array.from(new Set(Array.from(sqlOf(n).matchAll(/FROM\s+([\w.]+)/g)).map((m) => m[1])));

describe('katalog query G68', () => {
  it('memuat tepat 16 query G68 (15 kontrak §32.10 + opsi pemasok)', () => {
    expect(ALL).toHaveLength(16);
    for (const n of ALL) expect(Object.keys(QUERIES), n).toContain(n);
    const g68 = Object.keys(QUERIES).filter((n) => /^(pnl|waste|fr)_/.test(n));
    expect(g68.sort()).toEqual([...ALL].sort());
  });

  it.each(ALL)('%s: placeholder = params terdeklarasi bertipe benar, filter PT lapis 1, tanpa interpolasi, LIMIT literal', (n) => {
    const def = QUERIES[n];
    const found = new Map<string, string>();
    for (const m of def.sql.matchAll(PLACEHOLDER)) found.set(m[1], m[2]);
    expect(Array.from(found.keys()).sort()).toEqual([...def.params].sort());
    for (const [p, t] of found) expect(PARAM_TYPES[p as keyof typeof PARAM_TYPES], `${n}:${p}`).toBe(t);
    expect(def.sql).toContain('has({pt:Array(String)}, company_code)');
    expect(def.sql).not.toContain('${');
    expect(def.sql).not.toMatch(/LIMIT\s*\{/);
    if (/GROUP BY|DISTINCT/.test(def.sql)) expect(def.sql).toMatch(/LIMIT \d+\s*$/);
    expect(def.sql.trim().endsWith(';')).toBe(false);
  });

  it('parameter baru bertipe benar', () => {
    expect(PARAM_TYPES.m_to).toBe('Date');
    expect(PARAM_TYPES.uclass).toBe('Array(String)');
    expect(PARAM_TYPES.supplier).toBe('Array(String)');
  });

  it('hanya membaca view efn_insight yang dibungkus §32.7 (tanpa v_esb_fill_rate_line / v_esb_g68_quality / tabel dasar)', () => {
    const ok = new Set([
      'efn_insight.v_esb_pl_monthly',
      'efn_insight.v_esb_item_usage_daily',
      'efn_insight.v_esb_item_usage_product_monthly',
      'efn_insight.v_esb_fill_rate_daily',
      'efn_insight.v_esb_fill_rate_product_monthly',
      'efn_insight.v_esb_sales_branch_daily',
    ]);
    for (const n of ALL) {
      expect(viewsOf(n).length, n).toBeGreaterThan(0);
      for (const v of viewsOf(n)) expect(ok.has(v), `${n}: ${v}`).toBe(true);
    }
    for (const n of ALL) expect(sqlOf(n), n).not.toMatch(/v_esb_g68_quality|v_esb_fill_rate_line\b|fact_esb_|efn_dwh/);
  });

  it('pemetaan query -> view sesuai §32.10', () => {
    for (const n of PNL) expect(viewsOf(n), n).toEqual(['efn_insight.v_esb_pl_monthly']);
    for (const n of ['waste_trend', 'waste_by_purpose'] as const) expect(viewsOf(n), n).toEqual(['efn_insight.v_esb_item_usage_daily']);
    expect(viewsOf('waste_kpi').sort()).toEqual(['efn_insight.v_esb_item_usage_daily', 'efn_insight.v_esb_sales_branch_daily']);
    expect(viewsOf('waste_branch_rank').sort()).toEqual(['efn_insight.v_esb_item_usage_daily', 'efn_insight.v_esb_sales_branch_daily']);
    expect(viewsOf('waste_top_products')).toEqual(['efn_insight.v_esb_item_usage_product_monthly']);
    for (const n of ['fr_kpi', 'fr_trend_weekly', 'fr_by_outlet', 'fr_by_supplier', 'fr_suppliers'] as const) {
      expect(viewsOf(n), n).toEqual(['efn_insight.v_esb_fill_rate_daily']);
    }
    expect(viewsOf('fr_top_short_products')).toEqual(['efn_insight.v_esb_fill_rate_product_monthly']);
  });

  it('P&L: baris summary + scope branch (KPI/tren/peringkat), account (akun), brand hanya di pnl_statement; kunci pos dari lib/pnl', () => {
    for (const n of ['pnl_kpi', 'pnl_trend_monthly', 'pnl_branch_rank'] as const) {
      expect(sqlOf(n), n).toContain("line_type = 'summary' AND scope = 'branch'");
      expect(sqlOf(n), n).toContain(plKeySql());
    }
    expect(sqlOf('pnl_statement')).toContain("line_type = 'summary'");
    expect(sqlOf('pnl_statement')).toMatch(/GROUP BY period_month, scope, section/);
    expect(sqlOf('pnl_accounts')).toContain("line_type = 'account'");
    expect(sqlOf('pnl_accounts')).toMatch(/LIMIT 50 BY section\s+LIMIT 600$/);
    expect(sqlOf('pnl_branch_rank')).toMatch(/ORDER BY net_sales DESC\s+LIMIT 500$/);
    expect(sqlOf('pnl_kpi')).toContain('period_month BETWEEN {yoy_from:Date} AND {yoy_to:Date}');
    expect(plKeySql()).toMatch(/'net_sales'/);
    expect(plKeySql()).toMatch(/'net_profit'/);
  });

  it('waste: kelas pemakaian lewat parameter; KPI memakai kelas tetap; qty per satuan; produk ≤ 50', () => {
    for (const n of ['waste_trend', 'waste_by_purpose', 'waste_top_products'] as const) expect(sqlOf(n), n).toContain('has({uclass:Array(String)}, usage_class)');
    expect(sqlOf('waste_kpi')).toContain("usage_class = 'waste'");
    expect(sqlOf('waste_kpi')).toContain("usage_class = 'internal_use'");
    expect(sqlOf('waste_kpi')).not.toContain('uclass');
    expect(sqlOf('waste_trend')).toContain('toStartOfWeek(journal_date, 1)');
    expect(sqlOf('waste_top_products')).toMatch(/GROUP BY brand, product_code, uom/);
    expect(sqlOf('waste_top_products')).toMatch(/LIMIT 50$/);
    expect(sqlOf('waste_branch_rank')).toContain('UNION ALL');
  });

  it('fill rate: rasio dari kolom aditif (bukan rata-rata rasio), pemasok brand:branch_id, produk ≤ 50 urut short_qty', () => {
    expect(sqlOf('fr_kpi')).toContain('line_ratio_sum');
    expect(sqlOf('fr_kpi')).toContain('lines_full');
    expect(sqlOf('fr_kpi')).toContain('requests_full');
    expect(sqlOf('fr_kpi')).toContain('lead_days_sum');
    expect(sqlOf('fr_kpi')).toContain('lead_days_n');
    for (const n of ['fr_kpi', 'fr_trend_weekly', 'fr_by_outlet', 'fr_by_supplier'] as const) {
      expect(sqlOf(n), n).toContain("has({supplier:Array(String)}, concat(brand, ':', toString(supplier_branch_id)))");
    }
    expect(sqlOf('fr_suppliers')).not.toContain('{supplier:');
    expect(sqlOf('fr_top_short_products')).toMatch(/ORDER BY short_qty DESC\s+LIMIT 50$/);
    expect(sqlOf('fr_top_short_products')).toMatch(/GROUP BY brand, product_code, uom/);
    expect(sqlOf('fr_top_short_products')).toContain('sent_capped');
  });

  it('alias keluaran tidak sama dengan nama kolom view (alias siklik ClickHouse)', () => {
    const cyc = /\b(?:sum|any|min|max|sumIf)\([^)]*\)\s+AS\s+(amount|net|value_net|qty_out|request_qty|qty_sent|lines|requests|docs|branch_name|supplier_name|product_name)\b/;
    for (const n of ALL) expect(cyc.test(sqlOf(n)), n).toBe(false);
  });
});

describe('buildChSearchParams untuk query G68', () => {
  const scope = scopeSettings([5], ['PB'], [], KEY);
  const base = { from: '2026-11-01', to: '2026-11-21', prev_from: '2026-10-11', prev_to: '2026-10-31', pt: ['PB'], branch: [] as string[] };

  it('pnl_kpi: m_from/m_to/yoy_* wajib dan bertipe Date', () => {
    const bag = { ...base, m_from: '2025-12-01', m_to: '2026-11-01', yoy_from: '2024-12-01', yoy_to: '2025-11-01' };
    const sp = buildChSearchParams('pnl_kpi', scope, bag);
    expect(sp.get('param_m_to')).toBe('2026-11-01');
    expect(() => buildChSearchParams('pnl_kpi', scope, { ...bag, m_to: "2026-11-01'; DROP" })).toThrow();
    expect(() => buildChSearchParams('pnl_kpi', scope, { ...base })).toThrow();
  });
  it('waste_trend: uclass array bertipe; kelas dengan kutip ditolak/di-escape', () => {
    const sp = buildChSearchParams('waste_trend', scope, { ...base, weekly: 1, uclass: ['waste', 'internal_use'] });
    expect(sp.get('param_uclass')).toBe("['waste','internal_use']");
    expect(sp.get('param_weekly')).toBe('1');
    expect(() => buildChSearchParams('waste_trend', scope, { ...base, weekly: 2, uclass: ['waste'] })).toThrow();
    expect(() => buildChSearchParams('waste_trend', scope, { ...base, weekly: 0, uclass: ['a\\b'] })).toThrow();
  });
  it('fr_kpi: supplier array; parameter wajib hilang -> galat', () => {
    expect(buildChSearchParams('fr_kpi', scope, { ...base, supplier: ['pb:12'] }).get('param_supplier')).toBe("['pb:12']");
    expect(buildChSearchParams('fr_kpi', scope, { ...base, supplier: [] }).get('param_supplier')).toBe('[]');
    expect(() => buildChSearchParams('fr_kpi', scope, base)).toThrow();
  });
});
