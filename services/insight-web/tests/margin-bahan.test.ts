import { describe, expect, it } from 'vitest';
import { PARAM_TYPES, QUERIES } from '@/lib/queries';
import { fnbExecOnlyNavItems, fnbRouteDenial, isExecOnlyPath, type FnbSessionLike } from '@/lib/fnb-personas';
import { appliesTo, selectableCodes } from '@/lib/page-filters';
import { navGroups } from '@/lib/sm-personas';
import {
  groupContributions,
  groupSeries,
  grossMarginPct,
  indexPct,
  ingredientImpacts,
  kpis,
  menuCostChanges,
  monthsOf,
  type MbGroupRow,
  type MbMenuRow,
  type MbPriceRow,
} from '@/lib/margin-bahan';

// Data sintetis: nama generik, tanpa produk/supplier klien.
const M1 = '2026-08-01';
const M2 = '2026-09-01';
const M3 = '2026-10-01';

const g = (month: string, ing_group: string, basket_value: number, base_value: number, company_code = 'AA'): MbGroupRow => ({
  brand: 'aa',
  company_code,
  month,
  ing_group,
  basket_value,
  base_value,
});

const GROUPS: MbGroupRow[] = [
  g(M1, 'Kelompok A', 100, 100),
  g(M2, 'Kelompok A', 110, 100),
  g(M3, 'Kelompok A', 130, 100),
  g(M1, 'Kelompok B', 50, 50),
  g(M2, 'Kelompok B', 50, 50),
  g(M3, 'Kelompok B', 40, 50),
  g(M1, 'Kelompok C', 10, 10),
  g(M3, 'Kelompok C', 11, 10),
];

describe('helper dasar', () => {
  it('monthsOf unik dan terurut; indexPct/grossMarginPct aman terhadap pembagi nol', () => {
    expect(monthsOf([{ month: M3 }, { month: M1 }, { month: M3 }])).toEqual([M1, M3]);
    expect(indexPct(110, 100)).toBeCloseTo(110);
    expect(indexPct(5, 0)).toBeNull();
    expect(grossMarginPct(200, 150)).toBeCloseTo(25);
    expect(grossMarginPct(0, 10)).toBeNull();
    expect(grossMarginPct(-5, 1)).toBeNull();
  });
});

describe('groupSeries: 5 kelompok teratas dan total', () => {
  it('memilih kelompok menurut |kontribusi| bulan terakhir, urut menurun', () => {
    const s = groupSeries(GROUPS, 2);
    expect(s.months).toEqual([M1, M2, M3]);
    expect(s.groups.map((x) => x.name)).toEqual(['Kelompok A', 'Kelompok B']);
    expect(s.groups[0].contribution).toBe(30);
    expect(s.groups[1].contribution).toBe(-10); // penurunan tetap dihitung menurut besarnya
    expect(s.hidden).toBe(1);
  });

  it('indeks per kelompok 100-based; bulan tanpa baris = null; total = sum basket / sum base', () => {
    const s = groupSeries(GROUPS, 5);
    const c = s.groups.find((x) => x.name === 'Kelompok C');
    expect(c?.idx[1]).toBeNull();
    expect(c?.idx[2]).toBeCloseTo(110);
    // Bulan 3: (130 + 40 + 11) / (100 + 50 + 10)
    expect(s.total.idx[2]).toBeCloseTo((181 / 160) * 100);
    expect(s.total.idx[0]).toBeCloseTo(100);
    expect(s.total.contribution).toBe(30 - 10 + 1);
    expect(s.hidden).toBe(0);
  });

  it('menjumlahkan lintas PT dan tidak gagal pada input kosong', () => {
    const rows = [g(M1, 'X', 10, 10, 'AA'), g(M1, 'X', 30, 20, 'BB')];
    expect(groupSeries(rows).groups[0].idx[0]).toBeCloseTo((40 / 30) * 100);
    const e = groupSeries([]);
    expect(e.months).toEqual([]);
    expect(e.groups).toEqual([]);
    expect(e.total.idx).toEqual([]);
  });
});

