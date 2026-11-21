/**
 * Logika penggabungan ESB + SMD untuk halaman F&B (G51 B1) dan helper halaman (B3-B5). Murni, tanpa ClickHouse.
 */
import { describe, expect, it } from 'vitest';
import { brandName, codeColor } from '@/lib/brands';
import type { PtSelection } from '@/lib/filters';
import {
  ptShareSubtotals,
  segBadgeClass,
  segmentFooter,
  segmentTrendSeries,
  segParts,
  tagSegment,
  withSegmentColumn,
  addSales,
  allOk,
  esbOnlyMode,
  esbParts,
  esbSalesSums,
  hppPct,
  mergeBranchRank,
  mergeCategories,
  mergeHeat,
  mergePtShare,
  mergeSides,
  mergeWeekday,
  smdActive,
  smdBranchOptions,
  smdBranchRows,
  smdExcludedNote,
  smdParts,
  smdPayBranchRows,
  smdPayMethodRows,
  smdPayTrendRows,
  smdSalesSums,
  smdTrendRows,
  splitDaily,
  sumParts,
} from '@/lib/smd-merge';
import { trendSeries } from '@/lib/vm';

const sel = (o: Partial<PtSelection> = {}): PtSelection => ({ allowed: true, esb: true, smd: true, wh: [], ...o });
const none = { cat: [] as string[], product: [] as string[] };

describe('smdActive / esbOnlyMode / smdExcludedNote', () => {
  it('SMD aktif hanya bila terpilih; filter ESB-saja mengecualikan SMD', () => {
    expect(smdActive(sel({ smd: false }), none, null)).toBe(false);
    expect(smdActive(sel(), none, 'cat')).toBe(true);
    expect(smdActive(sel(), { cat: ['Roti'], product: [] }, 'cat')).toBe(false);
    expect(smdActive(sel(), { cat: ['Roti'], product: [] }, 'product')).toBe(true);
    expect(smdActive(sel(), { cat: [], product: ['pb:X'] }, 'product')).toBe(false);
    expect(smdActive(sel(), { cat: ['Roti'], product: [] }, null)).toBe(true);
  });
  it('mode kartu F&B-saja', () => {
    expect(esbOnlyMode(sel({ smd: false }))).toBe('esb');
    expect(esbOnlyMode(sel())).toBe('mixed');
    expect(esbOnlyMode(sel({ esb: false }))).toBe('only-smd');
  });
  it('catatan pengecualian hanya bila SMD dipilih dan filter ESB-saja aktif', () => {
    expect(smdExcludedNote(sel(), { cat: ['Roti'], product: [] }, 'cat')).toContain('kategori menu');
    expect(smdExcludedNote(sel(), { cat: [], product: ['pb:X'] }, 'product')).toContain('produk FIN');
    expect(smdExcludedNote(sel(), none, 'cat')).toBeNull();
    expect(smdExcludedNote(sel({ smd: false }), { cat: ['Roti'], product: [] }, 'cat')).toBeNull();
  });
});

describe('mergeSides / allOk (kegagalan sebagian)', () => {
  const ok = <T,>(data: T) => ({ ok: true as const, data });
  const bad = { ok: false as const, ref: 'r1' };
  it('dua sisi berhasil: tanpa catatan', () => {
    const m = mergeSides(true, ok([1]), true, ok([2]));
    expect(m).toEqual({ esb: [1], smd: [2], note: null, fail: null });
  });
  it('satu sisi gagal: kartu tetap tampil + catatan; sisi nonaktif diabaikan', () => {
    expect(mergeSides(true, bad, true, ok([2]))).toMatchObject({ esb: null, smd: [2], fail: null });
    expect(mergeSides(true, bad, true, ok([2])).note).toContain('F&B tidak tersedia');
    expect(mergeSides(true, ok([1]), true, bad).note).toContain('SMD tidak tersedia');
    expect(mergeSides(true, ok([1]), false, bad)).toEqual({ esb: [1], smd: null, note: null, fail: null });
  });
  it('semua sisi aktif gagal -> fail membawa ref', () => {
    expect(mergeSides(true, bad, true, { ok: false, ref: 'r2', reason: 'rate_limited' }).fail).toEqual({ ref: 'r1' });
    expect(mergeSides(false, null, true, { ok: false, ref: 'r2', reason: 'rate_limited' }).fail).toEqual({ ref: 'r2', reason: 'rate_limited' });
  });
  it('allOk: gagal pertama menang', () => {
    expect(allOk([ok(1), ok(2)])).toEqual({ ok: true, data: [1, 2] });
    expect(allOk([ok(1), bad, { ok: false, ref: 'x' }])).toBe(bad);
  });
});

