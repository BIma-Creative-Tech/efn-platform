import { createHash } from 'node:crypto';
import { FakeClock, FakeDb, FakeKvStore, catchApiError, testConfig } from '../common/testing/fakes';
import { parseCreatePayment, parseSimulate } from './dto/payment.dto';
import { MidtransGateway } from './gateways/midtrans.gateway';
import { MockGateway } from './gateways/mock.gateway';
import { XenditGateway } from './gateways/xendit.gateway';
import { createPaymentGateway } from './payment-gateway.factory';
import { PaymentGateway } from './payment-gateway';
import { PaymentsService, canTransition } from './payments.service';

const BODY = {
  order_ref: '3f2b8c1e-7d4a-4e0b-9c55-2a1f0e6d9b10',
  amount: 125000,
  currency: 'IDR',
  company_code: 'FNB',
  pos_config_id: 3,
  method: 'qris',
  idempotency_key: 'pos:3f2b8c1e:pay-1',
};

type Row = {
  id: string;
  provider: string;
  provider_ref: string | null;
  order_ref: string;
  company_code: string;
  amount: number;
  status: string;
  qr_string: string | null;
  expires_at: Date | null;
  paid_at: Date | null;
  idempotency_key: string;
};

/** Emulasi `platform.payment_intent` + `processed_message` untuk FakeDb. */
function fakePaymentDb() {
  const rows = new Map<string, Row>();
  const processed = new Set<string>();
  const db = new FakeDb((t, p) => {
    if (t.includes('INSERT INTO platform.payment_intent')) {
      const [id, provider, order_ref, company_code, amount, , , expires, key] = p as [string, string, string, string, number, string, string, string, string];
      if ([...rows.values()].some((r) => r.idempotency_key === key)) return { rows: [], rowCount: 0 };
      rows.set(id, { id, provider, provider_ref: null, order_ref, company_code, amount, status: 'pending', qr_string: null, expires_at: new Date(expires), paid_at: null, idempotency_key: key });
      return { rows: [{ id }], rowCount: 1 };
    }
    if (t.includes('SET provider_ref')) {
      const r = rows.get(p[0] as string)!;
      Object.assign(r, { provider_ref: p[1], qr_string: p[2], expires_at: new Date(p[3] as string) });
      return { rows: [], rowCount: 1 };
    }
    if (t.includes("SET status = 'failed'")) {
      rows.get(p[0] as string)!.status = 'failed';
      return { rows: [], rowCount: 1 };
    }
    if (t.includes('WHERE idempotency_key = $1')) {
      const r = [...rows.values()].find((x) => x.idempotency_key === p[0]);
      return { rows: r ? [r] : [], rowCount: r ? 1 : 0 };
    }
    if (t.includes('WHERE provider = $1 AND provider_ref = $2')) {
      const r = [...rows.values()].find((x) => x.provider === p[0] && x.provider_ref === p[1]);
      return { rows: r ? [{ id: r.id }] : [], rowCount: r ? 1 : 0 };
    }
    if (t.includes('FROM platform.payment_intent WHERE id = $1')) {
      const r = rows.get(p[0] as string);
      return { rows: r ? [r] : [], rowCount: r ? 1 : 0 };
    }
    if (t.includes('UPDATE platform.payment_intent SET status = $2')) {
      const r = rows.get(p[0] as string)!;
      if (r.status !== p[3]) return { rows: [], rowCount: 0 };
      r.status = p[1] as string;
      if (p[2]) r.paid_at = new Date(p[2] as string);
      return { rows: [], rowCount: 1 };
    }
    if (t.includes('INSERT INTO platform.processed_message')) {
      const k = `${p[0]}|${p[1]}`;
      if (processed.has(k)) return { rows: [], rowCount: 0 };
      processed.add(k);
      return { rows: [{ consumer: p[0] }], rowCount: 1 };
    }
    return undefined;
  });
  return { db, rows };
}

function setup(gw?: PaymentGateway | null, overrides: Record<string, string> = {}) {
  const clock = new FakeClock();
  const config = testConfig(overrides);
  const kv = new FakeKvStore(clock.now);
  const { db, rows } = fakePaymentDb();
  const mock = new MockGateway(() => config.webhookPaymentSecret);
  mock.now = clock.now;
  const gateway = gw === undefined ? mock : gw;
  const svc = new PaymentsService(config, db, kv, { get: () => gateway });
  svc.now = clock.now;
  return { clock, config, kv, db, rows, svc, mock };
}

