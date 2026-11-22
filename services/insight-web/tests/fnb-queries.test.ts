/**
 * Katalog query F&B persona (docs/04 §28.15.9, G52): 18 query bernama `fexec_*`/`fstore_*`/`fmkt_*`, pemeriksaan statis
 * (parameter terdeklarasi, view yang dibaca, tanpa cogs/mdr di query Toko, kelas view PT vs BR). Tidak menjalankan ClickHouse:
 * SQL belum dieksekusi terhadap ClickHouse 24.8 (lihat README).
 */
import { describe, expect, it } from 'vitest';
import { buildChSearchParams } from '@/lib/ch-params';
import { PARAM_TYPES, QUERIES, type QueryName } from '@/lib/queries';
import { scopeSettings } from '@/lib/scope';
import { KEY } from './helpers-v3';

const FEXEC = ['fexec_kpi', 'fexec_margin_monthly', 'fexec_pt_compare', 'fexec_mdr_cost', 'fexec_branch_rank', 'fexec_yoy_coverage'] as const;
const FSTORE = ['fstore_kpi', 'fstore_daily_vs_lastweek', 'fstore_top_menu', 'fstore_payment_mix', 'fstore_ck_receipts', 'fstore_branch_list'] as const;
const FMKT = ['fmkt_discount_kpi', 'fmkt_discount_trend', 'fmkt_discount_menu', 'fmkt_discount_branch', 'fmkt_channel_mix', 'fmkt_takeaway_share'] as const;
const ALL = [...FEXEC, ...FSTORE, ...FMKT] as const;

const PLACEHOLDER = /\{(\w+):([A-Za-z0-9()]+)\}/g;
const sqlOf = (n: QueryName): string => QUERIES[n].sql;
const viewsOf = (n: QueryName): string[] => Array.from(sqlOf(n).matchAll(/FROM\s+([\w.]+)/g)).map((m) => m[1]);

