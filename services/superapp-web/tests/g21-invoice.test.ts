/** G21: faktur atas nama pembeli (docs/04 §13.5, L-63): validasi, kelayakan PT PPN, Route Handler meneruskan tanpa bocor. */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { NextRequest } from 'next/server';

const { bffFetch } = vi.hoisted(() => ({ bffFetch: vi.fn() }));
vi.mock('server-only', () => ({}));
vi.mock('@/lib/bff', () => ({ bffFetch, bffBaseUrl: vi.fn() }));

import { POST as postOrder } from '@/app/api/orders/route';
import { allPpnCompanies } from '@/lib/brands';
import {
  buildInvoiceRequest,
  INVOICE_NOT_AVAILABLE_MESSAGE,
  INVOICE_PRIVACY_NOTICE,
  invoiceRequestAvailable,
  normalizeTaxId,
  parseInvoiceRequest,
  validateInvoiceRequest,
  type InvoiceRequestInput,
} from '@/lib/invoice-request';
import { onlineErrorMessage } from '@/lib/orders';

const ID = '3f2b8c1e-9a4d-4c7e-8b21-5d6e7f8a9b0c';
const QUOTE_ID = '9b1d2c3e-4f5a-4b6c-8d7e-0f1a2b3c4d5e';
// Data fiktif untuk test; bukan identitas nyata.
const VALID: InvoiceRequestInput = {
  buyer_type: 'npwp',
  tax_id: '0123456789012345',
  name: 'PT Contoh Fiktif',
  address: 'Jl. Contoh No. 1, Jakarta',
  email: 'finance@contoh.example',
};

describe('validateInvoiceRequest', () => {
  it('lolos untuk NPWP, NIK, dan paspor yang benar', () => {
    expect(validateInvoiceRequest(VALID)).toEqual({});
    expect(validateInvoiceRequest({ ...VALID, buyer_type: 'nik' })).toEqual({});
    expect(validateInvoiceRequest({ ...VALID, buyer_type: 'passport', tax_id: 'a1234567' })).toEqual({});
  });
  it('NPWP/NIK harus tepat 16 digit; pemisah umum dibuang', () => {
    for (const t of ['npwp', 'nik'] as const) {
      expect(validateInvoiceRequest({ ...VALID, buyer_type: t, tax_id: '123456789012345' }).tax_id).toBeDefined();
      expect(validateInvoiceRequest({ ...VALID, buyer_type: t, tax_id: '12345678901234567' }).tax_id).toBeDefined();
      expect(validateInvoiceRequest({ ...VALID, buyer_type: t, tax_id: '01234567890123AB' }).tax_id).toBeDefined();
    }
    expect(validateInvoiceRequest({ ...VALID, tax_id: '0123 4567-8901.2345' })).toEqual({});
    expect(normalizeTaxId('npwp', '0123 4567-8901.2345')).toBe('0123456789012345');
  });
  it('paspor 5–20 alfanumerik', () => {
    expect(validateInvoiceRequest({ ...VALID, buyer_type: 'passport', tax_id: 'AB12' }).tax_id).toBeDefined();
    expect(validateInvoiceRequest({ ...VALID, buyer_type: 'passport', tax_id: 'AB-1234567' }).tax_id).toBeDefined();
  });
  it('nama 2–100, alamat 5–255, email dasar', () => {
    expect(validateInvoiceRequest({ ...VALID, name: 'A' }).name).toBeDefined();
    expect(validateInvoiceRequest({ ...VALID, name: 'x'.repeat(101) }).name).toBeDefined();
    expect(validateInvoiceRequest({ ...VALID, address: 'abcd' }).address).toBeDefined();
    expect(validateInvoiceRequest({ ...VALID, address: 'x'.repeat(256) }).address).toBeDefined();
    expect(validateInvoiceRequest({ ...VALID, email: 'bukan-email' }).email).toBeDefined();
    expect(validateInvoiceRequest({ ...VALID, email: 'a b@c.id' }).email).toBeDefined();
  });
  it('pesan error tidak memuat nilai yang diketik', () => {
    const errs = validateInvoiceRequest({ ...VALID, tax_id: '999', name: 'RAHASIA-NAMA', email: 'rahasia' });
    const text = JSON.stringify(errs);
    expect(text).not.toContain('999');
    expect(text).not.toContain('RAHASIA');
  });
  it('buildInvoiceRequest memangkas dan menormalkan, tepat lima field', () => {
    const out = buildInvoiceRequest({ ...VALID, tax_id: '0123 4567 8901 2345', name: '  Budi  ', email: ' a@b.id ' });
    expect(out).toEqual({ ...VALID, name: 'Budi', email: 'a@b.id' });
    expect(Object.keys(out).sort()).toEqual(['address', 'buyer_type', 'email', 'name', 'tax_id']);
  });
});

