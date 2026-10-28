import { Logger } from '@nestjs/common';
import { createHash } from 'node:crypto';
import { ODOO_ONLINE_PATHS, OdooRpcError } from '../odoo-rpc/odoo-online.types';
import { MetricsService } from '../common/metrics/metrics.service';
import { FakeClock, FakeKvStore, catchApiError, testCipher, testConfig } from '../common/testing/fakes';
import { MemoryCartStore } from '../cart/cart-store';
import { CartService } from '../cart/cart.service';
import { QuoteService } from '../cart/quote.service';
import { CatalogService, ChannelInfo, ProductSummary } from '../catalog/catalog.service';
import { MockOdooOnline } from '../odoo-rpc/odoo-online.mock';
import { OnlineMetrics } from '../odoo-rpc/online-metrics';
import { parseCompensate, parseOrderPaid, parseRefundRequested, parseUnpaidClosed } from './online-consumers';
import { OnlineAdminService } from './online-admin.service';
import { OnlineJobsService } from './online-jobs.service';
import { OnlineSagaService } from './online-saga.service';
import { COUNTER_REFUND_AUTH_PENDING, COUNTER_REFUND_AUTH_REJECTED, COUNTER_REFUND_REASON } from './online.model';
import { OrderEventsService, parseOdooEvent } from './order-events.service';
import { CounterPaymentView, OrdersService } from './orders.service';
import { PICKUP_CODE_RE, counterCodeAad, decryptCounterCode, decryptPickupCode } from './pickup-code';
import { FakeOnlinePayments, MemoryOnlineRepo } from './testing/online-fakes';

/**
 * G20 docs/04 §12.2 (W-30/E-45) + L-50/L-52/L-55: bayar di kasir. Odoo = stub `MockOdooOnline` (`payAtCounter` meniru
 * sync POS), payment = `FakeOnlinePayments` dengan `failCreate=true` di jalur counter untuk membuktikan provider tidak
 * pernah dipanggil (provider `disabled` di pilot tidak boleh menggagalkan order).
 */

const MEMBER = '8d7b5a1e-2c3f-4a5b-9c8d-7e6f5a4b3c2d';
const HASH = 'b'.repeat(64);
const KEY = 'chk1_CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC';
const KEY2 = 'chk1_DDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDD';
const NAMES: Record<number, string> = { 11: 'Beras Premium 5 kg', 12: 'Minyak Goreng 2 L', 13: 'Produk Uji Stok-Kurang (mock)', 14: 'Produk Uji Habis (mock)' };

function ch(code: string, modes: ('qris' | 'counter')[], brand: string): ChannelInfo {
  return {
    channel_code: code,
    company_code: code,
    brand_code: brand,
    name: code,
    fulfilment: { pickup: true, delivery: false },
    outlets: [{ outlet_code: 'TOKO-01', public_name: 'Toko Demo 01', pickup_enabled: true, prep_minutes: 30 }],
    payment_modes: modes,
  };
}
const CHANNELS = [ch('GL', ['qris', 'counter'], 'GL'), ch('PB', ['qris'], 'PB'), ch('BCF', ['counter'], 'BACHA')];

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

function setup(over: Record<string, string> = {}) {
  const clock = new FakeClock();
  // G26 L-95: bayar di kasir dibekukan untuk pesanan online baru; regresi alur lama memakai saklar dev ONLINE_COUNTER_LEGACY
  const config = testConfig({ ONLINE_ENABLED: 'true', ODOO_ONLINE_MOCK: 'true', ONLINE_CHANNELS: 'GL,PB,BCF', ONLINE_COUNTER_LEGACY: 'true', ...over });
  const kv = new FakeKvStore(clock.now);
  const repo = new MemoryOnlineRepo();
  repo.now = clock.now;
  repo.state.members.set(MEMBER, HASH);
  const odoo = new MockOdooOnline();
  odoo.now = clock.now;
  const metrics = new OnlineMetrics(new MetricsService());
  const payments = new FakeOnlinePayments(repo);
  const saga = new OnlineSagaService(repo, odoo, payments, metrics);
  saga.now = clock.now;
  payments.saga = saga;
  const catalog = stubCatalog();
  const cart = new CartService(config, new MemoryCartStore(), catalog);
  const quotes = new QuoteService(config, cart, catalog, odoo, kv, metrics);
  quotes.now = clock.now;
  const cipher = testCipher(config);
  const orders = new OrdersService(config, repo, odoo, payments, quotes, cart, catalog, cipher, metrics, saga);
  orders.now = clock.now;
  const jobs = new OnlineJobsService(config, repo, odoo, payments, saga, metrics);
  jobs.now = clock.now;
  const events = new OrderEventsService(repo, saga, metrics);
  const admin = new OnlineAdminService(repo, odoo, saga, metrics);

  async function drain(): Promise<void> {
    for (let i = 0; i < 50; i += 1) {
      const msg = repo.state.outbox.shift();
      if (!msg) return;
      if (msg.queue === 'order.paid') await saga.handleOrderPaid(parseOrderPaid(msg.payload));
      else if (msg.queue === 'order.unpaid_closed') await saga.handleUnpaidClosed(parseUnpaidClosed(msg.payload));
      else if (msg.queue === 'order.compensate') await saga.handleCompensate(parseCompensate(msg.payload));
      else if (msg.queue === 'order.refund.requested') await saga.handleRefundRequested(parseRefundRequested(msg.payload));
    }
  }

  async function checkout(
    groups: { channel: string; lines: { product_id: number; qty: number }[] }[] = [{ channel: 'GL', lines: [{ product_id: 11, qty: 2 }] }],
    method: 'qris' | 'counter' = 'counter',
    key = KEY,
  ) {
    const c = await cart.get(MEMBER);
    await cart.put(MEMBER, {
      version: c.version,
      groups: groups.map((g) => ({ channel_code: g.channel, outlet_code: 'TOKO-01', fulfilment: 'pickup' as const, lines: g.lines })),
    });
    const q = await quotes.quote(MEMBER);
    return orders.checkout(MEMBER, key, { quoteId: q.quote_id, acceptTotal: q.amount_payable_total, paymentMethod: method });
  }

  const partsOf = (orderId: string) => [...repo.state.parts.values()].filter((p) => p.order_id === orderId);
  const orderRow = (id: string) => repo.state.orders.get(id)!;

  function event(partId: string, ev: string, seq: number, extra: Record<string, unknown> = {}) {
    const part = repo.state.parts.get(partId)!;
    return parseOdooEvent(
      Buffer.from(
        JSON.stringify({
          schema_version: 1,
          idempotency_key: `online:77:${ev}:${seq}`,
          seq,
          platform_order_ref: part.id,
          checkout_ref: part.order_id,
          channel_code: part.channel_code,
          company_code: part.company_code,
          event: ev,
          state: ev,
          occurred_at: new Date(clock.ms).toISOString(),
          refund: null,
          ...extra,
        }),
      ),
    );
  }
  /** Kasir melunasi di POS (stub) lalu Odoo mengirim event `paid` `{ payment: { method: "counter" } }`. */
  async function payAtCounter(orderId: string) {
    const [part] = partsOf(orderId);
    const o = odoo.payAtCounter(part.id);
    return events.handle(event(part.id, 'paid', o.seq, { state: o.state, payment: { method: 'counter' } }));
  }
  return { clock, config, repo, odoo, metrics, payments, saga, cart, quotes, cipher, orders, jobs, events, admin, drain, checkout, partsOf, orderRow, event, payAtCounter };
}

