/**
 * Logika murni G67 (docs/04 §31.9-§31.10): filter bulan P&L, pemetaan pos, tabel pos x bulan + "Tidak teralokasi ke outlet",
 * parsing `uclass`/`supplier`, spesifikasi grafik, cakupan, dan glosarium. Fixture sintetis (bukan data ESB).
 */
import { describe, expect, it } from 'vitest';
import { coverageHint, fillRateByOutletSpec, fillRateTrendSpec, isWeekly, parseSuppliers, parseUsageClasses, pctSafe, wasteTrendSpec } from '@/lib/g67-vm';
import { getTerm, termsIn } from '@/lib/glossary';
import {
  buildStatement,
  changePct,
  monthKey,
  parseMonthRange,
  pctOf,
  plKeyOf,
  pnlSides,
  statementColumns,
  UNALLOCATED_LABEL,
  type PnlKpiRaw,
  type StatementRaw,
} from '@/lib/pnl';

const NOW = new Date('2026-11-25T03:00:00Z'); // 25 Nov 2026 WIB

describe('parseMonthRange (/pnl)', () => {
  it('bawaan: 12 bulan terakhir s.d. bulan ini; pembanding setahun sebelumnya', () => {
    const r = parseMonthRange({}, NOW);
    expect(r.mfrom).toBe('2025-12');
    expect(r.mto).toBe('2026-11');
    expect(r.from).toBe('2025-12-01');
    expect(r.to).toBe('2026-11-01');
    expect(r.months).toHaveLength(12);
    expect(r.current).toBe('2026-11-01');
    expect(r.ly).toEqual({ from: '2024-12-01', to: '2025-11-01' });
    expect(r.adjusted).toBe(false);
  });
  it('rentang sah dipakai', () => {
    const r = parseMonthRange({ mfrom: '2026-01', mto: '2026-03' }, NOW);
    expect(r.months).toEqual(['2026-01-01', '2026-02-01', '2026-03-01']);
    expect(r.adjusted).toBe(false);
  });
  it('batas: maksimal 24 bulan, tidak boleh melewati bulan ini, mfrom <= mto, format YYYY-MM', () => {
    expect(parseMonthRange({ mfrom: '2024-12', mto: '2026-11' }, NOW).months).toHaveLength(24);
    for (const raw of [
      { mfrom: '2024-11', mto: '2026-11' }, // 25 bulan
      { mfrom: '2026-01', mto: '2026-12' }, // masa depan
      { mfrom: '2026-05', mto: '2026-02' }, // terbalik
      { mfrom: 'abc', mto: '2026-02' },
      { mfrom: '2026-13', mto: '2026-11' },
      { mfrom: '2026-1', mto: '2026-11' },
      { mfrom: "2026-01'; DROP", mto: '2026-02' },
    ]) {
      const r = parseMonthRange(raw, NOW);
      expect(r.adjusted, JSON.stringify(raw)).toBe(true);
      expect(r.mfrom).toBe('2025-12');
      expect(r.mto).toBe('2026-11');
    }
  });
  it('hanya salah satu terisi: sisi lain bawaan; param berulang memakai nilai pertama', () => {
    expect(parseMonthRange({ mfrom: '2026-05' }, NOW).months).toHaveLength(7);
    const r = parseMonthRange({ mto: '2026-06' }, NOW);
    expect(r.mfrom).toBe('2025-07');
    expect(r.mto).toBe('2026-06');
    expect(parseMonthRange({ mfrom: ['2026-02', '2025-01'], mto: '2026-03' }, NOW).months).toHaveLength(2);
  });
});