describe('KPI Ringkasan: gabungan per PT (SMD termasuk PPN, HPP tanpa pajak)', () => {
  const esb = esbParts({ net_cur: 1000, net_prev: 800, trx_cur: 100, trx_prev: 90, cogs_cur: 400, cogs_prev: 360 });
  const daily = [
    { day: '2026-10-25', rev_sum: 500, orders_sum: 5 }, // periode lalu
    { day: '2026-11-02', rev_sum: 1100, orders_sum: 10 },
    { day: '2026-11-03', rev_sum: 1100, orders_sum: 10 },
  ];
  it('splitDaily memisah periode ini (>= from) dan lalu', () => {
    expect(splitDaily(daily, '2026-11-01')).toEqual({ rev: 2200, revPrev: 500, trx: 20, trxPrev: 5 });
  });
  it('omzet dan transaksi dijumlah; HPP% = Σ cogs / Σ net TANPA pajak', () => {
    const smd = smdParts(daily, '2026-11-01', { net_cur: 2000, net_prev: 450 }, { cogs_cur: 1000, cogs_prev: 200 });
    const p = sumParts(esb, smd);
    expect(p.rev).toBe(1000 + 2200); // SMD incl PPN ikut di total (diberi label basis campuran di UI)
    expect(p.trx).toBe(120);
    expect(p.base).toBe(3000); // penyebut memakai net tanpa PPN SMD (2000), bukan 2200
    expect(hppPct(p).cur).toBeCloseTo((1400 / 3000) * 100, 6);
    expect(hppPct(p).prev).toBeCloseTo((560 / 1250) * 100, 6);
  });
  it('hanya ESB: sama dengan perilaku lama', () => {
    expect(hppPct(esb).cur).toBeCloseTo(40, 6);
    expect(hppPct(sumParts(esb, esbParts(null))).cur).toBeCloseTo(40, 6);
  });
  it('penyebut nol -> null, bukan 0', () => {
    expect(hppPct(esbParts(null)).cur).toBeNull();
  });
});

describe('tren: seri per brand termasuk SMD', () => {
  it('smdTrendRows -> satu garis "Supermarket (SMD)" (c7) bersama brand ESB', () => {
    const rows = [
      { sales_date: '2026-11-01', brand: 'pb', net: 10, trx: 1 },
      { sales_date: '2026-11-02', brand: 'pb', net: 20, trx: 2 },
      ...smdTrendRows([
        { day: '2026-11-01', rev_sum: 30, orders_sum: 3 },
        { day: '2026-11-02', rev_sum: 40, orders_sum: 4 },
      ]),
    ];
    const t = trendSeries(rows, '2026-11-01', '2026-11-02', 'net');
    expect(t.series.map((s) => s.name)).toEqual(['Paris Baguette', 'Supermarket (SMD)', 'Periode lalu (total)']);
    expect(t.series[1].data).toEqual([30, 40]);
    expect(t.series[1].color).toBe('c7');
  });

  it('segmentTrendSeries: subtotal F&B menjumlah semua brand ESB, nama & warna ditimpa', () => {
    const fb = [
      { sales_date: '2026-11-01', brand: 'pb', net: 10, trx: 1 },
      { sales_date: '2026-11-01', brand: 'wtz', net: 5, trx: 2 },
      { sales_date: '2026-11-02', brand: 'pb', net: 20, trx: 2 },
    ];
    const s = segmentTrendSeries(fb, '2026-11-01', '2026-11-02', 'net', 'Subtotal F&B', 'accent');
    expect(s.name).toBe('Subtotal F&B');
    expect(s.color).toBe('accent');
    expect(s.data).toEqual([15, 20]);
    expect(segmentTrendSeries(fb, '2026-11-01', '2026-11-02', 'trx', 'x', 'c7').data).toEqual([3, 2]);
  });
});

