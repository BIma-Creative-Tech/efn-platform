import { Logger } from '@nestjs/common';
import { createHash } from 'node:crypto';
import { MetricsService } from '../common/metrics/metrics.service';
import { FakeClock, FakeKvStore, catchApiError, randomSecret, testCipher, testConfig } from '../common/testing/fakes';
import { MemoryCartStore } from '../cart/cart-store';
import { CartService } from '../cart/cart.service';
import { QuoteService } from '../cart/quote.service';
import { CatalogService, ChannelInfo, ProductSummary } from '../catalog/catalog.service';
import { Json2OdooOnlineClient, Json2Transport, TransportResponse } from '../odoo-rpc/json2-client';
import { MockOdooOnline } from '../odoo-rpc/odoo-online.mock';
import { InvoiceBuyerProfile, OdooRpcError } from '../odoo-rpc/odoo-online.types';
import { OnlineMetrics } from '../odoo-rpc/online-metrics';
import { parseInvoiceRequest } from './invoice-request';
import { parseCheckout } from './orders.controller';
import { OrdersService, requestSha256 } from './orders.service';
import { FakeOnlinePayments, MemoryOnlineRepo } from './testing/online-fakes';

/**
 * G21 docs/04 §13.5 (W-06/E-39, L-63, K-36-7): `invoice_request` pada `POST /v1/orders`. Odoo = `MockOdooOnline`
 * (+ klien json2 dengan transport palsu untuk bentuk RPC). Fokus: validasi, tidak ada penyimpanan/log PII, panggilan
 * `efn_online_set_invoice_party` setelah place, kegagalan → anomali + lanjut, F&B / efn_pii mati → 422.
 */

const MEMBER = '8d7b5a1e-2c3f-4a5b-9c8d-7e6f5a4b3c2d';
const KEY = 'chk1_IIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIII';
const PROFILE: InvoiceBuyerProfile = {
  buyer_type: 'npwp',
  tax_id: '0123456789012345',
  name: 'Budi Santoso',
  address: 'Jl. Melati No. 5, Jakarta',
  email: 'budi.santoso@example.com',
};
const PII_VALUES = [PROFILE.tax_id, PROFILE.name, PROFILE.address, PROFILE.email, 'Melati', 'budi.santoso'];
const NAMES: Record<number, string> = { 11: 'Beras Premium 5 kg', 12: 'Minyak Goreng 2 L' };

function ch(code: string): ChannelInfo {
  return {
    channel_code: code,
    company_code: code,
    brand_code: code,
    name: code,
    fulfilment: { pickup: true, delivery: false },
    outlets: [{ outlet_code: 'TOKO-01', public_name: 'Toko Demo 01', pickup_enabled: true, prep_minutes: 30 }],
    payment_modes: ['qris', 'counter'],
  };
}
const CHANNELS = [ch('GL'), ch('PB'), ch('BCR')];

function stubCatalog(): CatalogService {
  return {
    channels: async () => CHANNELS,
    channel: async (c: string) => CHANNELS.find((x) => x.channel_code === c) ?? null,
    productsByIds: async (_c: string, ids: readonly number[]) =>
      new Map(
        ids
          .filter((id) => NAMES[id])
          .map((id): [number, ProductSummary] => [
            id,
            { product_id: id, name: NAMES[id], uom: 'Unit', category_id: 1, image_sha: null, price_incl: 0, price_excl: 0, tax_labels: [], max_qty: 20, weight_kg: null },
          ]),
      ),
  } as unknown as CatalogService;
}

