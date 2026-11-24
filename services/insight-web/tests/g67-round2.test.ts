/**
 * G67 putaran perbaikan (QA G67): irisan cakupan fill rate, keadaan "belum matang", filter Pemasok pada tabel produk,
 * dan pemetaan seluruh kunci `summaries` ESB. Fixture sintetis.
 */
import { describe, expect, it } from 'vitest';
import { COVERAGE_KEYS, coverageIntersectHint, fillRateEmptyKind } from '@/lib/g67-vm';
import { PL_KEYS, PL_SECTION_LABEL, PL_SECTION_ORDER, plKeyOf, plKeySql } from '@/lib/pnl';
import { QUERIES } from '@/lib/queries';

const row = (entity: string, first: string, last: string) => ({ brand: 'pb', company_code: 'PB', entity, first_day: first, last_day: last, days: 10 });

describe('coverageIntersectHint (fill rate = irisan permintaan dan kiriman)', () => {
  it('memakai awal terakhir s.d. akhir terawal, bukan gabungan', () => {
    const rows = [row('transfer_request', '2026-01-01', '2026-11-20'), row('delivery_ref', '2026-06-01', '2026-11-10')];
    const h = coverageIntersectHint(rows, COVERAGE_KEYS.fill) ?? '';
    expect(h).toMatch(/^Data tersedia /);
    expect(h).toContain('irisan');
    expect(h).toContain('1 Jun'); // awal = yang terlambat (kiriman)
    expect(h).toContain('10 Nov 2026'); // akhir = yang terawal (kiriman)
    expect(h).not.toContain('20 Nov');
    expect(h).not.toContain('1 Jan');
  });
  it('irisan kosong -> kalimat jujur; satu sumber tak dikenali atau tanpa baris -> null', () => {
    const apart = [row('transfer_request', '2026-01-01', '2026-03-01'), row('delivery_ref', '2026-06-01', '2026-11-10')];
    expect(coverageIntersectHint(apart, COVERAGE_KEYS.fill)).toContain('belum beririsan');
    expect(coverageIntersectHint([row('transfer_request', '2026-01-01', '2026-03-01')], COVERAGE_KEYS.fill)).toBeNull();
    expect(coverageIntersectHint(null, COVERAGE_KEYS.fill)).toBeNull();
    expect(coverageIntersectHint([], [])).toBeNull();
  });
});

describe('fillRateEmptyKind', () => {
  it('ada angka -> data; hanya baris belum matang -> immature; selain itu none', () => {
    expect(fillRateEmptyKind(10, false, 0, 0)).toBe('data');
    expect(fillRateEmptyKind(0, true, 0, 5)).toBe('data');
    expect(fillRateEmptyKind(0, false, 3, 0)).toBe('data');
    expect(fillRateEmptyKind(0, false, 0, 7)).toBe('immature');
    expect(fillRateEmptyKind(0, false, 0, 0)).toBe('none');
  });
});

describe('fr_top_short_products dan filter Pemasok', () => {
  it('view produk bulanan tanpa kolom pemasok: query tidak menerima param supplier (halaman melewati query + catatan UI)', () => {
    expect(QUERIES.fr_top_short_products.params).not.toContain('supplier');
    expect(QUERIES.fr_top_short_products.sql).not.toContain('supplier');
    for (const n of ['fr_kpi', 'fr_trend_weekly', 'fr_by_outlet', 'fr_by_supplier'] as const) expect(QUERIES[n].params).toContain('supplier');
  });
});

describe('PL_SYNONYMS vs kunci summaries ESB', () => {
  it('kunci yang dipakai KPI terpetakan', () => {
    expect(plKeyOf('netSales')).toBe('net_sales');
    expect(plKeyOf('grossProfit')).toBe('gross_profit');
    expect(plKeyOf('operationalExpenses')).toBe('opex');
    expect(plKeyOf('earningBeforeInterestTaxDepreciationAmortization')).toBe('ebitda');
    expect(plKeyOf('netProfitLoss')).toBe('net_profit');
  });
  it('kunci lain tidak salah masuk KPI (tetap tampil di tabel dengan label Indonesia)', () => {
    for (const k of ['sales', 'discounts', 'cogs', 'operationalProfitLoss', 'otherExpenses', 'earningBeforeInterestTax', 'earningBeforeTax', 'taxes', 'interests']) {
      expect(plKeyOf(k), k).toBeNull();
      expect(PL_SECTION_ORDER, k).toContain(k);
      expect(PL_SECTION_LABEL[k], k).toBeTruthy();
    }
  });
  it('SQL memuat seluruh kunci KPI dan tidak memuat kunci non-KPI', () => {
    const sql = plKeySql();
    for (const k of PL_KEYS) expect(sql).toContain(`'${k}'`);
    for (const bad of ["'sales'", "'cogs'", "'discounts'", "'operationalprofitloss'", "'earningbeforetax'"]) expect(sql).not.toContain(bad);
  });
});