describe('PaymentsService (docs/04 §8.3) — mock', () => {
  it('create → 201 body pending + QR dummy; idempotent per idempotency_key', async () => {
    const ctx = setup();
    const dto = parseCreatePayment(BODY);
    const a = await ctx.svc.create(dto);
    expect(a).toMatchObject({ provider: 'mock', status: 'pending' });
    expect(a.qr_string).toContain('BUKAN-QR-SUNGGUHAN');
    expect(a.expires_at).toBe(new Date(ctx.clock.ms + 600_000).toISOString());
    const b = await ctx.svc.create(dto);
    expect(b).toEqual(a);
    expect(ctx.rows.size).toBe(1);
  });

  it('idempotency_key sama dengan payload berbeda → 409 idempotency_conflict; QR belum jadi → 409 payment_in_progress', async () => {
    const ctx = setup();
    await ctx.svc.create(parseCreatePayment(BODY));
    const err = await catchApiError(() => ctx.svc.create(parseCreatePayment({ ...BODY, amount: 1 })));
    expect(err.body).toEqual({ error: { code: 'idempotency_conflict', message: expect.any(String) } });
    [...ctx.rows.values()][0].qr_string = null;
    const busy = await catchApiError(() => ctx.svc.create(parseCreatePayment(BODY)));
    expect(busy.body).toEqual({ error: { code: 'payment_in_progress', message: expect.any(String) } });
  });

  it('provider gagal → intent failed + 502; provider disabled → 503', async () => {
    const failing: PaymentGateway = {
      name: 'mock',
      createQris: async () => {
        throw new Error('down');
      },
      getStatus: async () => ({ status: 'pending', paidAt: null, amount: null }),
      cancel: async () => ({ status: 'cancelled' }),
      verifyWebhook: () => ({ ok: false, reason: 'x' }),
      parseWebhook: () => {
        throw new Error('x');
      },
    };
    const ctx = setup(failing);
    const err = await catchApiError(() => ctx.svc.create(parseCreatePayment(BODY)));
    expect(err.getStatus()).toBe(502);
    expect([...ctx.rows.values()][0].status).toBe('failed');
    const off = setup(null);
    expect((await catchApiError(() => off.svc.create(parseCreatePayment(BODY)))).body).toEqual({
      error: { code: 'payment_provider_unavailable', message: expect.any(String) },
    });
  });

  it('webhook valid → paid; replay event sama → diabaikan; webhook invalid / timestamp basi → 401', async () => {
    const ctx = setup();
    const created = await ctx.svc.create(parseCreatePayment(BODY));
    const row = ctx.rows.get(created.payment_id)!;
    const now = Math.floor(ctx.clock.ms / 1000);
    const event = { event_id: 'evt-1', provider_ref: row.provider_ref!, status: 'paid' as const, amount: 125000, paid_at: new Date(ctx.clock.ms).toISOString() };
    const req = MockGateway.buildWebhook(ctx.config.webhookPaymentSecret, event, now);

    expect(await ctx.svc.handleWebhook('mock', req)).toEqual({ ok: true });
    expect((await ctx.svc.get(created.payment_id)).status).toBe('paid');
    // replay (event id sama, signature masih dalam window) → tidak ada efek
    expect(await ctx.svc.handleWebhook('mock', req)).toEqual({ ok: true });
    expect(ctx.db.find('SET status = $2')).toHaveLength(1);

    const forged = MockGateway.buildWebhook('secret-penyerang-0123456789abcdef', { ...event, event_id: 'evt-2' }, now);
    expect((await catchApiError(() => ctx.svc.handleWebhook('mock', forged))).getStatus()).toBe(401);
    const stale = MockGateway.buildWebhook(ctx.config.webhookPaymentSecret, { ...event, event_id: 'evt-3' }, now - 301);
    expect((await catchApiError(() => ctx.svc.handleWebhook('mock', stale))).getStatus()).toBe(401);
    const tampered = { ...req, rawBody: Buffer.from(req.rawBody!.toString().replace('125000', '1')) };
    expect((await catchApiError(() => ctx.svc.handleWebhook('mock', tampered))).getStatus()).toBe(401);
    // provider bukan yang aktif → 404
    expect((await catchApiError(() => ctx.svc.handleWebhook('xendit', req))).getStatus()).toBe(404);
  });

  it('nominal webhook ≠ intent → status tetap pending', async () => {
    const ctx = setup();
    const created = await ctx.svc.create(parseCreatePayment(BODY));
    const row = ctx.rows.get(created.payment_id)!;
    const req = MockGateway.buildWebhook(
      ctx.config.webhookPaymentSecret,
      { event_id: 'evt-x', provider_ref: row.provider_ref!, status: 'paid', amount: 1000, paid_at: null },
      Math.floor(ctx.clock.ms / 1000),
    );
    await ctx.svc.handleWebhook('mock', req);
    expect(row.status).toBe('pending');
  });

  it('status: getStatus ke provider (mock → expired setelah expires_at), dibatasi PAYMENT_STATUS_REFRESH_SECONDS', async () => {
    const ctx = setup();
    const created = await ctx.svc.create(parseCreatePayment(BODY));
    expect((await ctx.svc.get(created.payment_id)).status).toBe('pending');
    ctx.clock.advanceSeconds(601);
    expect((await ctx.svc.get(created.payment_id)).status).toBe('expired');
    expect(await ctx.svc.get(created.payment_id)).toMatchObject({ payment_id: created.payment_id, amount: 125000, paid_at: null });
    expect((await catchApiError(() => ctx.svc.get('9b2e1c4d-5f6a-4b7c-8d9e-0f1a2b3c4d5e'))).getStatus()).toBe(404);
  });

  it('cancel → cancelled; cancel ulang / setelah final → status saat ini; pembayaran terlambat tetap tercatat paid', async () => {
    const ctx = setup();
    const created = await ctx.svc.create(parseCreatePayment(BODY));
    expect(await ctx.svc.cancel(created.payment_id)).toEqual({ status: 'cancelled' });
    expect(await ctx.svc.cancel(created.payment_id)).toEqual({ status: 'cancelled' });
    expect((await ctx.svc.simulate(created.payment_id, 'paid')).status).toBe('paid');
    expect(canTransition('paid', 'cancelled')).toBe(false);
    expect(canTransition('pending', 'pending')).toBe(false);
    expect(canTransition('expired', 'failed')).toBe(false);
  });

  it('simulate hanya dev + mock: production / provider lain → 404', async () => {
    const ctx = setup();
    const created = await ctx.svc.create(parseCreatePayment(BODY));
    expect((await ctx.svc.simulate(created.payment_id, 'failed')).status).toBe('failed');
    const prod = setup(undefined, { APP_ENV: 'production' });
    const c2 = await prod.svc.create(parseCreatePayment(BODY));
    expect((await catchApiError(() => prod.svc.simulate(c2.payment_id, 'paid'))).getStatus()).toBe(404);
    // G6 QA: staging (NODE_ENV=production, APP_ENV=staging) boleh simulate untuk UAT
    const staging = setup(undefined, { NODE_ENV: 'production', APP_ENV: 'staging' });
    const c3 = await staging.svc.create(parseCreatePayment(BODY));
    expect((await staging.svc.simulate(c3.payment_id, 'paid')).status).toBe('paid');
    expect((await catchApiError(() => parseSimulate({ status: 'pending' }))).getStatus()).toBe(400);
  });

  it('DTO: currency/method/amount/idempotency_key divalidasi', async () => {
    for (const bad of [{ currency: 'USD' }, { method: 'card' }, { amount: 0 }, { amount: 10.001 }, { idempotency_key: 'short' }, { company_code: 'XXX' }]) {
      expect((await catchApiError(() => parseCreatePayment({ ...BODY, ...bad }))).getStatus()).toBe(400);
    }
    expect(parseCreatePayment({ ...BODY, amount: 10.5 }).amount).toBe(10.5);
  });
});

