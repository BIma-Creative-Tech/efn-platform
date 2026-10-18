// G19 perbaikan: issue qty_limit dan retensi kode promo (fungsi murni).
import { describe, expect, it } from 'vitest';
import { issueLabel, parseIssues, parseQuote, quoteHasIssues, retainPromoCodes } from '@/lib/cart';

const quote = {
  quote_id: '3f1c2d4e-5a6b-4c7d-8e9f-0a1b2c3d4e5f',
  cart_version: 1,
  amount_payable_total: 1000,
  groups: [
    {
      channel_code: 'GL',
      company_code: 'GL',
      outlet_code: 'TOKO-01',
      fulfilment: 'pickup',
      lines: [],
      tax_groups: [],
      amount_total: 1000,
      amount_payable: 1000,
      issues: [{ product_id: 11, code: 'qty_limit' }],
    },
  ],
};

describe('issue qty_limit', () => {
  it('lolos allowlist dan memblokir checkout', () => {
    expect(parseIssues([{ product_id: 11, code: 'qty_limit' }])).toEqual([{ product_id: 11, code: 'qty_limit' }]);
    expect(quoteHasIssues(parseQuote(quote)!)).toBe(true);
  });
  it('kode tak dikenal dinormalkan tetapi tetap issue', () => {
    expect(parseIssues([{ product_id: 5, code: '<script>' }])).toEqual([{ product_id: 5, code: 'unknown_issue' }]);
  });
  it('label per baris', () => {
    expect(issueLabel('qty_limit')).toContain('Jumlah melebihi batas pembelian');
  });
});

describe('retainPromoCodes', () => {
  it('fallback (429/400) mempertahankan kode lama', () => {
    expect(retainPromoCodes(['HEMAT10'], [], true)).toEqual(['HEMAT10']);
    expect(retainPromoCodes(['HEMAT10'], null, false)).toEqual(['HEMAT10']);
  });
  it('quote normal mengikuti applied dari server', () => {
    expect(retainPromoCodes(['A', 'B'], ['A'], false)).toEqual(['A']);
    expect(retainPromoCodes([], [], false)).toEqual([]);
  });
  it('mengembalikan salinan, bukan referensi yang sama', () => {
    const cur = ['A'];
    expect(retainPromoCodes(cur, null, true)).not.toBe(cur);
  });
});
