import sharp from 'sharp';
import { MetricsService } from '../common/metrics/metrics.service';
import { FakeClock, FakeKvStore, catchApiError, testCipher, testConfig } from '../common/testing/fakes';
import { MemoryCartStore } from '../cart/cart-store';
import { CartService } from '../cart/cart.service';
import { QuoteService } from '../cart/quote.service';
import { CatalogService, ChannelInfo, ProductSummary } from '../catalog/catalog.service';
import { ImageProcessor } from '../market-scout/image/image-processor';
import { MOCK_TRANSFER_ACCOUNT, MockOdooOnline } from '../odoo-rpc/odoo-online.mock';
import { OnlineMetrics } from '../odoo-rpc/online-metrics';
import { parseCompensate, parseOrderPaid, parseRefundRequested, parseUnpaidClosed } from './online-consumers';
import { OnlineAdminService } from './online-admin.service';
import { OnlineJobsService } from './online-jobs.service';
import { OnlineSagaService } from './online-saga.service';
import { TRANSFER_REFUND_REASON } from './online.model';
import { OrderEventsService, parseOdooEvent } from './order-events.service';
import { OrdersService, TransferPaymentView } from './orders.service';
import { readTransferProofForm } from './transfer-proof-form';
import { FakeOnlinePayments, MemoryOnlineRepo } from './testing/online-fakes';

/**
 * G26 docs/04 §17 (K25-07, L-95..L-100): pesanan online transfer bank + bukti, diverifikasi Verifikator Odoo. Odoo =
 * stub `MockOdooOnline` (`transferSubmit` + `verifyTransfer` meniru efn_online_transfer); payment = `FakeOnlinePayments`
 * dengan `failCreate=true` untuk membuktikan provider tidak pernah dipanggil (pilot tanpa vendor gateway).
 */

const MEMBER = '8d7b5a1e-2c3f-4a5b-9c8d-7e6f5a4b3c2d';
const OTHER = '00000000-0000-4000-8000-000000000009';
const HASH = 'b'.repeat(64);
const KEY = 'chk1_TTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTTT';
const KEY2 = 'chk1_UUUUUUUUUUUUUUUUUUUUUUUUUUUUUUUUUUUUUUUUUUU';
const NAMES: Record<number, string> = { 11: 'Beras Premium 5 kg', 12: 'Minyak Goreng 2 L', 13: 'Produk Uji Stok-Kurang (mock)' };

function ch(code: string, modes: ('qris' | 'counter' | 'bank_transfer')[]): ChannelInfo {
  return {
    channel_code: code,
    company_code: code,
    brand_code: code,
    name: code,
    fulfilment: { pickup: true, delivery: false },
    outlets: [{ outlet_code: 'TOKO-01', public_name: 'Toko Demo 01', pickup_enabled: true, prep_minutes: 30 }],
    payment_modes: modes,
  };
}
const CHANNELS = [ch('GL', ['qris', 'bank_transfer']), ch('PB', ['qris', 'bank_transfer']), ch('BCR', ['qris'])];

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

async function photo(seed = 0, width = 640): Promise<Buffer> {
  // gambar acak deterministik (pola garis) agar dHash berbeda per seed
  const raw = Buffer.alloc(width * 400 * 3);
  for (let i = 0; i < raw.length; i += 1) raw[i] = ((i * (seed + 3)) >> 4) & 0xff;
  return sharp(raw, { raw: { width, height: 400, channels: 3 } }).png().toBuffer();
}

