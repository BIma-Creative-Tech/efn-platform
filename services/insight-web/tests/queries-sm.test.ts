import { describe, expect, it } from 'vitest';
import { buildSmSearchParams, serializeSmParam } from '@/lib/ch-params-sm';
import { ParamError } from '@/lib/ch-params';
import { SM_PARAM_TYPES, SM_QUERIES, type SmParamName, type SmQueryName } from '@/lib/queries-sm';
import { scopeSettingsSm } from '@/lib/scope';
import { K_ANON } from '@/lib/sm-defaults';
import { KEY } from './helpers';

const names = Object.keys(SM_QUERIES) as SmQueryName[];
const PLACEHOLDER = /\{(\w+):([A-Za-z0-9()]+)\}/g;
const ROW_POLICY_VIEWS = [
  'warehouse_list', 'product_list', 'sales_daily', 'outlet_daily', 'margin_daily', 'gmroi_monthly', 'waste_daily',
  'forecast_accuracy_weekly', 'stock_daily', 'oos_product_daily', 'stock_value_daily', 'stock_critical', 'replenishment',
  'replenishment_line', 'replenishment_bias_weekly', 'dc_fill_rate', 'dc_purchase', 'lot_expiry', 'expiry_daily', 'markdown',
  'price_index', 'price_coverage', 'price_signal', 'visitor_funnel_daily', 'search_terms', 'segment_daily', 'campaign_result',
  'freshness', 'kpi_config',
  // G51 (docs/04 §28.14.3): tiga view baru + v_sm_lot_stock (semua lot berstok, keputusan lead 21 Nov)
  'payment_daily', 'product_sales_daily', 'forecast_daily', 'lot_stock',
];