describe('Checkout bayar di kasir (§12.2 no. 2–4)', () => {
  it('sukses: awaiting_counter_payment, payment PERSIS { method, counter_code, expires_at }, TANPA intent/provider, kode bayar terenkripsi', async () => {
    const ctx = setup();
    ctx.payments.failCreate = true; // provider "disabled": pemanggilan apa pun akan gagal
    const order = await ctx.checkout();
    expect(order).toMatchObject({ status: 'awaiting_counter_payment', amount_payable: 159_800, currency: 'IDR', paid_at: null });
    const payment = order.payment as CounterPaymentView;
    expect(Object.keys(payment).sort()).toEqual(['counter_code', 'expires_at', 'method']);
    expect(payment).toEqual({ method: 'counter', counter_code: expect.stringMatching(/^[A-Z2-9]{4}-[A-Z2-9]{4}$/), expires_at: order.expires_at });
    expect(order.parts).toEqual([expect.objectContaining({ channel_code: 'GL', company_code: 'GL', status: 'placed' })]);
    // tanpa payment intent / porsi
    expect(ctx.repo.state.intents.size).toBe(0);
    expect(ctx.repo.state.portions).toEqual([]);
    expect(ctx.orderRow(order.order_id)).toMatchObject({ payment_method: 'counter', payment_intent_id: null });
    // Odoo menerima payment_mode counter + hash kode bayar (L-56); kode bayar ≠ kode ambil; BFF hanya menyimpan ciphertext
    const [part] = ctx.partsOf(order.order_id);
    expect(ctx.odoo.orders.get(part.id)).toMatchObject({ paymentMode: 'counter', counterSha: expect.stringMatching(/^[0-9a-f]{64}$/) });
    const counter = decryptCounterCode(ctx.cipher, part.counter_code_enc as Buffer, part.id);
    expect(counter).toMatch(PICKUP_CODE_RE);
    expect(payment.counter_code).toBe(`${counter.slice(0, 4)}-${counter.slice(4)}`);
    expect(counter).not.toBe(decryptPickupCode(ctx.cipher, part.pickup_code_enc as Buffer, part.id).code);
    expect(part.counter_code_enc!.toString('latin1')).not.toContain(counter);
    expect(counterCodeAad(part.id)).toBe(`efn:online_order_part.counter_code_enc:v2:${part.id}`);
    expect(() => ctx.cipher.decrypt(part.counter_code_enc as Buffer, `efn:online_order_part.pickup_code_enc:v2:${part.id}`)).toThrow();
    // GET /v1/orders/:id (pemilik) → bentuk sama; kode ambil belum tersedia
    const again = await ctx.orders.get(MEMBER, order.order_id);
    expect(again.payment).toEqual(payment);
    expect((await catchApiError(() => ctx.orders.pickupCodes(MEMBER, order.order_id))).body).toMatchObject({ error: { code: 'pickup_code_unavailable' } });
    // order milik member lain → 404 (counter_code tidak pernah keluar)
    expect((await catchApiError(() => ctx.orders.get('00000000-0000-4000-8000-000000000009', order.order_id))).getStatus()).toBe(404);
    // daftar order memuat status & metode
    const list = await ctx.orders.list(MEMBER, null, 20);
    expect(list.items).toEqual([expect.objectContaining({ order_id: order.order_id, status: 'awaiting_counter_payment', payment_method: 'counter' })]);
    expect(JSON.stringify(list)).not.toContain(counter);
  });

  it('idempotensi: key + body sama → order sama; key sama, metode beda → 409 idempotency_conflict', async () => {
    const ctx = setup();
    const order = await ctx.checkout();
    const again = await ctx.orders.checkout(MEMBER, KEY, { quoteId: ctx.orderRow(order.order_id).quote_id, acceptTotal: 159_800, paymentMethod: 'counter' });
    expect(again.order_id).toBe(order.order_id);
    const conflict = await catchApiError(() =>
      ctx.orders.checkout(MEMBER, KEY, { quoteId: ctx.orderRow(order.order_id).quote_id, acceptTotal: 159_800, paymentMethod: 'qris' }),
    );
    expect(conflict.body).toMatchObject({ error: { code: 'idempotency_conflict' } });
    expect(ctx.odoo.calls.filter((c) => c.method === 'place')).toHaveLength(1);
  });

  it('>1 bagian (lintas PT) → 422 counter_payment_single_part_only, tanpa order/Odoo', async () => {
    const ctx = setup();
    const err = await catchApiError(() =>
      ctx.checkout([
        { channel: 'GL', lines: [{ product_id: 11, qty: 1 }] },
        { channel: 'BCF', lines: [{ product_id: 12, qty: 1 }] },
      ]),
    );
    expect(err.getStatus()).toBe(422);
    expect(err.body).toMatchObject({ error: { code: 'counter_payment_single_part_only' } });
    expect(ctx.repo.state.orders.size).toBe(0);
    expect(ctx.odoo.calls.filter((c) => c.method === 'place')).toHaveLength(0);
  });

  it('kanal tidak mengizinkan metode → 422 payment_method_unavailable (PB tanpa counter; BCF tanpa qris)', async () => {
    const ctx = setup();
    const a = await catchApiError(() => ctx.checkout([{ channel: 'PB', lines: [{ product_id: 11, qty: 1 }] }], 'counter'));
    expect(a.getStatus()).toBe(422);
    expect(a.body).toMatchObject({ error: { code: 'payment_method_unavailable' } });
    const b = await catchApiError(() => ctx.checkout([{ channel: 'BCF', lines: [{ product_id: 11, qty: 1 }] }], 'qris', KEY2));
    expect(b.body).toMatchObject({ error: { code: 'payment_method_unavailable' } });
    expect(ctx.repo.state.orders.size).toBe(0);
  });

  it('L-55: metode di luar ONLINE_PAYMENT_METHODS → 422 payment_method_unavailable (pilot counter: qris ditolak, counter jalan)', async () => {
    const counterOnly = setup({ ONLINE_PAYMENT_METHODS: 'counter' });
    counterOnly.payments.failCreate = true;
    expect((await catchApiError(() => counterOnly.checkout(undefined, 'qris'))).body).toMatchObject({ error: { code: 'payment_method_unavailable' } });
    expect((await counterOnly.checkout(undefined, 'counter', KEY2)).status).toBe('awaiting_counter_payment');
    const qrisOnly = setup({ ONLINE_PAYMENT_METHODS: 'qris' });
    expect((await catchApiError(() => qrisOnly.checkout(undefined, 'counter'))).body).toMatchObject({ error: { code: 'payment_method_unavailable' } });
  });

  it('ONLINE_MAX_OPEN_COUNTER_ORDERS_PER_MEMBER=1 → order counter ke-2 → 429 too_many_open_orders; QRIS tetap boleh (batas umum 2)', async () => {
    const ctx = setup();
    await ctx.checkout();
    const err = await catchApiError(() => ctx.checkout([{ channel: 'GL', lines: [{ product_id: 12, qty: 1 }] }], 'counter', KEY2));
    expect(err.getStatus()).toBe(429);
    expect(err.body).toMatchObject({ error: { code: 'too_many_open_orders' } });
    const qris = await ctx.checkout([{ channel: 'GL', lines: [{ product_id: 12, qty: 1 }] }], 'qris', 'chk1_EEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEE');
    expect(qris.status).toBe('awaiting_payment');
    // batas umum (2) juga menghitung order counter
    expect(
      (await catchApiError(() => ctx.checkout([{ channel: 'GL', lines: [{ product_id: 12, qty: 2 }] }], 'qris', 'chk1_FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF'))).getStatus(),
    ).toBe(429);
  });

  it('L-56: kode bayar dibuat BFF & tersimpan terenkripsi SEBELUM place; dikirim di order.counter_code; Odoo hanya menyimpan hash', async () => {
    const ctx = setup();
    const sent: { counter_code?: string; payment_mode?: string; pickup_code: string }[] = [];
    const place = ctx.odoo.place.bind(ctx.odoo);
    ctx.odoo.place = async (c, o) => {
      // saat place dipanggil, bagian sudah membawa ciphertext kode bayar (transaksi checkout)
      const stored = ctx.repo.state.parts.get(o.platform_order_ref)!;
      expect(stored.counter_code_enc).not.toBeNull();
      expect(decryptCounterCode(ctx.cipher, stored.counter_code_enc as Buffer, stored.id)).toBe(o.counter_code);
      sent.push({ counter_code: o.counter_code, payment_mode: o.payment_mode, pickup_code: o.pickup_code });
      return place(c, o);
    };
    const order = await ctx.checkout();
    const [part] = ctx.partsOf(order.order_id);
    expect(sent).toHaveLength(1);
    expect(sent[0].payment_mode).toBe('counter');
    expect(sent[0].counter_code).toMatch(PICKUP_CODE_RE);
    expect(sent[0].counter_code).not.toBe(sent[0].pickup_code); // kode bayar ≠ kode ambil (L-50)
    const mock = ctx.odoo.orders.get(part.id)!;
    expect(mock.counterSha).toBe(createHash('sha256').update(sent[0].counter_code!, 'utf8').digest('hex'));
    expect(JSON.stringify(mock)).not.toContain(sent[0].counter_code!);
    // kode yang ditampilkan ke pelanggan = kode yang dikirim ke Odoo; kasir mengetik kode salah → ditolak
    expect((order.payment as CounterPaymentView).counter_code!.replace('-', '')).toBe(sent[0].counter_code);
    expect(() => ctx.odoo.payAtCounter(part.id, 'AAAA-AAAA')).toThrow(OdooRpcError);
    expect(ctx.odoo.payAtCounter(part.id, (order.payment as CounterPaymentView).counter_code).state).toBe('confirmed');
  });

  it('QRIS tidak membuat kode bayar dan tidak mengirim payment_mode/counter_code; stub menolak counter tanpa kode', async () => {
    const ctx = setup();
    const order = await ctx.checkout(undefined, 'qris');
    expect(ctx.partsOf(order.order_id)[0].counter_code_enc).toBeNull();
    const mock = ctx.odoo.orders.get(ctx.partsOf(order.order_id)[0].id)!;
    expect(mock).toMatchObject({ paymentMode: 'qris', counterSha: null });
    const q = await ctx.odoo.quote('GL', 'TOKO-01', 'pickup', [{ product_id: 11, qty: 1 }], []);
    const base = {
      idempotency_key: 'online:place:z', platform_order_ref: 'z', checkout_ref: 'o', outlet_code: 'TOKO-01', fulfilment: 'pickup' as const,
      lines: [{ product_id: 11, qty: 1 }], quote_hash: q.quote_hash, pickup_code: 'ABCDEFGH', promo_codes: [],
    };
    await expect(ctx.odoo.place('GL', { ...base, payment_mode: 'counter' })).rejects.toMatchObject({ code: 'invalid_input' });
    await expect(ctx.odoo.place('GL', { ...base, counter_code: 'HGFEDCBA' })).rejects.toMatchObject({ code: 'invalid_input' });
  });

  it('place gagal (transport) setelah kode bayar tersimpan → kompensasi: order failed, kode bayar & kode ambil dihapus', async () => {
    const ctx = setup();
    ctx.odoo.failNext.set('place', new OdooRpcError('transport'));
    expect((await catchApiError(() => ctx.checkout())).body).toMatchObject({ error: { code: 'online_unavailable' } });
    const [order] = [...ctx.repo.state.orders.values()];
    expect(order.status).toBe('failed');
    expect(ctx.partsOf(order.id)[0]).toMatchObject({ status: 'place_failed', counter_code_enc: null, pickup_code_enc: null });
  });

  it('§12.3: issue berat (BCF batas 10 kg demo) memblokir checkout → 409 cart_has_issues', async () => {
    const ctx = setup();
    const err = await catchApiError(() => ctx.checkout([{ channel: 'BCF', lines: [{ product_id: 11, qty: 3 }] }]));
    expect(err.body).toMatchObject({ error: { code: 'cart_has_issues' } });
    const q = await ctx.quotes.quote(MEMBER);
    expect(q.groups[0].issues).toEqual([{ product_id: null, code: 'weight_limit' }]);
  });
});