function setup(over: Record<string, string> = {}) {
  const clock = new FakeClock();
  const config = testConfig({ ONLINE_ENABLED: 'true', ODOO_ONLINE_MOCK: 'true', ONLINE_CHANNELS: 'GL,PB,BCR', ...over });
  const kv = new FakeKvStore(clock.now);
  const repo = new MemoryOnlineRepo();
  repo.now = clock.now;
  repo.state.members.set(MEMBER, HASH);
  const odoo = new MockOdooOnline();
  odoo.now = clock.now;
  const metrics = new OnlineMetrics(new MetricsService());
  const payments = new FakeOnlinePayments(repo);
  payments.failCreate = true; // transfer bank tidak pernah memanggil provider
  const saga = new OnlineSagaService(repo, odoo, payments, metrics);
  saga.now = clock.now;
  payments.saga = saga;
  const catalog = stubCatalog();
  const cart = new CartService(config, new MemoryCartStore(), catalog);
  const quotes = new QuoteService(config, cart, catalog, odoo, kv, metrics);
  quotes.now = clock.now;
  const orders = new OrdersService(config, repo, odoo, payments, quotes, cart, catalog, testCipher(config), metrics, saga);
  orders.now = clock.now;
  const jobs = new OnlineJobsService(config, repo, odoo, payments, saga, metrics);
  jobs.now = clock.now;
  const events = new OrderEventsService(repo, saga, metrics);
  const admin = new OnlineAdminService(repo, odoo, saga, metrics);
  const images = new ImageProcessor(config);
  const normalize = (b: Buffer) => images.normalize(b, { maxDimension: 1600, quality: 80 });

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
  async function checkout(groups = [{ channel: 'GL', lines: [{ product_id: 11, qty: 2 }] }], method: 'qris' | 'bank_transfer' = 'bank_transfer', key = KEY) {
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
          idempotency_key: `online:88:${ev}:${seq}`,
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
  async function upload(orderId: string, seed = 0) {
    return orders.submitTransferProof(MEMBER, orderId, await photo(seed), normalize);
  }
  /** Verifikator menyetujui di Odoo (stub) lalu Odoo mengirim `paid` `{ payment: { method: "bank_transfer" } }`. */
  async function approve(orderId: string) {
    const [part] = partsOf(orderId);
    const o = odoo.verifyTransfer(part.id, 'approve');
    return events.handle(event(part.id, 'paid', o.seq, { state: o.state, payment: { method: 'bank_transfer', points_value: 0 } }));
  }
  async function reject(orderId: string, reason = 'unreadable') {
    const [part] = partsOf(orderId);
    const o = odoo.verifyTransfer(part.id, 'reject');
    return events.handle(event(part.id, 'transfer_rejected', o.seq, { state: 'placed', transfer: { reason } }));
  }
  return { clock, config, repo, odoo, metrics, payments, saga, orders, jobs, events, admin, drain, checkout, partsOf, orderRow, event, upload, approve, reject, normalize };
}

describe('G26 checkout transfer bank (§17.2–§17.3)', () => {
  it('sukses: awaiting_payment tanpa intent; payment = rekening PT + kode berita + awaiting_proof; Odoo menerima payment_mode', async () => {
    const ctx = setup();
    const order = await ctx.checkout();
    expect(order.status).toBe('awaiting_payment');
    const pay = order.payment as TransferPaymentView;
    expect(pay).toEqual({
      method: 'bank_transfer',
      status: 'awaiting_proof',
      account: { ...MOCK_TRANSFER_ACCOUNT },
      transfer_ref: expect.stringMatching(/^[ABCDEFGHJKMNPQRSTUVWXYZ23456789]{8}$/),
      uploads_left: 3,
      reject_reason: null,
      upload_deadline: new Date(Date.parse(order.expires_at) - 600_000).toISOString(),
      expires_at: order.expires_at,
    });
    expect(ctx.repo.state.intents.size).toBe(0);
    expect(ctx.orderRow(order.order_id)).toMatchObject({ payment_method: 'bank_transfer', payment_intent_id: null, transfer_status: 'awaiting_proof' });
    const [part] = ctx.partsOf(order.order_id);
    expect(ctx.odoo.orders.get(part.id)).toMatchObject({ paymentMode: 'bank_transfer' });
    // daftar: metode tampil, blok payment tidak
    const list = await ctx.orders.list(MEMBER, null, 20);
    expect(list.items).toEqual([expect.objectContaining({ order_id: order.order_id, payment_method: 'bank_transfer' })]);
    expect(JSON.stringify(list)).not.toContain(MOCK_TRANSFER_ACCOUNT.account_number);
    // milik member lain → 404
    expect((await catchApiError(() => ctx.orders.get(OTHER, order.order_id))).getStatus()).toBe(404);
  });

  it('>1 bagian → 422 transfer_single_part_only; kanal tanpa bank_transfer → 422; env tanpa bank_transfer → 422', async () => {
    const ctx = setup();
    const multi = await catchApiError(() =>
      ctx.checkout([
        { channel: 'GL', lines: [{ product_id: 11, qty: 1 }] },
        { channel: 'PB', lines: [{ product_id: 12, qty: 1 }] },
      ]),
    );
    expect(multi.body).toMatchObject({ error: { code: 'transfer_single_part_only' } });
    const bcr = await catchApiError(() => ctx.checkout([{ channel: 'BCR', lines: [{ product_id: 11, qty: 1 }] }]));
    expect(bcr.body).toMatchObject({ error: { code: 'payment_method_unavailable' } });
    const qrisOnly = setup({ ONLINE_PAYMENT_METHODS: 'qris' });
    expect((await catchApiError(() => qrisOnly.checkout())).body).toMatchObject({ error: { code: 'payment_method_unavailable' } });
    expect(ctx.repo.state.orders.size).toBe(0);
  });

  it('idempotensi: key sama + metode beda → 409; batas 1 order transfer terbuka → 429 (QRIS tetap boleh)', async () => {
    const ctx = setup();
    ctx.payments.failCreate = false;
    const order = await ctx.checkout();
    const conflict = await catchApiError(() =>
      ctx.orders.checkout(MEMBER, KEY, { quoteId: ctx.orderRow(order.order_id).quote_id, acceptTotal: order.amount_payable, paymentMethod: 'qris' }),
    );
    expect(conflict.body).toMatchObject({ error: { code: 'idempotency_conflict' } });
    const second = await catchApiError(() => ctx.checkout([{ channel: 'GL', lines: [{ product_id: 12, qty: 1 }] }], 'bank_transfer', KEY2));
    expect(second.getStatus()).toBe(429);
    const qris = await ctx.checkout([{ channel: 'GL', lines: [{ product_id: 12, qty: 1 }] }], 'qris', 'chk1_VVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVV');
    expect(qris.status).toBe('awaiting_payment');
  });

  it('L-95: counter dibekukan — ONLINE_PAYMENT_METHODS memuat counter tanpa ONLINE_COUNTER_LEGACY → ConfigError', () => {
    expect(() => testConfig({ ONLINE_PAYMENT_METHODS: 'qris,counter' }).onlinePaymentMethods).toThrow(/ONLINE_COUNTER_LEGACY/);
    expect(testConfig({ ONLINE_PAYMENT_METHODS: 'qris,counter', ONLINE_COUNTER_LEGACY: 'true' }).onlinePaymentMethods).toEqual(['qris', 'counter']);
    expect(() => testConfig({ APP_ENV: 'staging', ONLINE_COUNTER_LEGACY: 'true' }).onlineCounterLegacy).toThrow(/dev\|test/);
    expect(testConfig().onlinePaymentMethods).toEqual(['qris', 'bank_transfer']);
    expect(() => testConfig({ ONLINE_PAYMENT_METHODS: 'va' }).onlinePaymentMethods).toThrow(/ONLINE_PAYMENT_METHODS/);
  });
});

describe('G26 unggah bukti transfer (§17.4)', () => {
  it('sukses: re-encode JPEG ke Odoo (tanpa metadata), BFF hanya menyimpan hash; status awaiting_verification; uploads_left turun', async () => {
    const ctx = setup();
    const order = await ctx.checkout();
    const view = await ctx.upload(order.order_id);
    expect(view).toMatchObject({ status: 'awaiting_verification', uploads_left: 2, reject_reason: null });
    const [proof] = ctx.repo.state.transferProofs;
    expect(proof).toMatchObject({ order_id: order.order_id, status: 'submitted', duplicate_suspect: false });
    expect(proof.sha256).toMatch(/^[0-9a-f]{64}$/);
    expect(proof.dhash).toMatch(/^[0-9a-f]{16}$/);
    // tidak ada gambar di state BFF (DB/outbox)
    expect(JSON.stringify([...ctx.repo.state.orders.values(), ctx.repo.state.outbox, ctx.repo.state.transferProofs])).not.toMatch(/\/9j\//);
    expect(ctx.odoo.calls.filter((c) => c.method === 'transferSubmit')).toHaveLength(1);
    // bukti kedua saat masih diverifikasi → 409
    expect((await catchApiError(() => ctx.upload(order.order_id, 1))).body).toMatchObject({ error: { code: 'transfer_not_awaiting_proof' } });
  });

  it('foto rusak → 415 transfer_proof_invalid (tanpa memanggil Odoo, tanpa menghitung unggahan)', async () => {
    const ctx = setup();
    const order = await ctx.checkout();
    const png = Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), Buffer.alloc(64, 1)]);
    const err = await catchApiError(() => ctx.orders.submitTransferProof(MEMBER, order.order_id, png, ctx.normalize));
    expect(err.getStatus()).toBe(415);
    expect(ctx.odoo.calls.filter((c) => c.method === 'transferSubmit')).toHaveLength(0);
    expect(ctx.orderRow(order.order_id).transfer_uploads).toBe(0);
  });

  it('Odoo tidak terjangkau → 503 transfer_proof_unavailable; unggahan tidak terhitung, tanpa baris hash', async () => {
    const ctx = setup();
    const order = await ctx.checkout();
    ctx.odoo.failTransferSubmit = true;
    const err = await catchApiError(() => ctx.upload(order.order_id));
    expect(err.getStatus()).toBe(503);
    expect(err.body).toMatchObject({ error: { code: 'transfer_proof_unavailable' } });
    expect(ctx.orderRow(order.order_id)).toMatchObject({ transfer_uploads: 0, transfer_status: 'awaiting_proof' });
    expect(ctx.repo.state.transferProofs).toHaveLength(0);
  });

  it('batas waktu: setelah expires_at − 600 s → 409 transfer_window_closed', async () => {
    const ctx = setup();
    const order = await ctx.checkout();
    ctx.clock.ms = Date.parse(order.expires_at) - 599_000;
    expect((await catchApiError(() => ctx.upload(order.order_id))).body).toMatchObject({ error: { code: 'transfer_window_closed' } });
  });

  it('order QRIS / milik member lain → 409 / 404', async () => {
    const ctx = setup();
    ctx.payments.failCreate = false;
    const qris = await ctx.checkout(undefined, 'qris');
    expect((await catchApiError(() => ctx.upload(qris.order_id))).body).toMatchObject({ error: { code: 'transfer_not_awaiting_proof' } });
    const ctx2 = setup();
    const order = await ctx2.checkout();
    expect((await catchApiError(async () => ctx2.orders.submitTransferProof(OTHER, order.order_id, await photo(), ctx2.normalize))).getStatus()).toBe(404);
  });

  it('bukti sama dipakai di order lain → duplicate_suspect=true diteruskan ke Odoo (tidak menolak)', async () => {
    const ctx = setup();
    const first = await ctx.checkout();
    await ctx.upload(first.order_id, 7);
    // order pertama dibatalkan pelanggan (masih awaiting_payment → boleh); hash buktinya tetap dipakai deteksi
    expect(await ctx.orders.cancel(MEMBER, first.order_id)).toEqual({ status: 'cancelled' });
    const second = await ctx.checkout([{ channel: 'GL', lines: [{ product_id: 12, qty: 1 }] }], 'bank_transfer', KEY2);
    const view = await ctx.upload(second.order_id, 7);
    expect(view.status).toBe('awaiting_verification');
    const proofs = ctx.repo.state.transferProofs.filter((p) => p.order_id === second.order_id);
    expect(proofs[0].duplicate_suspect).toBe(true);
  });
});