describe('kelayakan PT PPN', () => {
  it('hanya GL dan BCR', () => {
    expect(allPpnCompanies(['GL'])).toBe(true);
    expect(allPpnCompanies(['GL', 'BCR'])).toBe(true);
    expect(invoiceRequestAvailable(['BCR'])).toBe(true);
  });
  it('campuran dengan PB/BCF, kosong, atau kode tak dikenal -> tidak', () => {
    expect(invoiceRequestAvailable(['GL', 'PB'])).toBe(false);
    expect(invoiceRequestAvailable(['BCF'])).toBe(false);
    expect(invoiceRequestAvailable(['BCR', 'BCF'])).toBe(false);
    expect(invoiceRequestAvailable([])).toBe(false);
    expect(invoiceRequestAvailable(['XYZ', 'toString'])).toBe(false);
  });
});

describe('teks pelanggan', () => {
  it('pemberitahuan privasi menyebut tujuan dan retensi 10 tahun', () => {
    expect(INVOICE_PRIVACY_NOTICE).toMatch(/faktur pajak/);
    expect(INVOICE_PRIVACY_NOTICE).toMatch(/10 tahun/);
  });
  it('invoice_not_available -> pesan yang disepakati', () => {
    expect(INVOICE_NOT_AVAILABLE_MESSAGE).toBe('Faktur atas nama belum tersedia untuk pesanan ini');
    expect(onlineErrorMessage('invoice_not_available')).toBe(INVOICE_NOT_AVAILABLE_MESSAGE);
  });
});

describe('parseInvoiceRequest', () => {
  it('membuang field tambahan dan menolak bentuk salah', () => {
    expect(parseInvoiceRequest({ ...VALID, extra: 'x' })).toEqual(VALID);
    expect(parseInvoiceRequest(null)).toBeNull();
    expect(parseInvoiceRequest([])).toBeNull();
    expect(parseInvoiceRequest({ ...VALID, buyer_type: 'ktp' })).toBeNull();
    expect(parseInvoiceRequest({ ...VALID, name: 5 })).toBeNull();
    expect(parseInvoiceRequest({ ...VALID, tax_id: '1' })).toBeNull();
  });
});

describe('POST /api/orders invoice_request', () => {
  function post(body: unknown) {
    return new NextRequest('https://app.efn.example/api/orders', {
      method: 'POST',
      headers: { host: 'app.efn.example', origin: 'https://app.efn.example', cookie: 'efn_session=tok', 'content-type': 'application/json' },
      body: JSON.stringify(body),
    });
  }
  const logs = () => [vi.spyOn(console, 'log'), vi.spyOn(console, 'info'), vi.spyOn(console, 'warn'), vi.spyOn(console, 'error'), vi.spyOn(console, 'debug')];
  beforeEach(() => bffFetch.mockReset());
  afterEach(() => vi.restoreAllMocks());

  it('meneruskan lima field ke BFF; respons ke browser tidak memuat data faktur; tidak ada log', async () => {
    const spies = logs();
    bffFetch.mockResolvedValue({ ok: true, status: 201, data: { order_id: ID, status: 'awaiting_payment' } });
    const res = await postOrder(post({ quote_id: QUOTE_ID, accept_total: 1000, payment_method: 'qris', invoice_request: { ...VALID, extra: 'dibuang' } }));
    expect(res.status).toBe(201);
    const text = await res.text();
    for (const secret of [VALID.tax_id, VALID.name, VALID.address, VALID.email]) expect(text).not.toContain(secret);
    expect(bffFetch.mock.calls[0]![1].json).toEqual({ quote_id: QUOTE_ID, accept_total: 1000, payment_method: 'qris', invoice_request: VALID });
    expect(res.headers.get('cache-control')).toBe('no-store');
    for (const s of spies) {
      const dump = JSON.stringify(s.mock.calls);
      for (const secret of [VALID.tax_id, VALID.name, VALID.address, VALID.email]) expect(dump).not.toContain(secret);
    }
  });
  it('tanpa invoice_request -> tidak ada field itu ke BFF', async () => {
    bffFetch.mockResolvedValue({ ok: true, status: 201, data: { order_id: ID, status: 'awaiting_payment' } });
    await postOrder(post({ quote_id: QUOTE_ID, accept_total: 1000 }));
    expect(bffFetch.mock.calls[0]![1].json).not.toHaveProperty('invoice_request');
  });
  it('invoice_request tidak valid -> 400 generik tanpa BFF dan tanpa echo nilai', async () => {
    const res = await postOrder(post({ quote_id: QUOTE_ID, accept_total: 1000, invoice_request: { ...VALID, tax_id: '12', name: 'NAMA-RAHASIA' } }));
    expect(res.status).toBe(400);
    const text = await res.text();
    expect(text).not.toContain('NAMA-RAHASIA');
    expect(bffFetch).not.toHaveBeenCalled();
  });
  it('422 invoice_not_available -> pesan Indonesia, pesan BFF tidak diteruskan', async () => {
    bffFetch.mockResolvedValue({ ok: false, status: 422, code: 'invoice_not_available', message: 'pesan internal BFF', body: null });
    const res = await postOrder(post({ quote_id: QUOTE_ID, accept_total: 1000, invoice_request: VALID }));
    expect(res.status).toBe(422);
    const body = await res.json();
    expect(body.error.code).toBe('invoice_not_available');
    expect(body.error.message).toBe(INVOICE_NOT_AVAILABLE_MESSAGE);
    expect(JSON.stringify(body)).not.toContain('pesan internal BFF');
    expect(JSON.stringify(body)).not.toContain(VALID.tax_id);
  });
});
