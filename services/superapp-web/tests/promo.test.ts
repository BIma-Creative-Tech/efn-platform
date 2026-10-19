/** Kode promo & baris reward (docs/04 §11.2). */
import { describe, expect, it } from 'vitest';
import { normalizePromoCode, parseQuote, quoteHasIssues, sanitizePromoCodes } from '@/lib/cart';

const QUOTE_ID = '3f2b8c1e-9a4d-4c7e-8b21-5d6e7f8a9b0c';

const baseGroup = {
  channel_code: 'GL',
  company_code: 'GL',
  outlet_code: 'TOKO-01',
  fulfilment: 'pickup',
  lines: [
    { product_id: 11, name: 'Susu', qty: 2, price_unit_incl: 19900, subtotal_incl: 39800, tax_labels: ['PPN 11%'], available: true },
    { product_id: 900, name: 'Diskon HEMAT5', qty: 1, price_unit_incl: -5000, subtotal_incl: -5000, tax_labels: [], available: true, reward: true },
  ],
  tax_groups: [],
  amount_total: 34800,
  amount_payable: 34800,
  issues: [],
};
const quote = (promo: unknown) => ({ quote_id: QUOTE_ID, valid_until: null, cart_version: 1, groups: [baseGroup], amount_payable_total: 34800, promo });

describe('normalizePromoCode / sanitizePromoCodes', () => {
  it('trim + huruf besar; format salah -> null', () => {
    expect(normalizePromoCode('  hemat5 ')).toBe('HEMAT5');
    expect(normalizePromoCode('a_b-1')).toBe('A_B-1');
    for (const bad of ['', 'a b', 'kode!', 'x'.repeat(41), 5, null]) expect(normalizePromoCode(bad)).toBeNull();
  });
  it('maks 3, duplikat dibuang, satu kode salah menolak semua; kosong/undefined sah', () => {
    expect(sanitizePromoCodes(undefined)).toEqual([]);
    expect(sanitizePromoCodes([])).toEqual([]);
    expect(sanitizePromoCodes(['a', 'A', 'b'])).toEqual(['A', 'B']);
    expect(sanitizePromoCodes(['a', 'b', 'c', 'd'])).toBeNull();
    expect(sanitizePromoCodes(['a', 'bad code'])).toBeNull();
    expect(sanitizePromoCodes('A')).toBeNull();
  });
});

describe('parseQuote promo & reward', () => {
  it('baris reward ditandai dan tidak memblokir checkout', () => {
    const q = parseQuote(quote({ applied: ['hemat5'], invalid: ['x1'] }))!;
    expect(q.groups[0]!.lines.map((l) => l.reward)).toEqual([false, true]);
    expect(q.groups[0]!.lines[1]!.subtotal_incl).toBe(-5000);
    expect(q.promo).toEqual({ applied: ['HEMAT5'], invalid: ['X1'] });
    expect(quoteHasIssues(q)).toBe(false); // kode invalid bukan issue
  });
  it('promo tidak ada / rusak -> kosong; maks 3 kode', () => {
    expect(parseQuote(quote(undefined))!.promo).toEqual({ applied: [], invalid: [] });
    expect(parseQuote(quote('x'))!.promo).toEqual({ applied: [], invalid: [] });
    expect(parseQuote(quote({ applied: ['a', 'b', 'c', 'd'], invalid: [{}] }))!.promo).toEqual({ applied: ['A', 'B', 'C'], invalid: [] });
  });
  it('baris reward yang available=false tidak dianggap masalah, baris biasa tetap', () => {
    const g = { ...baseGroup, lines: [{ ...baseGroup.lines[1]!, available: false }] };
    expect(quoteHasIssues(parseQuote({ ...quote(undefined), groups: [g] })!)).toBe(false);
  });
});