describe('G26 keputusan Verifikator (event Odoo §17.5)', () => {
  it('disetujui → paid → bagian confirmed, kode ambil tersedia, tanpa intent/gateway; rekening tidak tampil lagi', async () => {
    const ctx = setup();
    const order = await ctx.checkout();
    await ctx.upload(order.order_id);
    expect(await ctx.approve(order.order_id)).toEqual({ ok: true, status: 'applied' });
    const fresh = await ctx.orders.get(MEMBER, order.order_id);
    expect(fresh.status).toBe('fulfilling');
    expect(fresh.parts[0].status).toBe('confirmed');
    expect(fresh.paid_at).not.toBeNull();
    expect(fresh.payment).toEqual(expect.objectContaining({ method: 'bank_transfer', status: 'none' }));
    expect(JSON.stringify(fresh.payment)).not.toContain(MOCK_TRANSFER_ACCOUNT.account_number);
    expect(ctx.repo.state.transferProofs[0].status).toBe('approved');
    expect((await ctx.orders.pickupCodes(MEMBER, order.order_id)).items).toHaveLength(1);
    expect(ctx.repo.state.intents.size).toBe(0);
  });

  it('ditolak → rejected + alasan + notifikasi tanpa parameter; unggah ulang diterima; maks 3 unggahan → 429', async () => {
    const ctx = setup();
    const order = await ctx.checkout();
    await ctx.upload(order.order_id, 1);
    expect(await ctx.reject(order.order_id, 'amount_mismatch')).toEqual({ ok: true, status: 'applied' });
    const view = (await ctx.orders.get(MEMBER, order.order_id)).payment as TransferPaymentView;
    expect(view).toMatchObject({ status: 'rejected', reject_reason: 'amount_mismatch', uploads_left: 2 });
    const notify = ctx.repo.state.outbox.filter((m) => m.queue === 'notify.push');
    expect(notify.map((m) => m.payload.template)).toEqual(['online_transfer_rejected']);
    expect(notify[0].payload.params).toEqual({});
    await ctx.upload(order.order_id, 2);
    await ctx.reject(order.order_id, 'not_received');
    await ctx.upload(order.order_id, 3);
    await ctx.reject(order.order_id, 'unreadable');
    expect((await catchApiError(() => ctx.upload(order.order_id, 4))).body).toMatchObject({ error: { code: 'transfer_proof_limit' } });
  });

  it('event transfer_rejected: alasan di luar allowlist → 400; untuk order QRIS → 409 payment_method_mismatch', async () => {
    const ctx = setup();
    const order = await ctx.checkout();
    const [part] = ctx.partsOf(order.order_id);
    expect(() => ctx.event(part.id, 'transfer_rejected', 5, { transfer: { reason: 'catatan bebas staf' } })).toThrow();
    const q = setup();
    q.payments.failCreate = false;
    const qris = await q.checkout(undefined, 'qris');
    const [qp] = q.partsOf(qris.order_id);
    const err = await catchApiError(() => q.events.handle(q.event(qp.id, 'transfer_rejected', 5, { transfer: { reason: 'unreadable' } })));
    expect(err.getStatus()).toBe(409);
    // event paid bank_transfer untuk order QRIS → 409
    const err2 = await catchApiError(() => q.events.handle(q.event(qp.id, 'paid', 6, { state: 'confirmed', payment: { method: 'bank_transfer' } })));
    expect(err2.body).toMatchObject({ error: { code: 'payment_method_mismatch' } });
  });

  it('kedaluwarsa: sweeper menanyakan Odoo dulu — sudah disetujui (event hilang) → paid, bukan expired', async () => {
    const ctx = setup();
    const order = await ctx.checkout();
    await ctx.upload(order.order_id);
    const [part] = ctx.partsOf(order.order_id);
    ctx.odoo.verifyTransfer(part.id, 'approve'); // event `paid` tidak pernah sampai
    ctx.clock.ms = Date.parse(order.expires_at) + 60_000;
    await ctx.jobs.expireOnce();
    expect(ctx.orderRow(order.order_id).status).toBe('fulfilling');
  });

  it('kedaluwarsa tanpa persetujuan → expired, bukti expired; persetujuan terlambat (review Odoo) → bagian review', async () => {
    const ctx = setup();
    const order = await ctx.checkout();
    await ctx.upload(order.order_id);
    ctx.clock.ms = Date.parse(order.expires_at) + 60_000;
    await ctx.jobs.expireOnce();
    expect(ctx.orderRow(order.order_id).status).toBe('expired');
    expect(ctx.repo.state.transferProofs[0].status).toBe('expired');
    await ctx.drain();
    const [part] = ctx.partsOf(order.order_id);
    const res = await ctx.events.handle(ctx.event(part.id, 'review', 50, { state: 'review', review_reason: 'transfer_paid_after_cancel' }));
    expect(res.status).toBe('applied');
    expect((await ctx.orders.get(MEMBER, order.order_id)).status).toBe('review');
  });
});

