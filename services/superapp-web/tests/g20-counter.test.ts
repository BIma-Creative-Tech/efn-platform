/** G20: bayar di kasir (docs/04 §12.2), issue berat (§12.3), peta kode entitas (§12.1). Fungsi murni + Route Handler. */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { NextRequest } from 'next/server';

const { bffFetch } = vi.hoisted(() => ({ bffFetch: vi.fn() }));
vi.mock('server-only', () => ({}));
vi.mock('@/lib/bff', () => ({ bffFetch, bffBaseUrl: vi.fn() }));

import { POST as postOrder } from '@/app/api/orders/route';
import { groupLevelIssues, issueLabel, parseIssues, parseQuote, productIssue, quoteHasIssues } from '@/lib/cart';
import {
  canCancel,
  isAwaitingCounter,
  onlineErrorMessage,
  orderStatusLabel,
  parseOrderDetail,
  pollIntervalMs,
  withoutCounterCode,
} from '@/lib/orders';
import {
  COUNTER_MULTI_PART_REASON,
  countdownAnnouncement,
  defaultPaymentMethod,
  formatCounterCode,
  paymentOptions,
  parsePaymentModes,
  resolvePaymentMethod,
  spellCounterCode,
  spokenDuration,
} from '@/lib/payment';

const ID = '3f2b8c1e-9a4d-4c7e-8b21-5d6e7f8a9b0c';
const QUOTE_ID = '9b1d2c3e-4f5a-4b6c-8d7e-0f1a2b3c4d5e';

describe('parsePaymentModes', () => {
  it('menyaring nilai tak dikenal; bukan array -> null (tidak diketahui)', () => {
    expect(parsePaymentModes(['counter', 'qris', 'qris', 'cash', 5])).toEqual(['counter', 'qris']);
    expect(parsePaymentModes([])).toEqual([]);
    expect(parsePaymentModes(undefined)).toBeNull();
    expect(parsePaymentModes('counter')).toBeNull();
  });
});

describe('paymentOptions', () => {
  const by = (o: ReturnType<typeof paymentOptions>, m: string) => o.find((x) => x.method === m)!;

  it('satu PT, field tidak dikirim: keduanya tersedia, default QRIS', () => {
    const o = paymentOptions([null]);
    expect(by(o, 'qris').enabled).toBe(true);
    expect(by(o, 'counter').enabled).toBe(true);
    expect(defaultPaymentMethod(o)).toBe('qris');
  });
  it('pilot (hanya counter): QRIS nonaktif, default counter', () => {
    const o = paymentOptions([['counter']]);
    expect(by(o, 'qris').enabled).toBe(false);
    expect(by(o, 'qris').reason).toMatch(/dilakukan di kasir/);
    expect(defaultPaymentMethod(o)).toBe('counter');
  });
  it('kanal tidak memuat counter: counter nonaktif dengan alasan', () => {
    const o = paymentOptions([['qris']]);
    expect(by(o, 'counter').enabled).toBe(false);
    expect(by(o, 'counter').reason).toMatch(/belum tersedia/);
  });
  it('lebih dari satu PT: counter nonaktif + penjelasan 2 pesanan', () => {
    const o = paymentOptions([['qris', 'counter'], ['qris', 'counter']]);
    expect(by(o, 'counter').enabled).toBe(false);
    expect(by(o, 'counter').reason).toBe(COUNTER_MULTI_PART_REASON);
    expect(by(o, 'qris').enabled).toBe(true);
  });
  it('multi-PT pilot (counter saja): tidak ada metode -> default null (checkout diblokir)', () => {
    const o = paymentOptions([['counter'], ['counter']]);
    expect(defaultPaymentMethod(o)).toBeNull();
  });
  it('keranjang kosong: counter tidak aktif', () => {
    expect(by(paymentOptions([]), 'counter').enabled).toBe(false);
  });
  it('resolvePaymentMethod: pilihan nonaktif jatuh ke default', () => {
    const o = paymentOptions([['counter']]);
    expect(resolvePaymentMethod('qris', o)).toBe('counter');
    expect(resolvePaymentMethod('counter', o)).toBe('counter');
    expect(resolvePaymentMethod(null, paymentOptions([null]))).toBe('qris');
  });
});