describe('pemetaan pos dan rasio', () => {
  it('plKeyOf: sinonim dinormalkan; pos tak dikenal null', () => {
    expect(plKeyOf('Net Sales')).toBe('net_sales');
    expect(plKeyOf('GROSS PROFIT')).toBe('gross_profit');
    expect(plKeyOf('Operating Expense')).toBe('opex');
    expect(plKeyOf('EBITDA')).toBe('ebitda');
    expect(plKeyOf('Laba Bersih')).toBe('net_profit');
    expect(plKeyOf('Net-Profit')).toBe('net_profit');
    expect(plKeyOf('Pos Lain')).toBeNull();
    // Kunci `section` yang disimpan esb-extract (camelCase ESB `summaries`, W-A G67).
    expect(plKeyOf('netSales')).toBe('net_sales');
    expect(plKeyOf('grossProfit')).toBe('gross_profit');
    expect(plKeyOf('operationalExpenses')).toBe('opex');
    expect(plKeyOf('earningBeforeInterestTaxDepreciationAmortization')).toBe('ebitda');
    expect(plKeyOf('netProfitLoss')).toBe('net_profit');
  });
  it('pctOf: null bila penyebut tidak positif; changePct: null bila pembanding nol/tak ada', () => {
    expect(pctOf(50, 200)).toBe(25);
    expect(pctOf(1, 0)).toBeNull();
    expect(pctOf(1, -5)).toBeNull();
    expect(pctOf(Number.NaN, 5)).toBeNull();
    expect(changePct(110, 100)).toBe(10);
    expect(changePct(5, null)).toBeNull();
    expect(changePct(5, 0)).toBeNull();
    expect(changePct(-50, -100)).toBe(50);
  });
  it('pnlSides: persen terhadap penjualan bersih; tahun lalu null bila semua nol', () => {
    const k: PnlKpiRaw = { net_sales: 1000, gross_profit: 600, opex: 400, ebitda: 200, net_profit: 100, net_sales_ly: 0, gross_profit_ly: 0, opex_ly: 0, ebitda_ly: 0, net_profit_ly: 0 };
    const { cur, ly } = pnlSides(k);
    expect(cur.gpPct).toBe(60);
    expect(cur.ebitdaPct).toBe(20);
    expect(cur.npPct).toBe(10);
    expect(ly).toBeNull();
    expect(pnlSides({ ...k, net_sales: 0 }).cur.gpPct).toBeNull();
    expect(pnlSides({ ...k, net_sales_ly: 800 }).ly?.net).toBe(800);
  });
});

describe('buildStatement: pos x bulan + Tidak teralokasi ke outlet', () => {
  const months = ['2026-10-01', '2026-11-01'];
  const rows: StatementRaw[] = [
    { period_month: '2026-10-01', scope: 'branch', section: 'Net Sales', amount_sum: 150 },
    { period_month: '2026-10-01', scope: 'brand', section: 'Net Sales', amount_sum: 160 },
    { period_month: '2026-11-01', scope: 'branch', section: 'Net Sales', amount_sum: 200 },
    { period_month: '2026-11-01', scope: 'brand', section: 'Net Sales', amount_sum: 200 },
    { period_month: '2026-10-01', scope: 'branch', section: 'Gross Profit', amount_sum: 80 },
    { period_month: '2026-10-01', scope: 'branch', section: 'Pos Lain', amount_sum: 5 },
  ];

  it('urutan: pos dikenal dulu; baris selisih tepat setelah pos-nya; total = jumlah bulan', () => {
    const t = buildStatement(rows, months);
    expect(t.map((r) => r.label)).toEqual(['Net Sales', `${UNALLOCATED_LABEL}: Net Sales`, 'Gross Profit', 'Pos Lain']);
    expect(t[0][monthKey('2026-10-01')]).toBe(150);
    expect(t[0][monthKey('2026-11-01')]).toBe(200);
    expect(t[0].total).toBe(350);
    expect(t[1][monthKey('2026-10-01')]).toBe(10);
    expect(t[1][monthKey('2026-11-01')]).toBe(0);
    expect(t[1].total).toBe(10);
    expect(t[2][monthKey('2026-11-01')]).toBeNull();
    expect(t[2].total).toBe(80);
  });
  it('tanpa baris brand (filter cabang terisi): tidak ada baris selisih', () => {
    const t = buildStatement(rows.filter((r) => r.scope === 'branch'), months);
    expect(t.map((r) => r.label)).toEqual(['Net Sales', 'Gross Profit', 'Pos Lain']);
  });
  it('selisih < Rp1 tidak ditampilkan; kosong -> tabel kosong', () => {
    const near: StatementRaw[] = [
      { period_month: '2026-10-01', scope: 'branch', section: 'Net Sales', amount_sum: 100 },
      { period_month: '2026-10-01', scope: 'brand', section: 'Net Sales', amount_sum: 100.4 },
    ];
    expect(buildStatement(near, months).map((r) => r.label)).toEqual(['Net Sales']);
    expect(buildStatement([], months)).toEqual([]);
  });
  it('kolom: Pos, satu per bulan (idr), Total periode', () => {
    const c = statementColumns(months);
    expect(c.map((x) => x.key)).toEqual(['label', 'm_2026_10', 'm_2026_11', 'total']);
    expect(c[1].header).toMatch(/2026/);
    expect(c[1].fmt).toBe('idr');
  });
});