describe('segmen: total + subtotal (Q-SM46)', () => {
  it('tagSegment & withSegmentColumn: teks "F&B"/"Supermarket", kolom Segmen setelah kolom pertama', () => {
    expect(tagSegment([{ a: 1 }], 'fb')[0].segment).toBe('F&B');
    expect(tagSegment([{ a: 1 }], 'sm')[0].segment).toBe('Supermarket');
    const cols = withSegmentColumn([{ key: 'a', header: 'A' }, { key: 'b', header: 'B' }]);
    expect(cols.map((c) => c.key)).toEqual(['a', 'segment', 'b']);
    expect(cols[1]).toMatchObject({ header: 'Segmen', fmt: 'segment' });
    expect(segBadgeClass('Supermarket')).toBe('seg-badge seg-badge--sm');
    expect(segBadgeClass('F&B')).toBe('seg-badge');
  });

  it('segmentFooter: subtotal per segmen + total; angka string numerik dijumlah; semua-null tetap null', () => {
    const esb = [
      { n: 'a', amt: 100, cnt: '2', mdr: 3 },
      { n: 'b', amt: '50', cnt: 1, mdr: 1 },
    ];
    const smd = [{ n: 'c', amt: 20, cnt: 4, mdr: null }];
    const f = segmentFooter(esb, smd, {
      labelKey: 'n',
      sumKeys: ['amt', 'cnt', 'mdr'],
      totalLabel: 'Total',
      derive: (kind, s) => ({ avg: kind === 'total' ? null : Number(s.amt) / Number(s.cnt) }),
    });
    expect(f.map((r) => r.n)).toEqual(['Subtotal F&B', 'Subtotal Supermarket', 'Total']);
    expect(f[0]).toMatchObject({ segment: 'F&B', amt: 150, cnt: 3, mdr: 4, avg: 50 });
    expect(f[1]).toMatchObject({ segment: 'Supermarket', amt: 20, cnt: 4, mdr: null, avg: 5 });
    expect(f[2]).toMatchObject({ segment: null, amt: 170, cnt: 7, mdr: 4, avg: null });
  });

  it('segmentFooter: segmen kosong dilewati; total hanya bila kedua segmen ada', () => {
    const spec = { labelKey: 'n', sumKeys: ['v'], totalLabel: 'Total' };
    expect(segmentFooter([{ v: 1 }], [], spec).map((r) => r.n)).toEqual(['Subtotal F&B']);
    expect(segmentFooter([], [{ v: 1 }], spec).map((r) => r.n)).toEqual(['Subtotal Supermarket']);
    expect(segmentFooter([], [], spec)).toEqual([]);
    expect(segmentFooter([{ v: 1 }], [{ v: 2 }], { labelKey: 'n', sumKeys: ['v'] }).map((r) => r.n)).toEqual(['Subtotal F&B', 'Subtotal Supermarket']);
  });

  it('segParts: F&B lalu Supermarket; catatan & fmt hanya bila diberikan', () => {
    expect(segParts(9, 2, { fmt: 'idr-short', smNote: 'termasuk PPN' })).toEqual([
      { label: 'F&B', value: 9, fmt: 'idr-short' },
      { label: 'Supermarket', value: 2, fmt: 'idr-short', note: 'termasuk PPN' },
    ]);
    expect(segParts(null, 1)).toEqual([{ label: 'F&B', value: null }, { label: 'Supermarket', value: 1 }]);
  });

  it('HPP% per segmen memakai penyebut tanpa pajak masing-masing', () => {
    const e = esbParts({ net_cur: 1000, net_prev: 0, trx_cur: 1, trx_prev: 0, cogs_cur: 300, cogs_prev: 0 });
    const s = smdParts([{ day: '2026-11-02', rev_sum: 2200, orders_sum: 2 }], '2026-11-01', { net_cur: 2000, net_prev: 0 }, { cogs_cur: 1000, cogs_prev: 0 });
    const parts = segParts(hppPct(e).cur, hppPct(s).cur, { fmt: 'pct' });
    expect(parts[0].value).toBeCloseTo(30);
    expect(parts[1].value).toBeCloseTo(50); // 1000 / 2000 (tanpa PPN), bukan / 2200
  });

  it('ptShareSubtotals memisahkan SMD dari PT F&B', () => {
    expect(ptShareSubtotals([{ company_code: 'PB', net: 5 }, { company_code: 'WTZ', net: 3 }, { company_code: 'SMD', net: 7 }])).toEqual({ fb: 8, sm: 7 });
    expect(ptShareSubtotals([])).toEqual({ fb: 0, sm: 0 });
  });

  it('mergeCategories menandai segmen; warna kode SMD = c7, ESB bukan c7', () => {
    const c = mergeCategories([{ category: 'Roti', net_sales: 1, qty_sold: 1, margin_pct: null }], [{ categ_id: 1, category_name: 'Susu', rev_sum: 1, qty_sum: 1 }]);
    expect(c.map((i) => i.segment)).toEqual(['fb', 'sm']);
    expect(codeColor('SMD')).toBe('c7');
    expect(['PB', 'WTZ', 'BCF', 'BCR', 'IMD', 'CUP'].map(codeColor)).not.toContain('c7');
    expect(brandName('smd')).toBe('Supermarket (SMD)');
  });
});

