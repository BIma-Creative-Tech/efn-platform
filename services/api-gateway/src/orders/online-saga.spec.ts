import { Logger } from '@nestjs/common';
import { MetricsService } from '../common/metrics/metrics.service';
import { FakeClock, FakeKvStore, catchApiError, testCipher, testConfig } from '../common/testing/fakes';
import { MemoryCartStore } from '../cart/cart-store';
import { CartService } from '../cart/cart.service';
import { QuoteService } from '../cart/quote.service';
import { CatalogService, ChannelInfo, ProductSummary } from '../catalog/catalog.service';
import { MockOdooOnline } from '../odoo-rpc/odoo-online.mock';
import { OdooRpcError } from '../odoo-rpc/odoo-online.types';
import { OnlineMetrics } from '../odoo-rpc/online-metrics';
import { parseCompensate, parseOrderPaid, parseRefundRequested, parseUnpaidClosed } from './online-consumers';
import { OnlineAdminService } from './online-admin.service';
import { OnlineJobsService } from './online-jobs.service';
import { OnlinePrivacyService } from './online-privacy.service';
import { OnlineSagaService } from './online-saga.service';
import { OrderEventsService, parseOdooEvent } from './order-events.service';
import { OrdersService } from './orders.service';
import { PICKUP_CODE_RE, decryptPickupCode } from './pickup-code';
import { FakeOnlinePayments, MemoryOnlineRepo } from './testing/online-fakes';

const MEMBER = '8d7b5a1e-2c3f-4a5b-9c8d-7e6f5a4b3c2d';
const OTHER = '00000000-0000-4000-8000-000000000001';
const HASH = 'a'.repeat(64);
const KEY = 'chk1_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA';

const CHANNEL: ChannelInfo = {
  channel_code: 'RTL',
  company_code: 'RTL',
  name: 'EFN Retail',
  fulfilment: { pickup: true, delivery: false },
  outlets: [{ outlet_code: 'TOKO-01', public_name: 'Toko Demo 01', pickup_enabled: true, prep_minutes: 30 }],
};
const NAMES: Record<number, string> = { 11: 'Beras Premium 5 kg', 12: 'Minyak Goreng 2 L', 13: 'Produk Uji Stok-Kurang (mock)', 14: 'Produk Uji Habis (mock)' };

function stubCatalog(): CatalogService {
  const stub = {
    channels: async () => [CHANNEL],
    channel: async (c: string) => (c === 'RTL' ? CHANNEL : null),
    productsByIds: async (_c: string, ids: readonly number[]) =>
      new Map(
        ids
          .filter((id) => NAMES[id])
          .map((id): [number, ProductSummary] => [
            id,
            { product_id: id, name: NAMES[id], uom: 'Unit', category_id: 1, image_sha: null, price_incl: 0, price_excl: 0, tax_labels: [], max_qty: 20 },
          ]),
      ),
  };
  return stub as unknown as CatalogService;
}

function setup(over: Record<string, string> = {}) {
  const clock = new FakeClock();
  const config = testConfig({ ONLINE_ENABLED: 'true', ODOO_ONLINE_MOCK: 'true', ...over });
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
  const carts = new MemoryCartStore();
  const cart = new CartService(config, carts, catalog);
  const quotes = new QuoteService(config, cart, catalog, odoo, kv, metrics);
  quotes.now = clock.now;
  const cipher = testCipher(config);
  const orders = new OrdersService(config, repo, odoo, payments, quotes, cart, catalog, cipher, metrics);
  orders.now = clock.now;
  const jobs = new OnlineJobsService(config, repo, odoo, payments, saga, metrics);
  jobs.now = clock.now;
  const events = new OrderEventsService(repo, saga, metrics);
  const admin = new OnlineAdminService(repo, odoo, saga, metrics);
  const privacy = new OnlinePrivacyService(repo, orders);

  /** Proses outbox saga sampai kosong (meniru consumer RabbitMQ). Pesan notify.push dikumpulkan. */
  const notifications: Record<string, unknown>[] = [];
  async function drain(): Promise<void> {
    for (let i = 0; i < 50; i += 1) {
      const msg = repo.state.outbox.shift();
      if (!msg) return;
      if (msg.queue === 'order.paid') await saga.handleOrderPaid(parseOrderPaid(msg.payload));
      else if (msg.queue === 'order.unpaid_closed') await saga.handleUnpaidClosed(parseUnpaidClosed(msg.payload));
      else if (msg.queue === 'order.compensate') await saga.handleCompensate(parseCompensate(msg.payload));
      else if (msg.queue === 'order.refund.requested') await saga.handleRefundRequested(parseRefundRequested(msg.payload));
      else if (msg.queue === 'notify.push') notifications.push(msg.payload);
    }
  }

  async function checkout(lines = [{ product_id: 11, qty: 2 }], key = KEY) {
    const c = await cart.get(MEMBER);
    await cart.put(MEMBER, { version: c.version, groups: [{ channel_code: 'RTL', outlet_code: 'TOKO-01', fulfilment: 'pickup', lines }] });
    const q = await quotes.quote(MEMBER);
    const order = await orders.checkout(MEMBER, key, { quoteId: q.quote_id, acceptTotal: q.amount_payable_total });
    return { q, order };
  }

  function orderRow(id: string) {
    return repo.state.orders.get(id)!;
  }
  function partsOf(id: string) {
    return [...repo.state.parts.values()].filter((p) => p.order_id === id);
  }
  function event(partId: string, ev: string, seq: number, extra: Record<string, unknown> = {}) {
    const part = repo.state.parts.get(partId)!;
    return parseOdooEvent(
      Buffer.from(
        JSON.stringify({
          schema_version: 1,
          idempotency_key: `online:42:${ev}:${seq}`,
          seq,
          platform_order_ref: part.id,
          checkout_ref: part.order_id,
          channel_code: 'RTL',
          company_code: 'RTL',
          event: ev,
          state: ev,
          occurred_at: new Date(clock.ms).toISOString(),
          refund: null,
          ...extra,
        }),
      ),
    );
  }
  return { clock, config, kv, repo, odoo, metrics, payments, saga, cart, carts, quotes, cipher, orders, jobs, events, admin, privacy, drain, checkout, orderRow, partsOf, event, notifications };
}

