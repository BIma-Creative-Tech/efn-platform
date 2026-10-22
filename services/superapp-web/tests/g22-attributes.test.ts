/**
 * G22 — modifier F&B online (atribut no_variant, docs/04 §14.B B-2/B-4): parser atribut, pilihan, identitas baris keranjang.
 */
import { describe, expect, it } from 'vitest';
import {
  describeSelection,
  extraExcl,
  normalizeAttributeIds,
  parseAttributes,
  sameIds,
  selectedIds,
  selectValue,
  validateSelection,
  type ProductAttribute,
} from '@/lib/attributes';
import {
  addItem,
  issueLabel,
  lineKey,
  mergeGroups,
  pairQuoteLines,
  parseGuestCart,
  parseIssues,
  parseQuote,
  parseQuoteLines,
  sanitizeCart,
  serializeGuestCart,
  setQty,
  type CartGroup,
} from '@/lib/cart';
import { channelBusinessType, parseChannels, parseProductSummary, priceDisplay } from '@/lib/catalog';

const RAW_ATTRS = [
  {
    attribute_id: 1,
    name: 'Gula',
    display_type: 'radio',
    required: true,
    values: [
      { id: 11, name: 'Normal', price_extra_excl: 0 },
      { id: 12, name: 'Kurang manis', price_extra_excl: 0 },
    ],
  },
  {
    attribute_id: 2,
    name: 'Topping',
    display_type: 'multi',
    required: false,
    values: [
      { id: 21, name: 'Boba', price_extra_excl: 5000 },
      { id: 22, name: 'Keju', price_extra_excl: 3000 },
    ],
  },
  { attribute_id: 3, name: 'Suhu', display_type: 'select', required: false, values: [{ id: 31, name: 'Panas', price_extra_excl: 0 }, { id: 32, name: 'Dingin', price_extra_excl: 0 }] },
];
const attrs = (): ProductAttribute[] => parseAttributes(RAW_ATTRS);

describe('parseAttributes (format A.6)', () => {
  it('mem-parse tipe tampilan, wajib, nilai, tambahan harga', () => {
    const a = attrs();
    expect(a.map((x) => [x.attribute_id, x.display_type, x.required])).toEqual([
      [1, 'radio', true],
      [2, 'multi', false],
      [3, 'select', false],
    ]);
    expect(a[1]!.values[0]).toEqual({ id: 21, name: 'Boba', price_extra_excl: 5000 });
  });
  it('entri rusak dibuang; tipe tak dikenal → radio; atribut tanpa nilai dibuang; harga negatif → 0', () => {
    const a = parseAttributes([
      { attribute_id: 1, name: 'A', display_type: 'warna', values: [{ id: 1, name: 'x', price_extra_excl: -5 }, { id: 1, name: 'dup' }, { name: 'tanpa id' }] },
      { attribute_id: 2, name: 'Kosong', values: [] },
      { attribute_id: 'x', name: 'Rusak', values: [{ id: 1, name: 'x' }] },
      'bukan objek',
      { attribute_id: 1, name: 'Duplikat id', values: [{ id: 9, name: 'y' }] },
    ]);
    expect(a).toHaveLength(1);
    expect(a[0]).toMatchObject({ attribute_id: 1, display_type: 'radio', required: false });
    expect(a[0]!.values).toEqual([{ id: 1, name: 'x', price_extra_excl: 0 }]);
    expect(parseAttributes(null)).toEqual([]);
    expect(parseAttributes('x')).toEqual([]);
  });
});

