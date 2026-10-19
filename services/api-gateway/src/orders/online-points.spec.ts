import { Logger } from '@nestjs/common';
import { MetricsService } from '../common/metrics/metrics.service';
import { FakeClock, FakeKvStore, catchApiError, testCipher, testConfig } from '../common/testing/fakes';
import { MemoryCartStore } from '../cart/cart-store';
import { CartService, parseCartPut } from '../cart/cart.service';
import { QuoteService, placeLines } from '../cart/quote.service';
import { CatalogService, ChannelInfo, ProductSummary } from '../catalog/catalog.service';
import { EffectiveLoyaltyParams, LoyaltyPolicyService } from '../loyalty/loyalty-policy.service';
import { LoyaltyService } from '../loyalty/loyalty.service';
import { MockOdooOnline } from '../odoo-rpc/odoo-online.mock';
import { OnlineMetrics } from '../odoo-rpc/online-metrics';
import { parseCompensate, parseOrderPaid, parseRefundRequested, parseUnpaidClosed } from './online-consumers';
import { OnlinePointsPort, PgOnlinePoints, QuotePoints, RedeemPlan } from './online-points';
import { OnlineSagaService } from './online-saga.service';
import { OnlineRepo } from './online.repository';
import { OrderRow, PartRow, RefundRow } from './online.model';
import { OrderEventsService, parseOdooEvent } from './order-events.service';
import { parsePointsRedeem } from './orders.controller';
import { OrdersService, requestSha256 } from './orders.service';
import { FakeOnlinePayments, MemoryOnlineRepo } from './testing/online-fakes';

/** G22 docs/04 §14.B (atribut, preparing) & §14.C (poin online) — saga dengan port poin palsu; Odoo stub. */

const MEMBER = '8d7b5a1e-2c3f-4a5b-9c8d-7e6f5a4b3c2d';
const HASH = 'a'.repeat(64);
const KEY = 'chk1_BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB';

const CHANNEL: ChannelInfo = {
  channel_code: 'GL',
  company_code: 'GL',
  brand_code: null,
  name: 'EFN Retail',
  fulfilment: { pickup: true, delivery: false },
  outlets: [{ outlet_code: 'TOKO-01', public_name: 'Toko Demo 01', pickup_enabled: true, prep_minutes: 30 }],
  payment_modes: ['qris', 'counter'],
  business_type: 'retail',
  loyalty: { earn: true, redeem: true },
};
const NAMES: Record<number, string> = { 11: 'Beras Premium 5 kg', 12: 'Minyak Goreng 2 L', 13: 'Produk Uji Stok-Kurang (mock)' };

function stubCatalog(): CatalogService {
  return {
    channels: async () => [CHANNEL],
    channel: async (c: string) => (c === 'GL' ? CHANNEL : null),
    productsByIds: async (_c: string, ids: readonly number[]) =>
      new Map(
        ids
          .filter((id) => NAMES[id])
          .map((id): [number, ProductSummary] => [id, { product_id: id, name: NAMES[id], uom: 'Unit', category_id: 1, image_sha: null, price_incl: 0, price_excl: 0, tax_labels: [], max_qty: 20 }]),
      ),
  } as unknown as CatalogService;
}