describe('Checkout online (docs/04 §10.3.5)', () => {
  it('sukses: awaiting_payment + QR, bagian placed, porsi = Σ amount_payable, kode ambil terenkripsi, cart & quote dihapus', async () => {
    const ctx = setup();
    const { q, order } = await ctx.checkout();
    expect(order).toMatchObject({ status: 'awaiting_payment', amount_payable: 159_800, currency: 'IDR', payment: { method: 'qris', status: 'pending' } });
    expect(order.payment.qr_string).toContain('EFN-MOCK-QRIS');
    expect(order.parts).toEqual([expect.objectContaining({ channel_code: 'RTL', company_code: 'RTL', outlet_name: 'Toko Demo 01', status: 'placed', sale_order_name: 'S00001' })]);
    const [part] = ctx.partsOf(order.order_id);
    expect(ctx.repo.state.portions).toEqual([expect.objectContaining({ part_id: part.id, amount: 159_800, company_code: 'RTL' })]);
    // Odoo menerima kunci deterministik & kode ambil sekali; BFF hanya menyimpan ciphertext
    const placed = ctx.odoo.orders.get(part.id)!;
    expect(placed.key).toBe(`online:place:${part.id}`);
    const code = decryptPickupCode(ctx.cipher, part.pickup_code_enc as Buffer);
    expect(code).toMatch(PICKUP_CODE_RE);
    expect(part.pickup_code_enc!.toString('latin1')).not.toContain(code);
    expect(JSON.stringify(order)).not.toContain(code);
    expect(await ctx.cart.get(MEMBER)).toEqual({ version: 0, groups: [] });
    expect(await ctx.quotes.load(q.quote_id, MEMBER)).toBeNull();
  });

  it('idempotensi: key + body sama → order sama (klik ganda); body beda → 409 idempotency_conflict; tanpa key → 400', async () => {
    const ctx = setup();
    const { q, order } = await ctx.checkout();
    const again = await ctx.orders.checkout(MEMBER, KEY, { quoteId: q.quote_id, acceptTotal: q.amount_payable_total });
    expect(again.order_id).toBe(order.order_id);
    expect(ctx.repo.state.orders.size).toBe(1);
    expect(ctx.odoo.calls.filter((c) => c.method === 'place')).toHaveLength(1);
    const conflict = await catchApiError(() => ctx.orders.checkout(MEMBER, KEY, { quoteId: q.quote_id, acceptTotal: 1 }));
    expect(conflict.body).toEqual({ error: { code: 'idempotency_conflict', message: expect.any(String) } });
    expect((await catchApiError(() => ctx.orders.checkout(MEMBER, undefined, { quoteId: q.quote_id, acceptTotal: 1 }))).body).toMatchObject({
      error: { code: 'idempotency_key_required' },
    });
    expect((await catchApiError(() => ctx.orders.checkout(MEMBER, 'pendek', { quoteId: q.quote_id, acceptTotal: 1 }))).getStatus()).toBe(400);
  });

  it('validasi: accept_total beda → price_changed; cart berubah → cart_changed; quote kedaluwarsa/milik orang lain → quote_expired; isu stok → cart_has_issues', async () => {
    const ctx = setup();
    await ctx.cart.put(MEMBER, { version: 0, groups: [{ channel_code: 'RTL', outlet_code: 'TOKO-01', fulfilment: 'pickup', lines: [{ product_id: 11, qty: 1 }] }] });
    const q = await ctx.quotes.quote(MEMBER);
    expect((await catchApiError(() => ctx.orders.checkout(MEMBER, KEY, { quoteId: q.quote_id, acceptTotal: q.amount_payable_total - 1 }))).body).toMatchObject({
      error: { code: 'price_changed' },
    });
    expect((await catchApiError(() => ctx.orders.checkout(OTHER, KEY, { quoteId: q.quote_id, acceptTotal: q.amount_payable_total }))).body).toMatchObject({
      error: { code: 'quote_expired' },
    });
    await ctx.cart.put(MEMBER, { version: 1, groups: [{ channel_code: 'RTL', outlet_code: 'TOKO-01', fulfilment: 'pickup', lines: [{ product_id: 11, qty: 3 }] }] });
    expect((await catchApiError(() => ctx.orders.checkout(MEMBER, KEY, { quoteId: q.quote_id, acceptTotal: q.amount_payable_total }))).body).toMatchObject({
      error: { code: 'cart_changed' },
    });
    const q2 = await ctx.quotes.quote(MEMBER);
    ctx.clock.advanceSeconds(601);
    expect((await catchApiError(() => ctx.orders.checkout(MEMBER, KEY, { quoteId: q2.quote_id, acceptTotal: q2.amount_payable_total }))).body).toMatchObject({
      error: { code: 'quote_expired' },
    });
    await ctx.cart.put(MEMBER, { version: 2, groups: [{ channel_code: 'RTL', outlet_code: 'TOKO-01', fulfilment: 'pickup', lines: [{ product_id: 14, qty: 1 }] }] });
    const q3 = await ctx.quotes.quote(MEMBER);
    expect(q3.groups[0].issues).toEqual([{ product_id: 14, code: 'out_of_stock' }]);
    expect(JSON.stringify(q3)).not.toContain('quote_hash');
    expect((await catchApiError(() => ctx.orders.checkout(MEMBER, KEY, { quoteId: q3.quote_id, acceptTotal: q3.amount_payable_total }))).body).toMatchObject({
      error: { code: 'cart_has_issues' },
    });
    expect(ctx.repo.state.orders.size).toBe(0);
  });

  it('batas order terbuka → 429 too_many_open_orders; DSAR hapus aktif → 409 account_deleting', async () => {
    const ctx = setup({ ONLINE_MAX_OPEN_ORDERS_PER_MEMBER: '1' });
    await ctx.checkout();
    const err = await catchApiError(() => ctx.checkout([{ product_id: 12, qty: 1 }], 'chk1_BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB'));
    expect(err.getStatus()).toBe(429);
    expect(err.body).toMatchObject({ error: { code: 'too_many_open_orders' } });
    const ctx2 = setup();
    ctx2.repo.state.activeDeletes.add(MEMBER);
    expect((await catchApiError(() => ctx2.checkout())).body).toMatchObject({ error: { code: 'account_deleting' } });
  });

  it('Odoo menolak place (price_changed) → 409, order failed, bagian dibatalkan di Odoo (kompensasi)', async () => {
    const ctx = setup();
    await ctx.cart.put(MEMBER, { version: 0, groups: [{ channel_code: 'RTL', outlet_code: 'TOKO-01', fulfilment: 'pickup', lines: [{ product_id: 11, qty: 1 }] }] });
    const q = await ctx.quotes.quote(MEMBER);
    ctx.odoo.priceVersion = 2; // pricelist berubah setelah quote
    const err = await catchApiError(() => ctx.orders.checkout(MEMBER, KEY, { quoteId: q.quote_id, acceptTotal: q.amount_payable_total }));
    expect(err.body).toMatchObject({ error: { code: 'price_changed' } });
    const order = [...ctx.repo.state.orders.values()][0];
    expect(order).toMatchObject({ status: 'failed', close_reason: 'place_failed' });
    expect(ctx.partsOf(order.id)[0]).toMatchObject({ status: 'place_failed', pickup_code_enc: null });
    expect(ctx.odoo.calls.filter((c) => c.method === 'cancel')).toHaveLength(1);
  });

  it('Odoo/gateway tidak tersedia → 503/502, order failed; cancel gagal → outbox order.unpaid_closed', async () => {
    const ctx = setup();
    ctx.odoo.failNext.set('place', new OdooRpcError('transport'));
    ctx.odoo.failNext.set('cancel', new OdooRpcError('transport'));
    const err = await catchApiError(() => ctx.checkout());
    expect(err.body).toMatchObject({ error: { code: 'online_unavailable' } });
    expect(ctx.repo.state.outbox.map((m) => m.queue)).toEqual(['order.unpaid_closed']);
    const ctx2 = setup();
    ctx2.payments.failCreate = true;
    expect((await catchApiError(() => ctx2.checkout())).getStatus()).toBe(502);
    const o = [...ctx2.repo.state.orders.values()][0];
    expect(o.status).toBe('failed');
    expect(ctx2.partsOf(o.id)[0].status).toBe('cancelled');
  });
});