describe('pilihan atribut', () => {
  it('radio/select = satu nilai; multi = toggle; null mengosongkan', () => {
    const a = attrs();
    let s = selectValue(a, {}, 1, 11);
    s = selectValue(a, s, 1, 12);
    expect(s[1]).toEqual([12]);
    s = selectValue(a, s, 2, 21);
    s = selectValue(a, s, 2, 22);
    expect(s[2]).toEqual([21, 22]);
    s = selectValue(a, s, 2, 21, false);
    expect(s[2]).toEqual([22]);
    s = selectValue(a, s, 2, 22, false);
    expect(s[2]).toBeUndefined();
    s = selectValue(a, s, 1, null);
    expect(s[1]).toBeUndefined();
  });
  it('nilai/atribut yang tidak ada diabaikan', () => {
    const a = attrs();
    expect(selectValue(a, {}, 1, 999)).toEqual({});
    expect(selectValue(a, {}, 99, 11)).toEqual({});
  });
  it('validateSelection: atribut wajib; id terurut; selectedIds membuang id asing', () => {
    const a = attrs();
    expect(validateSelection(a, {})).toEqual({ ok: false, reason: 'missing_required', missing: [1] });
    expect(validateSelection(a, { 1: [12], 2: [22, 21] })).toEqual({ ok: true, ids: [12, 21, 22] });
    expect(validateSelection([], {})).toEqual({ ok: true, ids: [] });
    expect(selectedIds(a, { 1: [11, 12], 2: [21, 777], 99: [5] })).toEqual([11, 21]); // radio: hanya satu; id asing dibuang
  });
  it('extraExcl & describeSelection', () => {
    const a = attrs();
    expect(extraExcl(a, [12, 21, 22])).toBe(8000);
    expect(describeSelection(a, [12, 21, 22])).toBe('Gula: Kurang manis; Topping: Boba, Keju');
    expect(describeSelection(a, [])).toBe('');
  });
  it('normalizeAttributeIds & sameIds', () => {
    expect(normalizeAttributeIds(undefined)).toEqual([]);
    expect(normalizeAttributeIds([3, 1, 3])).toEqual([1, 3]);
    expect(normalizeAttributeIds([0])).toBeNull();
    expect(normalizeAttributeIds(['1'])).toBeNull();
    expect(normalizeAttributeIds('1')).toBeNull();
    expect(normalizeAttributeIds(Array.from({ length: 11 }, (_, i) => i + 1))).toBeNull();
    expect(normalizeAttributeIds(Array.from({ length: 10 }, (_, i) => i + 1))).toHaveLength(10);
    expect(sameIds([1, 2], [2, 1])).toBe(true);
    expect(sameIds([1], [1, 2])).toBe(false);
  });
});