describe('groupContributions', () => {
  it('kontribusi = basket - base pada bulan terakhir, porsi terhadap total perubahan', () => {
    const t = groupContributions(GROUPS);
    expect(t.map((x) => x.ing_group)).toEqual(['Kelompok A', 'Kelompok B', 'Kelompok C']);
    expect(t[0]).toMatchObject({ contribution: 30, base_value: 100, basket_value: 130 });
    expect(t[0].idx).toBeCloseTo(130);
    expect(t[0].share).toBeCloseTo((30 / 21) * 100);
    expect(t[1].share).toBeCloseTo((-10 / 21) * 100);
  });

  it('total perubahan nol -> porsi null', () => {
    const t = groupContributions([g(M1, 'A', 110, 100), g(M1, 'B', 90, 100)]);
    expect(t.every((x) => x.share === null)).toBe(true);
  });
});

const p = (month: string, code: string, usage_value: number, idx: number | null, over: Partial<MbPriceRow> = {}): MbPriceRow => ({
  brand: 'aa',
  company_code: 'AA',
  month,
  product_code: code,
  product_name: `Bahan ${code}`,
  ing_group: 'Kelompok A',
  uom: 'KG',
  idx,
  usage_value,
  ...over,
});

describe('ingredientImpacts: (idx - 1) x nilai pakai', () => {
  const rows: MbPriceRow[] = [
    p(M1, 'P1', 2000, 1),
    p(M2, 'P1', 2000, 1.1),
    p(M3, 'P1', 2000, 1.2), // nilai pakai 2000, idx terakhir 1,2 -> dampak 400
    p(M1, 'P2', 10000, 1),
    p(M3, 'P2', 10000, 0.9), // dampak -1000 (|.| lebih besar dari P1)
    p(M2, 'P3', 5000, null), // tanpa indeks: dikeluarkan
  ];

  it('menghitung dampak, mengurutkan menurut |dampak|, membuang bahan tanpa indeks', () => {
    const r = ingredientImpacts(rows, 20);
    expect(r.map((x) => x.product_code)).toEqual(['P2', 'P1']);
    expect(r[1].impact).toBeCloseTo(400);
    expect(r[1].idx_chg).toBeCloseTo(20);
    expect(r[1].usage_value).toBeCloseTo(2000);
    expect(r[1].last_month).toBe(M3);
    expect(r[0].impact).toBeCloseTo(-1000);
  });

  it('membatasi jumlah baris (top-N) dan memisahkan satuan beli berbeda', () => {
    expect(ingredientImpacts(rows, 1)).toHaveLength(1);
    const split = ingredientImpacts([p(M1, 'P1', 100, 1.5), p(M1, 'P1', 100, 1.1, { uom: 'PCS' })], 20);
    expect(split).toHaveLength(2);
  });

  it('memakai baris terakhir yang punya indeks walau bulan terbaru null', () => {
    const r = ingredientImpacts([p(M1, 'P9', 100, 1.5), p(M2, 'P9', 100, null)], 20);
    expect(r[0].last_month).toBe(M1);
    expect(r[0].idx_chg).toBeCloseTo(50);
  });
});

const m = (month: string, name: string, over: Partial<MbMenuRow> = {}): MbMenuRow => ({
  brand: 'aa',
  company_code: 'AA',
  month,
  menu_name: name,
  menu_category: 'Kategori X',
  qty: 10,
  net: 1000,
  cogs: 400,
  bom_matched: 1,
  cost_idx: 1,
  leaf_coverage: 0.9,
  ...over,
});