describe('parseUsageClasses / parseSuppliers / isWeekly (/waste, /fill-rate)', () => {
  it('uclass: bawaan waste + internal_use; himpunan bagian kanonik; tak sah -> bawaan atau dibuang', () => {
    expect(parseUsageClasses({})).toEqual({ uclass: ['waste', 'internal_use'], adjusted: false });
    expect(parseUsageClasses({ uclass: 'rnd,waste' })).toEqual({ uclass: ['waste', 'rnd'], adjusted: false });
    expect(parseUsageClasses({ uclass: ['unclassified', 'other'] }).uclass).toEqual(['other', 'unclassified']);
    expect(parseUsageClasses({ uclass: 'bogus' })).toEqual({ uclass: ['waste', 'internal_use'], adjusted: true });
    expect(parseUsageClasses({ uclass: 'waste,bogus' })).toEqual({ uclass: ['waste'], adjusted: true });
    expect(parseUsageClasses({ uclass: "waste'; DROP" }).adjusted).toBe(true);
  });
  it('supplier: brand:id sah, huruf kecil, unik, maksimal 10; tak sah dibuang', () => {
    expect(parseSuppliers({})).toEqual({ supplier: [], adjusted: false });
    expect(parseSuppliers({ supplier: ['pb:1', 'PB:2', 'pb:1'] }).supplier).toEqual(['pb:1', 'pb:2']);
    const bad = parseSuppliers({ supplier: ['x:1', 'pb:abc', 'pb:12'] });
    expect(bad.supplier).toEqual(['pb:12']);
    expect(bad.adjusted).toBe(true);
    const many = parseSuppliers({ supplier: Array.from({ length: 12 }, (_, i) => `pb:${i + 1}`) });
    expect(many.supplier).toHaveLength(10);
    expect(many.adjusted).toBe(true);
    expect(parseSuppliers({ supplier: 'pb:99999999999' }).supplier).toEqual([]);
  });
  it('isWeekly: > 62 hari', () => {
    expect(isWeekly('2026-09-01', '2026-11-01')).toBe(false); // 62 hari
    expect(isWeekly('2026-09-01', '2026-11-02')).toBe(true); // 63 hari
    expect(isWeekly('2026-11-01', '2026-11-01')).toBe(false);
  });
  it('pctSafe', () => {
    expect(pctSafe(25, 100)).toBe(25);
    expect(pctSafe(5, 0)).toBeNull();
  });
});