describe('Pelunasan di kasir → event Odoo `paid` (§12.2 no. 6–7)', () => {
  it('event paid method counter → order paid → fulfilling, bagian confirmed, kode ambil tampil, kode bayar hilang; tanpa mark_paid/gateway; duplikat idempoten', async () => {
    const ctx = setup();
    ctx.payments.failCreate = true;
    const order = await ctx.checkout();
    const [part] = ctx.partsOf(order.order_id);
    const o = ctx.odoo.payAtCounter(part.id);
    const ev = ctx.event(part.id, 'paid', o.seq, { state: 'confirmed', payment: { method: 'counter' } });
    expect(ev.payment_method).toBe('counter');
    expect(await ctx.events.handle(ev)).toEqual({ ok: true, status: 'applied' });
    expect(ctx.orderRow(order.order_id)).toMatchObject({ status: 'fulfilling', payment_intent_id: null });
    expect(ctx.orderRow(order.order_id).paid_at).not.toBeNull();
    expect(ctx.partsOf(order.order_id)[0]).toMatchObject({ status: 'confirmed', counter_code_enc: null, last_seq: o.seq });
    const view = await ctx.orders.get(MEMBER, order.order_id);
    expect(view.payment).toEqual({ method: 'counter', expires_at: order.expires_at }); // status lain → tanpa counter_code
    expect((await ctx.orders.pickupCodes(MEMBER, order.order_id)).items).toHaveLength(1);
    expect(ctx.odoo.calls.filter((c) => c.method === 'markPaid')).toHaveLength(0);
    expect(ctx.payments.refunds).toEqual([]);
    expect(await ctx.events.handle(ev)).toEqual({ ok: true, status: 'duplicate' });
    // order counter tidak bisa dibatalkan pelanggan setelah lunas
    expect((await catchApiError(() => ctx.orders.cancel(MEMBER, order.order_id))).body).toMatchObject({ error: { code: 'order_not_cancellable' } });
  });

  it('event confirmed (tanpa payment) untuk order counter juga diterima sebagai pelunasan (urutan event tak dijamin)', async () => {
    const ctx = setup();
    const order = await ctx.checkout();
    const [part] = ctx.partsOf(order.order_id);
    const o = ctx.odoo.payAtCounter(part.id);
    await ctx.events.handle(ctx.event(part.id, 'confirmed', o.seq + 1));
    expect(ctx.orderRow(order.order_id).status).toBe('fulfilling');
    // event paid yang datang belakangan (seq lebih kecil) → ignored_stale
    expect(await ctx.events.handle(ctx.event(part.id, 'paid', o.seq, { state: 'confirmed', payment: { method: 'counter' } }))).toEqual({ ok: true, status: 'ignored_stale' });
  });

  it('metode tidak cocok → 409 payment_method_mismatch + anomali critical, status tidak berubah', async () => {
    const warn = jest.spyOn(Logger.prototype, 'warn').mockImplementation(() => undefined);
    try {
      const ctx = setup();
      const counter = await ctx.checkout();
      const [cp] = ctx.partsOf(counter.order_id);
      // event paid tanpa payment.method counter untuk order counter
      expect((await catchApiError(() => ctx.events.handle(ctx.event(cp.id, 'paid', 2, { state: 'confirmed' })))).body).toMatchObject({ error: { code: 'payment_method_mismatch' } });
      expect(ctx.orderRow(counter.order_id).status).toBe('awaiting_counter_payment');
      // pelunasan kasir untuk order QRIS
      const qris = await ctx.checkout([{ channel: 'GL', lines: [{ product_id: 12, qty: 1 }] }], 'qris', KEY2);
      const [qp] = ctx.partsOf(qris.order_id);
      expect((await catchApiError(() => ctx.events.handle(ctx.event(qp.id, 'paid', 2, { payment: { method: 'counter' } })))).body).toMatchObject({ error: { code: 'payment_method_mismatch' } });
      expect(ctx.orderRow(qris.order_id).status).toBe('awaiting_payment');
      expect(warn.mock.calls.filter((c) => String(c[0]).includes('"kind":"payment_method_mismatch"'))).toHaveLength(2);
      // metrik event memakai label sendiri (bukan company_mismatch)
      const outcomes = (await ctx.metrics.events.get()).values.map((v) => v.labels.outcome);
      expect(outcomes.filter((o) => o === 'payment_method_mismatch')).toHaveLength(1); // satu seri label {event:paid}
      expect(outcomes).not.toContain('company_mismatch');
      // payment.method tak dikenal → 400
      expect((await catchApiError(() => ctx.event(cp.id, 'paid', 3, { payment: { method: 'cash' } }))).getStatus()).toBe(400);
    } finally {
      warn.mockRestore();
    }
  });

  it('stok kurang saat pelunasan (produk 13) → stock_short → batal + refund TANPA gateway (manual_required, refund POS)', async () => {
    const ctx = setup();
    const order = await ctx.checkout([{ channel: 'GL', lines: [{ product_id: 13, qty: 1 }] }]);
    const warn = jest.spyOn(Logger.prototype, 'warn').mockImplementation(() => undefined);
    try {
      await ctx.payAtCounter(order.order_id);
      await ctx.drain();
    } finally {
      warn.mockRestore();
    }
    const [part] = ctx.partsOf(order.order_id);
    expect(part).toMatchObject({ status: 'cancelled', cancel_reason: 'stock_short' });
    const [rf] = [...ctx.repo.state.refunds.values()];
    // L-60: refund sistem counter tetap menunggu approver; otorisasi Odoo belum dipanggil
    expect(rf).toMatchObject({ part_id: part.id, payment_intent_id: null, status: 'approval_required', amount: 12_000 });
    expect(ctx.odoo.counterRefundAuths.size).toBe(0);
    expect(ctx.payments.refunds).toEqual([]);
    expect(ctx.orderRow(order.order_id).status).toBe('cancelled');
    const log = jest.spyOn(Logger.prototype, 'log').mockImplementation(() => undefined);
    const warn2 = jest.spyOn(Logger.prototype, 'warn').mockImplementation(() => undefined);
    try {
      await ctx.admin.approveRefund(rf.id, { actor: 'spv1/host-b', role: 'approver' }, 'stok kurang');
      await ctx.drain();
    } finally {
      log.mockRestore();
      warn2.mockRestore();
    }
    expect(ctx.repo.state.refunds.get(rf.id)).toMatchObject({ status: 'manual_required', failure_reason: COUNTER_REFUND_REASON });
    expect(ctx.odoo.counterRefundAuths.get(`online:counter_refund_auth:${rf.id}`)).toEqual({ ref: part.id, refundId: rf.id, refundRef: null, amount: 12_000 });
  });

  it('simulate-payment (dev, stub Odoo) untuk order counter → fulfilling; status selain paid → 400', async () => {
    const ctx = setup();
    const order = await ctx.checkout();
    expect((await catchApiError(() => ctx.orders.simulatePayment(MEMBER, order.order_id, 'expired'))).getStatus()).toBe(400);
    const res = await ctx.orders.simulatePayment(MEMBER, order.order_id, 'paid');
    expect(res.status).toBe('fulfilling');
    expect(res.payment).toEqual({ method: 'counter', expires_at: order.expires_at });
  });
});

