import { describe, expect, it } from 'vitest';
import { QUERIES } from '@/lib/queries';
import {
  branchStatus,
  bridge,
  dailySeries,
  gapNotes,
  lyRange,
  menuView,
  promo,
  spanDays,
  totals,
  type BranchRow,
  type MenuRow,
} from '@/lib/yoy';

const br = (p: Partial<BranchRow>): BranchRow => ({
  brand: 'pb',
  branch_id: 1,
  branch_name: 'A',
  company_code: 'PB',
  net_cur: 0,
  net_ly: 0,
  gross_cur: 0,
  gross_ly: 0,
  disc_cur: 0,
  disc_ly: 0,
  cogs_cur: 0,
  cogs_ly: 0,
  trx_cur: 0,
  trx_ly: 0,
  days_cur: 0,
  days_ly: 0,
  first_sale: '2025-02-01',
  ...p,
});

const menu = (p: Partial<MenuRow>): MenuRow => ({
  menu_name: 'CROISSANT T',
  category: 'Bread',
  qty_paid_cur: 0,
  qty_paid_ly: 0,
  gross_paid_cur: 0,
  gross_paid_ly: 0,
  disc_paid_cur: 0,
  disc_paid_ly: 0,
  cogs_paid_cur: 0,
  cogs_paid_ly: 0,
  qty_free_cur: 0,
  qty_free_ly: 0,
  cogs_free_cur: 0,
  cogs_free_ly: 0,
  qty_pkg_cur: 0,
  qty_pkg_ly: 0,
  cogs_pkg_cur: 0,
  cogs_pkg_ly: 0,
  pkg_gross_cur: 0,
  pkg_gross_ly: 0,
  pkg_disc_cur: 0,
  pkg_disc_ly: 0,
  ...p,
});

describe('periode pembanding', () => {
  it('digeser 364 hari: hari dalam minggu sama', () => {
    expect(lyRange('2026-11-01', '2026-11-23')).toEqual({ from: '2025-11-02', to: '2025-11-24' });
    expect(new Date('2026-11-01').getUTCDay()).toBe(new Date('2025-11-02').getUTCDay());
    expect(spanDays('2026-11-01', '2026-11-23')).toBe(23);
  });
});

describe('status cabang', () => {
  const lyFrom = '2025-11-02';
  it('sebanding hanya bila berjualan di kedua sisi dan sudah >= 28 hari sebelum periode tahun lalu', () => {
    expect(branchStatus(br({ net_cur: 1, net_ly: 1, first_sale: '2025-10-05' }), lyFrom)).toBe('same');
    expect(branchStatus(br({ net_cur: 1, net_ly: 1, first_sale: '2025-10-06' }), lyFrom)).toBe('young');
    expect(branchStatus(br({ net_cur: 1, net_ly: 0 }), lyFrom)).toBe('new');
    expect(branchStatus(br({ net_cur: 0, net_ly: 5 }), lyFrom)).toBe('closed');
    expect(branchStatus(br({ net_cur: 1, net_ly: 1, first_sale: '' }), lyFrom)).toBe('young');
  });
});

describe('jembatan perubahan', () => {
  it('komponen berjumlah tepat selisih total', () => {
    const rows = [
      br({ branch_id: 1, net_ly: 1000, trx_ly: 10, net_cur: 990, trx_cur: 9 }),
      br({ branch_id: 2, net_ly: 0, net_cur: 300, trx_cur: 3, first_sale: '2026-01-10' }),
      br({ branch_id: 3, net_ly: 400, trx_ly: 4, net_cur: 0 }),
      br({ branch_id: 4, net_ly: 50, trx_ly: 1, net_cur: 80, trx_cur: 1, first_sale: '2025-11-12' }),
    ];
    const b = bridge(rows, '2025-11-02');
    expect(b.ly).toBe(1450);
    expect(b.cur).toBe(1370);
    expect(b.trxEffect).toBe(-100); // -1 transaksi x 100 per transaksi tahun lalu
    expect(b.atvEffect).toBe(90); // 9 x (110 - 100)
    expect(b.newStores).toBe(300);
    expect(b.closedStores).toBe(-400);
    expect(b.youngStores).toBe(30);
    expect(b.ly + b.trxEffect + b.atvEffect + b.newStores + b.closedStores + b.youngStores).toBeCloseTo(b.cur, 6);
    expect(totals(rows).branches).toBe(4);
  });
});

describe('seri harian', () => {
  it('menjumlah per hari untuk cabang terpilih; hari tanpa data = null', () => {
    const s = dailySeries(
      [
        { day: '2026-11-01', brand: 'pb', branch_id: 1, net_cur: 10, net_ly: 8, trx_cur: 1, trx_ly: 1 },
        { day: '2026-11-01', brand: 'pb', branch_id: 2, net_cur: 5, net_ly: 0, trx_cur: 1, trx_ly: 0 },
      ],
      ['2026-11-01', '2026-11-02'],
      (r) => r.branch_id === 1,
    );
    expect(s.netCur).toEqual([10, null]);
    expect(s.netLy).toEqual([8, null]);
  });
});