describe('katalog query supermarket (docs/04 §28.13.9)', () => {
  it.each(names)('%s: placeholder = params terdeklarasi bertipe, tanpa interpolasi, tanpa titik koma', (n) => {
    const def = SM_QUERIES[n];
    const found = new Map<string, string>();
    for (const m of def.sql.matchAll(PLACEHOLDER)) found.set(m[1], m[2]);
    expect(Array.from(found.keys()).sort()).toEqual([...def.params].sort());
    for (const [p, t] of found) expect(SM_PARAM_TYPES[p as SmParamName]).toBe(t);
    expect(def.sql).not.toContain('${');
    expect(def.sql.trim().endsWith(';')).toBe(false);
  });

  it.each(names)('%s: hanya view efn_insight.v_sm_* yang dikenal kontrak', (n) => {
    const tables = Array.from(SM_QUERIES[n].sql.matchAll(/(?:FROM|JOIN)\s+([\w.]+)/g)).map((m) => m[1]);
    expect(tables.length).toBeGreaterThan(0);
    for (const t of tables) {
      expect(t).toMatch(/^efn_insight\.v_sm_[a-z_]+$/);
      expect(ROW_POLICY_VIEWS).toContain(t.replace('efn_insight.v_sm_', ''));
    }
  });

  it.each(names)('%s: lapis 1 company_id (kecuali kesegaran kelas F) dan warehouse_id', (n) => {
    const sql = SM_QUERIES[n].sql;
    if (n === 'sm_freshness') {
      expect(SM_QUERIES[n].params).toEqual([]);
      return;
    }
    expect(sql).toContain('company_id IN {cids:Array(UInt32)}');
    const warehouseBased = !['sm_categories', 'sm_kpi_config', 'sm_product_names', 'sm_shelf_products'].includes(n);
    if (warehouseBased) {
      expect(/warehouse_id IN \{(wh|whs|whz):Array\(UInt32\)\}|warehouse_id = 0/.test(sql)).toBe(true);
    }
  });

  it.each(names)('%s: non-agregat tunggal wajib LIMIT literal; tidak ada LIMIT berparameter', (n) => {
    const sql = SM_QUERIES[n].sql;
    expect(sql).not.toMatch(/LIMIT\s*\{/);
    if (/GROUP BY|ORDER BY/.test(sql)) expect(sql).toMatch(/LIMIT \d+\s*$/);
  });

  it('alias agregat tidak sama dengan kolom view (ClickHouse akan menafsirkan ulang alias di WHERE)', () => {
    const clash = /\b(?:sum|sumIf|any|count|countIf|uniqExact|uniqExactIf|max|min)\([^)]*\)\s+AS\s+(net_sales|gross_margin|waste_value_cost|qty_approved|viewers|purchasers|day|month|warehouse_id|company_id|delivery_date|week_start|categ_id|cogs)\b/;
    for (const n of names) expect(clash.test(SM_QUERIES[n].sql), n).toBe(false);
  });

  it('view berbiaya (margin, GMROI, stok total, PO) tidak dipakai query halaman Kepala toko', () => {
    const storeQueries: SmQueryName[] = ['sm_st_proposals', 'sm_st_expiry', 'sm_st_expiry_trend', 'sm_st_critical', 'sm_waste_sums', 'sm_waste_reasons', 'sm_sales_net'];
    for (const n of storeQueries) {
      expect(SM_QUERIES[n].sql).not.toMatch(/v_sm_(margin_daily|gmroi_monthly|stock_value_daily|dc_purchase)/);
    }
  });

  it('Omzet memakai net_sales_incl_tax; margin memakai net_sales tanpa PPN (L-202)', () => {
    expect(SM_QUERIES.sm_ex_sales.sql).toContain('net_sales_incl_tax');
    expect(SM_QUERIES.sm_ex_trend.sql).toContain('net_sales_incl_tax');
    expect(SM_QUERIES.sm_ex_margin.sql).not.toContain('incl_tax');
    expect(SM_QUERIES.sm_sales_net.sql).not.toContain('incl_tax');
  });

  it('G51: omzet SMD di halaman F&B memakai kolom termasuk PPN; HPP dari view margin tanpa PPN', () => {
    expect(SM_QUERIES.sm_fb_daily.sql).toContain('net_sales_incl_tax');
    expect(SM_QUERIES.sm_fb_weekday.sql).toContain('net_sales_incl_tax');
    expect(SM_QUERIES.sm_fb_by_category.sql).toContain('net_sales_incl_tax');
    expect(SM_QUERIES.sm_fb_top_products.sql).toContain('net_sales_incl_tax');
    expect(SM_QUERIES.sm_fb_top_products.sql).not.toMatch(/cogs/);
  });

  it('G51: query halaman F&B tidak memakai parameter filter ESB (pt/branch string) dan hanya parameter bertipe SM', () => {
    const g51 = names.filter((n) => /^sm_(fb|fc|shelf|dist)_/.test(n));
    expect(g51.length).toBe(18);
    for (const n of g51) {
      expect(SM_QUERIES[n].sql).not.toMatch(/\{(pt|branch|product|sort):/);
      for (const p of SM_QUERIES[n].params) expect(Object.keys(SM_PARAM_TYPES)).toContain(p);
    }
  });

  it('G51 B4: umur simpan membaca v_sm_lot_stock dan shelf_life_days dari v_sm_product_list', () => {
    expect(SM_QUERIES.sm_shelf_lots.sql).toContain('efn_insight.v_sm_lot_stock');
    expect(SM_QUERIES.sm_shelf_lots.sql).toContain('quantity > 0');
    expect(SM_QUERIES.sm_shelf_products.sql).toContain('shelf_life_days');
    expect(SM_QUERIES.sm_shelf_products.params).toContain('ids');
  });

  it('G51 B3: forecast 14 hari difilter tanggal [today, until] dan kolom pita p10/p90', () => {
    const sql = SM_QUERIES.sm_fc_next.sql;
    expect(sql).toContain('forecast_date BETWEEN {today:Date} AND {until:Date}');
    expect(sql).toContain('p10');
    expect(sql).toContain('p90');
  });

  it('k-anonim literal di query sinyal sama dengan K_ANON', () => {
    expect(K_ANON).toBe(10);
    expect(SM_QUERIES.sm_cm_signal.sql).toContain(`viewers_30d >= ${K_ANON}`);
  });
});

describe('serializeSmParam dan buildSmSearchParams', () => {
  it('Array(UInt32), Date, String, Float64, UInt32', () => {
    expect(serializeSmParam('Array(UInt32)', [1, 22, 0])).toBe('[1,22,0]');
    expect(serializeSmParam('Array(UInt32)', [])).toBe('[]');
    expect(serializeSmParam('Date', '2026-11-21')).toBe('2026-11-21');
    expect(serializeSmParam('String', 'web')).toBe('web');
    expect(serializeSmParam('Float64', 10.5)).toBe('10.5');
    expect(serializeSmParam('UInt32', 3)).toBe('3');
  });
  it('menolak nilai tidak sah', () => {
    for (const [t, v] of [
      ['Array(UInt32)', [-1]],
      ['Array(UInt32)', [1.5]],
      ['Array(UInt32)', [4294967296]],
      ['Array(UInt32)', 'x'],
      ['Array(UInt32)', Array.from({ length: 301 }, (_, i) => i)],
      ['Date', '2026-02-30'],
      ['String', "a'b"],
      ['String', 'a\\b'],
      ['Float64', Number.NaN],
      ['UInt32', -1],
    ] as const) {
      expect(() => serializeSmParam(t, v as never), `${t}:${String(v).slice(0, 20)}`).toThrow(ParamError);
    }
  });
  it('merakit setting cakupan sig2 + hanya param terdeklarasi; param wajib hilang -> ParamError', () => {
    const scope = scopeSettingsSm({ cids: [11], codes: ['SMD'], roles: ['store', 'user'], wids: [22], cats: [] }, KEY);
    const sp = buildSmSearchParams('sm_st_proposals', scope, { cids: [11], wh: [22], today: '2026-11-21', until: '2026-11-28', channel: 'x' });
    expect(sp.get('SQL_efn_wids')).toBe('22');
    expect(sp.get('SQL_efn_personas')).toBe('store');
    expect(sp.get('SQL_efn_cats')).toBe('');
    expect(sp.get('SQL_efn_sig2')).toBe(scope.SQL_efn_sig2);
    expect(sp.get('param_wh')).toBe('[22]');
    expect(sp.get('param_channel')).toBeNull(); // berlebih diabaikan
    expect(sp.get('database')).toBe('efn_insight');
    expect(() => buildSmSearchParams('sm_st_proposals', scope, { cids: [11] })).toThrow(ParamError);
  });
});