describe('keranjang online dengan modifier (identitas = produk + atribut terurut)', () => {
  const add = (groups: CartGroup[], ids?: number[], qty = 1, product = 50) => {
    const r = addItem(groups, { channel_code: 'PB', outlet_code: 'RESTO-1', product_id: product, qty, attribute_value_ids: ids });
    return r.ok ? r.groups : groups;
  };

  it('produk sama + modifier berbeda = dua baris; urutan id tidak berpengaruh; qty digabung', () => {
    let g = add([], [12, 21]);
    g = add(g, [21, 12], 2);
    g = add(g, [11]);
    g = add(g);
    expect(g[0]!.lines).toEqual([
      { product_id: 50, qty: 3, attribute_value_ids: [12, 21] },
      { product_id: 50, qty: 1, attribute_value_ids: [11] },
      { product_id: 50, qty: 1 },
    ]);
  });
  it('input atribut tidak valid ditolak', () => {
    expect(addItem([], { channel_code: 'PB', outlet_code: 'RESTO-1', product_id: 50, qty: 1, attribute_value_ids: [0] })).toEqual({ ok: false, reason: 'invalid' });
  });
  it('setQty memakai identitas atribut (baris lain tidak tersentuh)', () => {
    let g = add([], [11]);
    g = add(g, [12], 2);
    g = setQty(g, 'PB', 50, 5, undefined, [12]);
    expect(g[0]!.lines.map((l) => [l.attribute_value_ids, l.qty])).toEqual([[[11], 1], [[12], 5]]);
    g = setQty(g, 'PB', 50, 0, undefined, [11]);
    expect(g[0]!.lines).toEqual([{ product_id: 50, qty: 5, attribute_value_ids: [12] }]);
    expect(setQty(g, 'PB', 50, 0, undefined, [])[0]!.lines).toHaveLength(1); // tanpa atribut ≠ baris dengan atribut
  });
  it('mergeGroups (tamu → server) menjaga modifier', () => {
    const server = add([], [11]);
    const guest = add(add([], [11], 2), [12]);
    const merged = mergeGroups(server, guest);
    expect(merged[0]!.lines).toEqual([
      { product_id: 50, qty: 3, attribute_value_ids: [11] },
      { product_id: 50, qty: 1, attribute_value_ids: [12] },
    ]);
  });
  it('sanitizeCart: atribut diurutkan, ritel tetap tanpa field; duplikat identitas & atribut rusak ditolak', () => {
    const group = (lines: unknown[]) => ({ channel_code: 'PB', outlet_code: 'RESTO-1', fulfilment: 'pickup', lines });
    const ok = sanitizeCart({ version: 1, groups: [group([{ product_id: 50, qty: 1, attribute_value_ids: [21, 12], harga: 1 }, { product_id: 50, qty: 1 }, { product_id: 51, qty: 2, attribute_value_ids: [] }])] })!;
    expect(ok.groups[0]!.lines).toEqual([
      { product_id: 50, qty: 1, attribute_value_ids: [12, 21] },
      { product_id: 50, qty: 1 },
      { product_id: 51, qty: 2 },
    ]);
    expect(sanitizeCart({ version: 1, groups: [group([{ product_id: 50, qty: 1, attribute_value_ids: [1] }, { product_id: 50, qty: 2, attribute_value_ids: [1] }])] })).toBeNull();
    expect(sanitizeCart({ version: 1, groups: [group([{ product_id: 50, qty: 1, attribute_value_ids: ['x'] }])] })).toBeNull();
    expect(sanitizeCart({ version: 1, groups: [group([{ product_id: 50, qty: 1, attribute_value_ids: 5 }])] })).toBeNull();
  });
  it('keranjang tamu: ringkasan pilihan ikut tersimpan; tanpa pilihan format lama tetap sama', () => {
    const groups = add([], [12, 21]);
    const key = lineKey(50, [21, 12]);
    expect(key).toBe('50:12.21');
    const s = serializeGuestCart({ groups, names: { '50': 'Es Teh' }, options: { [key]: 'Gula: Kurang manis' } });
    expect(parseGuestCart(s)).toEqual({ groups, names: { '50': 'Es Teh' }, options: { '50:12.21': 'Gula: Kurang manis' } });
    const plain = serializeGuestCart({ groups: [], names: {} });
    expect(JSON.parse(plain)).toEqual({ groups: [], names: {} });
    expect(parseGuestCart(JSON.stringify({ groups, names: {}, options: { 'kunci jelek': 'x', '50:12': 5 } })).options).toBeUndefined();
  });
});