describe('Saga paid → confirmed → ready → handed_over (§10.6.3)', () => {
  it('jalur bahagia: webhook paid → order.paid → mark_paid confirmed → kode ambil → ready (notifikasi) → picked_up (kode dihapus) → completed', async () => {
    const ctx = setup();
    const { order } = await ctx.checkout();
    const intentId = ctx.orderRow(order.order_id).payment_intent_id!;
    expect((await catchApiError(() => ctx.orders.pickupCodes(MEMBER, order.order_id))).body).toMatchObject({ error: { code: 'pickup_code_unavailable' } });
    await ctx.payments.pay(intentId);
    expect(ctx.orderRow(order.order_id).status).toBe('paid');
    expect(ctx.partsOf(order.order_id)[0].status).toBe('paying');
    expect(ctx.repo.state.outbox[0]).toMatchObject({ queue: 'order.paid', messageId: `order.paid:${order.order_id}:${intentId}` });
    await ctx.drain();
    const part = ctx.partsOf(order.order_id)[0];
    expect(part.status).toBe('confirmed');
    expect(ctx.orderRow(order.order_id).status).toBe('fulfilling');
    expect(ctx.odoo.orders.get(part.id)!.paymentRef).toBe(intentId);
    const codes = await ctx.orders.pickupCodes(MEMBER, order.order_id);
    expect(codes.items[0].pickup_code).toMatch(/^[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4}$/);
    // event ready dari Odoo → notifikasi WA tanpa kode ambil
    const mo = ctx.odoo.advance(part.id, 'ready');
    expect(await ctx.events.handle(ctx.event(part.id, 'ready', mo.seq))).toEqual({ ok: true, status: 'applied' });
    await ctx.drain();
    expect(ctx.notifications).toEqual([
      expect.objectContaining({ template: 'online_order_ready', source: 'online_order', source_ref: part.id, member_hash: HASH, params: {} }),
    ]);
    expect(JSON.stringify(ctx.notifications)).not.toContain(codes.items[0].pickup_code);
    const mo2 = ctx.odoo.advance(part.id, 'picked_up');
    await ctx.events.handle(ctx.event(part.id, 'picked_up', mo2.seq));
    expect(ctx.partsOf(order.order_id)[0]).toMatchObject({ status: 'handed_over', pickup_code_enc: null });
    expect(ctx.orderRow(order.order_id)).toMatchObject({ status: 'completed' });
    expect(ctx.orderRow(order.order_id).closed_at).not.toBeNull();
    expect((await catchApiError(() => ctx.orders.pickupCodes(MEMBER, order.order_id))).getStatus()).toBe(409);
  });

  it('pesan order.paid ganda (redelivery/rekonsiliasi) → mark_paid idempoten, satu kali efek', async () => {
    const ctx = setup();
    const { order } = await ctx.checkout();
    const intentId = ctx.orderRow(order.order_id).payment_intent_id!;
    await ctx.payments.pay(intentId);
    const msg = parseOrderPaid(ctx.repo.state.outbox[0].payload);
    expect(await ctx.saga.handleOrderPaid(msg)).toBe('done');
    expect(await ctx.saga.handleOrderPaid(msg)).toBe('duplicate');
    expect(ctx.odoo.calls.filter((c) => c.method === 'markPaid')).toHaveLength(1);
  });

  it('nominal webhook ≠ intent → status tidak berubah + metrik amount_mismatch{stage=webhook}', async () => {
    const ctx = setup();
    const { order } = await ctx.checkout();
    await ctx.payments.pay(ctx.orderRow(order.order_id).payment_intent_id!, 1);
    expect(ctx.orderRow(order.order_id).status).toBe('awaiting_payment');
    const m = await ctx.metrics.amountMismatch.get();
    expect(m.values.find((v) => v.labels.stage === 'webhook')?.value).toBe(1);
  });

  it('transport error saat mark_paid → dilempar (retry), bagian tetap paying; rekonsiliasi menerbitkan ulang order.paid', async () => {
    const ctx = setup();
    const { order } = await ctx.checkout();
    await ctx.payments.pay(ctx.orderRow(order.order_id).payment_intent_id!);
    const msg = parseOrderPaid(ctx.repo.state.outbox.shift()!.payload);
    ctx.odoo.failNext.set('markPaid', new OdooRpcError('transport'));
    await expect(ctx.saga.handleOrderPaid(msg)).rejects.toBeInstanceOf(OdooRpcError);
    expect(ctx.partsOf(order.order_id)[0].status).toBe('paying');
    ctx.clock.advanceSeconds(6 * 60);
    const stats = await ctx.jobs.reconOnce();
    expect(stats).toMatchObject({ paid: 1 });
    await ctx.drain();
    expect(ctx.partsOf(order.order_id)[0].status).toBe('confirmed');
  });
});

