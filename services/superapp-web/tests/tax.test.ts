import { describe, expect, it } from 'vitest';
import { computeTax, describeTax, formatTaxRate, isValidTaxConfig, priceTaxHint, type TaxConfig } from '../lib/tax';
import { groupCartByCompany, loadTaxConfigs, MOCK_PRODUCTS, MOCK_TAX_CONFIG } from '../lib/mock-catalog';

const INCL: TaxConfig = { tax_label: 'PPN', rate: 0.11, price_include: true };
const EXCL: TaxConfig = { tax_label: 'PB1', rate: 0.1, price_include: false };

describe('computeTax', () => {
  it('inklusif: pajak bagian dari harga, total tidak bertambah', () => {
    const b = computeTax(111_000, INCL);
    expect(b).toEqual({ listed: 111_000, base: 100_000, tax: 11_000, total: 111_000 });
  });

  it('eksklusif: pajak ditambahkan di atas harga', () => {
    const b = computeTax(100_000, EXCL);
    expect(b).toEqual({ listed: 100_000, base: 100_000, tax: 10_000, total: 110_000 });
  });

  it('inklusif: base + tax selalu = listed (tanpa selisih pembulatan)', () => {
    for (const amt of [1, 99, 19_900, 78_500, 123_457, 9_999_999]) {
      const b = computeTax(amt, INCL);
      expect(b.base + b.tax).toBe(amt);
      expect(b.total).toBe(amt);
    }
  });

  it('eksklusif: membulatkan ke rupiah terdekat', () => {
    expect(computeTax(25_005, EXCL).tax).toBe(2_501); // 2500.5 -> 2501
    expect(computeTax(8_000, { ...EXCL, rate: 0.115 }).tax).toBe(920);
  });

  it('tarif 0 -> tanpa pajak', () => {
    expect(computeTax(50_000, { tax_label: 'Bebas', rate: 0, price_include: true })).toEqual({
      listed: 50_000,
      base: 50_000,
      tax: 0,
      total: 50_000,
    });
  });

  it('input negatif/NaN -> 0', () => {
    expect(computeTax(-10, EXCL).total).toBe(0);
    expect(computeTax(Number.NaN, INCL).total).toBe(0);
  });
});

describe('formatTaxRate / describeTax / priceTaxHint', () => {
  it.each([
    [0.11, '11%'],
    [0.1, '10%'],
    [0.12, '12%'],
    [0.07, '7%'],
    [0.0125, '1,25%'],
    [0, '0%'],
  ])('%s -> %s', (rate, expected) => {
    expect(formatTaxRate(rate)).toBe(expected);
  });

  it('label & tarif berasal dari konfigurasi', () => {
    expect(describeTax(INCL)).toBe('PPN 11% (sudah termasuk)');
    expect(describeTax(EXCL)).toBe('PB1 10% (ditambahkan)');
    expect(describeTax({ tax_label: 'PBJT', rate: 0.12, price_include: false })).toBe('PBJT 12% (ditambahkan)');
    expect(priceTaxHint(INCL)).toBe('termasuk PPN');
    expect(priceTaxHint(EXCL)).toBe('belum termasuk PB1');
  });
});

describe('isValidTaxConfig', () => {
  it('menerima bentuk { tax_label, rate, price_include }', () => {
    expect(isValidTaxConfig(INCL)).toBe(true);
    expect(isValidTaxConfig(EXCL)).toBe(true);
  });
  it.each([
    null,
    {},
    { tax_label: '', rate: 0.1, price_include: true },
    { tax_label: 'PPN', rate: 11, price_include: true }, // persen, bukan desimal
    { tax_label: 'PPN', rate: -0.1, price_include: true },
    { tax_label: 'PPN', rate: Number.NaN, price_include: true },
    { tax_label: 'PPN', rate: '0.11', price_include: true },
    { tax_label: 'PPN', rate: 0.11, price_include: 'true' },
  ])('menolak %j', (v) => {
    expect(isValidTaxConfig(v)).toBe(false);
  });
});

describe('mock-catalog: konfigurasi pajak per PT', () => {
  it('MOCK_TAX_CONFIG valid untuk semua PT', () => {
    for (const cfg of Object.values(MOCK_TAX_CONFIG)) expect(isValidTaxConfig(cfg)).toBe(true);
  });

  it('loadTaxConfigs hanya mengembalikan PT yang diminta', async () => {
    const m = await loadTaxConfigs(['FNB']);
    expect(Object.keys(m)).toEqual(['FNB']);
  });

  it('groupCartByCompany memisahkan PT dan memakai konfigurasi masing-masing', async () => {
    const rtl = MOCK_PRODUCTS.find((p) => p.company === 'RTL')!;
    const fnb = MOCK_PRODUCTS.find((p) => p.company === 'FNB')!;
    const groups = groupCartByCompany(
      [
        { product: rtl, qty: 2 },
        { product: fnb, qty: 1 },
      ],
      { RTL: INCL, FNB: EXCL },
    );
    expect(groups).toHaveLength(2);
    const g1 = groups.find((g) => g.company === 'RTL')!;
    const g2 = groups.find((g) => g.company === 'FNB')!;
    expect(g1.grandTotal).toBe(rtl.price * 2); // inklusif
    expect(g1.base + g1.tax).toBe(rtl.price * 2);
    expect(g2.grandTotal).toBe(fnb.price + Math.round(fnb.price * 0.1)); // eksklusif
  });

  it('tanpa konfigurasi pajak -> tidak memperkirakan pajak', () => {
    const fnb = MOCK_PRODUCTS.find((p) => p.company === 'FNB')!;
    const [g] = groupCartByCompany([{ product: fnb, qty: 1 }], {});
    expect(g!.taxConfig).toBeNull();
    expect(g!.tax).toBe(0);
    expect(g!.grandTotal).toBe(fnb.price);
  });

  it('mengabaikan qty <= 0', () => {
    const fnb = MOCK_PRODUCTS.find((p) => p.company === 'FNB')!;
    expect(groupCartByCompany([{ product: fnb, qty: 0 }], { FNB: EXCL })).toEqual([]);
  });
});