/** Port poin palsu: mencatat panggilan + reservasi in-memory (logika ledger diuji di PgOnlinePoints/loyalty). */
class FakePoints implements OnlinePointsPort {
  readonly calls: string[] = [];
  available = 10_000;
  reservations = new Map<string, { part: string; points: number; status: string }>();
  async quoteBlock(_m: string, _h: string | null, groups: { channel_code: string; amount_payable: number }[]): Promise<QuotePoints> {
    return {
      balance_available: this.available,
      point_value_idr: 100,
      groups: groups.map((g) => ({ channel_code: g.channel_code, redeem_allowed: true, min_points: 50, max_points: PgOnlinePoints.maxPoints(g.amount_payable, 50, 100, this.available), max_value_idr: 0 })),
    };
  }
  async plan(groups: { channel_code: string; company_code: string; amount_payable: number }[], req: { channel_code: string; points: number }[]): Promise<RedeemPlan[]> {
    return req.map((x) => ({ channel_code: x.channel_code, company_code: groups.find((g) => g.channel_code === x.channel_code)?.company_code as 'GL', points: x.points, value_idr: x.points * 100 }));
  }
  async reserve(_r: OnlineRepo, _m: string, partId: string, plan: RedeemPlan): Promise<string> {
    this.calls.push(`reserve:${partId}:${plan.points}`);
    const id = `00000000-0000-4000-8000-${String(this.reservations.size + 1).padStart(12, '0')}`;
    this.reservations.set(id, { part: partId, points: plan.points, status: 'reserved' });
    return id;
  }
  async commitOrder(_r: OnlineRepo, _o: OrderRow, parts: readonly PartRow[]): Promise<void> {
    for (const p of parts) {
      const res = p.loyalty_reservation_id ? this.reservations.get(p.loyalty_reservation_id) : undefined;
      if (res && res.status === 'reserved') {
        res.status = 'committed';
        this.calls.push(`commit:${p.id}`);
      }
    }
  }
  async releaseOrder(_r: OnlineRepo, _o: string, parts: readonly PartRow[]): Promise<void> {
    for (const p of parts) {
      const res = p.loyalty_reservation_id ? this.reservations.get(p.loyalty_reservation_id) : undefined;
      if (res && res.status === 'reserved') {
        res.status = 'released';
        this.calls.push(`release:${p.id}`);
      }
    }
  }
  async restoreOnSettled(_r: OnlineRepo, part: PartRow, refund: RefundRow): Promise<void> {
    this.calls.push(`restore:${part.id}:${refund.points_value ?? 0}`);
  }
  async earnOnPickup(r: OnlineRepo, _o: OrderRow, part: PartRow, eligible: number): Promise<void> {
    this.calls.push(`earn:${part.id}:${eligible}`);
    await r.updatePart(part.id, { earn_status: 'earned', points_earned: Math.floor(eligible / 10_000), eligible_amount: eligible });
  }
  async clawbackOnRefund(_r: OnlineRepo, part: PartRow, key: string, eligible: number): Promise<void> {
    this.calls.push(`claw:${part.id}:${key}:${eligible}`);
  }
  async reservedPoints(): Promise<number> {
    return 0;
  }
}

function setup() {
  const clock = new FakeClock();
  const config = testConfig({ ONLINE_ENABLED: 'true', ODOO_ONLINE_MOCK: 'true', ONLINE_LOYALTY_REDEEM_ENABLED: 'true', ONLINE_LOYALTY_EARN_ENABLED: 'true' });
  const kv = new FakeKvStore(clock.now);
  const repo = new MemoryOnlineRepo();
  repo.now = clock.now;
  repo.state.members.set(MEMBER, HASH);
  const odoo = new MockOdooOnline();
  odoo.now = clock.now;
  const metrics = new OnlineMetrics(new MetricsService());
  const payments = new FakeOnlinePayments(repo);
  const points = new FakePoints();
  const saga = new OnlineSagaService(repo, odoo, payments, metrics, points);
  saga.now = clock.now;
  payments.saga = saga;
  const catalog = stubCatalog();
  const cart = new CartService(config, new MemoryCartStore(), catalog);
  const quotes = new QuoteService(config, cart, catalog, odoo, kv, metrics, points);
  quotes.now = clock.now;
  const orders = new OrdersService(config, repo, odoo, payments, quotes, cart, catalog, testCipher(config), metrics, saga, points);
  orders.now = clock.now;
  const events = new OrderEventsService(repo, saga, metrics, points);
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
  async function checkout(pts: number | null, lines = [{ product_id: 11, qty: 2 }]) {
    const c = await cart.get(MEMBER);
    await cart.put(MEMBER, { version: c.version, groups: [{ channel_code: 'GL', outlet_code: 'TOKO-01', fulfilment: 'pickup', lines }] });
    const q = await quotes.quote(MEMBER);
    const order = await orders.checkout(MEMBER, KEY, {
      quoteId: q.quote_id,
      acceptTotal: q.amount_payable_total,
      ...(pts ? { pointsRedeem: [{ channel_code: 'GL', points: pts }] } : {}),
    });
    return { q, order };
  }
  const partsOf = (id: string) => [...repo.state.parts.values()].filter((p) => p.order_id === id);
  function event(partId: string, ev: string, seq: number, extra: Record<string, unknown> = {}) {
    const part = repo.state.parts.get(partId)!;
    return parseOdooEvent(
      Buffer.from(
        JSON.stringify({
          schema_version: 1,
          idempotency_key: `online:${part.id}:${ev}:${seq}`,
          seq,
          platform_order_ref: part.id,
          checkout_ref: part.order_id,
          channel_code: 'GL',
          company_code: 'GL',
          event: ev,
          state: ev,
          occurred_at: new Date(clock.ms).toISOString(),
          refund: null,
          ...extra,
        }),
      ),
    );
  }
  return { clock, config, repo, odoo, payments, points, saga, cart, quotes, orders, events, drain, checkout, partsOf, event };
}