describe('Timeout, bayar terlambat, stok kurang, refund (§10.6.5–10.6.6)', () => {
  it('timeout 15 menit → sweeper expired + intent dibatalkan → SO dibatalkan; bayar terlambat → refund penuh porsi tanpa mark_paid', async () => {
    const ctx = setup();
    const { order } = await ctx.checkout();
    const intentId = ctx.orderRow(order.order_id).payment_intent_id!;
    ctx.clock.advanceSeconds(14 * 60);
    expect((await ctx.jobs.expireOnce()).expired).toBe(0);
    ctx.clock.advanceSeconds(80); // 15:20 < expires_at + 30 s
    expect((await ctx.jobs.expireOnce()).expired).toBe(0);
    ctx.clock.advanceSeconds(20);
    expect((await ctx.jobs.expireOnce()).expired).toBe(1);
    expect(ctx.orderRow(order.order_id)).toMatchObject({ status: 'expired', close_reason: 'payment_expired' });
    expect(ctx.repo.state.intents.get(intentId)!.status).toBe('cancelled');
    await ctx.drain();
    const part = ctx.partsOf(order.order_id)[0];
    expect(part).toMatchObject({ status: 'cancelled', cancel_reason: 'payment_expired' });
    expect(ctx.odoo.orders.get(part.id)!.state).toBe('cancelled');
    // pembayaran terlambat (expired/cancelled → paid diizinkan §8.11)
    await ctx.payments.pay(intentId);
    expect(ctx.orderRow(order.order_id).status).toBe('expired');
    await ctx.drain();
    expect(ctx.odoo.calls.filter((c) => c.method === 'markPaid')).toHaveLength(0);
    const refunds = [...ctx.repo.state.refunds.values()];
    expect(refunds).toEqual([expect.objectContaining({ kind: 'late_payment', reason: 'late_payment', amount: 159_800, status: 'settled' })]);
    expect(ctx.payments.refunds).toEqual([{ paymentId: intentId, req: expect.objectContaining({ refundId: refunds[0].id, amount: 159_800 }) }]);
    expect(ctx.partsOf(order.order_id)[0].refunded_amount).toBe(159_800);
  });

  it('webhook paid menang balapan dengan sweeper: intent paid → order tidak di-expire', async () => {
    const ctx = setup();
    const { order } = await ctx.checkout();
    ctx.clock.advanceSeconds(16 * 60);
    await ctx.payments.pay(ctx.orderRow(order.order_id).payment_intent_id!);
    expect((await ctx.jobs.expireOnce()).expired).toBe(0);
    expect(ctx.orderRow(order.order_id).status).toBe('paid');
  });

  it('stok kurang setelah bayar → stock_short → cancel Odoo + refund penuh porsi bagian → settled (tanpa refund_settled Odoo)', async () => {
    const ctx = setup();
    const { order } = await ctx.checkout([{ product_id: 13, qty: 2 }]);
    await ctx.payments.pay(ctx.orderRow(order.order_id).payment_intent_id!);
    await ctx.drain();
    const part = ctx.partsOf(order.order_id)[0];
    expect(part).toMatchObject({ status: 'cancelled', cancel_reason: 'stock_short', refunded_amount: 24_000, pickup_code_enc: null });
    expect([...ctx.repo.state.refunds.values()]).toEqual([expect.objectContaining({ kind: 'full', reason: 'stock_short', amount: 24_000, status: 'settled', odoo_refund_ref: null })]);
    expect(ctx.odoo.calls.filter((c) => c.method === 'refundSettled')).toHaveLength(0);
    expect(ctx.orderRow(order.order_id).status).toBe('cancelled');
    expect(ctx.notifications.map((n) => n.template)).toEqual(['online_order_cancelled', 'online_order_refunded']);
  });

  it('gateway tidak mendukung refund → manual_required + anomali; CLI settle manual → settled', async () => {
    const ctx = setup();
    const warn = jest.spyOn(Logger.prototype, 'warn').mockImplementation(() => undefined);
    try {
      ctx.payments.refundMode = 'unsupported';
      const { order } = await ctx.checkout([{ product_id: 13, qty: 1 }]);
      await ctx.payments.pay(ctx.orderRow(order.order_id).payment_intent_id!);
      await ctx.drain();
      const rf = [...ctx.repo.state.refunds.values()][0];
      expect(rf.status).toBe('manual_required');
      expect(warn.mock.calls.map((c) => String(c[0])).some((l) => l.startsWith('EFN_ONLINE_ANOMALY') && l.includes('refund_manual_required'))).toBe(true);
      expect(await ctx.admin.settleManual(rf.id, 'TRF-2026-001', 'finance.ani', 'transfer manual ke rekening pelanggan')).toEqual({ status: 'settled' });
      expect(ctx.repo.state.refunds.get(rf.id)).toMatchObject({ status: 'settled', provider_refund_ref: 'TRF-2026-001' });
      expect(ctx.repo.state.audits).toEqual([expect.objectContaining({ action: 'refund_settle', actor: 'finance.ani', refund_id: rf.id, outcome: 'settled' })]);
    } finally {
      warn.mockRestore();
    }
  });

  it('refund CS parsial lewat CLI: ≤ porsi, kumulatif; melebihi porsi → failed; teraudit', async () => {
    const ctx = setup();
    const { order } = await ctx.checkout([{ product_id: 11, qty: 2 }]);
    await ctx.payments.pay(ctx.orderRow(order.order_id).payment_intent_id!);
    await ctx.drain();
    const part = ctx.partsOf(order.order_id)[0];
    const r1 = await ctx.admin.refund({ partId: part.id, lines: [{ product_id: 11, qty: 1 }], full: false, restock: true, reason: 'customer_cs', actor: 'cs.budi', note: 'barang rusak' });
    expect(r1).toMatchObject({ amount: 79_900, status: 'odoo_done' });
    await ctx.drain();
    expect(ctx.repo.state.refunds.get(r1.refund_id)!.status).toBe('settled');
    expect(ctx.odoo.calls.filter((c) => c.method === 'refundSettled')).toHaveLength(1);
    expect(ctx.partsOf(order.order_id)[0].refunded_amount).toBe(79_900);
    expect(ctx.repo.state.audits[0]).toMatchObject({ action: 'refund', actor: 'cs.budi', part_id: part.id, amount: 79_900 });
    // Odoo menolak refund melebihi sisa (kumulatif ≤ porsi)
    await expect(ctx.admin.refund({ partId: part.id, lines: [{ product_id: 11, qty: 2 }], full: false, restock: false, reason: 'customer_cs', actor: 'cs.budi', note: 'lagi' })).rejects.toThrow();
    await expect(ctx.admin.refund({ partId: part.id, lines: null, full: false, restock: false, reason: 'customer_cs', actor: 'x', note: 'abc' })).rejects.toThrow();
  });

  it('pemeriksaan BFF: refund melebihi porsi bagian → failed + anomali (DB juga memaksa refunded_amount ≤ amount_payable)', async () => {
    const ctx = setup();
    const { order } = await ctx.checkout([{ product_id: 13, qty: 1 }]);
    await ctx.payments.pay(ctx.orderRow(order.order_id).payment_intent_id!);
    ctx.odoo.failNext.set('markPaid', new OdooRpcError('transport'));
    await expect(ctx.drain()).rejects.toBeInstanceOf(OdooRpcError);
    const part = ctx.partsOf(order.order_id)[0];
    const rf = await ctx.repo.insertRefund({
      id: '11111111-1111-4111-8111-111111111111',
      part_id: part.id,
      payment_intent_id: ctx.orderRow(order.order_id).payment_intent_id!,
      kind: 'full',
      reason: 'review',
      lines: null,
      amount: part.amount_payable + 1,
      odoo_refund_ref: null,
      status: 'requested',
      requested_by: 'system',
    });
    expect(await ctx.saga.handleRefundRequested({ schema_version: 1, idempotency_key: 'k-overrun', refund_id: rf.id })).toBe('done');
    expect(ctx.repo.state.refunds.get(rf.id)!.status).toBe('failed');
    expect(ctx.payments.refunds).toHaveLength(0);
  });

  it('pelanggan batal saat awaiting_payment → cancelled + intent cancelled + SO dibatalkan; setelah bayar → 409; idempoten', async () => {
    const ctx = setup();
    const { order } = await ctx.checkout();
    expect(await ctx.orders.cancel(MEMBER, order.order_id)).toEqual({ status: 'cancelled' });
    expect(await ctx.orders.cancel(MEMBER, order.order_id)).toEqual({ status: 'cancelled' });
    await ctx.drain();
    expect(ctx.partsOf(order.order_id)[0]).toMatchObject({ status: 'cancelled', cancel_reason: 'customer' });
    const ctx2 = setup();
    const o2 = (await ctx2.checkout()).order;
    await ctx2.payments.pay(ctx2.orderRow(o2.order_id).payment_intent_id!);
    expect((await catchApiError(() => ctx2.orders.cancel(MEMBER, o2.order_id))).body).toMatchObject({ error: { code: 'order_not_cancellable' } });
  });

  it('review dari mark_paid → CLI resolve cancel → batal Odoo + refund porsi', async () => {
    const ctx = setup();
    const { order } = await ctx.checkout();
    const part = ctx.partsOf(order.order_id)[0];
    ctx.odoo.orders.get(part.id)!.amountPayable += 1; // Odoo melihat nominal berbeda → review
    await ctx.payments.pay(ctx.orderRow(order.order_id).payment_intent_id!);
    await ctx.drain();
    expect(ctx.partsOf(order.order_id)[0].status).toBe('review');
    expect(await ctx.admin.resolve(part.id, 'cancel', 'ops.sari', 'nominal tidak cocok')).toEqual({ status: 'cancelled' });
    await ctx.drain();
    expect([...ctx.repo.state.refunds.values()][0]).toMatchObject({ reason: 'review', status: 'settled' });
  });
});