function setup() {
  const clock = new FakeClock();
  const config = testConfig({ ONLINE_ENABLED: 'true', ODOO_ONLINE_MOCK: 'true', ONLINE_CHANNELS: 'GL,PB,BCR' });
  const kv = new FakeKvStore(clock.now);
  const repo = new MemoryOnlineRepo();
  repo.now = clock.now;
  repo.state.members.set(MEMBER, 'c'.repeat(64));
  const odoo = new MockOdooOnline();
  odoo.now = clock.now;
  const metrics = new OnlineMetrics(new MetricsService());
  const payments = new FakeOnlinePayments(repo);
  const catalog = stubCatalog();
  const cart = new CartService(config, new MemoryCartStore(), catalog);
  const quotes = new QuoteService(config, cart, catalog, odoo, kv, metrics);
  quotes.now = clock.now;
  const orders = new OrdersService(config, repo, odoo, payments, quotes, cart, catalog, testCipher(config), metrics);
  orders.now = clock.now;
  async function checkout(channels: string[] = ['GL'], invoice: InvoiceBuyerProfile | null = PROFILE, key = KEY) {
    const c = await cart.get(MEMBER);
    await cart.put(MEMBER, {
      version: c.version,
      groups: channels.map((channel) => ({ channel_code: channel, outlet_code: 'TOKO-01', fulfilment: 'pickup' as const, lines: [{ product_id: 11, qty: 1 }] })),
    });
    const q = await quotes.quote(MEMBER);
    last.input = { quoteId: q.quote_id, acceptTotal: q.amount_payable_total, paymentMethod: 'qris' as const, ...(invoice ? { invoiceRequest: invoice } : {}) };
    return orders.checkout(MEMBER, key, last.input);
  }
  const last: { input: Parameters<OrdersService['checkout']>[2] | null } = { input: null };
  return { clock, config, repo, odoo, metrics, payments, orders, checkout, last };
}

/** Semua argumen log Nest (log/warn/error/debug) selama `fn`. */
async function captureLogs<T>(fn: () => Promise<T>): Promise<{ result: T | null; error: unknown; lines: string }> {
  const lines: string[] = [];
  const spies = (['log', 'warn', 'error', 'debug', 'verbose'] as const).map((m) =>
    jest
      .spyOn(Logger.prototype as unknown as Record<string, (...a: unknown[]) => void>, m)
      .mockImplementation((...args: unknown[]) => void lines.push(args.map((a) => (a instanceof Error ? `${a.message} ${a.stack}` : String(a))).join(' '))),
  );
  try {
    return { result: await fn(), error: null, lines: lines.join('\n') };
  } catch (error) {
    return { result: null, error, lines: lines.join('\n') };
  } finally {
    for (const s of spies) s.mockRestore();
  }
}

const noPii = (s: string): void => {
  for (const v of PII_VALUES) expect(s).not.toContain(v);
};