describe('peringkat cabang dan kontribusi PT', () => {
  it('outlet SMD: kunci sm:<id>, kode SMD, label menyebut PPN, HPP dari margin tanpa PPN', () => {
    const rows = smdBranchRows(
      [{ warehouse_id: 21, rev_cur: 1100, orders_cur: 10 }],
      [{ warehouse_id: 21, net_cur: 1000, gm_cur: 300 }],
      new Map([[21, 'Outlet Kelapa Gading']]),
    );
    expect(rows[0]).toMatchObject({ branch_key: 'sm:21', company_code: 'SMD', net_sales: 1100, trx: 10, avg_trx: 110 });
    expect(String(rows[0].branch_label)).toContain('termasuk PPN');
    expect(rows[0].hpp_pct).toBeCloseTo(70, 6);
  });
  it('tanpa data margin: HPP% null; nama tidak dikenal -> "Outlet #id"', () => {
    const rows = smdBranchRows([{ warehouse_id: 9, rev_cur: 10, orders_cur: 0 }], [], new Map());
    expect(rows[0].hpp_pct).toBeNull();
    expect(rows[0].avg_trx).toBeNull();
    expect(String(rows[0].branch_label)).toContain('Outlet #9');
  });
  it('mergeBranchRank mengurutkan menurun lintas PT; mergePtShare menambah baris SMD', () => {
    const merged = mergeBranchRank([{ net_sales: 5 }, { net_sales: 50 }], [{ net_sales: 20 }]);
    expect(merged.map((r) => r.net_sales)).toEqual([50, 20, 5]);
    expect(mergePtShare([{ company_code: 'PB', net: 10 }], 30)).toEqual([
      { company_code: 'SMD', net: 30 },
      { company_code: 'PB', net: 10 },
    ]);
    expect(mergePtShare([{ company_code: 'PB', net: 10 }], null)).toEqual([{ company_code: 'PB', net: 10 }]);
  });
});

describe('Penjualan: KPI, hari, heatmap, kategori', () => {
  it('addSales menjumlah; margin = Σ gm / Σ net tanpa pajak', () => {
    const e = esbSalesSums({ net_cur: 1000, net_prev: 0, gross_cur: 1100, gross_prev: 0, discount_cur: 100, discount_prev: 0, qty_cur: 50, qty_prev: 0, cogs_cur: 600, cogs_prev: 0 });
    const s = smdSalesSums(
      [{ day: '2026-11-02', rev_sum: 2200, orders_sum: 1 }],
      '2026-11-01',
      { gross_cur: 2000, gross_prev: 0, disc_cur: 100, disc_prev: 0, qty_cur: 70, qty_prev: 0 },
      { net_cur: 2000, net_prev: 0, gm_cur: 500, gm_prev: 0 },
    );
    const p = addSales(e, s);
    expect(p.net).toBe(3200);
    expect(p.qty).toBe(120);
    expect(p.disc / p.gross).toBeCloseTo(200 / 3100, 6);
    expect(p.gm / p.base).toBeCloseTo((400 + 500) / 3000, 6);
  });
  it('mergeWeekday: ESB avg + SMD (rev/hari); null bila keduanya kosong', () => {
    const v = mergeWeekday([{ dow: 1, avg_net: 100 }], [{ dow: 1, rev_sum: 300, days_n: 3 }, { dow: 2, rev_sum: 50, days_n: 1 }]);
    expect(v).toHaveLength(7);
    expect(v[0]).toBe(200);
    expect(v[1]).toBe(50);
    expect(v[2]).toBeNull();
  });
  it('mergeHeat menjumlah sel (minggu, hari) yang sama', () => {
    const h = mergeHeat([{ week: '2026-11-02', dow: 1, net: 10 }], [{ week_key: '2026-11-02', dow: 1, rev_sum: 5 }, { week_key: '2026-11-02', dow: 2, rev_sum: 7 }]);
    expect(h).toEqual([
      { week: '2026-11-02', dow: 1, net: 15 },
      { week: '2026-11-02', dow: 2, net: 7 },
    ]);
  });
  it('mergeCategories: kategori SMD berawalan "SMD:" dan tidak bisa di-drill; ESB tanpa nama -> "Tanpa kategori"', () => {
    const c = mergeCategories(
      [{ category: 'Roti', net_sales: 10, qty_sold: 2, margin_pct: 40 }, { category: '', net_sales: 1, qty_sold: 1, margin_pct: null }],
      [{ categ_id: 4, category_name: 'Susu', rev_sum: 99, qty_sum: 9 }],
    );
    expect(c.map((i) => [i.label, i.drillable])).toEqual([['Roti', true], ['Tanpa kategori', false], ['SMD: Susu', false]]);
    expect(c[2].margin).toBeNull();
  });
});