describe('Event Odoo → BFF (§10.5)', () => {
  async function confirmed() {
    const ctx = setup();
    const { order } = await ctx.checkout();
    await ctx.payments.pay(ctx.orderRow(order.order_id).payment_intent_id!);
    await ctx.drain();
    return { ctx, order, part: ctx.partsOf(order.order_id)[0] };
  }

  it('duplikat → duplicate; seq lama → ignored_stale; event tak dikenal → ignored_unknown_event; mundur diabaikan', async () => {
    const { ctx, part } = await confirmed();
    const ready = ctx.event(part.id, 'ready', 5);
    expect((await ctx.events.handle(ready)).status).toBe('applied');
    expect((await ctx.events.handle(ready)).status).toBe('duplicate');
    expect((await ctx.events.handle(ctx.event(part.id, 'confirmed', 4))).status).toBe('ignored_stale');
    expect((await ctx.events.handle(ctx.event(part.id, 'teleported', 6))).status).toBe('ignored_unknown_event');
    expect((await ctx.events.handle(ctx.event(part.id, 'confirmed', 7))).status).toBe('applied'); // seq baru, transisi mundur diabaikan
    expect(ctx.partsOf(part.order_id)[0]).toMatchObject({ status: 'ready', last_seq: 7 });
    expect((await ctx.metrics.backwardTransitions.get()).values[0].value).toBe(1);
    expect([...ctx.repo.state.events.values()].map((e) => e.outcome)).toEqual(['applied', 'ignored_stale', 'ignored_unknown_event', 'applied']);
  });

  it('bagian tidak ada → 404 (klaim rollback); kanal/PT beda → 409 company_mismatch', async () => {
    const { ctx, part } = await confirmed();
    const ev = ctx.event(part.id, 'ready', 9);
    const bad = { ...ev, checkout_ref: '00000000-0000-4000-8000-000000000099' };
    expect((await catchApiError(() => ctx.events.handle(bad))).getStatus()).toBe(404);
    expect(await ctx.repo.isClaimed('online.order-events', ev.idempotency_key)).toBe(false);
    expect((await catchApiError(() => ctx.events.handle({ ...ev, company_code: 'FNB' }))).body).toMatchObject({ error: { code: 'company_mismatch' } });
    expect((await ctx.events.handle(ev)).status).toBe('applied');
  });

  it('cancelled inisiatif Odoo membawa refund → online_refund(odoo_done) → gateway → refund_settled', async () => {
    const { ctx, part } = await confirmed();
    const ev = ctx.event(part.id, 'cancelled', 10, { refund: { refund_ref: 'RINV/0001', amount: 159_800, kind: 'full', initiator: 'odoo' } });
    expect((await ctx.events.handle(ev)).status).toBe('applied');
    await ctx.drain();
    expect([...ctx.repo.state.refunds.values()]).toEqual([expect.objectContaining({ odoo_refund_ref: 'RINV/0001', reason: 'ops_cancelled', status: 'settled' })]);
    expect(ctx.odoo.calls.filter((c) => c.method === 'refundSettled')).toHaveLength(1);
    expect(ctx.orderRow(part.order_id).status).toBe('cancelled');
  });

  it('validasi skema: schema_version/seq/kunci/ukuran salah → 400', async () => {
    const ok = { schema_version: 1, idempotency_key: 'online:1:ready:2', seq: 2, platform_order_ref: MEMBER, checkout_ref: OTHER, channel_code: 'RTL', company_code: 'RTL', event: 'ready', state: 'ready', occurred_at: null, refund: null };
    expect(parseOdooEvent(Buffer.from(JSON.stringify(ok))).seq).toBe(2);
    for (const bad of [{ schema_version: 2 }, { seq: -1 }, { idempotency_key: 'x' }, { platform_order_ref: '1' }, { refund: { refund_ref: 'r', amount: 1.5, kind: 'full', initiator: 'odoo' } }]) {
      expect((await catchApiError(() => parseOdooEvent(Buffer.from(JSON.stringify({ ...ok, ...bad }))))).getStatus()).toBe(400);
    }
    expect((await catchApiError(() => parseOdooEvent(Buffer.alloc(9000, 0x20)))).getStatus()).toBe(400);
  });
});