describe('Konfigurasi & adapter vendor (kerangka, belum diuji sandbox)', () => {
  it('production menolak mock; default production = disabled; xendit/midtrans wajib PAYMENT_PROVIDER_EXPERIMENTAL', () => {
    expect(() => testConfig({ APP_ENV: 'production', PAYMENT_PROVIDER: 'mock' }).paymentProvider).toThrow('mock ditolak');
    expect(testConfig({ APP_ENV: 'production' }).paymentProvider).toBe('disabled');
    expect(createPaymentGateway(testConfig({ APP_ENV: 'production' }))).toBeNull();
    expect(testConfig().paymentProvider).toBe('mock');
    // G6 QA: staging = image production (NODE_ENV=production) + APP_ENV=staging → mock diizinkan (§8.3); tanpa APP_ENV → fail safe
    expect(testConfig({ NODE_ENV: 'production', APP_ENV: 'staging', PAYMENT_PROVIDER: 'mock' }).paymentProvider).toBe('mock');
    expect(() => testConfig({ NODE_ENV: 'production', APP_ENV: 'production', PAYMENT_PROVIDER: 'mock' }).paymentProvider).toThrow('mock ditolak');
    expect(() => testConfig({ NODE_ENV: 'production', PAYMENT_PROVIDER: 'mock' }).paymentProvider).toThrow('mock ditolak');
    expect(() => testConfig({ PAYMENT_PROVIDER: 'xendit' }).paymentProvider).toThrow('PAYMENT_PROVIDER_EXPERIMENTAL');
    expect(() => testConfig({ PAYMENT_PROVIDER: 'paypal' }).paymentProvider).toThrow();
    const http = jest.fn();
    expect(() => new XenditGateway({ apiKey: 'k', callbackToken: 't', timeoutMs: 1000, experimental: false }, http)).toThrow('belum diuji');
    expect(() => new MidtransGateway({ serverKey: 'k', acquirer: 'gopay', timeoutMs: 1000, experimental: false }, http)).toThrow('belum diuji');
  });

  it('Xendit: create memakai Basic auth + api-version; webhook diverifikasi x-callback-token; event qr.payment', async () => {
    const http = jest.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ id: 'qr_123', qr_string: '000201...', expires_at: '2026-10-05T03:10:00Z' }),
      text: async () => '',
    });
    const gw = new XenditGateway({ apiKey: 'xnd_test_key', callbackToken: 'cb-token-0123456789', timeoutMs: 1000, experimental: true }, http);
    const r = await gw.createQris({ paymentId: 'p1', orderRef: 'o', amount: 125000, currency: 'IDR', companyCode: 'FNB', expiresAt: new Date(0) });
    expect(r.providerRef).toBe('qr_123');
    const [url, init] = http.mock.calls[0] as [string, { headers: Record<string, string>; body: string }];
    expect(url).toBe('https://api.xendit.co/qr_codes');
    expect(init.headers.Authorization).toBe(`Basic ${Buffer.from('xnd_test_key:').toString('base64')}`);
    expect(init.headers['api-version']).toBe('2022-07-31');
    expect(JSON.parse(init.body)).toMatchObject({ reference_id: 'p1', type: 'DYNAMIC', currency: 'IDR', amount: 125000 });

    const body = Buffer.from(JSON.stringify({ event: 'qr.payment', data: { id: 'qrpy_1', qr_id: 'qr_123', status: 'SUCCEEDED', amount: 125000 } }));
    expect(gw.verifyWebhook({ headers: { 'x-callback-token': 'cb-token-0123456789' }, rawBody: body }).ok).toBe(true);
    expect(gw.verifyWebhook({ headers: { 'x-callback-token': 'salah' }, rawBody: body }).ok).toBe(false);
    expect(gw.parseWebhook(body)).toMatchObject({ eventId: 'qrpy_1', providerRef: 'qr_123', status: 'paid', amount: 125000 });
  });

  it('Midtrans: signature_key SHA512(order_id+status_code+gross_amount+server_key); status mapping', () => {
    const gw = new MidtransGateway({ serverKey: 'SB-Mid-server-xyz', acquirer: 'gopay', timeoutMs: 1000, experimental: true }, jest.fn());
    const n = { order_id: 'p1', status_code: '200', gross_amount: '125000.00', transaction_id: 't1', transaction_status: 'settlement' };
    const sig = createHash('sha512').update(`p1200125000.00SB-Mid-server-xyz`).digest('hex');
    const ok = Buffer.from(JSON.stringify({ ...n, signature_key: sig }));
    expect(gw.verifyWebhook({ headers: {}, rawBody: ok }).ok).toBe(true);
    const bad = Buffer.from(JSON.stringify({ ...n, gross_amount: '1.00', signature_key: sig }));
    expect(gw.verifyWebhook({ headers: {}, rawBody: bad }).ok).toBe(false);
    expect(gw.parseWebhook(ok)).toMatchObject({ eventId: 't1:settlement', providerRef: 'p1', status: 'paid', amount: 125000 });
  });
});