describe('Batal & kedaluwarsa order bayar di kasir (L-52 no. 3, §12.2 no. 7)', () => {
  it('pelanggan batal awaiting_counter_payment → cancelled + SO dibatalkan, TANPA refund/provider; kode bayar dihapus; idempoten', async () => {
    const ctx = setup();
    ctx.payments.failCreate = true;
    const order = await ctx.checkout();
    expect(await ctx.orders.cancel(MEMBER, order.order_id)).toEqual({ status: 'cancelled' });
    expect(ctx.partsOf(order.order_id)[0].counter_code_enc).toBeNull();
    await ctx.drain();
    expect(ctx.orderRow(order.order_id)).toMatchObject({ status: 'cancelled', close_reason: 'customer' });
    expect(ctx.partsOf(order.order_id)[0]).toMatchObject({ status: 'cancelled', counter_code_enc: null });
    expect(ctx.odoo.orders.get(ctx.partsOf(order.order_id)[0].id)!.state).toBe('cancelled');
    expect(ctx.repo.state.refunds.size).toBe(0);
    expect(await ctx.orders.cancel(MEMBER, order.order_id)).toEqual({ status: 'cancelled' });
    expect((await ctx.orders.get(MEMBER, order.order_id)).payment).toEqual({ method: 'counter', expires_at: order.expires_at });
  });

  it('kedaluwarsa tanpa bayar: job → Odoo masih placed → expired + order.unpaid_closed → SO batal, tanpa refund', async () => {
    const ctx = setup();
    const order = await ctx.checkout();
    ctx.clock.advanceSeconds(3600 + 31);
    expect(await ctx.jobs.expireOnce()).toEqual({ expired: 1, skipped: 0 });
    await ctx.drain();
    expect(ctx.orderRow(order.order_id)).toMatchObject({ status: 'expired', close_reason: 'payment_expired' });
    expect(ctx.partsOf(order.order_id)[0]).toMatchObject({ status: 'cancelled', counter_code_enc: null });
    expect(ctx.repo.state.refunds.size).toBe(0);
  });

  it('event paid hilang: kedaluwarsa menanyakan status Odoo dulu → sudah lunas di kasir → diterapkan, TIDAK dibatalkan', async () => {
    const ctx = setup();
    const order = await ctx.checkout();
    ctx.clock.advanceSeconds(59 * 60);
    ctx.odoo.payAtCounter(ctx.partsOf(order.order_id)[0].id); // event tidak pernah sampai
    ctx.clock.advanceSeconds(2 * 60);
    expect(await ctx.jobs.expireOnce()).toEqual({ expired: 0, skipped: 1 });
    expect(ctx.orderRow(order.order_id).status).toBe('fulfilling');
    expect(ctx.partsOf(order.order_id)[0].status).toBe('confirmed');
    expect(ctx.odoo.calls.filter((c) => c.method === 'cancel')).toHaveLength(0);
  });

  it('Odoo tak terjangkau saat kedaluwarsa: dicoba ulang dalam toleransi 10 menit, lalu tetap dikedaluwarsakan', async () => {
    const ctx = setup();
    const order = await ctx.checkout();
    ctx.clock.advanceSeconds(3600 + 31);
    const status = ctx.odoo.status.bind(ctx.odoo);
    ctx.odoo.status = async () => {
      throw new Error('ECONNRESET');
    };
    const warn = jest.spyOn(Logger.prototype, 'warn').mockImplementation(() => undefined);
    try {
      expect(await ctx.jobs.expireOnce()).toEqual({ expired: 0, skipped: 1 });
      ctx.clock.advanceSeconds(10 * 60);
      expect(await ctx.jobs.expireOnce()).toEqual({ expired: 1, skipped: 0 });
    } finally {
      warn.mockRestore();
      ctx.odoo.status = status;
    }
    expect(ctx.orderRow(order.order_id).status).toBe('expired');
  });

  it('rekonsiliasi status (15 menit) memulihkan pelunasan yang event-nya hilang', async () => {
    const ctx = setup();
    const order = await ctx.checkout();
    ctx.odoo.payAtCounter(ctx.partsOf(order.order_id)[0].id);
    const log = jest.spyOn(Logger.prototype, 'log').mockImplementation(() => undefined);
    try {
      expect(await ctx.jobs.statusOnce()).toBe(1);
    } finally {
      log.mockRestore();
    }
    expect(ctx.orderRow(order.order_id).status).toBe('fulfilling');
    expect(ctx.partsOf(order.order_id)[0]).toMatchObject({ status: 'confirmed', counter_code_enc: null });
  });

  it('bayar terlambat di kasir (order sudah dibatalkan BFF, SO belum) → bagian review + anomali late_counter_payment; order tidak dibuka ulang', async () => {
    const ctx = setup();
    const order = await ctx.checkout();
    await ctx.orders.cancel(MEMBER, order.order_id); // outbox unpaid_closed belum diproses
    const warn = jest.spyOn(Logger.prototype, 'warn').mockImplementation(() => undefined);
    try {
      expect(await ctx.payAtCounter(order.order_id)).toEqual({ ok: true, status: 'applied' });
      expect(warn.mock.calls.some((c) => String(c[0]).includes('"kind":"late_counter_payment"'))).toBe(true);
    } finally {
      warn.mockRestore();
    }
    expect(ctx.orderRow(order.order_id).status).toBe('cancelled');
    expect(ctx.partsOf(order.order_id)[0].status).toBe('review');
  });

  it('L-57: event review `counter_paid_after_cancel` → bagian cancelled → review (pengecualian monoton), anomali critical, pelanggan melihat review, tanpa refund', async () => {
    const ctx = setup();
    const order = await ctx.checkout();
    await ctx.orders.cancel(MEMBER, order.order_id);
    await ctx.drain(); // SO dibatalkan, bagian `cancelled`
    const [part] = ctx.partsOf(order.order_id);
    expect(part.status).toBe('cancelled');
    const warn = jest.spyOn(Logger.prototype, 'warn').mockImplementation(() => undefined);
    try {
      const ev = ctx.event(part.id, 'review', part.last_seq + 1, { state: 'review', review_reason: 'counter_paid_after_cancel' });
      expect(ev.review_reason).toBe('counter_paid_after_cancel');
      expect(await ctx.events.handle(ev)).toEqual({ ok: true, status: 'applied' });
      expect(warn.mock.calls.some((c) => String(c[0]).includes('"kind":"late_counter_payment"') && String(c[0]).includes('counter_paid_after_cancel'))).toBe(true);
    } finally {
      warn.mockRestore();
    }
    expect(ctx.partsOf(order.order_id)[0].status).toBe('review');
    expect(ctx.orderRow(order.order_id).status).toBe('cancelled'); // status DB tetap ditutup
    expect((await ctx.orders.get(MEMBER, order.order_id)).status).toBe('review'); // pelanggan: "sedang diperiksa"
    expect(((await ctx.orders.list(MEMBER, null, 20)).items[0] as { status: string }).status).toBe('review');
    expect((await catchApiError(() => ctx.orders.pickupCodes(MEMBER, order.order_id))).body).toMatchObject({ error: { code: 'order_in_review' } });
    await ctx.drain();
    expect(ctx.repo.state.refunds.size).toBe(0); // tanpa refund otomatis — CS memutuskan
    expect(ctx.repo.state.outbox.filter((m) => m.queue.startsWith('order.'))).toEqual([]);
  });

  it('L-57: counter_paid_after_expiry pada order kedaluwarsa juga diterapkan', async () => {
    const ctx = setup();
    const order = await ctx.checkout();
    ctx.clock.advanceSeconds(3600 + 31);
    await ctx.jobs.expireOnce();
    await ctx.drain();
    const [part] = ctx.partsOf(order.order_id);
    const warn = jest.spyOn(Logger.prototype, 'warn').mockImplementation(() => undefined);
    try {
      await ctx.events.handle(ctx.event(part.id, 'review', part.last_seq + 1, { state: 'review', review_reason: 'counter_paid_after_expiry' }));
    } finally {
      warn.mockRestore();
    }
    expect(ctx.partsOf(order.order_id)[0].status).toBe('review');
    expect((await ctx.orders.get(MEMBER, order.order_id)).status).toBe('review');
  });

  it('L-57: event lain tetap ditolak — review tanpa alasan, alasan tak dikenal, atau order QRIS: cancelled tetap cancelled (ignored_stale)', async () => {
    const ctx = setup();
    const counter = await ctx.checkout();
    await ctx.orders.cancel(MEMBER, counter.order_id);
    const qris = await ctx.checkout([{ channel: 'GL', lines: [{ product_id: 12, qty: 1 }] }], 'qris', KEY2);
    await ctx.orders.cancel(MEMBER, qris.order_id);
    await ctx.drain();
    const [cp] = ctx.partsOf(counter.order_id);
    const [qp] = ctx.partsOf(qris.order_id);
    const warn = jest.spyOn(Logger.prototype, 'warn').mockImplementation(() => undefined);
    try {
      expect((await ctx.events.handle(ctx.event(cp.id, 'review', cp.last_seq + 1, { state: 'review' }))).status).toBe('ignored_stale');
      const unknown = ctx.event(cp.id, 'review', cp.last_seq + 2, { state: 'review', review_reason: 'whatever' });
      expect(unknown.review_reason).toBeNull();
      expect((await ctx.events.handle(unknown)).status).toBe('ignored_stale');
      expect((await ctx.events.handle(ctx.event(qp.id, 'review', qp.last_seq + 1, { state: 'review', review_reason: 'counter_paid_after_cancel' }))).status).toBe('ignored_stale');
      // event lain (mis. ready) pada bagian cancelled counter tetap ditolak
      expect((await ctx.events.handle(ctx.event(cp.id, 'ready', cp.last_seq + 3))).status).toBe('ignored_stale');
    } finally {
      warn.mockRestore();
    }
    expect(ctx.partsOf(counter.order_id)[0].status).toBe('cancelled');
    expect(ctx.partsOf(qris.order_id)[0].status).toBe('cancelled');
    expect((await ctx.orders.get(MEMBER, counter.order_id)).status).toBe('cancelled');
  });

  it('retensi menghapus kode bayar yang tidak lagi berlaku', async () => {
    const ctx = setup();
    const order = await ctx.checkout();
    const [part] = ctx.partsOf(order.order_id);
    const enc = part.counter_code_enc;
    ctx.repo.state.orders.get(order.order_id)!.status = 'expired'; // mis. ditutup di jalur lain sebelum kode dihapus
    ctx.repo.state.parts.get(part.id)!.counter_code_enc = enc;
    const res = (await ctx.jobs.retentionOnce()) as { counterNulled: number };
    expect(res.counterNulled).toBe(1);
    expect(ctx.repo.state.parts.get(part.id)!.counter_code_enc).toBeNull();
  });
});

