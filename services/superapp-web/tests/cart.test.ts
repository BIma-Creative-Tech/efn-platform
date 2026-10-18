import { describe, expect, it } from 'vitest';
import {
  addItem,
  countItems,
  mergeGroups,
  parseCartPutResult,
  parseGuestCart,
  parseQuote,
  quoteHasIssues,
  sanitizeCart,
  serializeGuestCart,
  setOutlet,
  setQty,
  type CartGroup,
} from '@/lib/cart';

const QUOTE_ID = '3f2b8c1e-9a4d-4c7e-8b21-5d6e7f8a9b0c';

const group = (lines: [number, number][], channel = 'GL', outlet = 'TOKO-01'): CartGroup => ({
  channel_code: channel,
  outlet_code: outlet,
  fulfilment: 'pickup',
  lines: lines.map(([product_id, qty]) => ({ product_id, qty })),
});

describe('sanitizeCart (tidak ada harga dari klien)', () => {
  it('membuang field di luar kontrak (harga, total) dan grup kosong', () => {
    const c = sanitizeCart({
      version: 3,
      total: 1,
      groups: [
        { channel_code: 'GL', outlet_code: 'TOKO-01', fulfilment: 'pickup', price: 1, lines: [{ product_id: 11, qty: 2, price_unit_incl: 1 }] },
        { channel_code: 'PB', outlet_code: 'RESTO-1', fulfilment: 'pickup', lines: [] },
      ],
    });
    expect(c).toEqual({ version: 3, groups: [group([[11, 2]])] });
    expect(JSON.stringify(c)).not.toMatch(/price|total/);
  });
  it('menolak bentuk tidak valid', () => {
    const bad = [
      null,
      { version: -1, groups: [] },
      { version: 0, groups: [{ ...group([[1, 0]]) }] },
      { version: 0, groups: [{ ...group([[1, 1.5]]) }] },
      { version: 0, groups: [group([[1, 1]]), group([[2, 1]])] }, // kanal ganda
      { version: 0, groups: [{ ...group([[1, 1]]), fulfilment: 'drone' }] },
      { version: 0, groups: [{ ...group([[1, 1], [1, 2]]) }] }, // produk ganda
      { version: 0, groups: [{ ...group([[1, 1]]), channel_code: 'rtl' }] },
      { version: 0, groups: [group(Array.from({ length: 51 }, (_, i) => [i + 1, 1] as [number, number]))] },
    ];
    for (const b of bad) expect(sanitizeCart(b)).toBeNull();
  });
  it('parseCartPutResult membawa warnings', () => {
    const r = parseCartPutResult({ version: 4, groups: [group([[11, 1]])], warnings: [{ product_id: 12, code: 'item_unavailable' }, { x: 1 }] });
    expect(r?.warnings).toEqual([{ product_id: 12, code: 'item_unavailable' }]);
  });
});

describe('operasi keranjang', () => {
  it('addItem menjumlahkan qty, dibatasi max_qty', () => {
    let r = addItem([], { channel_code: 'GL', outlet_code: 'TOKO-01', product_id: 11, qty: 2 });
    expect(r.ok && r.groups).toEqual([group([[11, 2]])]);
    r = addItem(r.ok ? r.groups : [], { channel_code: 'GL', outlet_code: 'TOKO-01', product_id: 11, qty: 5, max_qty: 6 });
    expect(r.ok && r.groups[0]!.lines[0]!.qty).toBe(6);
  });
  it('satu outlet per kanal: outlet beda memindahkan grup', () => {
    const r = addItem([group([[11, 1]])], { channel_code: 'GL', outlet_code: 'TOKO-02', product_id: 12, qty: 1 });
    expect(r.ok && r.outletChanged).toBe(true);
    expect(r.ok && r.groups).toHaveLength(1);
    expect(r.ok && r.groups[0]!.outlet_code).toBe('TOKO-02');
  });
  it('multi-PT: kanal lain menjadi grup terpisah', () => {
    const r = addItem([group([[11, 1]])], { channel_code: 'PB', outlet_code: 'RESTO-1', product_id: 99, qty: 1 });
    expect(r.ok && r.groups.map((g) => g.channel_code)).toEqual(['GL', 'PB']);
  });
  it('batas 50 baris & input tidak valid', () => {
    const full = [group(Array.from({ length: 50 }, (_, i) => [i + 1, 1] as [number, number]))];
    expect(addItem(full, { channel_code: 'GL', outlet_code: 'TOKO-01', product_id: 999, qty: 1 })).toEqual({ ok: false, reason: 'too_many_lines' });
    expect(addItem([], { channel_code: 'GL', outlet_code: 'TOKO-01', product_id: 1, qty: 0 }).ok).toBe(false);
  });
  it('setQty: 0 menghapus baris & grup kosong; setOutlet; input asli tidak dimutasi', () => {
    const g = [group([[11, 2]])];
    expect(setQty(g, 'GL', 11, 0)).toEqual([]);
    expect(setQty(g, 'GL', 11, 500)[0]!.lines[0]!.qty).toBe(99);
    expect(setOutlet(g, 'GL', 'TOKO-09')[0]!.outlet_code).toBe('TOKO-09');
    expect(setOutlet(g, 'GL', 'bad code')[0]!.outlet_code).toBe('TOKO-01');
    expect(g[0]!.lines[0]!.qty).toBe(2);
  });
  it('mergeGroups: tamu digabung, outlet server menang', () => {
    const merged = mergeGroups([group([[11, 1]])], [group([[11, 2], [12, 1]], 'GL', 'TOKO-02'), group([[50, 1]], 'PB', 'RESTO-1')]);
    expect(merged).toEqual([group([[11, 3], [12, 1]]), group([[50, 1]], 'PB', 'RESTO-1')]);
    expect(countItems(merged)).toBe(5);
  });
});

