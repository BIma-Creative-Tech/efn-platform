import { MetricsService } from '../common/metrics/metrics.service';
import { FakeKvStore, catchApiError, testConfig } from '../common/testing/fakes';
import { CatalogService, ChannelInfo, ProductSummary } from '../catalog/catalog.service';
import { MockOdooOnline } from '../odoo-rpc/odoo-online.mock';
import { OdooRpcError } from '../odoo-rpc/odoo-online.types';
import { OnlineMetrics } from '../odoo-rpc/online-metrics';
import { MemoryCartStore, cartKey, parseStored } from './cart-store';
import { CartService, parseCartPut } from './cart.service';
import { QuoteService, purgeMemberOnlineRedis, quoteIndexKey, quoteKey } from './quote.service';

const M = '8d7b5a1e-2c3f-4a5b-9c8d-7e6f5a4b3c2d';
const CH: ChannelInfo = {
  channel_code: 'RTL',
  company_code: 'RTL',
  name: 'Retail',
  fulfilment: { pickup: true, delivery: false },
  outlets: [
    { outlet_code: 'TOKO-01', public_name: 'Toko 1', pickup_enabled: true, prep_minutes: 10 },
    { outlet_code: 'TOKO-02', public_name: 'Toko 2', pickup_enabled: false, prep_minutes: 10 },
  ],
};
const catalog = {
  channel: async (c: string) => (c === 'RTL' ? CH : null),
  productsByIds: async (_c: string, ids: readonly number[]) =>
    new Map(ids.filter((i) => i >= 11 && i <= 14).map((i): [number, ProductSummary] => [i, { product_id: i, name: `P${i}`, uom: '', category_id: 1, image_sha: null, price_incl: 1, price_excl: 1, tax_labels: [], max_qty: 5 }])),
} as unknown as CatalogService;

function group(lines: Record<string, unknown>[], extra: Record<string, unknown> = {}) {
  return { channel_code: 'RTL', outlet_code: 'TOKO-01', fulfilment: 'pickup', lines, ...extra };
}

function setup() {
  const config = testConfig({ ONLINE_ENABLED: 'true' });
  const store = new MemoryCartStore();
  const cart = new CartService(config, store, catalog);
  const kv = new FakeKvStore();
  const odoo = new MockOdooOnline();
  const quotes = new QuoteService(config, cart, catalog, odoo, kv, new OnlineMetrics(new MetricsService()));
  return { config, store, cart, kv, odoo, quotes };
}

describe('Cart (§10.3.3)', () => {
  it('tanpa harga: field harga/tak dikenal ditolak; baris duplikat digabung; ≤ 50 baris; qty 1..max', async () => {
    expect(parseCartPut({ version: 0, groups: [group([{ product_id: 11, qty: 1 }, { product_id: 11, qty: 2 }])] }).groups[0].lines).toEqual([{ product_id: 11, qty: 3 }]);
    for (const bad of [
      { version: 0, groups: [group([{ product_id: 11, qty: 1, price: 1 }])] },
      { version: 0, groups: [group([{ product_id: 11, qty: 0 }])] },
      { version: 0, groups: [group([{ product_id: 11, qty: 1.5 }])] },
      { version: -1, groups: [] },
      { version: 0, groups: [group([], { price_incl: 1 })] },
      { version: 0, groups: [group([]), group([])] },
      { version: 0, groups: [group(Array.from({ length: 51 }, (_, i) => ({ product_id: i + 1, qty: 1 })))] },
      { version: 0, groups: [], total: 5 },
    ]) {
      expect((await catchApiError(() => parseCartPut(bad))).getStatus()).toBe(400);
    }
  });

  it('PUT: produk hilang dibuang + warnings item_unavailable; versi naik; konflik versi → 409', async () => {
    const ctx = setup();
    const r = await ctx.cart.put(M, parseCartPut({ version: 0, groups: [group([{ product_id: 11, qty: 2 }, { product_id: 77, qty: 1 }])] }));
    expect(r).toEqual({ version: 1, groups: [group([{ product_id: 11, qty: 2 }])], warnings: [{ product_id: 77, code: 'item_unavailable' }] });
    expect(await ctx.cart.get(M)).toEqual({ version: 1, groups: [group([{ product_id: 11, qty: 2 }])] });
    expect((await catchApiError(() => ctx.cart.put(M, parseCartPut({ version: 0, groups: [] })))).body).toMatchObject({ error: { code: 'cart_version_conflict' } });
  });

  it('kanal/outlet/fulfilment divalidasi; qty > max_qty produk → 400', async () => {
    const ctx = setup();
    const put = (g: Record<string, unknown>) => ctx.cart.put(M, parseCartPut({ version: 0, groups: [g] }));
    expect((await catchApiError(() => put(group([{ product_id: 11, qty: 1 }], { channel_code: 'FNB' })))).body).toMatchObject({ error: { code: 'item_unavailable' } });
    expect((await catchApiError(() => put(group([{ product_id: 11, qty: 1 }], { outlet_code: 'TOKO-02' })))).body).toMatchObject({ error: { code: 'item_unavailable' } });
    expect((await catchApiError(() => put(group([{ product_id: 11, qty: 1 }], { fulfilment: 'delivery' })))).getStatus()).toBe(422);
    expect((await catchApiError(() => put(group([{ product_id: 11, qty: 6 }])))).getStatus()).toBe(400);
  });

  it('format Redis: hash cart:<member_id> v/b; data rusak → kosong', () => {
    expect(cartKey(M)).toBe(`cart:${M}`);
    expect(parseStored('3', '[{"channel_code":"RTL"}]').version).toBe(3);
    expect(parseStored('x', '{bad')).toEqual({ version: 0, groups: [] });
  });
});

