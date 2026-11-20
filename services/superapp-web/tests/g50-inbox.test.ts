import { describe, expect, it } from 'vitest';
import { countNew, isExpired, parseInbox } from '../lib/inbox';
import { parseFeatures } from '../lib/tracking';

const base = { id: 'a1', title: 'Diskon susu', body: 'Hemat 20%', valid_to: '2026-12-01T00:00:00.000Z', created_at: '2026-11-20T01:00:00.000Z' };

describe('parseInbox', () => {
  it('membaca item dengan voucher dan tanpa voucher', () => {
    const items = parseInbox({
      items: [
        { ...base, voucher: { code: 'HEMAT-20', mode: 'anonymous_code' } },
        { ...base, id: 'a2', voucher: null },
      ],
    });
    expect(items).toHaveLength(2);
    expect(items[0]!.voucher).toEqual({ code: 'HEMAT-20', mode: 'anonymous_code' });
    expect(items[1]!.voucher).toBeNull();
  });
  it('membuang item rusak; voucher dengan mode/kode asing menjadi null', () => {
    const items = parseInbox({
      items: [
        { ...base, id: '../x', voucher: null },
        { ...base, valid_to: 'bukan-tanggal', voucher: null },
        { ...base, id: 'b', title: 5, voucher: null },
        { ...base, id: 'c', voucher: { code: 'A B', mode: 'public_code' } },
        { ...base, id: 'd', voucher: { code: 'OK1', mode: 'member_bound' } },
        { ...base, id: 'e', voucher: { code: 'PUBLIK', mode: 'public_code' } },
        null,
      ],
    });
    expect(items.map((i) => i.id)).toEqual(['c', 'd', 'e']);
    expect(items[0]!.voucher).toBeNull();
    expect(items[1]!.voucher).toBeNull();
    expect(items[2]!.voucher).toEqual({ code: 'PUBLIK', mode: 'public_code' });
  });
  it('input sampah -> []', () => {
    for (const v of [null, undefined, 'x', 3, {}, { items: 'x' }]) expect(parseInbox(v)).toEqual([]);
  });
  it('maksimal 50 item', () => {
    const items = Array.from({ length: 80 }, (_, i) => ({ ...base, id: `i${i}`, voucher: null }));
    expect(parseInbox({ items })).toHaveLength(50);
  });
});

describe('countNew / isExpired', () => {
  const items = parseInbox({
    items: [
      { ...base, id: 'n1', created_at: '2026-11-20T05:00:00.000Z', voucher: null },
      { ...base, id: 'n2', created_at: '2026-11-19T05:00:00.000Z', voucher: null },
    ],
  });
  it('menghitung item lebih baru dari penanda', () => {
    expect(countNew(items, '2026-11-20T00:00:00.000Z')).toBe(1);
    expect(countNew(items, null)).toBe(2);
    expect(countNew(items, 'rusak')).toBe(2);
  });
  it('kedaluwarsa', () => {
    expect(isExpired(items[0]!, Date.parse('2026-12-02T00:00:00Z'))).toBe(true);
    expect(isExpired(items[0]!, Date.parse('2026-11-30T00:00:00Z'))).toBe(false);
  });
});

describe('features.campaign_inbox', () => {
  it('hanya true yang menyalakan', () => {
    expect(parseFeatures({ features: { campaign_inbox: true } }).campaignInbox).toBe(true);
    expect(parseFeatures({ features: { campaign_inbox: 'true' } }).campaignInbox).toBe(false);
    expect(parseFeatures({}).campaignInbox).toBe(false);
  });
});
