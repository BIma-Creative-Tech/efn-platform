/**
 * G69: katalog query ESB OMS (`foms_*`): nama view, parameter terikat, cakupan PT + cabang, tanpa interpolasi.
 * Tidak menjalankan ClickHouse (view dibangun paralel oleh data engineer; belum dieksekusi).
 */
import { describe, expect, it } from 'vitest';
import { buildChSearchParams } from '@/lib/ch-params';
import { PARAM_TYPES, QUERIES, type QueryName } from '@/lib/queries';
import { scopeSettings } from '@/lib/scope';
import { KEY } from './helpers-v3';

const OMS = ['foms_freshness', 'foms_hourly', 'foms_fin_hourly', 'foms_fin_supply', 'foms_fin_quality'] as const;
const PLACEHOLDER = /\{(\w+):([A-Za-z0-9()]+)\}/g;
const sqlOf = (n: QueryName): string => QUERIES[n].sql;
const viewsOf = (n: QueryName): string[] => Array.from(sqlOf(n).matchAll(/FROM\s+([\w.]+)/g)).map((m) => m[1]);

describe('katalog query OMS (G69)', () => {
  it('terdaftar tepat lima query foms_*', () => {
    expect(Object.keys(QUERIES).filter((n) => n.startsWith('foms_')).sort()).toEqual([...OMS].sort());
  });

  it.each(OMS)('%s: placeholder = params terdeklarasi bertipe benar, PT lapis 1, tanpa interpolasi, LIMIT literal', (n) => {
    const def = QUERIES[n];
    const found = new Map<string, string>();
    for (const m of def.sql.matchAll(PLACEHOLDER)) found.set(m[1], m[2]);
    expect(Array.from(found.keys()).sort()).toEqual([...def.params].sort());
    for (const [p, t] of found) expect(PARAM_TYPES[p as keyof typeof PARAM_TYPES], `${n}:${p}`).toBe(t);
    expect(def.sql).toContain('has({pt:Array(String)}, company_code)');
    expect(def.sql).not.toContain('${');
    expect(def.sql).not.toMatch(/LIMIT\s*\{/);
    // Agregat tunggal (tanpa GROUP BY) = 1 baris; selebihnya wajib LIMIT literal.
    if (/GROUP BY|DISTINCT/.test(def.sql) || !/\bmax(OrNull)?\(/.test(def.sql)) expect(def.sql).toMatch(/LIMIT \d+\s*$/);
    expect(def.sql.trim().endsWith(';')).toBe(false);
  });

  it('nama view sesuai kontrak (butir 6) dan hanya efn_insight.v_esb_*', () => {
    expect(viewsOf('foms_freshness')).toEqual(['efn_insight.v_esb_oms_freshness']);
    expect(viewsOf('foms_hourly')).toEqual(['efn_insight.v_esb_oms_hourly']);
    expect(viewsOf('foms_fin_hourly')).toEqual(['efn_insight.v_esb_oms_fin_hourly']);
    expect(viewsOf('foms_fin_quality')).toEqual(['efn_insight.v_esb_oms_fin_mapping_quality']);
    expect(viewsOf('foms_fin_supply')[0]).toBe('efn_insight.v_esb_flow_daily');
    for (const n of OMS) for (const v of viewsOf(n)) expect(v, n).toMatch(/^efn_insight\.v_esb_[a-z_]+$/);
  });

  it('semua query OMS memakai makro cabang BR (brand:branch_id) seperti view BR lain; hari lewat ref_day Date', () => {
    for (const n of OMS) {
      expect(sqlOf(n), n).toContain("has({branch:Array(String)}, concat(brand, ':', toString(branch_id)))");
      expect(sqlOf(n), n).not.toContain('branch_code');
    }
    for (const n of ['foms_hourly', 'foms_fin_hourly', 'foms_fin_quality'] as const) expect(sqlOf(n), n).toContain('sales_date = {ref_day:Date}');
    expect(sqlOf('foms_fin_supply')).toContain('day = {ref_day:Date}');
  });

  it('kolom final: freshness = maxOrNull(last_finished_at) dalam WIB; mutu memakai unmapped_qty dan menu_qty', () => {
    expect(sqlOf('foms_freshness')).toContain(
      "toString(toTimeZone(maxOrNull(last_finished_at), 'Asia/Jakarta')) AS finished_at",
    );
    expect(sqlOf('foms_fin_quality')).toContain('unmapped_qty');
    expect(sqlOf('foms_fin_quality')).toContain('menu_qty');
    for (const n of OMS) expect(sqlOf(n), n).not.toMatch(/SELECT\s+\*/);
  });

  it('per jam dikelompokkan per jam dan kanal; FIN per brand, produk, jam', () => {
    expect(sqlOf('foms_hourly')).toMatch(/GROUP BY hour, channel_name/);
    expect(sqlOf('foms_fin_hourly')).toMatch(/GROUP BY brand, product_key, uom, hour/); // uom = satuan resep, dipisah agar non-EA terdeteksi
  });

  it('filter produk: product_key view = kode polos, dibandingkan sebagai brand:product_key (bukan product_key mentah)', () => {
    const sql = sqlOf('foms_fin_hourly');
    expect(sql).toContain("has({product:Array(String)}, concat(brand, ':', product_key))");
    expect(sql).not.toContain('{product:Array(String)}, product_key)');
  });

  it('pasokan memakai definisi SUPPLY yang sama dengan sell-through /flow', () => {
    expect(sqlOf('foms_fin_supply')).toContain('in_ck + in_other + store_prod + st_in - st_out');
  });

  it('tidak membaca PII/PCI atau kolom di luar kontrak', () => {
    for (const n of OMS) {
      const sql = sqlOf(n).toLowerCase();
      for (const bad of ['member', 'card', 'account', 'voucher', 'sales_num', 'created_by']) expect(sql, `${n}: ${bad}`).not.toContain(bad);
    }
  });

  it('alias keluaran tidak sama dengan nama kolom view (alias siklik ClickHouse)', () => {
    for (const n of OMS) expect(/\b(?:sum|any)\([^)]*\)\s+AS\s+(bills|pax|items|qty_fin|product_name|net_sales|gross_sales|supply)\b/.test(sqlOf(n)), n).toBe(false);
  });
});

describe('buildChSearchParams untuk query OMS', () => {
  const scope = scopeSettings([5], ['PB'], ['pb:7'], KEY);
  it('foms_hourly: ref_day Date, cabang dan cakupan brs dikirim terikat', () => {
    const sp = buildChSearchParams('foms_hourly', scope, { ref_day: '2026-11-21', pt: ['PB'], branch: ['pb:7'] });
    expect(sp.get('param_ref_day')).toBe('2026-11-21');
    expect(sp.get('param_branch')).toBe("['pb:7']");
    expect(sp.get('SQL_efn_brs')).toBe('pb:7');
    expect(sp.has('param_product')).toBe(false);
  });
  it('foms_fin_hourly: product wajib; ref_day bukan tanggal ditolak', () => {
    const base = { ref_day: '2026-11-21', pt: ['PB'], branch: [] as string[] };
    expect(() => buildChSearchParams('foms_fin_hourly', scope, base)).toThrow();
    expect(buildChSearchParams('foms_fin_hourly', scope, { ...base, product: ['pb:A'] }).get('param_product')).toBe("['pb:A']");
    expect(() => buildChSearchParams('foms_fin_hourly', scope, { ...base, ref_day: "2026-11-21'; DROP", product: [] })).toThrow();
  });
  it('foms_freshness: pt + branch, tanpa ref_day', () => {
    const sp = buildChSearchParams('foms_freshness', scope, { pt: ['PB'], branch: ['pb:7'], ref_day: '2026-11-21' });
    expect(sp.get('param_pt')).toBe("['PB']");
    expect(sp.get('param_branch')).toBe("['pb:7']");
    expect(sp.has('param_ref_day')).toBe(false);
  });
});