describe('Quote (§10.3.4)', () => {
  it('harga & pajak dari Odoo apa adanya; quote_hash hanya di Redis; TTL ≤ ONLINE_QUOTE_TTL_SECONDS', async () => {
    const ctx = setup();
    await ctx.cart.put(M, parseCartPut({ version: 0, groups: [group([{ product_id: 11, qty: 2 }])] }));
    const q = await ctx.quotes.quote(M);
    expect(q.groups[0]).toMatchObject({ company_code: 'RTL', amount_payable: 159_800, lines: [expect.objectContaining({ product_id: 11, qty: 2, subtotal_incl: 159_800 })] });
    expect(JSON.stringify(q)).not.toContain('quote_hash');
    const stored = JSON.parse((await ctx.kv.get(quoteKey(q.quote_id)))!);
    expect(stored.groups[0].quote_hash).toMatch(/^[0-9a-f]{64}$/);
    expect(stored.member_id).toBe(M);
    expect(await ctx.kv.ttl(quoteKey(q.quote_id))).toBeLessThanOrEqual(600);
    expect(await ctx.quotes.load(q.quote_id, '00000000-0000-4000-8000-000000000001')).toBeNull();
  });

  it('cart kosong → 409; Odoo gagal → 503 online_unavailable; produk tak dikenal Odoo → 409 item_unavailable', async () => {
    const ctx = setup();
    expect((await catchApiError(() => ctx.quotes.quote(M))).getStatus()).toBe(409);
    await ctx.cart.put(M, parseCartPut({ version: 0, groups: [group([{ product_id: 11, qty: 1 }])] }));
    ctx.odoo.failNext.set('quote', new OdooRpcError('transport'));
    expect((await catchApiError(() => ctx.quotes.quote(M))).body).toMatchObject({ error: { code: 'online_unavailable' } });
    ctx.odoo.failNext.set('quote', new OdooRpcError('business', 'not_found'));
    expect((await catchApiError(() => ctx.quotes.quote(M))).body).toMatchObject({ error: { code: 'item_unavailable' } });
  });
});

describe('Erasure Redis online (§10.8.3)', () => {
  it('quote terindeks per member dengan key ber-HMAC; purge menghapus cart, semua quote, dan indeks', async () => {
    const ctx = setup();
    await ctx.cart.put(M, parseCartPut({ version: 0, groups: [group([{ product_id: 11, qty: 1 }])] }));
    const q1 = await ctx.quotes.quote(M);
    const q2 = await ctx.quotes.quote(M);
    const idx = quoteIndexKey(ctx.config.redisKeyPepper, M);
    expect(idx).toMatch(/^oq:m:[0-9a-f]{32}$/);
    expect(idx).not.toContain(M);
    expect(JSON.parse((await ctx.kv.get(idx))!)).toEqual([q1.quote_id, q2.quote_id]);
    // cart Redis nyata memakai hash; FakeKvStore: tulis key cart sebagai string untuk memeriksa penghapusan
    await ctx.kv.setEx(cartKey(M), '{}', 60);
    const n = await purgeMemberOnlineRedis(ctx.kv, ctx.config.redisKeyPepper, M);
    expect(n).toBe(4);
    for (const k of [quoteKey(q1.quote_id), quoteKey(q2.quote_id), idx, cartKey(M)]) expect(await ctx.kv.get(k)).toBeNull();
    expect(await purgeMemberOnlineRedis(ctx.kv, ctx.config.redisKeyPepper, M)).toBe(0); // idempoten
  });
});