describe('Refund setelah bayar di kasir (§12.2 no. 8)', () => {
  it('CS refund → approver → manual_required (TANPA gateway) → settle manual dengan ref refund POS → settled', async () => {
    const ctx = setup();
    const order = await ctx.checkout();
    await ctx.payAtCounter(order.order_id);
    const [part] = ctx.partsOf(order.order_id);
    const log = jest.spyOn(Logger.prototype, 'log').mockImplementation(() => undefined);
    const warn = jest.spyOn(Logger.prototype, 'warn').mockImplementation(() => undefined);
    try {
      const rf = await ctx.admin.refund({ partId: part.id, lines: null, full: true, restock: true, reason: 'customer_cs', actor: 'cs1/host-a', note: 'pelanggan batal' });
      expect(rf.status).toBe('approval_required');
      await ctx.admin.approveRefund(rf.refund_id, { actor: 'spv1/host-b', role: 'approver' }, 'disetujui');
      await ctx.drain();
      expect(ctx.repo.state.refunds.get(rf.refund_id)).toMatchObject({ status: 'manual_required', failure_reason: COUNTER_REFUND_REASON, payment_intent_id: null });
      expect(ctx.payments.refunds).toEqual([]); // gateway tidak pernah dipanggil
      // L-60: otorisasi refund POS dipanggil sekali, setelah approver, dengan ref & nominal persis
      expect(ctx.odoo.calls.filter((c) => c.method === 'counterRefundAuthorize')).toHaveLength(1);
      const dbRf = ctx.repo.state.refunds.get(rf.refund_id)!;
      expect(ctx.odoo.counterRefundAuths.get(`online:counter_refund_auth:${rf.refund_id}`)).toEqual({
        ref: part.id,
        refundId: rf.refund_id,
        refundRef: dbRf.odoo_refund_ref,
        amount: 159_800,
      });
      const out = await ctx.admin.settleManual(rf.refund_id, 'POS/RFD/0001', { actor: 'spv1/host-b', role: 'approver' }, 'refund POS tunai');
      expect(out.status).toBe('settled');
      expect(ctx.partsOf(order.order_id)[0].refunded_amount).toBe(159_800);
      // L-57: Odoo menolak refund_settled & mark_paid untuk counter → BFF tidak pernah memanggilnya
      expect(ctx.odoo.calls.filter((c) => c.method === 'refundSettled' || c.method === 'markPaid')).toHaveLength(0);
    } finally {
      log.mockRestore();
      warn.mockRestore();
    }
  });

  it('L-58: `refunded` = refund DIMINTA (tidak settle); event `refund_settled` (ref + order + nominal) → settled + notifikasi; idempoten', async () => {
    const ctx = setup();
    const order = await ctx.checkout();
    await ctx.payAtCounter(order.order_id);
    const [part] = ctx.partsOf(order.order_id);
    const log = jest.spyOn(Logger.prototype, 'log').mockImplementation(() => undefined);
    const warn = jest.spyOn(Logger.prototype, 'warn').mockImplementation(() => undefined);
    try {
      const rf = await ctx.admin.refund({ partId: part.id, lines: null, full: true, restock: true, reason: 'customer_cs', actor: 'cs1/host-a', note: 'pelanggan batal' });
      await ctx.admin.approveRefund(rf.refund_id, { actor: 'spv1/host-b', role: 'approver' }, 'disetujui');
      await ctx.drain();
      const ref = ctx.repo.state.refunds.get(rf.refund_id)!.odoo_refund_ref!;
      const seq = () => ctx.partsOf(order.order_id)[0].last_seq + 1;
      // `refunded` (nota kredit dibuat) tidak pernah menandai settled
      await ctx.events.handle(ctx.event(part.id, 'refunded', seq(), { state: 'confirmed', refund: { refund_ref: ref, amount: 159_800, kind: 'full', initiator: 'bff' } }));
      expect(ctx.repo.state.refunds.get(rf.refund_id)!.status).toBe('manual_required');
      expect(ctx.partsOf(order.order_id)[0].refunded_amount).toBe(0);
      // nominal beda → ignored_stale + anomali, tidak berubah
      const pos = 'pos:3f2b6c1e-8a4d-4c2e-9b1f-0a2b3c4d5e6f';
      const settled = (amount: number, s: number) =>
        ctx.event(part.id, 'refund_settled', s, { state: 'confirmed', refund: { refund_ref: ref, amount, settled_at: new Date(ctx.clock.ms).toISOString(), provider_refund_ref: pos } });
      expect((await ctx.events.handle(settled(100_000, seq()))).status).toBe('ignored_stale');
      expect(warn.mock.calls.some((c) => String(c[0]).includes('"kind":"refund_settled_rejected"') && String(c[0]).includes('mismatch'))).toBe(true);
      expect(ctx.repo.state.refunds.get(rf.refund_id)!.status).toBe('manual_required');
      // cocok → settled
      const ok = settled(159_800, seq());
      expect(ok.settlement).toMatchObject({ refund_ref: ref, amount: 159_800, provider_refund_ref: pos });
      expect((await ctx.events.handle(ok)).status).toBe('applied');
      expect(ctx.repo.state.refunds.get(rf.refund_id)).toMatchObject({ status: 'settled', provider_refund_ref: pos });
      expect(ctx.partsOf(order.order_id)[0].refunded_amount).toBe(159_800);
      expect(ctx.repo.state.outbox.some((m) => m.queue === 'notify.push' && (m.payload as { template: string }).template === 'online_order_refunded')).toBe(true);
      // replay persis → duplicate; event baru (seq lain) untuk refund yang sama → ignored_stale (tanpa dobel)
      expect((await ctx.events.handle(ok)).status).toBe('duplicate');
      expect((await ctx.events.handle(settled(159_800, seq()))).status).toBe('ignored_stale');
      expect(ctx.partsOf(order.order_id)[0].refunded_amount).toBe(159_800);
      expect(ctx.repo.state.refunds.size).toBe(1);
      expect(ctx.odoo.calls.filter((c) => c.method === 'refundSettled')).toHaveLength(0);
    } finally {
      log.mockRestore();
      warn.mockRestore();
    }
  });

  it('L-58: refund_settled untuk refund QRIS (punya intent) → ignored_stale + anomali critical; ref tak dikenal → ignored_stale; payload salah → 400', async () => {
    const ctx = setup();
    const order = await ctx.checkout(undefined, 'qris');
    const intentId = ctx.orderRow(order.order_id).payment_intent_id!;
    const log = jest.spyOn(Logger.prototype, 'log').mockImplementation(() => undefined);
    const warn = jest.spyOn(Logger.prototype, 'warn').mockImplementation(() => undefined);
    try {
      await ctx.payments.pay(intentId);
      await ctx.drain();
      const [part] = ctx.partsOf(order.order_id);
      const rf = await ctx.admin.refund({ partId: part.id, lines: null, full: true, restock: true, reason: 'customer_cs', actor: 'cs1/host-a', note: 'qris' });
      const ref = ctx.repo.state.refunds.get(rf.refund_id)!.odoo_refund_ref!;
      const pos = 'pos:3f2b6c1e-8a4d-4c2e-9b1f-0a2b3c4d5e6f';
      const mk = (refundRef: string, s: number) =>
        ctx.event(part.id, 'refund_settled', s, { state: 'confirmed', refund: { refund_ref: refundRef, amount: 159_800, settled_at: null, provider_refund_ref: pos } });
      expect((await ctx.events.handle(mk(ref, part.last_seq + 1))).status).toBe('ignored_stale');
      expect(warn.mock.calls.some((c) => String(c[0]).includes('"kind":"refund_settled_rejected"') && String(c[0]).includes('"severity":"critical"') && String(c[0]).includes('qris'))).toBe(true);
      expect(ctx.repo.state.refunds.get(rf.refund_id)!.status).toBe('approval_required');
      expect((await ctx.events.handle(mk('RINV/none', ctx.partsOf(order.order_id)[0].last_seq + 1))).status).toBe('ignored_stale');
      for (const bad of [{ refund_ref: ref, amount: 1 }, { refund_ref: ref, amount: 0, provider_refund_ref: pos }, { refund_ref: ref, amount: 1, provider_refund_ref: 'gw:x' }, null]) {
        expect((await catchApiError(() => ctx.event(part.id, 'refund_settled', 99, { refund: bad }))).getStatus()).toBe(400);
      }
    } finally {
      log.mockRestore();
      warn.mockRestore();
    }
    expect(ctx.partsOf(order.order_id)[0].refunded_amount).toBe(0);
  });

  it('order counter yang belum lunas → CS refund ditolak "order tanpa pembayaran"', async () => {
    const ctx = setup();
    const order = await ctx.checkout();
    const [part] = ctx.partsOf(order.order_id);
    ctx.repo.state.parts.get(part.id)!.status = 'confirmed'; // keadaan tidak konsisten disengaja
    await expect(
      ctx.admin.refund({ partId: part.id, lines: null, full: true, restock: true, reason: 'customer_cs', actor: 'cs1/host-a', note: 'uji' }),
    ).rejects.toThrow('order tanpa pembayaran');
  });
});