describe('parseInvoiceRequest (§13.5 validasi BFF)', () => {
  it('NPWP/NIK 16 digit (pemisah dibuang), paspor huruf besar; teks dipangkas; null/undefined = tanpa faktur', () => {
    expect(parseInvoiceRequest(undefined)).toBeNull();
    expect(parseInvoiceRequest(null)).toBeNull();
    expect(parseInvoiceRequest({ ...PROFILE, tax_id: '01.234.567.8-901.2345', name: '  Budi   Santoso ' })).toEqual({ ...PROFILE, name: 'Budi Santoso' });
    expect(parseInvoiceRequest({ ...PROFILE, buyer_type: 'nik', tax_id: '3171 0123 4567 8901' })?.tax_id).toBe('3171012345678901');
    expect(parseInvoiceRequest({ ...PROFILE, buyer_type: 'passport', tax_id: ' a1234567 ' })?.tax_id).toBe('A1234567');
  });

  it.each([
    ['NPWP 15 digit', { tax_id: '012345678901234' }],
    ['NPWP huruf', { tax_id: '01234567890123AB' }],
    ['NIK 17 digit', { buyer_type: 'nik', tax_id: '31710123456789012' }],
    ['paspor 4 karakter', { buyer_type: 'passport', tax_id: 'A123' }],
    ['jenis tak dikenal', { buyer_type: 'ktp' }],
    ['nama 1 karakter', { name: 'B' }],
    ['nama 101 karakter', { name: 'x'.repeat(101) }],
    ['alamat 4 karakter', { address: 'Jl 1' }],
    ['alamat 256 karakter', { address: 'a'.repeat(256) }],
    ['email tanpa @', { email: 'budi.example.com' }],
    ['email > 254', { email: `${'a'.repeat(250)}@x.id` }],
    ['karakter kontrol di nama', { name: 'Budi\nSantoso' }],
    ['field tambahan', { phone: '6281234567890' }],
    ['bukan string', { name: 42 }],
  ])('%s → 400 (pesan tidak memuat nilai)', async (_l, over) => {
    const err = await catchApiError(() => parseInvoiceRequest({ ...PROFILE, ...over }));
    expect(err.getStatus()).toBe(400);
    noPii(JSON.stringify(err.body));
  });

  it('parseCheckout menerima invoice_request opsional; bentuk lama tidak berubah', async () => {
    const base = { quote_id: '11111111-2222-4333-8444-555555555555', accept_total: 1000 };
    expect(parseCheckout(base)).toEqual({ quoteId: base.quote_id, acceptTotal: 1000, paymentMethod: 'qris' });
    expect(parseCheckout({ ...base, invoice_request: PROFILE }).invoiceRequest).toEqual(PROFILE);
    expect((await catchApiError(() => parseCheckout({ ...base, invoice_request: 'ya' }))).getStatus()).toBe(400);
  });

  it('requestSha256: hanya penanda ada/tidaknya faktur; isi profil tidak memengaruhi sidik', () => {
    const base = { quoteId: 'q', acceptTotal: 1, paymentMethod: 'qris' as const };
    const withA = requestSha256({ ...base, invoiceRequest: PROFILE });
    expect(withA).not.toBe(requestSha256(base));
    expect(requestSha256({ ...base, invoiceRequest: { ...PROFILE, name: 'Orang Lain' } })).toBe(withA);
    const plain = createHash('sha256').update(JSON.stringify({ quote_id: 'q', accept_total: 1 }), 'utf8').digest('hex');
    expect(requestSha256(base)).toBe(plain); // pra-G21 tidak berubah
  });
});