describe('keranjang tamu (localStorage)', () => {
  it('round-trip tanpa harga; data rusak -> kosong', () => {
    const s = serializeGuestCart({ groups: [group([[11, 1]])], names: { '11': 'Susu' } });
    expect(parseGuestCart(s)).toEqual({ groups: [group([[11, 1]])], names: { '11': 'Susu' } });
    expect(parseGuestCart('{rusak')).toEqual({ groups: [], names: {} });
    expect(parseGuestCart(null)).toEqual({ groups: [], names: {} });
    expect(parseGuestCart(JSON.stringify({ groups: [{ channel_code: 'x' }] })).groups).toEqual([]);
  });
});

describe('parseQuote §10.3.4', () => {
  const quote = {
    quote_id: QUOTE_ID,
    valid_until: '2026-10-09T10:10:00Z',
    cart_version: 7,
    quote_hash: 'rahasia-internal',
    groups: [
      {
        channel_code: 'GL',
        company_code: 'GL',
        outlet_code: 'TOKO-01',
        fulfilment: 'pickup',
        quote_hash: 'rahasia-internal',
        lines: [{ product_id: 11, name: 'Susu', qty: 2, price_unit_incl: 19900, subtotal_incl: 39800, tax_labels: ['PPN 11%'], available: true }],
        tax_groups: [{ label: 'PPN 11%', base: 35855.86, amount: 3944.14 }],
        amount_total: 39800.0,
        amount_payable: 39800,
        issues: [],
      },
    ],
    amount_payable_total: 39800,
  };
  it('allowlist: quote_hash tidak pernah diteruskan', () => {
    const q = parseQuote(quote)!;
    expect(JSON.stringify(q)).not.toContain('rahasia-internal');
    expect(q.amount_payable_total).toBe(39800);
    expect(q.groups[0]!.tax_groups[0]!.amount).toBe(3944.14);
    expect(quoteHasIssues(q)).toBe(false);
  });
  it('menolak nominal payable non-bulat & quote_id bukan UUID', () => {
    expect(parseQuote({ ...quote, amount_payable_total: 39800.5 })).toBeNull();
    expect(parseQuote({ ...quote, groups: [{ ...quote.groups[0], amount_payable: 1.2 }] })).toBeNull();
    expect(parseQuote({ ...quote, quote_id: '1' })).toBeNull();
  });
  it('issues / baris tidak tersedia memblokir checkout', () => {
    expect(quoteHasIssues(parseQuote({ ...quote, groups: [{ ...quote.groups[0], issues: [{ product_id: 11, code: 'out_of_stock' }] }] })!)).toBe(true);
    const unavailable = { ...quote.groups[0], lines: [{ ...quote.groups[0]!.lines[0], available: false }] };
    expect(quoteHasIssues(parseQuote({ ...quote, groups: [unavailable] })!)).toBe(true);
  });
});