describe('L-60 (SEC-145): otorisasi refund POS `efn_online_counter_refund_authorize`', () => {
  async function paidCounter(over: Record<string, string> = {}) {
    const ctx = setup(over);
    const order = await ctx.checkout();
    await ctx.payAtCounter(order.order_id);
    const [part] = ctx.partsOf(order.order_id);
    return { ctx, order, part };
  }
  const authCalls = (ctx: ReturnType<typeof setup>) => ctx.odoo.calls.filter((c) => c.method === 'counterRefundAuthorize').length;
  const quiet = () => [jest.spyOn(Logger.prototype, 'log').mockImplementation(() => undefined), jest.spyOn(Logger.prototype, 'warn').mockImplementation(() => undefined)];

  it('belum disetujui approver → TIDAK dipanggil (refund CS di bawah ambang pun tetap approval_required untuk counter); refund_settled → unapproved critical', async () => {
    const { ctx, part } = await paidCounter({ ONLINE_CS_REFUND_APPROVAL_MIN_IDR: '99999999' });
    const admin = new OnlineAdminService(ctx.repo, ctx.odoo, ctx.saga, ctx.metrics, ctx.config);
    const spies = quiet();
    try {
      const rf = await admin.refund({ partId: part.id, lines: null, full: true, restock: true, reason: 'customer_cs', actor: 'cs1/host-a', note: 'uji ambang' });
      expect(rf.status).toBe('approval_required');
      await ctx.drain();
      expect(authCalls(ctx)).toBe(0);
      const ref = ctx.repo.state.refunds.get(rf.refund_id)!.odoo_refund_ref!;
      const ev = ctx.event(part.id, 'refund_settled', ctx.partsOf(part.order_id)[0].last_seq + 1, {
        state: 'confirmed',
        refund: { refund_ref: ref, amount: 159_800, settled_at: null, provider_refund_ref: 'pos:3f2b6c1e-8a4d-4c2e-9b1f-0a2b3c4d5e6f' },
      });
      expect((await ctx.events.handle(ev)).status).toBe('ignored_stale');
      const warn = spies[1];
      expect(warn.mock.calls.some((c) => String(c[0]).includes('"kind":"refund_settled_rejected"') && String(c[0]).includes('"severity":"critical"') && String(c[0]).includes('unapproved'))).toBe(true);
      // settle CLI juga ditolak sebelum persetujuan/otorisasi
      await expect(admin.settleManual(rf.refund_id, 'POS/RFD/1', { actor: 'spv1/host-b', role: 'approver' }, 'uji')).rejects.toThrow(/tidak dapat di-settle/);
    } finally {
      spies.forEach((s) => s.mockRestore());
    }
  });

  it('setelah approver → dipanggil SEKALI (key online:counter_refund_auth:<id>); pesan ulang / key lain → tidak dipanggil lagi; replay Odoo idempoten', async () => {
    const { ctx, part } = await paidCounter();
    const spies = quiet();
    try {
      const rf = await ctx.admin.refund({ partId: part.id, lines: null, full: true, restock: true, reason: 'customer_cs', actor: 'cs1/host-a', note: 'uji' });
      await ctx.admin.approveRefund(rf.refund_id, { actor: 'spv1/host-b', role: 'approver' }, 'disetujui uji');
      await ctx.drain();
      expect(authCalls(ctx)).toBe(1);
      const msg = { schema_version: 1 as const, idempotency_key: `order.refund.requested:${rf.refund_id}`, refund_id: rf.refund_id };
      expect(await ctx.saga.handleRefundRequested(msg)).toBe('duplicate');
      expect(await ctx.saga.handleRefundRequested({ ...msg, idempotency_key: `${msg.idempotency_key}:x` })).toBe('done');
      expect(authCalls(ctx)).toBe(1);
      // Odoo idempoten per key: payload sama → sukses; payload beda → idempotency_conflict
      const dbRf = ctx.repo.state.refunds.get(rf.refund_id)!;
      const input = { idempotency_key: `online:counter_refund_auth:${rf.refund_id}`, refund_id: rf.refund_id, refund_ref: dbRf.odoo_refund_ref, amount: 159_800 };
      expect(await ctx.odoo.counterRefundAuthorize('GL', part.id, input)).toEqual({ authorized: true, state: 'authorized', amount: 159_800 });
      await expect(ctx.odoo.counterRefundAuthorize('GL', part.id, { ...input, amount: 1 })).rejects.toMatchObject({ code: 'idempotency_conflict' });
    } finally {
      spies.forEach((s) => s.mockRestore());
    }
  });

  it('galat transien Odoo → tetap manual_required (auth_pending) + anomali, tidak diklaim; rekonsiliasi menerbitkan ulang → terotorisasi', async () => {
    const { ctx, part } = await paidCounter();
    const spies = quiet();
    try {
      const rf = await ctx.admin.refund({ partId: part.id, lines: null, full: true, restock: true, reason: 'customer_cs', actor: 'cs1/host-a', note: 'uji' });
      await ctx.admin.approveRefund(rf.refund_id, { actor: 'spv1/host-b', role: 'approver' }, 'disetujui uji');
      ctx.odoo.failNext.set('counterRefundAuthorize', new OdooRpcError('transport'));
      await ctx.drain();
      expect(ctx.repo.state.refunds.get(rf.refund_id)).toMatchObject({ status: 'manual_required', failure_reason: COUNTER_REFUND_AUTH_PENDING });
      expect(spies[1].mock.calls.some((c) => String(c[0]).includes('"kind":"counter_refund_auth_failed"'))).toBe(true);
      ctx.clock.advanceSeconds(16 * 60);
      await ctx.jobs.reconOnce();
      await ctx.drain();
      expect(ctx.repo.state.refunds.get(rf.refund_id)!.failure_reason).toBe(COUNTER_REFUND_REASON);
      expect(authCalls(ctx)).toBe(2);
      expect(ctx.odoo.counterRefundAuths.size).toBe(1);
    } finally {
      spies.forEach((s) => s.mockRestore());
    }
  });

  it('Odoo menolak (UserError) → manual_required + auth_rejected + anomali critical; tidak dicoba ulang; tidak bisa di-settle', async () => {
    const { ctx, part } = await paidCounter();
    const spies = quiet();
    try {
      const rf = await ctx.admin.refund({ partId: part.id, lines: null, full: true, restock: true, reason: 'customer_cs', actor: 'cs1/host-a', note: 'uji' });
      await ctx.admin.approveRefund(rf.refund_id, { actor: 'spv1/host-b', role: 'approver' }, 'disetujui uji');
      ctx.odoo.failNext.set('counterRefundAuthorize', new OdooRpcError('business', 'invalid_state'));
      await ctx.drain();
      expect(ctx.repo.state.refunds.get(rf.refund_id)).toMatchObject({ status: 'manual_required', failure_reason: COUNTER_REFUND_AUTH_REJECTED });
      expect(spies[1].mock.calls.some((c) => String(c[0]).includes('"kind":"counter_refund_auth_rejected"') && String(c[0]).includes('"severity":"critical"'))).toBe(true);
      ctx.clock.advanceSeconds(16 * 60);
      await ctx.jobs.reconOnce();
      await ctx.drain();
      expect(authCalls(ctx)).toBe(1);
      await expect(ctx.admin.settleManual(rf.refund_id, 'POS/RFD/1', { actor: 'spv1/host-b', role: 'approver' }, 'uji')).rejects.toThrow(/belum diotorisasi/);
    } finally {
      spies.forEach((s) => s.mockRestore());
    }
  });

  it('json2: path allowlist memuat efn_online_counter_refund_authorize', () => {
    expect(ODOO_ONLINE_PATHS.counterRefundAuthorize).toBe('efn.online.order/efn_online_counter_refund_authorize');
  });
});
