/** Forecast F&B (ESB): katalog query fnb_fc_* (statis) + parsing INSIGHT_FNB_DC_CODES + eksklusi gudang pusat. */
import { describe, expect, it } from 'vitest';
import { buildChSearchParams } from '@/lib/ch-params';
import { parseConfig } from '@/lib/config';
import { DC_LABEL, isDcCode, parseDcCodes, splitDc, sumNonDc } from '@/lib/fnb-forecast';
import { ACTIONS, odooListUrl } from '@/lib/odoo-links';
import { PARAM_TYPES, QUERIES, type QueryName } from '@/lib/queries';
import { scopeSettings } from '@/lib/scope';
import { KEY } from './helpers-v3';

const FC = ['fnb_fc_next', 'fnb_fc_by_wh', 'fnb_fc_top_products'] as const;
const sqlOf = (n: QueryName): string => QUERIES[n].sql;
const PLACEHOLDER = /\{(\w+):([A-Za-z0-9()]+)\}/g;

describe('query forecast F&B', () => {
  it.each(FC)('%s: hanya v_esb_forecast_daily, placeholder = params, filter PT, LIMIT literal, tanpa kolom terlarang', (n) => {
    const def = QUERIES[n];
    expect(Array.from(def.sql.matchAll(/FROM\s+([\w.]+)/g)).map((m) => m[1])).toEqual(['efn_insight.v_esb_forecast_daily']);
    const found = new Map<string, string>();
    for (const m of def.sql.matchAll(PLACEHOLDER)) found.set(m[1], m[2]);
    expect(Array.from(found.keys()).sort()).toEqual([...def.params].sort());
    for (const [p, t] of found) expect(PARAM_TYPES[p as keyof typeof PARAM_TYPES], `${n}:${p}`).toBe(t);
    expect(def.sql).toContain('has({pt:Array(String)}, company_code)');
    expect(def.sql).not.toContain('${');
    expect(def.sql).toMatch(/LIMIT \d+$/);
    expect(def.sql).not.toMatch(/\b(cogs|mdr|margin|hpp)\b/i);
  });

  it('fnb_fc_next dan top produk mengeluarkan DC lewat parameter; per cabang tidak (DC diberi label di app)', () => {
    expect(sqlOf('fnb_fc_next')).toContain('NOT has({dc_codes:Array(String)}, warehouse_code)');
    expect(sqlOf('fnb_fc_top_products')).toContain('NOT has({dc_codes:Array(String)}, warehouse_code)');
    expect(QUERIES.fnb_fc_by_wh.params).not.toContain('dc_codes');
  });

  it('jendela today..until, urutan, dan LIMIT sesuai spesifikasi', () => {
    for (const n of FC) expect(sqlOf(n)).toContain('forecast_date BETWEEN {today:Date} AND {until:Date}');
    expect(sqlOf('fnb_fc_next')).toMatch(/GROUP BY forecast_date\s+ORDER BY forecast_date\s+LIMIT 60$/);
    expect(sqlOf('fnb_fc_top_products')).toMatch(/ORDER BY yhat_sum DESC\s+LIMIT 20$/);
    expect(sqlOf('fnb_fc_by_wh')).toMatch(/GROUP BY company_code, warehouse_id/);
    expect(sqlOf('fnb_fc_by_wh')).toContain('uniqExact(product_id)');
    expect(sqlOf('fnb_fc_next')).toContain('max(generated_at)');
  });

  it('parameter dc_codes terserialisasi sebagai Array(String), boleh kosong; tanggal divalidasi', () => {
    const scope = scopeSettings([5], ['PB'], [], KEY);
    const bag = { pt: ['PB'], today: '2026-11-23', until: '2026-12-06' };
    expect(buildChSearchParams('fnb_fc_next', scope, { ...bag, dc_codes: [] }).get('param_dc_codes')).toBe('[]');
    const sp = buildChSearchParams('fnb_fc_next', scope, { ...bag, dc_codes: ['DC01'] });
    expect(sp.get('param_dc_codes')).toBe("['DC01']");
    expect(sp.get('param_until')).toBe('2026-12-06');
    expect(sp.get('SQL_efn_codes')).toBe('PB');
    expect(() => buildChSearchParams('fnb_fc_next', scope, { ...bag })).toThrow();
    expect(() => buildChSearchParams('fnb_fc_next', scope, { ...bag, today: "2026-11-23'; DROP", dc_codes: [] })).toThrow();
  });
});

describe('INSIGHT_FNB_DC_CODES', () => {
  it('kosong/undefined -> []', () => {
    expect(parseDcCodes(undefined)).toEqual([]);
    expect(parseDcCodes('')).toEqual([]);
    expect(parseDcCodes(' , ,')).toEqual([]);
    expect(parseConfig({}).fnbDcCodes).toEqual([]);
  });
  it('dipisah koma, trim, duplikat dibuang', () => {
    expect(parseDcCodes('DC01')).toEqual(['DC01']);
    expect(parseDcCodes(' DC01 , DC-01,DC01,a_b ')).toEqual(['DC01', 'DC-01', 'a_b']);
    expect(parseConfig({ INSIGHT_FNB_DC_CODES: 'DC01,X1' }).fnbDcCodes).toEqual(['DC01', 'X1']);
  });
  it('format tidak valid diabaikan (karakter aneh, kutip, spasi dalam, terlalu panjang)', () => {
    expect(parseDcCodes("DC01,a'b,x y,;DROP,a/b,A".concat(',', 'x'.repeat(17), ',', 'y'.repeat(16)))).toEqual(['DC01', 'A', 'y'.repeat(16)]);
  });
});

describe('eksklusi gudang pusat (DC)', () => {
  const rows = [
    { wh_code: 'DC01', yhat_sum: 1000 },
    { wh_code: 'O001', yhat_sum: 400 },
    { wh_code: 'O002', yhat_sum: 600 },
  ];
  const toNum = (v: unknown): number => Number(v);
  it('DC tidak masuk total; outlet saja', () => {
    expect(sumNonDc(rows, ['DC01'], toNum)).toBe(1000);
    const { outlets, dc } = splitDc(rows, ['DC01']);
    expect(outlets.map((r) => r.wh_code)).toEqual(['O001', 'O002']);
    expect(dc.map((r) => r.wh_code)).toEqual(['DC01']);
  });
  it('tanpa DC terkonfigurasi semua baris dihitung', () => {
    expect(sumNonDc(rows, [], toNum)).toBe(2000);
    expect(splitDc(rows, []).dc).toEqual([]);
  });
  it('isDcCode tepat sama (peka huruf besar) dan tahan nilai non-string', () => {
    expect(isDcCode('DC01', ['DC01'])).toBe(true);
    expect(isDcCode('p004', ['DC01'])).toBe(false);
    expect(isDcCode(null, ['DC01'])).toBe(false);
    expect(DC_LABEL).toBe('Gudang pusat (agregat outlet)');
  });
});

describe('tautan Odoo forecast harian', () => {
  it('efn.forecast.daily terdaftar dan menghasilkan URL daftar', () => {
    expect(ACTIONS['efn.forecast.daily']).toBe('efn_demand_forecast.efn_forecast_daily_action');
    expect(odooListUrl('https://erp.test', 'efn.forecast.daily')).toBe('https://erp.test/odoo/action-efn_demand_forecast.efn_forecast_daily_action');
  });
});
