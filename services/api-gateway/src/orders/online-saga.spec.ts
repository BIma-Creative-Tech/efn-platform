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
import { OnlineAdminService, estimateLinesAmount, sameActor } from './online-admin.service';
import { OnlineJobsService } from './online-jobs.service';
import { OnlinePrivacyService } from './online-privacy.service';
import { OnlineSagaService } from './online-saga.service';
import { OrderEventsService, eventFingerprint, parseOdooEvent, stableEventKey } from './order-events.service';
import { OrdersService } from './orders.service';
import { PICKUP_CODE_AAD_LEGACY, PICKUP_CODE_RE, decryptPickupCode, pickupCodeAad } from './pickup-code';
import { FakeOnlinePayments, MemoryOnlineRepo } from './testing/online-fakes';

const MEMBER = '8d7b5a1e-2c3f-4a5b-9c8d-7e6f5a4b3c2d';
const OTHER = '00000000-0000-4000-8000-000000000001';
const HASH = 'a'.repeat(64);
const KEY = 'chk1_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA';

const CHANNEL: ChannelInfo = {
  channel_code: 'RTL',
  company_code: 'RTL',
  brand_code: null,
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
    const code = decryptPickupCode(ctx.cipher, part.pickup_code_enc as Buffer, part.id).code;
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
  it('timeout default 60 menit (G19 §11.1) → sweeper expired + intent dibatalkan → SO dibatalkan; bayar terlambat → refund penuh porsi tanpa mark_paid', async () => {
    const ctx = setup();
    expect(ctx.config.onlinePaymentTimeoutSeconds).toBe(3600);
    const { order } = await ctx.checkout();
    const intentId = ctx.orderRow(order.order_id).payment_intent_id!;
    ctx.clock.advanceSeconds(59 * 60);
    expect((await ctx.jobs.expireOnce()).expired).toBe(0);
    ctx.clock.advanceSeconds(80); // 60:20 < expires_at + 30 s
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
    ctx.clock.advanceSeconds(61 * 60); // lewat batas bayar 60 menit (default G19)
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
      // SEC-112: peran cs ditolak (teraudit), approver boleh
      await expect(ctx.admin.settleManual(rf.id, 'TRF-2026-001', 'finance.ani', 'transfer manual')).rejects.toThrow('peran approver');
      expect(ctx.repo.state.audits.at(-1)).toMatchObject({ action: 'refund_settle', outcome: 'rejected_role' });
      expect(ctx.repo.state.refunds.get(rf.id)!.status).toBe('manual_required');
      ctx.repo.state.audits.length = 0;
      expect(await ctx.admin.settleManual(rf.id, 'TRF-2026-001', { actor: 'finance.ani', role: 'approver' }, 'transfer manual ke rekening pelanggan')).toEqual({ status: 'settled' });
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
    // L-19c: SEMUA refund CS menunggu approver (ambang default 0); tanpa persetujuan tidak ada gateway
    expect(r1).toMatchObject({ amount: 79_900, status: 'approval_required' });
    await ctx.drain();
    expect(ctx.payments.refunds).toHaveLength(0);
    expect(await ctx.admin.approveRefund(r1.refund_id, { actor: 'spv.sari/h1', role: 'approver' }, 'disetujui supervisor')).toEqual({ status: 'odoo_done' });
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
    await expect(ctx.admin.resolve(part.id, 'cancel', 'ops.sari', 'nominal tidak cocok')).rejects.toThrow('approver'); // L-19c
    expect(await ctx.admin.resolve(part.id, 'cancel', { actor: 'ops.sari', role: 'approver' }, 'nominal tidak cocok')).toEqual({ status: 'cancelled' });
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
    // L-14: seq baru tetapi transisi mundur tidak diterapkan → dicatat ignored_stale (seq tetap maju)
    expect((await ctx.events.handle(ctx.event(part.id, 'confirmed', 7))).status).toBe('ignored_stale');
    expect(ctx.partsOf(part.order_id)[0]).toMatchObject({ status: 'ready', last_seq: 7 });
    expect((await ctx.metrics.backwardTransitions.get()).values[0].value).toBe(1);
    expect([...ctx.repo.state.events.values()].map((e) => e.outcome)).toEqual(['applied', 'ignored_stale', 'ignored_unknown_event', 'ignored_stale']);
  });

  it('L-14: confirmed|ready → review dari Odoo SELALU diterapkan; selama review kode ambil → 409 order_in_review; selesai → kode kembali', async () => {
    const { ctx, part } = await confirmed();
    expect((await ctx.orders.pickupCodes(MEMBER, part.order_id)).items).toHaveLength(1);
    expect((await ctx.events.handle(ctx.event(part.id, 'ready', 5))).status).toBe('applied');
    expect((await ctx.events.handle(ctx.event(part.id, 'review', 6))).status).toBe('applied');
    expect(ctx.partsOf(part.order_id)[0]).toMatchObject({ status: 'review', last_seq: 6 });
    const view = await ctx.orders.get(MEMBER, part.order_id);
    expect(view.parts[0].status).toBe('review'); // frontend: "Sedang diperiksa tim kami"
    expect(view.status).toBe('review'); // status pelanggan (tampilan); DB tetap fulfilling
    expect(ctx.orderRow(part.order_id).status).toBe('fulfilling');
    expect((await ctx.orders.list(MEMBER, null, 20)).items[0]).toMatchObject({ status: 'review' });
    const err = await catchApiError(() => ctx.orders.pickupCodes(MEMBER, part.order_id));
    expect(err.getStatus()).toBe(409);
    expect(err.body).toEqual({ error: { code: 'order_in_review', message: expect.any(String) } });
    // ops menyelesaikan → confirmed; kode ambil tersedia lagi (tidak dihapus saat review)
    expect((await ctx.events.handle(ctx.event(part.id, 'confirmed', 7))).status).toBe('applied');
    expect((await ctx.orders.pickupCodes(MEMBER, part.order_id)).items).toHaveLength(1);
    expect((await ctx.orders.get(MEMBER, part.order_id)).status).toBe('fulfilling');
    // dari confirmed langsung ke review juga diterapkan
    expect((await ctx.events.handle(ctx.event(part.id, 'review', 8))).status).toBe('applied');
    expect(ctx.partsOf(part.order_id)[0].status).toBe('review');
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

  it('SEC-95: cancelled inisiatif Odoo atas bagian berbayar → refund approval_required (TANPA gateway); CS menyetujui lewat CLI → settled', async () => {
    const { ctx, part } = await confirmed();
    const warn = jest.spyOn(Logger.prototype, 'warn').mockImplementation(() => undefined);
    try {
      const ev = ctx.event(part.id, 'cancelled', 10, { refund: { refund_ref: 'RINV/0001', amount: 159_800, kind: 'full', initiator: 'odoo' } });
      expect((await ctx.events.handle(ev)).status).toBe('applied');
      await ctx.drain();
      const [rf] = [...ctx.repo.state.refunds.values()];
      expect(rf).toMatchObject({ odoo_refund_ref: 'RINV/0001', reason: 'ops_cancelled', status: 'approval_required' });
      expect(ctx.payments.refunds).toHaveLength(0);
      expect(ctx.repo.state.outbox.filter((m) => m.queue === 'order.refund.requested')).toHaveLength(0);
      expect(warn.mock.calls.some((c) => String(c[0]).includes('odoo_refund_needs_approval'))).toBe(true);
      // rekonsiliasi TIDAK menerbitkan pesan refund untuk approval_required
      ctx.clock.advanceSeconds(20 * 60);
      await ctx.jobs.reconOnce();
      expect(ctx.repo.state.outbox.filter((m) => m.queue === 'order.refund.requested')).toHaveLength(0);
      expect((await ctx.metrics.stuck.get()).values.find((v) => v.labels.state === 'refund_attention')?.value).toBe(1);
      // persetujuan CS (aktor = identitas OS/host)
      // L-19c: peran cs ditolak (teraudit); refund dari Odoo (odoo-uid) cukup syarat peran approver
      await expect(ctx.admin.approveRefund(rf.id, 'cs_budi/efn-prod-01', 'tiket CS-77')).rejects.toThrow('peran approver');
      expect(ctx.repo.state.audits.at(-1)).toMatchObject({ outcome: 'rejected_role' });
      expect(ctx.repo.state.refunds.get(rf.id)!.status).toBe('approval_required');
      expect(await ctx.admin.approveRefund(rf.id, { actor: 'cs_budi/efn-prod-01', role: 'approver' }, 'tiket CS-77 disetujui')).toEqual({ status: 'odoo_done' });
      await ctx.drain();
      expect(ctx.repo.state.refunds.get(rf.id)!.status).toBe('settled');
      expect(ctx.odoo.calls.filter((c) => c.method === 'refundSettled')).toHaveLength(1);
      expect(ctx.repo.state.audits.at(-1)).toMatchObject({ action: 'refund_approve', actor: 'cs_budi/efn-prod-01', refund_id: rf.id, outcome: 'odoo_done' });
      await expect(ctx.admin.approveRefund(rf.id, { actor: 'cs_budi/efn-prod-01', role: 'approver' }, 'ulang')).rejects.toThrow('tidak menunggu persetujuan');
      await expect(ctx.admin.approveRefund(rf.id, 'ani@efn.co.id', 'email ditolak')).rejects.toThrow('operator');
    } finally {
      warn.mockRestore();
    }
  });

  it('SEC-95 empat mata: penyetuju = pemicu (login Odoo / pembuat CS) ditolak + teraudit; uid/NULL tidak memblokir', async () => {
    const { ctx, part } = await confirmed();
    const ev = ctx.event(part.id, 'cancelled', 10, {
      refund: { refund_ref: 'RINV/0002', amount: 159_800, kind: 'full', initiator: 'odoo', initiator_uid: 7, initiator_login: 'Budi.Ops' },
    });
    await ctx.events.handle(ev);
    const [rf] = [...ctx.repo.state.refunds.values()];
    expect(rf.initiated_by).toBe('odoo-uid:7'); // uid diutamakan (bukan PII)
    // login (bila uid tidak ada) disimpan huruf kecil; email diabaikan, tidak menolak event
    expect(parseOdooEvent(Buffer.from(JSON.stringify({ ...okEvent(), refund: { refund_ref: 'R', amount: 1, kind: 'full', initiator: 'odoo', initiator_login: 'Budi.Ops' } }))).refund?.initiated_by).toBe('odoo-login:budi.ops');
    expect(parseOdooEvent(Buffer.from(JSON.stringify({ ...okEvent(), refund: { refund_ref: 'R', amount: 1, kind: 'full', initiator: 'odoo', initiator_login: 'budi@efn.co.id' } }))).refund?.initiated_by).toBeNull();
    // uid tidak bisa dipetakan ke user OS → tidak memblokir
    expect(await ctx.admin.approveRefund(rf.id, { actor: 'budi.ops/h1', role: 'approver' }, 'tiket ok')).toEqual({ status: 'odoo_done' });
    // pemicu login sama dengan user OS penyetuju → ditolak, status tetap, audit tercatat
    ctx.repo.state.refunds.get(rf.id)!.status = 'approval_required';
    ctx.repo.state.refunds.get(rf.id)!.initiated_by = 'odoo-login:budi.ops';
    await expect(ctx.admin.approveRefund(rf.id, { actor: 'Budi.Ops/efn-prod-01', role: 'approver' }, 'coba setujui sendiri')).rejects.toThrow('empat mata');
    expect(ctx.repo.state.refunds.get(rf.id)!.status).toBe('approval_required');
    expect(ctx.repo.state.audits.at(-1)).toMatchObject({ action: 'refund_approve', actor: 'Budi.Ops/efn-prod-01', outcome: 'rejected_same_actor' });
    // pembuat CS yang sama juga ditolak; data lama tanpa pembuat tidak pernah ditolak
    ctx.repo.state.refunds.get(rf.id)!.initiated_by = 'cs_ani/host-a';
    await expect(ctx.admin.approveRefund(rf.id, { actor: 'cs_ani/host-b', role: 'approver' }, 'x y z')).rejects.toThrow('empat mata');
    ctx.repo.state.refunds.get(rf.id)!.initiated_by = null;
    expect(await ctx.admin.approveRefund(rf.id, { actor: 'cs_ani/host-b', role: 'approver' }, 'data lama')).toEqual({ status: 'odoo_done' });
    expect(sameActor(null, 'a/b')).toBe(false);
    expect(sameActor('odoo-uid:7', 'odoo-uid:7')).toBe(false);
    expect(sameActor('odoo-login:sari', 'SARI/h')).toBe(true);
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

describe('Review r10 (L-16)', () => {
  async function paidConfirmed(lines = [{ product_id: 11, qty: 2 }]) {
    const ctx = setup();
    const { order } = await ctx.checkout(lines);
    const intentId = ctx.orderRow(order.order_id).payment_intent_id!;
    await ctx.payments.pay(intentId);
    await ctx.drain();
    return { ctx, order, intentId, part: ctx.partsOf(order.order_id)[0] };
  }
  function newRefund(ctx: ReturnType<typeof setup>, partId: string, intentId: string, id: string, amount: number, status: 'requested' | 'odoo_done' = 'requested') {
    return ctx.repo.insertRefund({ id, part_id: partId, payment_intent_id: intentId, kind: 'lines', reason: 'customer_cs', lines: null, amount, odoo_refund_ref: `R/${id.slice(0, 4)}`, status, requested_by: 'cs' });
  }

  it('SEC-97: klaim eksklusif — pesan refund ganda/paralel memanggil gateway SEKALI', async () => {
    const { ctx, part, intentId } = await paidConfirmed();
    const rf = await newRefund(ctx, part.id, intentId, '21111111-1111-4111-8111-111111111111', 10_000);
    const msg = { schema_version: 1 as const, idempotency_key: 'k1', refund_id: rf.id };
    const [a, b] = await Promise.all([ctx.saga.handleRefundRequested(msg), ctx.saga.handleRefundRequested({ ...msg, idempotency_key: 'k2' })]);
    expect([a, b].sort()).toEqual(['done', 'pending']);
    expect(ctx.payments.refunds).toHaveLength(1);
    expect(ctx.repo.state.refunds.get(rf.id)!.status).toBe('settled');
    // klaim in-flight lain (belum basi) tidak dapat diambil
    const rf2 = await newRefund(ctx, part.id, intentId, '31111111-1111-4111-8111-111111111111', 10_000);
    ctx.repo.state.refunds.get(rf2.id)!.status = 'gateway_in_flight';
    expect(await ctx.saga.handleRefundRequested({ ...msg, idempotency_key: 'k3', refund_id: rf2.id })).toBe('pending');
    expect(ctx.payments.refunds).toHaveLength(1);
    // basi > 15 menit (proses mati) → boleh diklaim ulang
    ctx.clock.advanceSeconds(16 * 60);
    expect(await ctx.saga.handleRefundRequested({ ...msg, idempotency_key: 'k4', refund_id: rf2.id })).toBe('done');
    expect(ctx.payments.refunds).toHaveLength(2);
  });

  it('SEC-97: refund in-flight ikut dihitung dalam batas porsi → yang melebihi ditolak (failed, refund_exceeds_portion), tanpa gateway', async () => {
    const { ctx, part, intentId } = await paidConfirmed();
    const r1 = await newRefund(ctx, part.id, intentId, '41111111-1111-4111-8111-111111111111', 100_000);
    ctx.repo.state.refunds.get(r1.id)!.status = 'gateway_pending'; // sedang di gateway
    const r2 = await newRefund(ctx, part.id, intentId, '51111111-1111-4111-8111-111111111111', 60_000);
    expect(await ctx.saga.handleRefundRequested({ schema_version: 1, idempotency_key: 'x', refund_id: r2.id })).toBe('done');
    expect(ctx.repo.state.refunds.get(r2.id)).toMatchObject({ status: 'failed', failure_reason: 'refund_exceeds_portion' });
    expect(ctx.payments.refunds).toHaveLength(0);
    // transien di gateway → klaim dilepas (status semula), bisa dicoba ulang
    const r3 = await newRefund(ctx, part.id, intentId, '61111111-1111-4111-8111-111111111111', 10_000, 'odoo_done');
    jest.spyOn(ctx.payments, 'refund').mockRejectedValueOnce(new Error('ETIMEDOUT'));
    await expect(ctx.saga.handleRefundRequested({ schema_version: 1, idempotency_key: 'y', refund_id: r3.id })).rejects.toThrow('ETIMEDOUT');
    expect(ctx.repo.state.refunds.get(r3.id)!.status).toBe('odoo_done');
  });

  it('SEC-98: CLI refund — cek porsi SEBELUM nota kredit Odoo; settle manual menolak refund_exceeds_portion', async () => {
    const { ctx, part, intentId } = await paidConfirmed();
    await newRefund(ctx, part.id, intentId, '71111111-1111-4111-8111-111111111111', 150_000, 'odoo_done');
    await expect(
      ctx.admin.refund({ partId: part.id, lines: [{ product_id: 11, qty: 1 }], full: false, restock: false, reason: 'customer_cs', actor: 'cs_budi/h1', note: 'cek porsi' }),
    ).rejects.toThrow('melebihi sisa porsi');
    expect(ctx.odoo.calls.filter((c) => c.method === 'refund')).toHaveLength(0);
    const bad = await newRefund(ctx, part.id, intentId, '81111111-1111-4111-8111-111111111111', 5_000);
    ctx.repo.state.refunds.get(bad.id)!.status = 'failed';
    ctx.repo.state.refunds.get(bad.id)!.failure_reason = 'refund_exceeds_portion';
    await expect(ctx.admin.settleManual(bad.id, 'TRF-1', { actor: 'finance_ani/h1', role: 'approver' }, 'manual')).rejects.toThrow('melebihi porsi');
    await expect(ctx.admin.refund({ partId: part.id, lines: null, full: true, restock: false, reason: 'customer_cs', actor: 'free text actor!', note: 'abc' })).rejects.toThrow('operator');
  });

  it('SEC-99: kode ambil dihapus saat stock_short/cancelled; confirmed/review tanpa ready dihapus retensi 7 hari (COALESCE(ready_at, confirmed_at))', async () => {
    const { ctx, part } = await paidConfirmed();
    expect(part.pickup_code_enc).not.toBeNull();
    await ctx.events.handle(ctx.event(part.id, 'review', 9));
    ctx.clock.advanceSeconds(6 * 86_400);
    await ctx.jobs.retentionOnce();
    expect(ctx.partsOf(part.order_id)[0].pickup_code_enc).not.toBeNull();
    ctx.clock.advanceSeconds(2 * 86_400);
    await ctx.jobs.retentionOnce();
    expect(ctx.partsOf(part.order_id)[0].pickup_code_enc).toBeNull();
    const s = setup();
    const o = (await s.checkout([{ product_id: 13, qty: 1 }])).order;
    await s.payments.pay(s.orderRow(o.order_id).payment_intent_id!);
    const msg = s.repo.state.outbox.shift()!;
    await s.saga.handleOrderPaid(parseOrderPaid(msg.payload));
    expect(s.partsOf(o.order_id)[0]).toMatchObject({ status: 'stock_short', pickup_code_enc: null });
  });

  it('SEC-100: erasure/DSAR mengunci member & cek ulang order terbuka (lockAndAssertNoOpenOrders); checkout ditolak setelah hapus terjadwal', async () => {
    const ctx = setup();
    const { order } = await ctx.checkout();
    const q = { query: jest.fn().mockResolvedValue({ rows: [{ n: '0' }], rowCount: 1 }) };
    // jalur SQL: kunci advisory member yang sama dengan checkout dipanggil lebih dulu
    await ctx.privacy.lockAndAssertNoOpenOrders(q, MEMBER).catch(() => undefined);
    expect(String(q.query.mock.calls[0][0])).toContain("pg_advisory_xact_lock(hashtext('efn:online:member:'");
    expect(q.query.mock.calls[0][1]).toEqual([MEMBER]);
    // order belum dibayar masih ada → 409 (dibatalkan dulu oleh prepareDelete)
    const q2 = { query: jest.fn().mockImplementation(async (t: string) => (t.includes("status IN ('created', 'awaiting_payment')") ? { rows: [{ id: order.order_id }], rowCount: 1 } : { rows: [{ n: '0' }], rowCount: 1 })) };
    await expect(ctx.privacy.lockAndAssertNoOpenOrders(q2, MEMBER)).rejects.toMatchObject({ body: { error: { code: 'open_online_orders' } } });
    ctx.repo.state.activeDeletes.add(MEMBER);
    expect((await catchApiError(() => ctx.checkout([{ product_id: 12, qty: 1 }], 'chk1_DDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDD'))).body).toMatchObject({ error: { code: 'account_deleting' } });
  });

  it('SEC-102: AAD kode ambil terikat part_id (salin antar-bagian gagal); baris lama AAD v1 terbaca lalu ditulis ulang v2', async () => {
    const ctx = setup();
    const { order } = await ctx.checkout();
    await ctx.payments.pay(ctx.orderRow(order.order_id).payment_intent_id!);
    await ctx.drain();
    const part = ctx.partsOf(order.order_id)[0];
    const other = '91111111-1111-4111-8111-111111111111';
    expect(() => decryptPickupCode(ctx.cipher, part.pickup_code_enc as Buffer, other)).toThrow();
    const legacy = ctx.cipher.encrypt('ABCDEFGH', PICKUP_CODE_AAD_LEGACY);
    ctx.repo.state.parts.get(part.id)!.pickup_code_enc = legacy;
    expect((await ctx.orders.pickupCodes(MEMBER, order.order_id)).items[0].pickup_code).toBe('ABCD-EFGH');
    const rewritten = ctx.repo.state.parts.get(part.id)!.pickup_code_enc as Buffer;
    expect(Buffer.compare(Buffer.from(rewritten), legacy)).not.toBe(0);
    expect(decryptPickupCode(ctx.cipher, rewritten, part.id)).toEqual({ code: 'ABCDEFGH', legacy: false });
    expect(pickupCodeAad(part.id)).toBe(`efn:online_order_part.pickup_code_enc:v2:${part.id}`);
  });
});

function okEvent(): Record<string, unknown> {
  return { schema_version: 1, idempotency_key: 'online:1:cancelled:2', seq: 2, platform_order_ref: MEMBER, checkout_ref: OTHER, channel_code: 'RTL', company_code: 'RTL', event: 'cancelled', state: 'cancelled', occurred_at: null };
}

describe('Re-review G17 (L-19)', () => {
  async function paid() {
    const ctx = setup();
    const { order } = await ctx.checkout([{ product_id: 11, qty: 2 }]);
    const intentId = ctx.orderRow(order.order_id).payment_intent_id!;
    await ctx.payments.pay(intentId);
    await ctx.drain();
    return { ctx, intentId, part: ctx.partsOf(order.order_id)[0] };
  }

  it('SEC-107: refund buatan CS — pembuat yang sama (meski berperan approver) tidak boleh menyetujui; approver lain boleh; alasan disamarkan', async () => {
    const { ctx, part } = await paid();
    const r = await ctx.admin.refund({ partId: part.id, lines: [{ product_id: 11, qty: 1 }], full: false, restock: false, reason: 'customer_cs', actor: 'sari/h1', role: 'approver', note: 'pelanggan 081234567890 budi@mail.com' });
    expect(r.status).toBe('approval_required');
    expect(ctx.repo.state.refunds.get(r.refund_id)!.initiated_by).toBe('sari/h1');
    expect(ctx.repo.state.audits.at(-1)!.reason).toBe('pelanggan [disamarkan] [disamarkan]');
    await expect(ctx.admin.approveRefund(r.refund_id, { actor: 'sari/h2', role: 'approver' }, 'setujui sendiri')).rejects.toThrow('empat mata');
    expect(await ctx.admin.approveRefund(r.refund_id, { actor: 'dodi/h2', role: 'approver' }, 'ok supervisor')).toEqual({ status: 'odoo_done' });
  });

  it('SEC-107: ambang ONLINE_CS_REFUND_APPROVAL_MIN_IDR — di bawah ambang langsung ke gateway', async () => {
    const { ctx, part } = await paid();
    const admin = new OnlineAdminService(ctx.repo, ctx.odoo, ctx.saga, ctx.metrics, testConfig({ ONLINE_CS_REFUND_APPROVAL_MIN_IDR: '100000' }));
    const r = await admin.refund({ partId: part.id, lines: [{ product_id: 11, qty: 1 }], full: false, restock: false, reason: 'customer_cs', actor: 'cs/h', note: 'kecil' });
    expect(r.status).toBe('odoo_done');
    await ctx.drain();
    expect(ctx.repo.state.refunds.get(r.refund_id)!.status).toBe('settled');
    expect(testConfig().onlineCsRefundApprovalMinIdr).toBe(0);
  });

  it('SEC-106: klaim direbut setelah 15 menit — hasil gateway konsumen lama TIDAK menimpa status (claim_token)', async () => {
    const { ctx, part, intentId } = await paid();
    const rf = await ctx.repo.insertRefund({ id: 'a1111111-1111-4111-8111-111111111111', part_id: part.id, payment_intent_id: intentId, kind: 'lines', reason: 'customer_cs', lines: null, amount: 10_000, odoo_refund_ref: 'R/1', status: 'odoo_done', requested_by: 'cs' });
    let finishA: ((v: { status: 'failed'; providerRefundRef: null }) => void) | null = null;
    const spy = jest.spyOn(ctx.payments, 'refund').mockImplementationOnce(() => new Promise((res) => (finishA = res)));
    const a = ctx.saga.handleRefundRequested({ schema_version: 1, idempotency_key: 'kA', refund_id: rf.id });
    for (let k = 0; k < 5; k += 1) await new Promise((res) => setImmediate(res));
    expect(ctx.repo.state.refunds.get(rf.id)!.status).toBe('gateway_in_flight');
    const tokenA = ctx.repo.state.refunds.get(rf.id)!.claim_token;
    expect(tokenA).toMatch(/^[0-9a-f-]{36}$/);
    ctx.clock.advanceSeconds(16 * 60);
    expect(await ctx.saga.handleRefundRequested({ schema_version: 1, idempotency_key: 'kB', refund_id: rf.id })).toBe('done');
    expect(ctx.repo.state.refunds.get(rf.id)).toMatchObject({ status: 'settled', claim_token: null });
    (finishA as unknown as (v: { status: 'failed'; providerRefundRef: null }) => void)({ status: 'failed', providerRefundRef: null });
    expect(await a).toBe('pending');
    expect(ctx.repo.state.refunds.get(rf.id)!.status).toBe('settled'); // tidak ditimpa 'failed'
    spy.mockRestore();
  });

  it('SEC-112: settle manual oleh pembuat refund yang sama ditolak (rejected_same_actor, teraudit); approver lain boleh', async () => {
    const { ctx, part } = await paid();
    ctx.payments.refundMode = 'unsupported';
    const r = await ctx.admin.refund({ partId: part.id, lines: [{ product_id: 11, qty: 1 }], full: false, restock: false, reason: 'customer_cs', actor: 'sari/h1', role: 'approver', note: 'barang cacat' });
    await ctx.admin.approveRefund(r.refund_id, { actor: 'dodi/h2', role: 'approver' }, 'ok supervisor');
    await ctx.drain();
    expect(ctx.repo.state.refunds.get(r.refund_id)!.status).toBe('manual_required');
    await expect(ctx.admin.settleManual(r.refund_id, 'TRF-9', { actor: 'sari/h3', role: 'approver' }, 'saya transfer')).rejects.toThrow('empat mata');
    expect(ctx.repo.state.audits.at(-1)).toMatchObject({ action: 'refund_settle', actor: 'sari/h3', outcome: 'rejected_same_actor' });
    expect(ctx.repo.state.refunds.get(r.refund_id)!.status).toBe('manual_required');
    expect(await ctx.admin.settleManual(r.refund_id, 'TRF-9', { actor: 'dodi/h2', role: 'approver' }, 'transfer finance')).toEqual({ status: 'settled' });
  });

  it('SEC-106: pelepasan klaim pada error transien hanya dengan token; status asal dipertahankan', async () => {
    const { ctx, part, intentId } = await paid();
    const rf = await ctx.repo.insertRefund({ id: 'b1111111-1111-4111-8111-111111111111', part_id: part.id, payment_intent_id: intentId, kind: 'lines', reason: 'customer_cs', lines: null, amount: 10_000, odoo_refund_ref: null, status: 'requested', requested_by: 'cs' });
    jest.spyOn(ctx.payments, 'refund').mockRejectedValueOnce(new Error('ETIMEDOUT'));
    await expect(ctx.saga.handleRefundRequested({ schema_version: 1, idempotency_key: 'kC', refund_id: rf.id })).rejects.toThrow('ETIMEDOUT');
    expect(ctx.repo.state.refunds.get(rf.id)).toMatchObject({ status: 'requested', claim_token: null });
  });
});

describe('Retensi audit CLI CS (ONLINE_ADMIN_AUDIT_RETENTION_DAYS)', () => {
  it('default 3650 hari (W-19); baris lebih tua dihapus, yang baru tetap; metrik per jenis tanpa PII; idempoten', async () => {
    expect(testConfig().onlineAdminAuditRetentionDays).toBe(3650); // W-19 (Q-P19)
    expect(testConfig({ ONLINE_ADMIN_AUDIT_RETENTION_DAYS: '730' }).onlineAdminAuditRetentionDays).toBe(730);
    expect(() => testConfig({ ONLINE_ADMIN_AUDIT_RETENTION_DAYS: '5' }).onlineAdminAuditRetentionDays).toThrow();
    const ctx = setup();
    await ctx.repo.audit({ action: 'refund', actor: 'cs/h', reason: 'lama', order_id: null, part_id: null, refund_id: null, amount: 1, outcome: 'ok' });
    ctx.clock.advanceSeconds(3651 * 86_400); // W-19: default 3650 hari
    await ctx.repo.audit({ action: 'refund', actor: 'cs/h', reason: 'baru', order_id: null, part_id: null, refund_id: null, amount: 1, outcome: 'ok' });
    const res = (await ctx.jobs.retentionOnce()) as { adminAuditPurged: number };
    expect(res.adminAuditPurged).toBe(1);
    expect(ctx.repo.state.audits.map((a) => a.reason)).toEqual(['baru']);
    expect(((await ctx.jobs.retentionOnce()) as { adminAuditPurged: number }).adminAuditPurged).toBe(0);
    const m = await ctx.metrics.retentionRows.get();
    expect(m.values).toEqual([expect.objectContaining({ labels: { kind: 'admin_audit_deleted' }, value: 1 })]);
  });
});

describe('G18-QA-01: kunci idempotensi event Odoo stabil lintas restore DB Odoo', () => {
  async function confirmedPart() {
    const ctx = setup();
    const { order } = await ctx.checkout();
    await ctx.payments.pay(ctx.orderRow(order.order_id).payment_intent_id!);
    await ctx.drain();
    return { ctx, part: ctx.partsOf(order.order_id)[0] };
  }
  const spyWarn = () => jest.spyOn(Logger.prototype, 'warn').mockImplementation(() => undefined);

  it('kunci baris = odoo-ev:<platform_order_ref>:<event>:<seq> (+ event_id); duplikat sejati tetap duplicate', async () => {
    const { ctx, part } = await confirmedPart();
    const ready = ctx.event(part.id, 'ready', 5);
    expect((await ctx.events.handle(ready)).status).toBe('applied');
    const key = `odoo-ev:${part.id}:ready:5`;
    expect(stableEventKey(ready)).toBe(key);
    const row = ctx.repo.state.events.get(key)!;
    expect(row).toMatchObject({ part_id: part.id, source_key: 'online:42:ready:5', payload_sha256: eventFingerprint(ready) });
    expect(ctx.repo.state.events.has('online:42:ready:5')).toBe(false);
    // retry identik (occurred_at boleh beda) → duplicate
    ctx.clock.advanceSeconds(30);
    expect((await ctx.events.handle(ctx.event(part.id, 'ready', 5))).status).toBe('duplicate');
    // event_id (uuid) ikut kunci bila dikirim; bentuk salah → 400
    const withId = ctx.event(part.id, 'ready', 6, { event_id: '3F2B6C1E-8A4D-4C2E-9B1F-0A2B3C4D5E6F' });
    expect(stableEventKey(withId)).toBe(`odoo-ev:${part.id}:ready:6:3f2b6c1e-8a4d-4c2e-9b1f-0a2b3c4d5e6f`);
    expect((await catchApiError(() => ctx.event(part.id, 'ready', 6, { event_id: 'x' }))).getStatus()).toBe(400);
    // format kunci Odoo baru (uuid bagian) juga diterima
    expect(ctx.event(part.id, 'ready', 7, { idempotency_key: `online:${part.id}:ready:7` }).idempotency_key).toBe(`online:${part.id}:ready:7`);
  });

  it('DR: kunci Odoo lama (id lokal) yang dipakai ulang untuk bagian lain TIDAK diabaikan; kunci lama bagian sama tetap duplicate', async () => {
    const { ctx, part } = await confirmedPart();
    const other = '00000000-0000-4000-8000-0000000000aa';
    // baris lama (sebelum perbaikan) milik bagian LAIN dengan kunci Odoo yang sama (id lokal 42 terpakai ulang setelah restore)
    ctx.repo.state.events.set('online:42:ready:5', { part_id: other, source: 'odoo', event: 'ready', seq: 5, idempotency_key: 'online:42:ready:5', outcome: 'applied', occurred_at: null });
    const warn = spyWarn();
    try {
      expect((await ctx.events.handle(ctx.event(part.id, 'ready', 5))).status).toBe('applied');
      expect(ctx.partsOf(part.order_id)[0].status).toBe('ready');
      expect(warn.mock.calls.some((c) => String(c[0]).includes('"kind":"odoo_event_key_reused"'))).toBe(true);
    } finally {
      warn.mockRestore();
    }
    // baris lama milik bagian YANG SAMA (event diproses sebelum upgrade BFF, Odoo retry) → duplicate
    const { ctx: c2, part: p2 } = await confirmedPart();
    c2.repo.state.events.set('online:42:ready:5', { part_id: p2.id, source: 'odoo', event: 'ready', seq: 5, idempotency_key: 'online:42:ready:5', outcome: 'applied', occurred_at: null });
    expect((await c2.events.handle(c2.event(p2.id, 'ready', 5))).status).toBe('duplicate');
    expect(c2.partsOf(p2.order_id)[0].status).not.toBe('ready');
  });

  it('kunci sama, isi berbeda → 409 event_conflict + anomali critical (tidak di-skip diam-diam, tidak ada perubahan)', async () => {
    const { ctx, part } = await confirmedPart();
    expect((await ctx.events.handle(ctx.event(part.id, 'ready', 5))).status).toBe('applied');
    const before = ctx.repo.state.events.size;
    const warn = spyWarn();
    try {
      const err = await catchApiError(() => ctx.events.handle(ctx.event(part.id, 'ready', 5, { state: 'done' })));
      expect(err.getStatus()).toBe(409);
      expect(err.body).toMatchObject({ error: { code: 'event_conflict' } });
      expect(warn.mock.calls.some((c) => String(c[0]).includes('"kind":"odoo_event_conflict","severity":"critical"'))).toBe(true);
    } finally {
      warn.mockRestore();
    }
    expect(ctx.repo.state.events.size).toBe(before);
    // baris lama tanpa sidik (sebelum migrasi) → duplicate (tidak bisa dibandingkan)
    const k = `odoo-ev:${part.id}:picked_up:6`;
    ctx.repo.state.events.set(k, { part_id: part.id, source: 'odoo', event: 'picked_up', seq: 6, idempotency_key: k, outcome: 'applied', occurred_at: null });
    expect((await ctx.events.handle(ctx.event(part.id, 'picked_up', 6))).status).toBe('duplicate');
  });
});

describe('G19 §11.1–11.2: reservasi 1 jam, batas order terbuka, promo saat place', () => {
  async function quoteWith(ctx: ReturnType<typeof setup>, codes: string[], lines = [{ product_id: 11, qty: 2 }]) {
    const c = await ctx.cart.get(MEMBER);
    await ctx.cart.put(MEMBER, { version: c.version, groups: [{ channel_code: 'RTL', outlet_code: 'TOKO-01', fulfilment: 'pickup', lines }] });
    return ctx.quotes.quote(MEMBER, codes);
  }

  it('default ONLINE_PAYMENT_TIMEOUT_SECONDS 3600 & ONLINE_MAX_OPEN_ORDERS_PER_MEMBER 2; order ke-3 → 429 too_many_open_orders', async () => {
    const ctx = setup();
    expect(ctx.config.onlinePaymentTimeoutSeconds).toBe(3600);
    expect(ctx.config.onlineMaxOpenOrdersPerMember).toBe(2);
    const { order } = await ctx.checkout();
    expect(new Date(order.expires_at).getTime() - ctx.clock.ms).toBe(3600 * 1000);
    await ctx.checkout([{ product_id: 12, qty: 1 }], 'chk1_BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB');
    const err = await catchApiError(() => ctx.checkout([{ product_id: 12, qty: 2 }], 'chk1_CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC'));
    expect(err.getStatus()).toBe(429);
    expect(err.body).toMatchObject({ error: { code: 'too_many_open_orders' } });
  });

  it('§10.3.5 no. 5 tetap: Odoo expires_at ≥ order + 5 menit — 70 menit lolos, 65 batas lolos, 64 / 25 (Odoo belum dinaikkan) → 503 + anomali', async () => {
    for (const [minutes, ok] of [[70, true], [65, true], [64, false], [25, false]] as const) {
      const ctx = setup();
      ctx.odoo.paymentTimeoutMinutes = minutes;
      const warn = jest.spyOn(Logger.prototype, 'warn').mockImplementation(() => undefined);
      try {
        if (ok) {
          expect((await ctx.checkout()).order.status).toBe('awaiting_payment');
        } else {
          const err = await catchApiError(() => ctx.checkout());
          expect(err.body).toMatchObject({ error: { code: 'online_unavailable' } });
          expect(warn.mock.calls.some((c) => String(c[0]).includes('odoo_expiry_too_short'))).toBe(true);
          const o = [...ctx.repo.state.orders.values()][0];
          expect(o.status).toBe('failed');
          expect(ctx.odoo.calls.filter((c) => c.method === 'cancel')).toHaveLength(1); // reservasi Odoo dilepas
        }
      } finally {
        warn.mockRestore();
      }
    }
  });

  it('place mengirim promo_applied grup dari quote tersimpan (bukan dari klien), hanya baris produk; baris reward tersimpan & tampil di status order', async () => {
    const ctx = setup();
    const q = await quoteWith(ctx, ['DEMO5K', 'NOPE42']);
    expect(q.promo).toEqual({ applied: ['DEMO5K'], invalid: ['NOPE42'] });
    expect(q.amount_payable_total).toBe(159_800 - 5_000);
    const order = await ctx.orders.checkout(MEMBER, KEY, { quoteId: q.quote_id, acceptTotal: q.amount_payable_total });
    expect(order).toMatchObject({ status: 'awaiting_payment', amount_payable: 154_800 });
    const [part] = ctx.partsOf(order.order_id);
    const placed = ctx.odoo.orders.get(part.id)!;
    expect(placed.promoCodes).toEqual(['DEMO5K']); // kode invalid TIDAK dikirim saat place
    expect(placed.lines).toEqual([{ product_id: 11, qty: 2 }]); // tanpa baris reward
    expect(part.lines).toEqual([
      expect.not.objectContaining({ reward: expect.anything() }),
      expect.objectContaining({ product_id: 9001, subtotal_incl: -5_000, reward: true }),
    ]);
    expect(order.parts[0].lines.filter((l) => l.reward === true)).toHaveLength(1);
    expect(ctx.repo.state.portions).toEqual([expect.objectContaining({ part_id: part.id, amount: 154_800 })]);
  });

  it('promo berakhir/kuota habis di antara quote & place → 409 price_changed (klien quote ulang), order failed + kompensasi', async () => {
    const ctx = setup();
    const q = await quoteWith(ctx, ['DEMO5K']);
    ctx.odoo.expiredPromos.add('DEMO5K');
    const err = await catchApiError(() => ctx.orders.checkout(MEMBER, KEY, { quoteId: q.quote_id, acceptTotal: q.amount_payable_total }));
    expect(err.body).toMatchObject({ error: { code: 'price_changed' } });
    const o = [...ctx.repo.state.orders.values()][0];
    expect(o).toMatchObject({ status: 'failed', close_reason: 'place_failed' });
    // quote ulang: kode kini invalid, tidak memblokir checkout
    const q2 = await quoteWith(ctx, ['DEMO5K']);
    expect(q2.promo).toEqual({ applied: [], invalid: ['DEMO5K'] });
    const ok = await ctx.orders.checkout(MEMBER, 'chk1_DDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDD', { quoteId: q2.quote_id, acceptTotal: q2.amount_payable_total });
    expect(ok).toMatchObject({ status: 'awaiting_payment', amount_payable: 159_800 });
    expect(ctx.odoo.orders.get(ctx.partsOf(ok.order_id)[0].id)!.promoCodes).toEqual([]);
  });

  it('quote tersimpan pra-G19 (tanpa promo_applied) → place mengirim promo_codes []', async () => {
    const ctx = setup();
    const q = await quoteWith(ctx, []);
    const key = `oq:${q.quote_id}`;
    const raw = JSON.parse((await ctx.kv.get(key))!);
    delete raw.promo_codes;
    for (const g of raw.groups) delete g.promo_applied;
    await ctx.kv.setEx(key, JSON.stringify(raw), 300);
    const order = await ctx.orders.checkout(MEMBER, KEY, { quoteId: q.quote_id, acceptTotal: q.amount_payable_total });
    expect(ctx.odoo.orders.get(ctx.partsOf(order.order_id)[0].id)!.promoCodes).toEqual([]);
  });

  it('refund CS per baris mengabaikan baris reward saat memperkirakan nominal (pre-check porsi)', async () => {
    const ctx = setup();
    const q = await quoteWith(ctx, ['DEMO5K']);
    const order = await ctx.orders.checkout(MEMBER, KEY, { quoteId: q.quote_id, acceptTotal: q.amount_payable_total });
    const [part] = ctx.partsOf(order.order_id);
    expect(estimateLinesAmount(part, [{ product_id: 11, qty: 1 }])).toBe(79_900);
    expect(() => estimateLinesAmount(part, [{ product_id: 9001, qty: 1 }])).toThrow(/tidak ada di bagian/);
  });
});