async function code(p: Promise<unknown>): Promise<string> {
  const err = await catchApiError(() => p);
  return `${err.getStatus()} ${(err.body as { error: { code: string } }).error.code}`;
}

describe('Poin online — checkout & saga (§14.C.3)', () => {
  it('quote membawa blok poin; checkout reserve DI transaksi order; place membawa points_payment; intent = porsi tunai', async () => {
    const ctx = setup();
    const { q, order } = await ctx.checkout(300);
    expect(q.points).toMatchObject({ balance_available: 10_000, point_value_idr: 100, groups: [{ channel_code: 'GL', redeem_allowed: true }] });
    expect(order).toMatchObject({ status: 'awaiting_payment', amount_payable: 159_800, amount_points_value: 30_000, amount_cash: 129_800 });
    const [part] = ctx.partsOf(order.order_id);
    expect(part).toMatchObject({ points_redeemed: 300, points_value_idr: 30_000, amount_cash: 129_800 });
    expect(ctx.points.calls).toEqual([`reserve:${part.id}:300`]);
    expect(ctx.odoo.orders.get(part.id)).toMatchObject({ pointsValue: 30_000, amountDue: 129_800 });
    expect(ctx.repo.state.portions).toEqual([expect.objectContaining({ part_id: part.id, amount: 129_800 })]);
    expect(order.parts[0]).toMatchObject({ points_redeemed: 300, points_value_idr: 30_000, amount_cash: 129_800, points_earned: 0, points_restored: 0 });
  });

  it('webhook paid → commit poin (transaksi webhook) → mark_paid tunai + points_value; picked_up loyalty → earn', async () => {
    const ctx = setup();
    const { order } = await ctx.checkout(300);
    const [part] = ctx.partsOf(order.order_id);
    await ctx.payments.pay(ctx.repo.state.orders.get(order.order_id)!.payment_intent_id!);
    expect(ctx.points.calls).toContain(`commit:${part.id}`);
    await ctx.drain();
    expect(ctx.repo.state.parts.get(part.id)?.status).toBe('confirmed');
    expect(ctx.odoo.orders.get(part.id)?.state).toBe('confirmed'); // amount_paid = amount_due & points_value cocok
    ctx.odoo.advance(part.id, 'ready');
    await ctx.events.handle(ctx.event(part.id, 'ready', 3, { ready_source: 'kds' }));
    await ctx.events.handle(ctx.event(part.id, 'picked_up', 4, { loyalty: { eligible_amount: 116_936.7 } }));
    expect(ctx.points.calls).toContain(`earn:${part.id}:116936`);
    expect((await ctx.orders.get(MEMBER, order.order_id)).parts[0].points_earned).toBe(11);
  });

  it('batal pelanggan sebelum bayar → reservasi dilepas di transaksi batal; Odoo menolak poin (points_invalid) → 409 exceeds_max_pct (L-79) + release', async () => {
    const ctx = setup();
    const { order } = await ctx.checkout(300);
    const [part] = ctx.partsOf(order.order_id);
    await ctx.orders.cancel(MEMBER, order.order_id);
    expect(ctx.points.calls).toContain(`release:${part.id}`);
    const b = setup();
    b.odoo.redeemMaxPct = 10; // kebijakan Odoo lebih ketat dari BFF → points_invalid
    const warn = jest.spyOn(Logger.prototype, 'warn').mockImplementation(() => undefined);
    try {
      expect(await code(b.checkout(300))).toBe('409 exceeds_max_pct');
    } finally {
      warn.mockRestore();
    }
    expect(b.points.calls.some((c) => c.startsWith('release:'))).toBe(true);
    expect([...b.repo.state.orders.values()][0].status).toBe('failed');
  });

  it('stok kurang setelah bayar → refund penuh: amount bruto, points_value = porsi poin, gateway hanya tunai; settled → restore', async () => {
    const ctx = setup();
    const { order } = await ctx.checkout(300, [{ product_id: 13, qty: 10 }]); // 120 000; poin 30 000
    const [part] = ctx.partsOf(order.order_id);
    await ctx.payments.pay(ctx.repo.state.orders.get(order.order_id)!.payment_intent_id!);
    await ctx.drain();
    const rf = [...ctx.repo.state.refunds.values()][0];
    expect(rf).toMatchObject({ amount: 120_000, points_value: 30_000, cash_amount: 90_000, status: 'settled' });
    expect(ctx.payments.refunds[0].req.amount).toBe(90_000);
    expect(ctx.points.calls).toContain(`restore:${part.id}:30000`);
  });

  it('event refunded dengan eligible_amount_refunded → clawback berkunci refund; preparing → kitchen_started_at tanpa transisi', async () => {
    const ctx = setup();
    const { order } = await ctx.checkout(null);
    const [part] = ctx.partsOf(order.order_id);
    await ctx.payments.pay(ctx.repo.state.orders.get(order.order_id)!.payment_intent_id!);
    await ctx.drain();
    expect(await ctx.events.handle(ctx.event(part.id, 'preparing', 3, { state: 'confirmed', kitchen_started_at: new Date(ctx.clock.ms).toISOString() }))).toMatchObject({ status: 'applied' });
    expect(ctx.repo.state.parts.get(part.id)).toMatchObject({ status: 'confirmed', kitchen_started_at: expect.any(Date) });
    ctx.odoo.advance(part.id, 'ready');
    ctx.odoo.advance(part.id, 'picked_up');
    await ctx.events.handle(ctx.event(part.id, 'picked_up', 5, { loyalty: { eligible_amount: 140_000 } }));
    const warn = jest.spyOn(Logger.prototype, 'warn').mockImplementation(() => undefined);
    try {
      await ctx.events.handle(
        ctx.event(part.id, 'refunded', 6, { state: 'picked_up', refund: { refund_ref: 'RINV/1', amount: 79_900, kind: 'lines', initiator: 'odoo', points_value: 0, cash_amount: 79_900, eligible_amount_refunded: 70_000 } }),
      );
    } finally {
      warn.mockRestore();
    }
    const rf = [...ctx.repo.state.refunds.values()].find((x) => x.odoo_refund_ref === 'RINV/1')!;
    expect(ctx.points.calls).toContain(`claw:${part.id}:online_reverse:${rf.id}:70000`);
  });
});

