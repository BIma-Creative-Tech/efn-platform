import { FakeClock, FakeDb, FakeKvStore, catchApiError, testConfig } from '../common/testing/fakes';
import { MockGateway } from './gateways/mock.gateway';
import { XenditGateway } from './gateways/xendit.gateway';
import { PaymentOperationUnsupportedError } from './payment-gateway';
import { PaymentsService } from './payments.service';

const ORDER = '3f2b8c1e-7d4a-4e0b-9c55-2a1f0e6d9b10';
const PART = '5e0c3a2b-1d4f-4e6a-8b7c-9d0e1f2a3b4c';

type Row = Record<string, unknown>;

function setup() {
  const clock = new FakeClock();
  const config = testConfig();
  const rows = new Map<string, Row>();
  const portions: unknown[][] = [];
  const db = new FakeDb((t, p) => {
    if (t.includes('INSERT INTO platform.payment_intent_portion')) {
      portions.push([...p]);
      return { rows: [], rowCount: 1 };
    }
    if (t.includes('INSERT INTO platform.payment_intent')) {
      if ([...rows.values()].some((r) => r.idempotency_key === p[5])) return { rows: [], rowCount: 0 };
      rows.set(String(p[0]), { id: p[0], provider: p[1], order_ref: p[2], company_code: 'EFN', amount: p[3], status: 'pending', qr_string: null, provider_ref: null, expires_at: p[4], paid_at: null, idempotency_key: p[5], purpose: 'online', online_order_id: p[6] });
      return { rows: [{ id: p[0] }], rowCount: 1 };
    }
    if (t.includes('SET provider_ref')) {
      Object.assign(rows.get(String(p[0]))!, { provider_ref: p[1], qr_string: p[2] });
      return { rows: [], rowCount: 1 };
    }
    if (t.includes('WHERE idempotency_key = $1')) {
      const r = [...rows.values()].find((x) => x.idempotency_key === p[0]);
      return { rows: r ? [r] : [], rowCount: r ? 1 : 0 };
    }
    if (t.includes('FROM platform.payment_intent WHERE id = $1')) {
      const r = rows.get(String(p[0]));
      return { rows: r ? [r] : [], rowCount: r ? 1 : 0 };
    }
    if (t.includes('WHERE provider = $1 AND provider_ref = $2')) {
      const r = [...rows.values()].find((x) => x.provider_ref === p[1]);
      return { rows: r ? [{ id: r.id }] : [], rowCount: r ? 1 : 0 };
    }
    if (t.includes('UPDATE platform.payment_intent SET status = $2')) {
      const r = rows.get(String(p[0]))!;
      if (r.status !== p[3]) return { rows: [], rowCount: 0 };
      r.status = p[1];
      return { rows: [], rowCount: 1 };
    }
    if (t.includes('INSERT INTO platform.processed_message')) return { rows: [{}], rowCount: 1 };
    return undefined;
  });
  const mock = new MockGateway(() => config.webhookPaymentSecret);
  const svc = new PaymentsService(config, db, new FakeKvStore(clock.now), { get: () => mock });
  svc.now = clock.now;
  const hook = jest.fn().mockResolvedValue(undefined);
  svc.setOnlineHook(hook);
  return { svc, rows, portions, db, hook, config, clock };
}

const input = { orderId: ORDER, amount: 39_800, portions: [{ partId: PART, channelCode: 'RTL', companyCode: 'RTL', amount: 39_800 }], expiresAt: new Date(Date.UTC(2026, 9, 5, 3, 15)), idempotencyKey: `online:intent:${ORDER}` };

describe('Payment online (§10.7)', () => {
  it('intent purpose=online, company EFN, porsi per bagian; idempoten per online:intent:<order_id>; Σ porsi ≠ amount ditolak', async () => {
    const ctx = setup();
    const a = await ctx.svc.createOnlineIntent(input);
    expect(a).toMatchObject({ status: 'pending', amount: 39_800, provider: 'mock' });
    expect(ctx.db.find("'online', $7")[0].text).toContain("'EFN'");
    expect(ctx.portions).toEqual([[a.paymentId, PART, 'RTL', 'RTL', 39_800]]);
    const b = await ctx.svc.createOnlineIntent(input);
    expect(b.paymentId).toBe(a.paymentId);
    await expect(ctx.svc.createOnlineIntent({ ...input, amount: 1 })).rejects.toThrow('porsi');
    expect((await catchApiError(() => ctx.svc.createOnlineIntent({ ...input, orderId: PART, portions: [{ ...input.portions[0] }] }))).getStatus()).toBe(409);
  });

  it('webhook paid → hook saga dipanggil DI DALAM transaksi; nominal beda → hook amountMismatch, status tetap', async () => {
    const ctx = setup();
    const a = await ctx.svc.createOnlineIntent(input);
    const ref = String(ctx.rows.get(a.paymentId)!.provider_ref);
    const now = Math.floor(ctx.clock.ms / 1000);
    await ctx.svc.handleWebhook('mock', MockGateway.buildWebhook(ctx.config.webhookPaymentSecret, { event_id: 'e1', provider_ref: ref, status: 'paid', amount: 1, paid_at: null }, now));
    expect(ctx.hook.mock.calls[0][1]).toMatchObject({ paymentId: a.paymentId, onlineOrderId: ORDER, amountMismatch: true });
    expect(ctx.rows.get(a.paymentId)!.status).toBe('pending');
    await ctx.svc.handleWebhook('mock', MockGateway.buildWebhook(ctx.config.webhookPaymentSecret, { event_id: 'e2', provider_ref: ref, status: 'paid', amount: 39_800, paid_at: null }, now));
    expect(ctx.hook).toHaveBeenCalledTimes(2);
    expect(ctx.hook.mock.calls[1][1]).toEqual({ paymentId: a.paymentId, onlineOrderId: ORDER, from: 'pending', to: 'paid', amountMismatch: false });
  });

  it('refund: mock idempoten per refundId, Σ ≤ amount; intent belum paid → failed; xendit → unsupported (manual_required)', async () => {
    const ctx = setup();
    const a = await ctx.svc.createOnlineIntent(input);
    expect(await ctx.svc.refund(a.paymentId, { refundId: 'r1', amount: 10, reason: 'x' })).toEqual({ status: 'failed', providerRefundRef: null });
    ctx.rows.get(a.paymentId)!.status = 'paid';
    expect((await ctx.svc.refund(a.paymentId, { refundId: 'r1', amount: 30_000, reason: 'x' })).status).toBe('succeeded');
    expect((await ctx.svc.refund(a.paymentId, { refundId: 'r1', amount: 30_000, reason: 'x' })).status).toBe('succeeded');
    expect((await ctx.svc.refund(a.paymentId, { refundId: 'r2', amount: 10_000, reason: 'x' })).status).toBe('failed');
    expect((await ctx.svc.refund(a.paymentId, { refundId: 'r3', amount: 9_800, reason: 'x' })).status).toBe('succeeded');
    const x = new XenditGateway({ apiKey: 'k', callbackToken: 't', timeoutMs: 1000, experimental: true }, jest.fn());
    await expect(x.refund()).rejects.toBeInstanceOf(PaymentOperationUnsupportedError);
  });
});