describe('kode bayar', () => {
  it('format tampil ABCD-EFGH; menerima huruf kecil/spasi/tanpa strip', () => {
    expect(formatCounterCode('ABCD-EFGH')).toBe('ABCD-EFGH');
    expect(formatCounterCode('abcdefgh')).toBe('ABCD-EFGH');
    expect(formatCounterCode('ABCD EFGH')).toBe('ABCD-EFGH');
  });
  it('menolak di luar alfabet (I, L, O, 0, 1), panjang salah, bukan string, markup', () => {
    for (const bad of ['ABCD-EFG1', 'ABCD-EFGO', 'ABCDEFG', 'ABCDEFGHJ', '<script>', null, 12345678, '']) {
      expect(formatCounterCode(bad)).toBeNull();
    }
  });
  it('ejaan untuk pembaca layar', () => {
    expect(spellCounterCode('ABCD-EFGH')).toBe('A B C D, E F G H');
    expect(spellCounterCode('bukan kode')).toBe('');
  });
  it('durasi & pengumuman hitung mundur (tidak berubah tiap detik)', () => {
    expect(spokenDuration(125)).toBe('2 menit 5 detik');
    expect(spokenDuration(45)).toBe('45 detik');
    expect(countdownAnnouncement(300)).toBe(countdownAnnouncement(241));
    expect(countdownAnnouncement(300)).not.toBe(countdownAnnouncement(240));
    expect(countdownAnnouncement(61)).toMatch(/2 menit/);
    expect(countdownAnnouncement(60)).toMatch(/kurang dari satu menit/);
    expect(countdownAnnouncement(1)).toBe(countdownAnnouncement(59));
    expect(countdownAnnouncement(0)).toMatch(/habis/);
  });
});

describe('order awaiting_counter_payment', () => {
  const detail = {
    order_id: ID,
    status: 'awaiting_counter_payment',
    created_at: '2026-10-18T10:00:00Z',
    expires_at: '2026-10-18T11:00:00Z',
    paid_at: null,
    amount_payable: 39800,
    payment: { method: 'counter', status: 'pending', counter_code: 'abcd-efgh', qr_string: 'harusnya-dibuang', expires_at: '2026-10-18T11:00:00Z' },
    parts: [],
  };
  it('kode bayar dinormalkan; qr dibuang; status/label/cancel/poll', () => {
    const o = parseOrderDetail(detail)!;
    expect(o.payment?.counter_code).toBe('ABCD-EFGH');
    expect(o.payment?.qr_string).toBeNull();
    expect(isAwaitingCounter(o)).toBe(true);
    expect(canCancel(o)).toBe(true);
    expect(orderStatusLabel('awaiting_counter_payment')).toBe('Menunggu pembayaran di kasir');
    expect(pollIntervalMs('awaiting_counter_payment')).toBe(10_000);
  });
  it('kode bayar dibuang di status lain dan bila format salah', () => {
    expect(parseOrderDetail({ ...detail, status: 'fulfilling' })!.payment?.counter_code).toBeNull();
    expect(parseOrderDetail({ ...detail, payment: { ...detail.payment, counter_code: 'ABCD-EFG1' } })!.payment?.counter_code).toBeNull();
    expect(parseOrderDetail({ ...detail, payment: { ...detail.payment, counter_code: undefined } })!.payment?.counter_code).toBeNull();
  });
  it('kode bayar tidak bocor lewat field lain / kode ambil tidak pernah di-parse', () => {
    const o = parseOrderDetail({ ...detail, pickup_code: 'WXYZ-2345', counter_code: 'JKMN-PQRS' })!;
    expect(JSON.stringify(o)).not.toContain('WXYZ-2345');
    expect(JSON.stringify(o)).not.toContain('JKMN-PQRS'); // hanya payment.counter_code yang dibaca
  });
  it('withoutCounterCode untuk render server', () => {
    const o = parseOrderDetail(detail)!;
    const s = withoutCounterCode(o);
    expect(s.payment?.counter_code).toBeNull();
    expect(JSON.stringify(s)).not.toContain('ABCD-EFGH');
    expect(o.payment?.counter_code).toBe('ABCD-EFGH'); // tidak memutasi asli
  });
  it('pesan error §12.2', () => {
    expect(onlineErrorMessage('counter_payment_single_part_only')).toMatch(/satu PT/);
    expect(onlineErrorMessage('payment_method_unavailable')).toMatch(/belum tersedia/);
    expect(onlineErrorMessage('too_many_open_orders')).toMatch(/belum dibayar/);
  });
});