describe('G26 simulasi Verifikator (dev/mock, §10.7)', () => {
  it('failed + alasan → rejected; unggah ulang; paid → fulfilling; expired → 400; sebelum unggah → 409', async () => {
    const ctx = setup();
    const order = await ctx.checkout();
    expect((await catchApiError(() => ctx.orders.simulatePayment(MEMBER, order.order_id, 'paid'))).body).toMatchObject({ error: { code: 'invalid_state' } });
    await ctx.upload(order.order_id, 1);
    expect((await catchApiError(() => ctx.orders.simulatePayment(MEMBER, order.order_id, 'expired'))).getStatus()).toBe(400);
    const rej = await ctx.orders.simulatePayment(MEMBER, order.order_id, 'failed', 'wrong_account');
    expect(rej.payment).toMatchObject({ method: 'bank_transfer', status: 'rejected', reject_reason: 'wrong_account', uploads_left: 2 });
    expect(ctx.repo.state.outbox.filter((m) => m.queue === 'notify.push').map((m) => m.payload.template)).toEqual(['online_transfer_rejected']);
    await ctx.upload(order.order_id, 2);
    const paid = await ctx.orders.simulatePayment(MEMBER, order.order_id, 'paid');
    expect(paid.status).toBe('fulfilling');
    expect(paid.payment).toMatchObject({ method: 'bank_transfer', status: 'none' });
  });

  it('production → 404', async () => {
    const ctx = setup({ APP_ENV: 'production' });
    expect((await catchApiError(() => ctx.orders.simulatePayment(MEMBER, '0f8fad5b-d9cb-469f-a165-70867728950e', 'paid'))).getStatus()).toBe(404);
  });
});