describe('katalog query F&B persona', () => {
  it('memuat tepat 18 query G52 dengan nama sesuai kontrak', () => {
    expect(ALL).toHaveLength(18);
    for (const n of ALL) expect(Object.keys(QUERIES), n).toContain(n);
    const g52 = Object.keys(QUERIES).filter((n) => /^(fexec|fstore|fmkt)_/.test(n));
    expect(g52.sort()).toEqual([...ALL].sort());
  });

  it.each(ALL)('%s: placeholder = params terdeklarasi dengan tipe benar, filter PT lapis 1, tanpa interpolasi, LIMIT literal', (n) => {
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

  it('parameter G52 bertipe Date dan terdaftar', () => {
    for (const p of ['yoy_from', 'yoy_to', 'm_from', 'ref_day'] as const) expect(PARAM_TYPES[p]).toBe('Date');
  });

  it('semua query hanya membaca view efn_insight.v_esb_* (tidak ada tabel dwh/mart langsung)', () => {
    for (const n of ALL) {
      expect(viewsOf(n).length, n).toBeGreaterThan(0);
      for (const v of viewsOf(n)) expect(v, n).toMatch(/^efn_insight\.v_esb_[a-z_]+$/);
    }
  });

  it('alias keluaran tidak sama dengan nama kolom view (alias siklik ClickHouse)', () => {
    const cyc = /\b(?:sum|any|min|max|sumIf)\([^)]*\)\s+AS\s+(net|cogs|gross|discount|qty|amount|mdr|trx_count|payment_method|menu_name|branch_name|product_name|first_day)\b/;
    for (const n of ALL) expect(cyc.test(sqlOf(n)), n).toBe(false);
  });
});

describe('query Toko (kelas BR, tanpa HPP/margin/MDR; L-244, Q-INS-F3)', () => {
  it.each(FSTORE)('%s: tanpa cogs, mdr, margin, hpp, dan hanya view toko atau daftar cabang', (n) => {
    const sql = sqlOf(n).toLowerCase();
    for (const bad of ['cogs', 'mdr', 'margin', 'hpp', ' gm', 'v_esb_sales_daily', 'v_esb_sales_branch_daily', 'v_esb_payment_daily', 'v_esb_movement_daily']) {
      expect(sql, `${n}: ${bad}`).not.toContain(bad);
    }
    for (const v of viewsOf(n)) expect(v, n).toMatch(/^efn_insight\.v_esb_(store_(sales|menu|payment|receipt)_daily|branch_list)$/);
  });

  it('pemetaan query -> view toko sesuai §28.15.9', () => {
    expect(viewsOf('fstore_kpi')).toEqual(['efn_insight.v_esb_store_sales_daily']);
    expect(viewsOf('fstore_daily_vs_lastweek')).toEqual(['efn_insight.v_esb_store_sales_daily']);
    expect(viewsOf('fstore_top_menu')).toEqual(['efn_insight.v_esb_store_menu_daily']);
    expect(viewsOf('fstore_payment_mix')).toEqual(['efn_insight.v_esb_store_payment_daily']);
    expect(viewsOf('fstore_ck_receipts')).toEqual(['efn_insight.v_esb_store_receipt_daily']);
    expect(viewsOf('fstore_branch_list')).toEqual(['efn_insight.v_esb_branch_list']);
  });

  it('fstore_kpi: hari acuan vs 7 hari sebelumnya; daily: 14 hari; top menu LIMIT 20 urut qty; resi CK LIMIT 500 dan uom di GROUP BY', () => {
    expect(sqlOf('fstore_kpi')).toContain('subtractDays({ref_day:Date}, 7)');
    expect(sqlOf('fstore_daily_vs_lastweek')).toContain('subtractDays({ref_day:Date}, 13)');
    expect(sqlOf('fstore_top_menu')).toMatch(/ORDER BY qty_sold DESC\s+LIMIT 20$/);
    expect(sqlOf('fstore_ck_receipts')).toMatch(/GROUP BY day, branch_id, origin_branch_name, item_group, product_code, uom/);
    expect(sqlOf('fstore_ck_receipts')).toMatch(/LIMIT 500$/);
  });

  it('fstore_branch_list hanya cabang toko aktif; query data toko memakai makro cabang BR', () => {
    expect(sqlOf('fstore_branch_list')).toContain('is_store = 1 AND active = 1');
    for (const n of FSTORE.filter((x) => x !== 'fstore_branch_list')) {
      expect(sqlOf(n), n).toContain("concat(brand, ':', toString(branch_id))");
    }
  });
});

describe('query Eksekutif dan Marketing (kelas PT)', () => {
  it('pemetaan query -> view sesuai §28.15.9', () => {
    for (const n of ['fexec_kpi', 'fexec_margin_monthly', 'fexec_pt_compare', 'fexec_branch_rank', 'fmkt_discount_kpi', 'fmkt_discount_trend', 'fmkt_discount_branch'] as const) {
      expect(viewsOf(n), n).toContain('efn_insight.v_esb_sales_branch_daily');
    }
    expect(viewsOf('fexec_mdr_cost')).toEqual(['efn_insight.v_esb_payment_daily']);
    expect(viewsOf('fmkt_channel_mix')).toEqual(['efn_insight.v_esb_payment_daily']);
    expect(viewsOf('fexec_yoy_coverage')).toEqual(['efn_insight.v_esb_coverage']);
    expect(viewsOf('fmkt_discount_menu')).toEqual(['efn_insight.v_esb_sales_daily']);
    expect(viewsOf('fmkt_takeaway_share')).toEqual(['efn_insight.v_esb_sales_daily']);
  });

  it('margin kotor = net - cogs; YoY memakai rentang yoy_from..yoy_to; 13 bulan memakai m_from; cakupan YoY entity sales', () => {
    expect(sqlOf('fexec_kpi')).toContain('sumIf(net - cogs');
    expect(sqlOf('fexec_kpi')).toContain('sales_date BETWEEN {yoy_from:Date} AND {yoy_to:Date}');
    expect(sqlOf('fexec_margin_monthly')).toContain('sales_date BETWEEN {m_from:Date} AND {to:Date}');
    expect(sqlOf('fexec_margin_monthly')).toContain('toStartOfMonth(sales_date)');
    expect(sqlOf('fexec_yoy_coverage')).toContain("entity = 'sales'");
  });

  it('peringkat cabang: urut gm turun, LIMIT 500, hanya cabang penjual; ≤ 100 menu diskon; tebakan takeaway dari is_takeaway', () => {
    expect(sqlOf('fexec_branch_rank')).toMatch(/ORDER BY gm DESC\s+LIMIT 500$/);
    expect(sqlOf('fexec_branch_rank')).toContain('FROM efn_insight.v_esb_branch_list WHERE is_store = 1 OR is_ck = 1');
    expect(sqlOf('fmkt_discount_menu')).toMatch(/LIMIT 100$/);
    expect(sqlOf('fmkt_takeaway_share')).toContain('is_takeaway');
  });

  it('MDR hanya di query Eksekutif; query Marketing tidak membaca mdr atau cogs', () => {
    expect(sqlOf('fexec_mdr_cost')).toContain('mdr');
    for (const n of FMKT) {
      expect(sqlOf(n), n).not.toContain('mdr');
      expect(sqlOf(n), n).not.toContain('cogs');
    }
  });
});

describe('buildChSearchParams untuk query F&B', () => {
  const scope = scopeSettings([5], ['PB'], ['pb:7'], KEY);
  it('fstore_kpi: param_ref_day terserialisasi sebagai Date, cakupan brs dikirim', () => {
    const sp = buildChSearchParams('fstore_kpi', scope, { ref_day: '2026-11-21', pt: ['PB'], branch: ['pb:7'] });
    expect(sp.get('param_ref_day')).toBe('2026-11-21');
    expect(sp.get('param_branch')).toBe("['pb:7']");
    expect(sp.get('SQL_efn_brs')).toBe('pb:7');
  });
  it('fexec_kpi: param yoy_from/yoy_to wajib; nilai bukan tanggal ditolak', () => {
    const base = { from: '2026-11-01', to: '2026-11-21', pt: ['PB'], branch: [] as string[] };
    expect(() => buildChSearchParams('fexec_kpi', scope, base)).toThrow();
    expect(buildChSearchParams('fexec_kpi', scope, { ...base, yoy_from: '2025-11-01', yoy_to: '2025-11-21' }).get('param_yoy_to')).toBe('2025-11-21');
    expect(() => buildChSearchParams('fexec_kpi', scope, { ...base, yoy_from: "2025-11-01'; DROP", yoy_to: '2025-11-21' })).toThrow();
  });
});
