import { describe, expect, it } from 'vitest';
import {
  canCancel,
  clockSkewMs,
  formatCountdown,
  GENERIC_ONLINE_ERROR,
  hasPickupCode,
  ONLINE_ERROR_MESSAGES,
  onlineErrorMessage,
  orderStatusLabel,
  parseOrderDetail,
  parseOrderList,
  parsePickupCodes,
  pollIntervalMs,
  remainingSeconds,
} from '@/lib/orders';

const ID = '3f2b8c1e-9a4d-4c7e-8b21-5d6e7f8a9b0c';

/** Semua kode pelanggan dari tabel K-ON5 (docs/04 §10.3.7). */
const K_ON5_CODES = [
  'price_changed',
  'out_of_stock',
  'item_unavailable',
  'order_not_cancellable',
  'invalid_state',
  'online_unavailable',
  'payment_provider_error',
  'payment_provider_unavailable',
  'invalid_request',
  'not_found',
  'quote_expired',
  'cart_changed',
  'cart_has_issues',
  'cart_version_conflict',
  'idempotency_conflict',
  'idempotency_key_required',
  'too_many_open_orders',
  'fulfilment_unavailable',
  'pickup_code_unavailable',
  'order_in_review',
  'rate_limited',
];

describe('peta error K-ON5 -> Bahasa Indonesia', () => {
  it('setiap kode punya pesan khusus, tanpa istilah teknis', () => {
    for (const code of K_ON5_CODES) {
      const msg = onlineErrorMessage(code);
      expect(msg, code).not.toBe(GENERIC_ONLINE_ERROR);
      expect(msg).not.toMatch(/odoo|traceback|exception|sale\.order|http|json/i);
    }
    expect(Object.keys(ONLINE_ERROR_MESSAGES)).toEqual(expect.arrayContaining(K_ON5_CODES));
  });
  it('kode tak dikenal / kosong -> pesan generik; tidak memakai prototype', () => {
    expect(onlineErrorMessage('odoo_traceback')).toBe(GENERIC_ONLINE_ERROR);
    expect(onlineErrorMessage(undefined)).toBe(GENERIC_ONLINE_ERROR);
    expect(onlineErrorMessage('toString')).toBe(GENERIC_ONLINE_ERROR);
  });
  it('L-14: order_in_review menjelaskan kode ambil ditahan', () => {
    expect(onlineErrorMessage('order_in_review')).toMatch(/sedang diperiksa.*kode ambil ditahan/);
  });
  it('kill switch ramah', () => {
    expect(onlineErrorMessage('online_unavailable')).toMatch(/tidak tersedia.*toko/);
  });
});

describe('polling & hitung mundur', () => {
  it('3 dtk saat menunggu bayar, 30 dtk setelahnya, berhenti di final', () => {
    expect(pollIntervalMs('awaiting_payment')).toBe(3000);
    expect(pollIntervalMs('fulfilling')).toBe(30000);
    for (const s of ['completed', 'cancelled', 'expired', 'failed'] as const) expect(pollIntervalMs(s)).toBeNull();
  });
  it('remainingSeconds + skew; formatCountdown mm:ss', () => {
    const now = Date.parse('2026-10-09T10:00:00Z');
    expect(remainingSeconds('2026-10-09T10:15:00Z', now)).toBe(900);
    expect(remainingSeconds('2026-10-09T10:15:00Z', now, 60_000)).toBe(840);
    expect(remainingSeconds('2026-10-09T09:00:00Z', now)).toBe(0);
    expect(remainingSeconds('bukan tanggal', now)).toBe(0);
    expect(formatCountdown(900)).toBe('15:00');
    expect(formatCountdown(61)).toBe('01:01');
    expect(formatCountdown(-5)).toBe('00:00');
  });
  it('clockSkewMs dari header Date', () => {
    const client = Date.parse('2026-10-09T10:00:00Z');
    expect(clockSkewMs('Fri, 09 Oct 2026 10:01:00 GMT', client)).toBe(60_000);
    expect(clockSkewMs('Fri, 09 Oct 2026 10:00:01 GMT', client)).toBe(0);
    expect(clockSkewMs(null, client)).toBe(0);
    expect(clockSkewMs('Fri, 09 Oct 2027 10:00:00 GMT', client)).toBe(3_600_000);
  });
});

describe('kontrak order', () => {
  const detail = {
    order_id: ID,
    status: 'awaiting_payment',
    created_at: '2026-10-09T10:00:00Z',
    expires_at: '2026-10-09T10:15:00Z',
    paid_at: null,
    amount_payable: 39800,
    payment: { status: 'pending', qr_string: '00020101021226…', expires_at: '2026-10-09T10:15:00Z', provider_ref: 'internal' },
    pickup_code: 'ABCD-EFGH',
    parts: [
      {
        channel_code: 'RTL',
        company_code: 'RTL',
        outlet_code: 'TOKO-01',
        outlet_name: 'Toko Pusat',
        status: 'placed',
        sale_order_name: 'S00042',
        lines: [{ product_id: 11, name: 'Susu', qty: 2, price_unit_incl: 19900, subtotal_incl: 39800 }],
        tax_groups: [{ label: 'PPN 11%', base: 35855.86, amount: 3944.14 }],
        amount_total: 39800,
        amount_payable: 39800,
        refunded_amount: 0,
        pickup_code: 'ABCD-EFGH',
        odoo_id: 42,
      },
    ],
  };
  it('allowlist: kode ambil/id internal tidak pernah ikut di detail', () => {
    const o = parseOrderDetail(detail)!;
    const s = JSON.stringify(o);
    expect(s).not.toContain('ABCD-EFGH');
    expect(s).not.toContain('odoo_id');
    expect(s).not.toContain('provider_ref');
    expect(o.payment?.qr_string).toBeTruthy();
    expect(canCancel(o)).toBe(true);
    expect(hasPickupCode(o)).toBe(false);
  });
  it('qr dibuang di luar awaiting_payment; kode ambil tersedia saat confirmed/ready', () => {
    const o = parseOrderDetail({ ...detail, status: 'fulfilling', parts: [{ ...detail.parts[0], status: 'ready' }] })!;
    expect(o.payment?.qr_string).toBeNull();
    expect(canCancel(o)).toBe(false);
    expect(hasPickupCode(o)).toBe(true);
    expect(parseOrderDetail({ ...detail, order_id: '42' })).toBeNull();
  });
  it('daftar order & label status', () => {
    const l = parseOrderList({ items: [{ order_id: ID, status: 'completed', amount_payable: 1, parts: [{ channel_code: 'RTL', status: 'handed_over' }] }, { order_id: 'x' }], next_cursor: null })!;
    expect(l.items).toHaveLength(1);
    expect(orderStatusLabel('awaiting_payment')).toBe('Menunggu pembayaran');
    expect(orderStatusLabel('aneh')).toBe('Status tidak dikenal');
  });
  it('kode ambil: hanya format alfabet §10.2', () => {
    const items = parsePickupCodes({
      items: [
        { channel_code: 'RTL', outlet_code: 'TOKO-01', outlet_name: 'Toko Pusat', pickup_code: 'ABCD-EFGH' },
        { channel_code: 'RTL', outlet_code: 'TOKO-02', pickup_code: 'ABCD-EFG1' }, // '1' bukan alfabet
        { channel_code: 'RTL', pickup_code: '<script>' },
      ],
    });
    expect(items).toEqual([{ channel_code: 'RTL', outlet_code: 'TOKO-01', outlet_name: 'Toko Pusat', pickup_code: 'ABCD-EFGH' }]);
  });
});