describe('spesifikasi grafik G67', () => {
  it('waste: batang bertumpuk per kelas, tanpa kelas yang tidak muncul, nol untuk sel kosong', () => {
    const s = wasteTrendSpec(
      [
        { bucket: '2026-11-01', usage_class: 'waste', value_sum: 100 },
        { bucket: '2026-11-01', usage_class: 'internal_use', value_sum: 50 },
        { bucket: '2026-11-02', usage_class: 'waste', value_sum: 30 },
      ],
      false,
    );
    expect(s.kind).toBe('bar');
    expect(s.series.map((x) => x.name)).toEqual(['Waste', 'Pemakaian internal']);
    expect(s.series[0].data).toEqual([100, 30]);
    expect(s.series[1].data).toEqual([50, 0]);
    expect(s.series.every((x) => x.stack === 'usage')).toBe(true);
    expect(s.summary).toContain('total');
    expect(wasteTrendSpec([], true).summary).toContain('tidak ada data');
    expect(wasteTrendSpec([{ bucket: '2026-11-02', usage_class: 'waste', value_sum: 1 }], true).categories[0]).toMatch(/^Mgg /);
  });
  it('fill rate: tren dua seri; outlet terendah dibatasi dan nilai null dibuang; tanpa warna ambang', () => {
    const t = fillRateTrendSpec([
      { week_start: '2026-11-02', fill_rate_line: 90, pct_lines_full: 80 },
      { week_start: '2026-11-09', fill_rate_line: null, pct_lines_full: null },
    ]);
    expect(t.kind).toBe('line');
    expect(t.series).toHaveLength(2);
    expect(t.series[0].data).toEqual([90, null]);
    const o = fillRateByOutletSpec([{ label: 'A', value: 50 }, { label: 'B', value: null }, { label: 'C', value: 99 }], 2);
    expect(o.categories).toEqual(['A', 'C']);
    expect(o.kind).toBe('barH');
    expect(o.series[0].itemColors).toBeUndefined();
  });
});

describe('coverageHint', () => {
  const row = (entity: string, first: string, last: string) => ({ brand: 'pb', company_code: 'PB', entity, first_day: first, last_day: last, days: 10 });
  it('mencocokkan entitas longgar dan merangkum rentang; tanpa kecocokan atau tanpa baris -> null', () => {
    const h = coverageHint([row('sales', '2026-01-01', '2026-11-20'), row('transfer_request_line', '2026-10-01', '2026-11-20')], ['transfer_request']);
    expect(h).toMatch(/^Data tersedia /);
    expect(h).toContain('2026');
    expect(coverageHint([row('sales', '2026-01-01', '2026-11-20')], ['transfer_request'])).toBeNull();
    expect(coverageHint(null, ['transfer_request'])).toBeNull();
  });
});

describe('glosarium G67', () => {
  it('entri baru tersedia dan id fill-rate-line terpisah dari fill-rate (SMD)', () => {
    for (const id of ['laba-kotor', 'ebitda', 'laba-bersih', 'tidak-teralokasi', 'waste', 'pemakaian-internal', 'fill-rate-line', 'permintaan-penuh', 'waktu-kirim', 'permintaan-matang']) {
      expect(getTerm(id), id).toBeDefined();
    }
    expect(getTerm('fill-rate')?.def).toContain('usulan yang disetujui');
    expect(getTerm('fill-rate-line')?.def).toContain('baris permintaan');
  });
  it('label F&B memilih entri F&B; label SMD tetap memilih fill-rate', () => {
    expect(termsIn('Fill rate outlet (baris)')).toEqual(['fill-rate-line']);
    expect(termsIn('% permintaan penuh')).toEqual(['permintaan-penuh']);
    expect(termsIn('Waktu kirim rata-rata (hari)')).toEqual(['waktu-kirim']);
    expect(termsIn('EBITDA')).toEqual(['ebitda']);
    expect(termsIn('Laba kotor')).toEqual(['laba-kotor']);
    expect(termsIn('Fill rate baris')).toEqual(['fill-rate']);
    expect(termsIn('Fill rate qty')).toEqual(['fill-rate']);
    expect(termsIn('Fill rate per outlet')).toEqual(['fill-rate']);
    expect(termsIn('Waste & Pemakaian Internal').sort()).toEqual(['pemakaian-internal', 'waste']);
  });
});