describe('Atribut F&B (B-4, L-78) & parser', () => {
  it('cart: identitas (product_id, PTAV terurut) — modifier beda = dua baris; PTAV hanya dikirim bila ada', () => {
    const put = parseCartPut({
      version: 0,
      groups: [{ channel_code: 'PB', outlet_code: 'CAFE-01', fulfilment: 'pickup', lines: [{ product_id: 22, qty: 1, attribute_value_ids: [201, 101] }, { product_id: 22, qty: 2, attribute_value_ids: [101, 201] }, { product_id: 22, qty: 1 }] }],
    });
    expect(put.groups[0].lines).toEqual([{ product_id: 22, qty: 3, attribute_value_ids: [101, 201] }, { product_id: 22, qty: 1 }]);
    expect(placeLines({ lines: [{ product_id: 22, name: 'L', qty: 3, price_unit_incl: 1, subtotal_incl: 3, tax_labels: [], available: true, attribute_value_ids: [101] } as never] })).toEqual([
      { product_id: 22, qty: 3, attribute_value_ids: [101] },
    ]);
  });

  it('stub quote: PTAV bukan milik produk → issue attribute_invalid (409 cart_has_issues saat checkout); PTAV sah → attributes_text', async () => {
    const odoo = new MockOdooOnline();
    odoo.attributes.set(11, [{ attribute_id: 1, name: 'Kemasan', display_type: 'radio', required: true, values: [{ id: 501, name: 'Karung', price_extra_excl: 1_000 }] }]);
    const ok = await odoo.quote('GL', 'TOKO-01', 'pickup', [{ product_id: 11, qty: 1, attribute_value_ids: [501] }], []);
    // faktor pajak stub excl→incl = 122/111 (sama dengan price_excl katalog stub): round((79 900·111/122 + 1 000)·122/111) = 80 999
    expect(ok.lines[0]).toMatchObject({ attribute_value_ids: [501], attributes_text: 'Kemasan: Karung', price_unit_incl: 80_999 });
    const bad = await odoo.quote('GL', 'TOKO-01', 'pickup', [{ product_id: 11, qty: 1, attribute_value_ids: [999] }], []);
    expect(bad.issues).toEqual([{ product_id: 11, code: 'attribute_invalid' }]);
  });

  it('points_redeem: satu entri per kanal, poin bulat > 0; ikut sidik idempotensi hanya bila ada', async () => {
    expect(parsePointsRedeem([{ channel_code: 'GL', points: 300 }])).toEqual([{ channel_code: 'GL', points: 300 }]);
    for (const bad of [[{ channel_code: 'GL', points: 0 }], [{ channel_code: 'GL', points: 1 }, { channel_code: 'GL', points: 2 }], [{ channel_code: 'GL', points: 1, value_idr: 100 }], 'x']) {
      expect((await catchApiError(() => parsePointsRedeem(bad))).getStatus()).toBe(400);
    }
    const base = { quoteId: '3f2b8c1e-7d4a-4e0b-9c55-2a1f0e6d9b10', acceptTotal: 100 };
    expect(requestSha256(base)).toBe(requestSha256({ ...base, pointsRedeem: [] }));
    expect(requestSha256(base)).not.toBe(requestSha256({ ...base, pointsRedeem: [{ channel_code: 'GL', points: 300 }] }));
  });
});