describe('quote dengan modifier', () => {
  const ql = (product_id: number, text: string, extra: Record<string, unknown> = {}) => ({ product_id, name: `P${product_id}`, qty: 1, price_unit_incl: 1000, subtotal_incl: 1000, attributes_text: text, ...extra });

  it('attributes_text di-parse (dipotong, default kosong)', () => {
    const lines = parseQuoteLines([ql(1, 'Gula: Normal'), { ...ql(2, ''), attributes_text: undefined }, ql(3, 'x'.repeat(500))]);
    expect(lines.map((l) => l.attributes_text.length)).toEqual([12, 0, 200]);
  });
  it('pairQuoteLines: baris ke-n produk P ↔ baris quote ke-n produk P; reward diabaikan', () => {
    const quoteLines = parseQuoteLines([ql(50, 'Gula: Kurang manis, Topping: Boba'), ql(60, ''), ql(50, 'Gula: Normal'), { ...ql(50, 'promo'), reward: true, subtotal_incl: -100 }]);
    const cart = [
      { product_id: 50, qty: 1, attribute_value_ids: [12, 21] },
      { product_id: 60, qty: 1 },
      { product_id: 50, qty: 1, attribute_value_ids: [11] },
    ];
    const paired = pairQuoteLines(cart, quoteLines);
    expect(paired.map((p) => p?.attributes_text)).toEqual(['Gula: Kurang manis, Topping: Boba', '', 'Gula: Normal']);
    expect(pairQuoteLines([{ product_id: 999, qty: 1 }], quoteLines)).toEqual([undefined]);
  });
  it('issue attribute_invalid dikenal, berlabel, dan memblokir checkout', () => {
    expect(parseIssues([{ product_id: 50, code: 'attribute_invalid' }])).toEqual([{ product_id: 50, code: 'attribute_invalid' }]);
    expect(issueLabel('attribute_invalid')).toMatch(/Pilihan tambahan/);
    const q = parseQuote({
      quote_id: '9b1d2c3e-4f5a-4b6c-8d7e-0f1a2b3c4d5e',
      cart_version: 1,
      amount_payable_total: 1000,
      groups: [{ channel_code: 'PB', company_code: 'PB', outlet_code: 'RESTO-1', fulfilment: 'pickup', lines: [ql(50, 'x')], tax_groups: [], amount_total: 1000, amount_payable: 1000, issues: [{ product_id: 50, code: 'attribute_invalid' }] }],
    })!;
    expect(q.groups[0]!.issues[0]!.code).toBe('attribute_invalid');
  });
});

describe('katalog F&B (B-2, B-5)', () => {
  it('produk membawa attributes (default [])', () => {
    const p = parseProductSummary({ product_id: 5, name: 'Es Teh', price_incl: 11100, price_excl: 10000, tax_labels: ['PB1 10%'], attributes: RAW_ATTRS })!;
    expect(p.attributes).toHaveLength(3);
    expect(parseProductSummary({ product_id: 5, name: 'Roti', price_incl: 1000 })!.attributes).toEqual([]);
  });
  it('kanal: business_type & loyalty (null bila tak dikirim); fallback jenis usaha dari PT', () => {
    const [pb, gl] = parseChannels({
      items: [
        { channel_code: 'PB', company_code: 'PB', name: 'PB', business_type: 'fnb', loyalty: { earn: true, redeem: false }, fulfilment: {}, outlets: [] },
        { channel_code: 'GL', company_code: 'GL', name: 'GL', fulfilment: {}, outlets: [] },
      ],
    })!;
    expect(pb!.business_type).toBe('fnb');
    expect(pb!.loyalty).toEqual({ earn: true, redeem: false });
    expect(gl!.business_type).toBeNull();
    expect(gl!.loyalty).toBeNull();
    expect(channelBusinessType(gl!)).toBe('retail');
    expect(channelBusinessType({ business_type: null, company_code: 'BCF' })).toBe('fnb');
    expect(channelBusinessType({ business_type: null, company_code: 'ZZ' })).toBeNull();
  });
  it('priceDisplay: F&B = harga sebelum pajak + label; ritel = termasuk pajak', () => {
    const p = { price_incl: 11100, price_excl: 10000, tax_labels: ['PB1 10%'] };
    expect(priceDisplay(p, 'fnb')).toEqual({ amount: 10000, taxNote: 'belum termasuk PB1 10%', excl: true });
    expect(priceDisplay({ ...p, tax_labels: [] }, 'fnb').taxNote).toBe('belum termasuk pajak');
    expect(priceDisplay(p, 'retail')).toEqual({ amount: 11100, taxNote: 'termasuk PB1 10%', excl: false });
    expect(priceDisplay({ ...p, tax_labels: [] }, null)).toEqual({ amount: 11100, taxNote: '', excl: false });
  });
});