describe('Pembayaran SMD', () => {
  const matrix = [
    { warehouse_id: 21, method_name: 'Tunai', method_type: 'cash', amount_sum: 100, pay_n: 4 },
    { warehouse_id: 22, method_name: 'Tunai', method_type: 'cash', amount_sum: 50, pay_n: 1 },
    { warehouse_id: 21, method_name: 'QRIS', method_type: 'bank', amount_sum: 300, pay_n: 3 },
  ];
  it('tabel metode: digabung antar outlet, MDR kosong (null), tipe berawalan "SMD - "', () => {
    const rows = smdPayMethodRows(matrix);
    const cash = rows.find((r) => r.method === 'Tunai');
    expect(cash).toMatchObject({ payment_type: 'SMD - Tunai', cnt: 5, amount_total: 150, mdr_total: null, mdr_pct: null });
    expect(cash?.avg_amount).toBe(30);
    expect(rows).toHaveLength(2);
  });
  it('tabel cabang: metode dominan per outlet, kunci sm:<id>', () => {
    const rows = smdPayBranchRows(matrix, new Map([[21, 'Kelapa Gading']]));
    const r21 = rows.find((r) => r.branch_key === 'sm:21');
    expect(r21).toMatchObject({ amount: 400, top_method: 'QRIS', mdr: null });
    expect(String(r21?.branch_name)).toContain('Kelapa Gading');
  });
  it('tren: tipe berawalan "SMD - " agar tidak menyatu dengan tipe ESB', () => {
    const t = smdPayTrendRows([{ day: '2026-11-02', method_type: 'pay_later', amount_sum: 9, pay_n: 1 }, { day: '2026-11-02', method_type: 'x', amount_sum: 1, pay_n: 1 }]);
    expect(t.map((r) => r.payment_type)).toEqual(['SMD - Bayar nanti', 'SMD - x']);
  });
  it('QA G51: method_type dari mart = payment_method_type Odoo 19 (none/terminal/qr_code), bukan mentah "none"', () => {
    const t = smdPayTrendRows([
      { day: '2026-11-02', method_type: 'none', amount_sum: 1, pay_n: 1 },
      { day: '2026-11-02', method_type: 'terminal', amount_sum: 1, pay_n: 1 },
      { day: '2026-11-02', method_type: 'qr_code', amount_sum: 1, pay_n: 1 },
      { day: '2026-11-02', method_type: '', amount_sum: 1, pay_n: 1 },
    ]);
    expect(t.map((r) => r.payment_type)).toEqual([
      'SMD - Manual (tunai / transfer / kartu)',
      'SMD - Terminal kartu (EDC)',
      'SMD - QR code',
      'SMD - Tidak diketahui',
    ]);
  });
});

describe('opsi cabang SMD (daftar Cabang)', () => {
  it('hanya outlet (DC tidak), nilai sm:<id>, kode SMD, nonaktif diberi tanda, duplikat dibuang', () => {
    const o = smdBranchOptions([
      { warehouse_id: 20, warehouse_name: 'DC Pusat', outlet_type: 'dc', active: 1 },
      { warehouse_id: 21, warehouse_name: 'Outlet A', outlet_type: 'outlet', active: 1 },
      { warehouse_id: 21, warehouse_name: 'Outlet A', outlet_type: 'outlet', active: 1 },
      { warehouse_id: 22, warehouse_name: 'Outlet B', outlet_type: 'outlet', active: 0 },
      { warehouse_id: 0, warehouse_name: 'X', outlet_type: 'outlet', active: 1 },
    ]);
    expect(o.map((x) => x.value)).toEqual(['sm:21', 'sm:22']);
    expect(o.every((x) => x.code === 'SMD')).toBe(true);
    expect(o[1].label).toBe('Outlet B (nonaktif)');
  });
});