describe('menuCostChanges', () => {
  const rows: MbMenuRow[] = [
    m(M1, 'Menu 1', { net: 1000, cogs: 400, cost_idx: 1 }),
    m(M3, 'Menu 1', { net: 1000, cogs: 500, cost_idx: 1.25 }),
    m(M1, 'Menu 2', { net: 1000, cogs: 400, cost_idx: 1 }),
    m(M3, 'Menu 2', { net: 1000, cogs: 420, cost_idx: 1.05 }),
    m(M3, 'Menu 3', { bom_matched: 0, cost_idx: null }), // tanpa BOM: tidak ikut
    m(M3, 'Menu 4', { cost_idx: 0.95, net: 0, cogs: 0 }), // net 0 -> margin null
  ];

  it('mengurutkan menurut kenaikan cost_idx dan menghitung margin kotor awal/akhir', () => {
    const r = menuCostChanges(rows, 20);
    expect(r.map((x) => x.menu_name)).toEqual(['Menu 1', 'Menu 2', 'Menu 4']);
    expect(r[0].cost_chg).toBeCloseTo(25);
    expect(r[0].gm_base).toBeCloseTo(60);
    expect(r[0].gm_last).toBeCloseTo(50);
    expect(r[0].gm_pp).toBeCloseTo(-10);
    expect(r[0].coverage).toBeCloseTo(90);
    expect(r[2].cost_chg).toBeCloseTo(-5);
    expect(r[2].gm_last).toBeNull();
    expect(r[2].gm_pp).toBeNull();
  });

  it('top-N memotong daftar', () => {
    expect(menuCostChanges(rows, 2).map((x) => x.menu_name)).toEqual(['Menu 1', 'Menu 2']);
  });
});

describe('kpis', () => {
  const menus: MbMenuRow[] = [
    m(M1, 'Menu 1', { net: 1000, cogs: 400 }),
    m(M1, 'Menu 2', { net: 1000, cogs: 600 }),
    m(M3, 'Menu 1', { net: 1000, cogs: 500 }),
    m(M3, 'Menu 2', { net: 1000, cogs: 700 }),
    m(M3, 'Menu 3', { net: 500, cogs: 300, bom_matched: 0, cost_idx: null }),
  ];

  it('margin kotor semua menu per bulan = (net - cogs) / net; cakupan = COGS ber-BOM / COGS total', () => {
    const k = kpis(menus, GROUPS);
    expect(k.baseMonth).toBe(M1);
    expect(k.lastMonth).toBe(M3);
    expect(k.gmBase).toBeCloseTo(50); // (2000 - 1000) / 2000
    expect(k.gmLast).toBeCloseTo(((2500 - 1500) / 2500) * 100);
    expect(k.bomCoverage).toBeCloseTo((1200 / 1500) * 100);
    expect(k.costIdx).toBeCloseTo((181 / 160) * 100);
  });

  it('data kosong -> semua null', () => {
    expect(kpis([], [])).toEqual({ baseMonth: null, lastMonth: null, costIdx: null, gmBase: null, gmLast: null, bomCoverage: null });
  });
});

describe('katalog query margin bahan', () => {
  const names = ['mb_group_monthly', 'mb_price_monthly', 'mb_menu_monthly'] as const;
  const views = {
    mb_group_monthly: 'efn_insight.v_esb_ing_group_monthly',
    mb_price_monthly: 'efn_insight.v_esb_ing_price_monthly',
    mb_menu_monthly: 'efn_insight.v_esb_menu_cost_monthly',
  } as const;

  it.each(names)('%s: hanya parameter pt, view wrapper efn_insight, tanpa kolom _version/supplier', (n) => {
    expect(QUERIES[n].params).toEqual(['pt']);
    expect(PARAM_TYPES.pt).toBe('Array(String)');
    expect(QUERIES[n].sql).toContain(`FROM ${views[n]}`);
    expect(QUERIES[n].sql).not.toMatch(/_version|supplier/i);
  });

  it('kolom sesuai kontrak §31.3', () => {
    expect(QUERIES.mb_group_monthly.sql).toContain('brand, company_code, month, ing_group, basket_value, base_value');
    expect(QUERIES.mb_menu_monthly.sql).toContain('menu_name, menu_category, qty, net, cogs, bom_matched, cost_idx, leaf_coverage');
    expect(QUERIES.mb_price_monthly.sql).toContain('product_code, product_name, ing_group, uom, idx, usage_value');
  });
});