describe('G26 refund transfer bank (§17.8)', () => {
  it('stok kurang setelah disetujui → refund approval_required → manual_required (transfer_refund_manual), tanpa gateway/POS', async () => {
    const ctx = setup();
    const order = await ctx.checkout([{ channel: 'GL', lines: [{ product_id: 13, qty: 1 }] }]);
    await ctx.upload(order.order_id);
    await ctx.approve(order.order_id);
    await ctx.drain();
    const refunds = [...ctx.repo.state.refunds.values()];
    expect(refunds).toHaveLength(1);
    expect(refunds[0]).toMatchObject({ status: 'approval_required', payment_intent_id: null });
    // approver CS menyetujui (empat mata) → consumer → manual_required transfer (Finance mentransfer balik)
    await ctx.admin.approveRefund(refunds[0].id, { actor: 'spv1/host-b', role: 'approver' }, 'stok kurang');
    await ctx.drain();
    expect(ctx.repo.state.refunds.get(refunds[0].id)).toMatchObject({ status: 'manual_required', failure_reason: TRANSFER_REFUND_REASON });
    expect(ctx.odoo.calls.filter((c) => c.method === 'counterRefundAuthorize')).toHaveLength(0);
    expect(ctx.payments.refunds).toHaveLength(0);
    // Finance sudah mentransfer balik → settle manual oleh approver → settled + Odoo refund_settled (jurnal transfer)
    const res = await ctx.admin.settleManual(refunds[0].id, 'manual:BCA-REF-0001', { actor: 'spv1/host-b', role: 'approver' }, 'transfer balik');
    expect(res.status).toBe('settled');
    // stok kurang terjadi SEBELUM faktur Odoo → tanpa nota kredit → tidak ada refund_settled Odoo (dana dicatat Finance)
    expect(ctx.odoo.calls.filter((c) => c.method === 'refundSettled')).toHaveLength(refunds[0].odoo_refund_ref ? 1 : 0);
  });
});