describe('issue berat §12.3', () => {
  const quote = (issues: unknown[]) => ({
    quote_id: QUOTE_ID,
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
        issues,
      },
    ],
  });
  it('weight_limit (grup) dan weight_missing (produk) lolos allowlist & memblokir checkout', () => {
    const q = parseQuote(quote([{ product_id: null, code: 'weight_limit' }, { product_id: 11, code: 'weight_missing' }]))!;
    expect(q.groups[0]!.issues).toEqual([
      { product_id: null, code: 'weight_limit' },
      { product_id: 11, code: 'weight_missing' },
    ]);
    expect(quoteHasIssues(q)).toBe(true);
  });
  it('pemisahan issue grup vs produk', () => {
    const g = parseQuote(quote([{ product_id: null, code: 'weight_limit' }, { product_id: 11, code: 'weight_missing' }]))!.groups[0]!;
    expect(groupLevelIssues(g)).toEqual([{ product_id: null, code: 'weight_limit' }]);
    expect(productIssue(g, 11)?.code).toBe('weight_missing');
    expect(productIssue(g, 12)).toBeUndefined();
  });
  it('pesan jelas', () => {
    expect(issueLabel('weight_limit')).toMatch(/Total berat pesanan melebihi batas/);
    expect(issueLabel('weight_missing')).toMatch(/Berat barang ini belum tercatat/);
    expect(parseIssues([{ product_id: null, code: 'weight_x' }])).toEqual([{ product_id: null, code: 'unknown_issue' }]);
  });
});

describe('POST /api/orders payment_method', () => {
  function post(body: unknown) {
    return new NextRequest('https://app.efn.example/api/orders', {
      method: 'POST',
      headers: { host: 'app.efn.example', origin: 'https://app.efn.example', cookie: 'efn_session=tok', 'content-type': 'application/json' },
      body: JSON.stringify(body),
    });
  }
  beforeEach(() => bffFetch.mockReset());

  it('counter diteruskan; respons ke browser hanya id + status (kode bayar tidak ikut)', async () => {
    bffFetch.mockResolvedValue({
      ok: true,
      status: 201,
      data: { order_id: ID, status: 'awaiting_counter_payment', payment: { method: 'counter', counter_code: 'ABCD-EFGH', expires_at: 'x' } },
    });
    const res = await postOrder(post({ quote_id: QUOTE_ID, accept_total: 1000, payment_method: 'counter' }));
    expect(res.status).toBe(201);
    const text = await res.text();
    expect(JSON.parse(text)).toEqual({ order_id: ID, status: 'awaiting_counter_payment' });
    expect(text).not.toContain('ABCD-EFGH');
    expect(bffFetch.mock.calls[0]![1].json).toEqual({ quote_id: QUOTE_ID, accept_total: 1000, payment_method: 'counter' });
  });
  it('payment_method tidak valid -> 400 tanpa BFF', async () => {
    for (const pm of ['cash', 1, null, {}]) {
      expect((await postOrder(post({ quote_id: QUOTE_ID, accept_total: 1, payment_method: pm }))).status).toBe(400);
    }
    expect(bffFetch).not.toHaveBeenCalled();
  });
  it.each([
    [422, 'counter_payment_single_part_only', /satu PT/],
    [422, 'payment_method_unavailable', /belum tersedia/],
    [429, 'too_many_open_orders', /belum dibayar/],
  ])('BFF %i %s -> pesan Indonesia', async (status, code, msg) => {
    bffFetch.mockResolvedValue({ ok: false, status, code, message: 'pesan internal BFF', body: null });
    const res = await postOrder(post({ quote_id: QUOTE_ID, accept_total: 1, payment_method: 'counter' }));
    expect(res.status).toBe(status);
    const body = await res.json();
    expect(body.error.code).toBe(code);
    expect(body.error.message).toMatch(msg);
    expect(JSON.stringify(body)).not.toContain('pesan internal BFF');
  });
  it('409 cart_has_issues (issue berat) diteruskan dengan pesan', async () => {
    bffFetch.mockResolvedValue({ ok: false, status: 409, code: 'cart_has_issues', message: 'x', body: null });
    const res = await postOrder(post({ quote_id: QUOTE_ID, accept_total: 1 }));
    expect(res.status).toBe(409);
    expect((await res.json()).error.code).toBe('cart_has_issues');
  });
});