describe('akses /margin-bahan: hanya persona exec', () => {
  const s = (roles: string[], codes: string[] = ['PB'], brs: string[] = [], v = 3): FnbSessionLike => ({ v, roles, codes, brs });

  it('exec berkode ESB boleh; persona lain, tanpa kode ESB, ber-cabang, dan sesi lama ditolak', () => {
    expect(fnbRouteDenial('/margin-bahan', s(['exec', 'user']), true)).toBeNull();
    expect(fnbRouteDenial('/margin-bahan', s(['marketing', 'user']), true)).toBe('tidak-berwenang');
    expect(fnbRouteDenial('/margin-bahan', s(['store', 'user']), true)).toBe('tidak-berwenang');
    expect(fnbRouteDenial('/margin-bahan', s(['user']), true)).toBe('tidak-berwenang');
    expect(fnbRouteDenial('/margin-bahan', s(['exec', 'user'], ['SMD']), true)).toBe('tidak-berwenang');
    expect(fnbRouteDenial('/margin-bahan', s(['exec', 'user'], ['PB'], ['pb:7']), true)).toBe('tidak-berwenang');
    expect(fnbRouteDenial('/margin-bahan', s(['exec', 'user'], ['PB'], [], 2), true)).toBe('buka-ulang');
    expect(fnbRouteDenial('/margin-bahan', s(['exec', 'user']), false)).toBe('fnb-belum-aktif');
  });

  it('rute dikenali termasuk sub-path, bukan awalan sembarang', () => {
    expect(isExecOnlyPath('/margin-bahan')).toBe(true);
    expect(isExecOnlyPath('/margin-bahan/x')).toBe(true);
    expect(isExecOnlyPath('/margin-bahanx')).toBe(false);
    expect(isExecOnlyPath('/yoy')).toBe(false);
  });

  it('menu hanya untuk exec: item ada di grup F&B exec, tidak ada untuk marketing/store/ber-cabang', () => {
    const hrefs = (x: Parameters<typeof navGroups>[0]) => navGroups(x, false, true).flatMap((gr) => gr.items.map((i) => i.href));
    expect(hrefs({ codes: ['PB'], roles: ['exec', 'user'], v: 3 })).toContain('/margin-bahan');
    expect(hrefs({ codes: ['PB'], roles: ['marketing', 'user'], v: 3 })).not.toContain('/margin-bahan');
    expect(hrefs({ codes: ['PB'], roles: ['store', 'user'], v: 3 })).not.toContain('/margin-bahan');
    expect(hrefs({ codes: ['PB'], roles: ['exec', 'user'], v: 3, brs: ['pb:7'] })).not.toContain('/margin-bahan');
    expect(hrefs({ codes: ['PB'], roles: ['exec', 'user'], v: 2 })).not.toContain('/margin-bahan');
    expect(navGroups({ codes: ['PB'], roles: ['exec', 'user'], v: 3 }, false, false).flatMap((gr) => gr.items.map((i) => i.href))).not.toContain('/margin-bahan');
    expect(fnbExecOnlyNavItems({ codes: ['SMD'], roles: ['exec'], v: 3 })).toEqual([]);
  });

  it('filter halaman: hanya Brand / PT; SMD tidak ditawarkan', () => {
    expect(appliesTo('/margin-bahan', 'pt')).toBe(true);
    expect(appliesTo('/margin-bahan', 'date')).toBe(false);
    expect(appliesTo('/margin-bahan', 'branch')).toBe(false);
    expect(selectableCodes('/margin-bahan', ['PB', 'SMD'])).toEqual(['PB']);
  });
});