describe('harga jual vs COGS', () => {
  it('harga dari baris berbayar saja: item gratis tidak menurunkan harga', () => {
    const [v] = menuView([
      menu({ qty_paid_ly: 100, gross_paid_ly: 2_900_000, cogs_paid_ly: 695_000, qty_paid_cur: 80, gross_paid_cur: 2_320_000, cogs_paid_cur: 627_040, qty_free_cur: 20, disc_paid_cur: 80_000 }),
    ]);
    expect(v.price_ly).toBe(29_000);
    expect(v.price_cur).toBe(29_000);
    expect(v.price_chg).toBe(0);
    expect(v.cogs_cur).toBeCloseTo(7_838, 6);
    expect(v.fc_ly).toBeCloseTo(23.966, 2);
    expect(v.free_share_cur).toBe(20);
    expect(v.net_price_cur).toBe(28_000);
  });
  it('menu tanpa penjualan berbayar sekarang tidak ditampilkan', () => {
    expect(menuView([menu({ qty_paid_ly: 5, gross_paid_ly: 10 })])).toEqual([]);
  });
});

describe('promo', () => {
  it('bundel = nilai list komponen - harga paket; diskon header tidak dihitung dua kali', () => {
    const rows = [
      menu({ menu_name: 'CROISSANT T', qty_paid_cur: 10, gross_paid_cur: 290_000, qty_pkg_cur: 3, qty_free_cur: 1, disc_paid_cur: 10_000 }),
      menu({ menu_name: '3 PASTRY 120K T', category: 'Package', pkg_gross_cur: 80_000, pkg_disc_cur: 5_000 }),
    ];
    const p = promo(rows, 'cur');
    expect(p.discount).toBe(15_000);
    expect(p.bundle).toBe(3 * 29_000 - 80_000);
    expect(p.free).toBe(29_000);
    expect(p.listValue).toBe(290_000 + 87_000 + 29_000);
    // nilai list - promo = omzet bersih (a la carte + paket setelah diskon)
    expect(p.listValue - p.discount - p.bundle - p.free).toBe(290_000 - 10_000 + 80_000 - 5_000);
    expect(p.pkgShare).toBeCloseTo((80_000 / 370_000) * 100, 6);
  });
  it('harga sisi lain dipakai bila menu tidak terjual berbayar; tanpa harga sama sekali = unpriced', () => {
    const p = promo([menu({ qty_paid_ly: 2, gross_paid_ly: 58_000, qty_free_cur: 1 }), menu({ menu_name: 'X', qty_pkg_cur: 4 })], 'cur');
    expect(p.free).toBe(29_000);
    expect(p.unpriced).toBe(4);
  });
});

describe('celah data', () => {
  it('kalimat per hari', () => {
    expect(gapNotes([{ day: '2026-11-17', miss_ly: 0, miss_cur: 11 }], (d) => d)).toEqual(['2026-11-17: 11 cabang belum ditarik untuk tahun ini']);
  });
});

describe('query /yoy', () => {
  it('semua query yoy_* memakai pasangan cabang-hari lengkap dan periode 364 hari', () => {
    for (const n of ['yoy_branch', 'yoy_daily', 'yoy_menu'] as const) {
      expect(QUERIES[n].sql, n).toContain('efn_insight.v_esb_sales_done_day');
      expect(QUERIES[n].sql, n).toContain('day - 364');
      expect(QUERIES[n].sql, n).toContain('sales_date + 364');
      expect(QUERIES[n].sql, n).toContain('is_store = 1 OR is_ck = 1');
    }
    expect(QUERIES.yoy_menu.sql).toContain('efn_insight.v_esb_menu_econ_daily');
    expect(QUERIES.yoy_gaps.sql).toContain('day + 364');
  });
});

describe('Eksekutif F&B memakai pembanding sebanding yang sama', () => {
  it('fexec_*: delta YoY hanya dari pasangan cabang-hari lengkap (364 hari)', () => {
    for (const n of ['fexec_kpi', 'fexec_pt_compare', 'fexec_branch_rank'] as const) {
      expect(QUERIES[n].sql, n).toContain('efn_insight.v_esb_sales_done_day');
      expect(QUERIES[n].sql, n).toContain('sales_date + 364');
    }
    for (const c of ['net_cmp', 'cogs_cmp', 'gm_cmp', 'trx_cmp']) expect(QUERIES.fexec_kpi.sql).toContain(`AS ${c}`);
    expect(QUERIES.fexec_pt_compare.sql).toContain('AS net_cmp');
  });
});