describe('PgOnlinePoints — batas redeem (C.2, L-79)', () => {
  const policy = (over: Partial<EffectiveLoyaltyParams> = {}): EffectiveLoyaltyParams => ({
    companyCode: 'GL',
    source: 'policy',
    policyVersion: 3,
    pointValueVersion: 1,
    earnIdrPerPoint: 10_000,
    pointValueIdr: 100,
    redeemMinPoints: 50,
    redeemMaxPct: 50,
    maxPointsPerOrder: 5_000,
    dailyMaxPointsMember: 2_000,
    posConfigHourlyMax: 0,
    companyHourlyMax: 0,
    earnOnMoneyOnly: true,
    ...over,
  });
  const mk = (p: EffectiveLoyaltyParams, env: Record<string, string> = { ONLINE_LOYALTY_REDEEM_ENABLED: 'true' }) => {
    const config = testConfig(env);
    const policies = { effective: async () => p } as unknown as LoyaltyPolicyService;
    return new PgOnlinePoints(config, { query: async () => ({ rows: [], rowCount: 0 }) } as never, {} as LoyaltyService, new MetricsService(), policies);
  };
  const groups = [{ channel_code: 'GL', company_code: 'GL', amount_payable: 100_000, redeem: true }];

  it('max_points = min(saldo, floor(floor(T × pct/100)/nilai), floor((T−1)/nilai))', () => {
    expect(PgOnlinePoints.maxPoints(100_000, 50, 100, 10_000)).toBe(500);
    expect(PgOnlinePoints.maxPoints(100_000, 50, 100, 120)).toBe(120);
    expect(PgOnlinePoints.maxPoints(10_000, 100, 100, 1_000)).toBe(99); // tunai ≥ Rp1
  });

  it('plan: di bawah minimum / melebihi pct / menutup penuh / kebijakan env (L-79) / kill switch → error kontrak', async () => {
    expect(await mk(policy()).plan(groups, [{ channel_code: 'GL', points: 300 }])).toEqual([{ channel_code: 'GL', company_code: 'GL', points: 300, value_idr: 30_000 }]);
    expect(await code(mk(policy()).plan(groups, [{ channel_code: 'GL', points: 49 }]))).toBe('409 below_minimum');
    expect(await code(mk(policy()).plan(groups, [{ channel_code: 'GL', points: 501 }]))).toBe('409 exceeds_max_pct');
    expect(await code(mk(policy({ redeemMaxPct: 100 })).plan(groups, [{ channel_code: 'GL', points: 1_000 }]))).toBe('422 points_cover_full_amount');
    expect(await code(mk(policy({ source: 'env' })).plan(groups, [{ channel_code: 'GL', points: 300 }]))).toBe('422 points_unavailable');
    expect(await code(mk(policy({ pointValueVersion: null })).plan(groups, [{ channel_code: 'GL', points: 300 }]))).toBe('422 points_unavailable');
    expect(await code(mk(policy(), {}).plan(groups, [{ channel_code: 'GL', points: 300 }]))).toBe('422 points_unavailable');
    expect(await code(mk(policy()).plan([{ ...groups[0], redeem: false }], [{ channel_code: 'GL', points: 300 }]))).toBe('422 points_unavailable');
  });
});