describe('G26 form multipart bukti', () => {
  async function* parts(list: unknown[]) {
    for (const p of list) yield p;
  }
  it('hanya satu berkas photo, tanpa field teks; magic bytes dicek', async () => {
    const jpeg = await sharp({ create: { width: 8, height: 8, channels: 3, background: '#fff' } }).jpeg().toBuffer();
    const file = (name: string, buf: Buffer) => ({ fieldname: name, toBuffer: async () => buf, file: { truncated: false } });
    await expect(readTransferProofForm(parts([file('photo', jpeg)]), { maxFileBytes: 1_000_000 })).resolves.toHaveLength(jpeg.length);
    await expect(readTransferProofForm(parts([{ fieldname: 'note', value: 'x' }]), { maxFileBytes: 1_000_000 })).rejects.toMatchObject({ status: 400 });
    await expect(readTransferProofForm(parts([file('photo', jpeg), file('photo', jpeg)]), { maxFileBytes: 1_000_000 })).rejects.toMatchObject({ status: 400 });
    await expect(readTransferProofForm(parts([file('photo', Buffer.from('bukan gambar sama sekali'))]), { maxFileBytes: 1_000_000 })).rejects.toMatchObject({ status: 415 });
    await expect(readTransferProofForm(parts([file('photo', jpeg)]), { maxFileBytes: 10 })).rejects.toMatchObject({ status: 413 });
    await expect(readTransferProofForm(parts([]), { maxFileBytes: 10 })).rejects.toMatchObject({ status: 400 });
  });
});