describe('Anti-enumeration, privasi, retensi', () => {
  it('order milik member lain → 404 sama dengan tidak ada', async () => {
    const ctx = setup();
    const { order } = await ctx.checkout();
    for (const fn of [() => ctx.orders.get(OTHER, order.order_id), () => ctx.orders.cancel(OTHER, order.order_id), () => ctx.orders.pickupCodes(OTHER, order.order_id)]) {
      expect((await catchApiError(fn)).body).toMatchObject({ error: { code: 'not_found' } });
    }
    const list = await ctx.orders.list(OTHER, null, 20);
    expect(list.items).toEqual([]);
    const mine = await ctx.orders.list(MEMBER, null, 20);
    expect(mine.items).toEqual([expect.objectContaining({ order_id: order.order_id, parts: [{ channel_code: 'RTL', company_code: 'RTL', status: 'placed' }] })]);
  });

  it('L-4: DELETE /v1/me — order belum bayar dibatalkan otomatis; bagian paying/confirmed/ready/review → 409 open_online_orders', async () => {
    const ctx = setup();
    const { order } = await ctx.checkout();
    await ctx.privacy.prepareDelete(MEMBER);
    expect(ctx.orderRow(order.order_id).status).toBe('cancelled');
    const o2 = (await ctx.checkout([{ product_id: 12, qty: 1 }], 'chk1_CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC')).order;
    await ctx.payments.pay(ctx.orderRow(o2.order_id).payment_intent_id!);
    await ctx.drain();
    expect((await catchApiError(() => ctx.privacy.prepareDelete(MEMBER))).body).toMatchObject({ error: { code: 'open_online_orders' } });
    const exp = await ctx.privacy.exportOrders(MEMBER);
    expect(exp.items).toHaveLength(2);
    expect(JSON.stringify(exp)).not.toMatch(/pickup|qr_string|payment_intent|quote_hash/);
  });

  it('retensi: kode ambil dihapus 7 hari setelah ready; tautan member dilepas setelah 730 hari tutup', async () => {
    const ctx = setup();
    const { order } = await ctx.checkout();
    await ctx.payments.pay(ctx.orderRow(order.order_id).payment_intent_id!);
    await ctx.drain();
    const part = ctx.partsOf(order.order_id)[0];
    await ctx.events.handle(ctx.event(part.id, 'ready', 5));
    ctx.clock.advanceSeconds(8 * 86_400);
    await ctx.jobs.retentionOnce();
    expect(ctx.partsOf(order.order_id)[0].pickup_code_enc).toBeNull();
    await ctx.events.handle(ctx.event(part.id, 'picked_up', 6));
    ctx.clock.advanceSeconds(731 * 86_400);
    await ctx.jobs.retentionOnce();
    expect(ctx.orderRow(order.order_id).member_id).toBeNull();
  });

  it('log saga tidak memuat kode ambil, QR, atau member', async () => {
    const ctx = setup();
    const spies = (['log', 'warn', 'error'] as const).map((m) => jest.spyOn(Logger.prototype, m).mockImplementation(() => undefined));
    try {
      const { order } = await ctx.checkout();
      await ctx.payments.pay(ctx.orderRow(order.order_id).payment_intent_id!);
      await ctx.drain();
      const code = (await ctx.orders.pickupCodes(MEMBER, order.order_id)).items[0].pickup_code;
      const logged = spies.flatMap((s) => s.mock.calls.map((c) => c.map(String).join(' '))).join('\n');
      expect(logged).not.toContain(code);
      expect(logged).not.toContain(code.replace('-', ''));
      expect(logged).not.toContain('EFN-MOCK-QRIS');
      expect(logged).not.toContain(MEMBER);
      expect(logged).not.toMatch(/bearer /i);
    } finally {
      spies.forEach((s) => s.mockRestore());
    }
  });
});