describe('POST /v1/orders + invoice_request → efn_online_set_invoice_party (§13.5)', () => {
  it('kanal PPN (GL): place lalu set_invoice_party sekali per bagian (kunci idempoten), respons applied; PII tidak tersimpan/di-log', async () => {
    const ctx = setup();
    const { result, lines } = await captureLogs(() => ctx.checkout(['GL']));
    expect(result?.status).toBe('awaiting_payment');
    expect(result?.invoice_request).toEqual({ status: 'applied' });
    const part = [...ctx.repo.state.parts.values()][0];
    expect(ctx.odoo.invoiceCalls).toEqual([
      { ref: part.id, channel: 'GL', key: `online:invoice:${part.id}`, buyerType: 'npwp', profileSha: createHash('sha256').update(JSON.stringify(PROFILE), 'utf8').digest('hex') },
    ]);
    // urutan: place sebelum set_invoice_party
    const methods = ctx.odoo.calls.map((c) => c.method);
    expect(methods.indexOf('place')).toBeLessThan(methods.indexOf('setInvoiceParty'));
    // tidak ada PII di DB (order/part/outbox/event/audit), log, maupun respons
    noPii(JSON.stringify([...ctx.repo.state.orders.values(), ...ctx.repo.state.parts.values(), ctx.repo.state.outbox, [...ctx.repo.state.events.values()], ctx.repo.state.audits]));
    noPii(lines);
    noPii(JSON.stringify(result));
    // replay key sama → order yang sama tanpa memanggil Odoo ulang; key sama tanpa invoice_request → idempotency_conflict
    const input = ctx.last.input!;
    const before = ctx.odoo.calls.length;
    expect((await ctx.orders.checkout(MEMBER, KEY, input)).order_id).toBe(result?.order_id);
    expect(ctx.odoo.calls.length).toBe(before);
    const { invoiceRequest: _drop, ...withoutInvoice } = input;
    void _drop;
    const err = await catchApiError(() => ctx.orders.checkout(MEMBER, KEY, withoutInvoice));
    expect((err.body as { error: { code: string } }).error.code).toBe('idempotency_conflict');
  });

  it('kanal F&B (PB) atau campuran GL+PB → 422 invoice_not_available SEBELUM order dibuat (place tidak dipanggil)', async () => {
    for (const channels of [['PB'], ['GL', 'PB']]) {
      const ctx = setup();
      const err = await catchApiError(() => ctx.checkout(channels));
      expect(`${err.getStatus()} ${(err.body as { error: { code: string } }).error.code}`).toBe('422 invoice_not_available');
      expect(ctx.repo.state.orders.size).toBe(0);
      expect(ctx.odoo.calls.some((c) => c.method === 'place')).toBe(false);
    }
    // tanpa invoice_request kanal F&B tetap normal
    const ok = setup();
    expect((await ok.checkout(['PB'], null)).status).toBe('awaiting_payment');
  });

  it('Odoo menjawab invoice_not_available (efn_pii belum aktif, L-63) → 422 diteruskan, order dikompensasi (failed + cancel), tanpa intent', async () => {
    const ctx = setup();
    ctx.odoo.invoicePiiActive = false;
    const { error, lines } = await captureLogs(() => ctx.checkout(['BCR']));
    const err = error as { getStatus(): number; body: { error: { code: string } } };
    expect(`${err.getStatus()} ${err.body.error.code}`).toBe('422 invoice_not_available');
    const order = [...ctx.repo.state.orders.values()][0];
    expect(order.status).toBe('failed');
    expect(ctx.odoo.calls.some((c) => c.method === 'cancel')).toBe(true);
    expect(ctx.repo.state.intents.size).toBe(0);
    noPii(lines);
  });

  it('gagal lain (transport) → anomali invoice_party_failed tanpa PII + order tetap jalan (pending_cs)', async () => {
    const ctx = setup();
    ctx.odoo.failNext.set('setInvoiceParty', new OdooRpcError('transport', null, 'timeout'));
    const { result, lines } = await captureLogs(() => ctx.checkout(['GL']));
    expect(result?.status).toBe('awaiting_payment');
    expect(result?.invoice_request).toEqual({ status: 'pending_cs' });
    const anomaly = lines.split('\n').find((l) => l.includes('EFN_ONLINE_ANOMALY') && l.includes('invoice_party_failed'));
    expect(anomaly).toBeDefined();
    expect(anomaly).toContain('"code":"transport"');
    noPii(lines);
  });

  it('klien json2: path ke-12, argumen bernama kontrak; UserError invoice_not_available → business; log tanpa PII', async () => {
    const GL_KEY = randomSecret();
    const config = testConfig({ ODOO_API_KEY_ONLINE_GL_FILE: '/k/gl' }, (p) => {
      if (p === '/k/gl') return Buffer.from(GL_KEY);
      throw new Error('ENOENT');
    });
    const sent: { path: string; body: Record<string, unknown> }[] = [];
    const responses: TransportResponse[] = [
      { status: 200, body: JSON.stringify({ state: 'applied', partner_name: 'TIDAK DIBACA' }) },
      { status: 422, body: JSON.stringify({ name: 'odoo.exceptions.UserError', message: 'efn_online:invoice_not_available' }) },
    ];
    const transport: Json2Transport = async (path, body) => {
      sent.push({ path, body: JSON.parse(body) as Record<string, unknown> });
      return responses.shift() as TransportResponse;
    };
    const client = new Json2OdooOnlineClient(config, transport, new OnlineMetrics(new MetricsService()));
    const { result, lines } = await captureLogs(async () => {
      const ok = await client.setInvoiceParty('GL', 'part-1', PROFILE, 'online:invoice:part-1');
      const err = await client.setInvoiceParty('GL', 'part-2', PROFILE, 'online:invoice:part-2').catch((e: unknown) => e);
      return { ok, err };
    });
    expect(result?.ok).toEqual({ state: 'applied' });
    expect(result?.err).toMatchObject({ kind: 'business', code: 'invoice_not_available' });
    expect(sent[0]).toEqual({
      path: 'efn.online.order/efn_online_set_invoice_party',
      body: { channel_code: 'GL', platform_order_ref: 'part-1', profile: PROFILE, idempotency_key: 'online:invoice:part-1', context: { lang: 'id_ID' } },
    });
    noPii(lines);
  });
});
