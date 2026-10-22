import { APP_FILTER } from '@nestjs/core';
import { FastifyAdapter, NestFastifyApplication } from '@nestjs/platform-fastify';
import { Test } from '@nestjs/testing';
import { ROUTE_ARGS_METADATA } from '@nestjs/common/constants';
import { RouteParamtypes } from '@nestjs/common/enums/route-paramtypes.enum';
import { AppConfig } from '../config/app-config';
import { ContractExceptionFilter } from '../common/errors/contract-exception.filter';
import { GLOBAL_PREFIX, GLOBAL_PREFIX_EXCLUDE } from '../common/http/global-prefix';
import { MemberService } from '../common/member/member.service';
import { RateLimiter } from '../common/rate-limit/rate-limiter';
import { KV_STORE } from '../common/redis/kv-store';
import { AccessTokenService } from '../common/security/access-token.service';
import { JwtAuthGuard } from '../common/security/jwt-auth.guard';
import { ServiceKeyGuard } from '../common/security/service-key.guard';
import { computeWebhookSignature } from '../common/security/webhook-signature';
import { FakeClock, FakeKvStore, randomSecret, testConfig } from '../common/testing/fakes';
import { CartController } from '../cart/cart.controller';
import { CartService } from '../cart/cart.service';
import { QuoteService } from '../cart/quote.service';
import { CatalogController } from '../catalog/catalog.controller';
import { CatalogService } from '../catalog/catalog.service';
import { OnlineEventHmacGuard } from './order-events.guard';
import { OrderEventsService } from './order-events.service';
import { OrderEventsController, OrdersController } from './orders.controller';
import { OrdersService } from './orders.service';

const MEMBER = '8d7b5a1e-2c3f-4a5b-9c8d-7e6f5a4b3c2d';
const ORDER = '3f2b8c1e-7d4a-4e0b-9c55-2a1f0e6d9b10';
const HMAC = randomSecret();

async function boot(over: Record<string, string> = {}) {
  const clock = new FakeClock(Date.now());
  const config = testConfig({ ONLINE_ENABLED: 'true', ODOO_ONLINE_EVENT_HMAC_FILE: '/s/hmac', ...over }, (p) => {
    if (p === '/s/hmac') return Buffer.from(HMAC);
    throw new Error('ENOENT');
  });
  const kv = new FakeKvStore(clock.now);
  const limiter = new RateLimiter(kv);
  limiter.now = clock.now;
  const orders = {
    checkout: jest.fn().mockResolvedValue({ order_id: ORDER, status: 'awaiting_payment' }),
    list: jest.fn().mockResolvedValue({ items: [], next_cursor: null }),
    get: jest.fn().mockResolvedValue({ order_id: ORDER }),
    cancel: jest.fn().mockResolvedValue({ status: 'cancelled' }),
    pickupCodes: jest.fn().mockResolvedValue({ items: [] }),
    simulatePayment: jest.fn().mockResolvedValue({ order_id: ORDER }),
  };
  const catalog = {
    channels: jest.fn().mockResolvedValue([]),
    categories: jest.fn().mockResolvedValue({ items: [] }),
    products: jest.fn().mockResolvedValue({ items: [], next_cursor: null, catalog_synced_at: null }),
    product: jest.fn(),
    image: jest.fn().mockResolvedValue(Buffer.from([0xff, 0xd8, 0xff, 0xe0])),
    stock: jest.fn().mockResolvedValue({ items: [], checked_at: 'x' }),
  };
  const cart = { get: jest.fn().mockResolvedValue({ version: 0, groups: [] }), put: jest.fn(), clear: jest.fn() };
  const quotes = { quote: jest.fn().mockResolvedValue({ quote_id: 'q' }) };
  const events = { handle: jest.fn().mockResolvedValue({ ok: true, status: 'applied' }) };
  const tokens = new AccessTokenService(config);
  const mod = await Test.createTestingModule({
    controllers: [CatalogController, CartController, OrdersController, OrderEventsController],
    providers: [
      { provide: AppConfig, useValue: config },
      { provide: KV_STORE, useValue: kv },
      { provide: RateLimiter, useValue: limiter },
      { provide: AccessTokenService, useValue: tokens },
      { provide: MemberService, useValue: { findActive: async (id: string) => (id === MEMBER ? { id, memberHash: 'a'.repeat(64) } : null) } },
      JwtAuthGuard,
      ServiceKeyGuard,
      OnlineEventHmacGuard,
      { provide: OrdersService, useValue: orders },
      { provide: CatalogService, useValue: catalog },
      { provide: CartService, useValue: cart },
      { provide: QuoteService, useValue: quotes },
      { provide: OrderEventsService, useValue: events },
      { provide: APP_FILTER, useClass: ContractExceptionFilter },
    ],
  }).compile();
  const app = mod.createNestApplication<NestFastifyApplication>(new FastifyAdapter(), { rawBody: true });
  app.setGlobalPrefix(GLOBAL_PREFIX, { exclude: GLOBAL_PREFIX_EXCLUDE });
  await app.init();
  await app.getHttpAdapter().getInstance().ready();
  const bearer = { authorization: `Bearer ${tokens.issue(MEMBER).accessToken}` };
  return { app, config, orders, catalog, cart, quotes, events, bearer, clock };
}

describe('Route G19 (§11.2 promo, §11.3 brand) — Fastify nyata', () => {
  it('GET /v1/catalog/channels?brand=: valid → diteruskan; tanpa → null; salah/berulang/kosong → 400 invalid_request', async () => {
    const { app, catalog } = await boot();
    expect((await app.inject({ method: 'GET', url: '/v1/catalog/channels?brand=BACHA' })).statusCode).toBe(200);
    expect(catalog.channels).toHaveBeenLastCalledWith('BACHA');
    expect((await app.inject({ method: 'GET', url: '/v1/catalog/channels' })).statusCode).toBe(200);
    expect(catalog.channels).toHaveBeenLastCalledWith(null);
    for (const q of ['brand=', 'brand=gl', 'brand=GL-1', 'brand=GL&brand=PB', 'brand=%2E%2E%2FGL', 'brand=ABCDEFGHIJK']) {
      const r = await app.inject({ method: 'GET', url: `/v1/catalog/channels?${q}` });
      expect(r.statusCode).toBe(400);
      expect(r.json()).toEqual({ error: { code: 'invalid_request', message: expect.any(String) } });
    }
    expect(catalog.channels).toHaveBeenCalledTimes(2);
    await app.close();
  });

  it('POST /v1/cart/quote: tanpa body / {} → []; promo_codes dinormalisasi; format salah → 400 invalid_request tanpa memanggil quote', async () => {
    const { app, bearer, quotes } = await boot();
    expect((await app.inject({ method: 'POST', url: '/v1/cart/quote', headers: bearer })).statusCode).toBe(200);
    expect(quotes.quote).toHaveBeenLastCalledWith(MEMBER, []);
    expect((await app.inject({ method: 'POST', url: '/v1/cart/quote', headers: bearer, payload: {} })).statusCode).toBe(200);
    expect(quotes.quote).toHaveBeenLastCalledWith(MEMBER, []);
    const ok = await app.inject({ method: 'POST', url: '/v1/cart/quote', headers: bearer, payload: { promo_codes: ['demo5k', 'DEMO5K', 'gajian'] } });
    expect(ok.statusCode).toBe(200);
    expect(ok.headers['cache-control']).toBe('no-store');
    expect(quotes.quote).toHaveBeenLastCalledWith(MEMBER, ['DEMO5K', 'GAJIAN']);
    const calls = quotes.quote.mock.calls.length;
    for (const payload of [{ promo_codes: ['A', 'B', 'C', 'D'] }, { promo_codes: ['A B'] }, { promo_codes: 'A' }, { foo: 1 }]) {
      const r = await app.inject({ method: 'POST', url: '/v1/cart/quote', headers: bearer, payload });
      expect(r.statusCode).toBe(400);
      expect(r.json()).toEqual({ error: { code: 'invalid_request', message: expect.any(String) } });
    }
    expect(quotes.quote.mock.calls.length).toBe(calls);
    await app.close();
  });

  it('rate limit promo per member terpisah dari QUOTE → 429 rate_limited + Retry-After', async () => {
    const { app, bearer, quotes } = await boot({ ONLINE_RL_PROMO_MEMBER_LIMIT: '1' });
    expect((await app.inject({ method: 'POST', url: '/v1/cart/quote', headers: bearer, payload: { promo_codes: ['X1'] } })).statusCode).toBe(200);
    const r = await app.inject({ method: 'POST', url: '/v1/cart/quote', headers: bearer, payload: { promo_codes: ['X2'] } });
    expect(r.statusCode).toBe(429);
    expect(r.json()).toMatchObject({ error: { code: 'rate_limited' } });
    expect(r.headers['retry-after']).toBeDefined();
    expect((await app.inject({ method: 'POST', url: '/v1/cart/quote', headers: bearer, payload: {} })).statusCode).toBe(200);
    expect(quotes.quote).toHaveBeenCalledTimes(2);
    await app.close();
  });
});

describe('Route online (Fastify nyata, prefix /v1)', () => {
  it('katalog publik: tanpa login, Cache-Control public; gambar immutable image/jpeg; validasi query', async () => {
    const { app, catalog } = await boot();
    const r = await app.inject({ method: 'GET', url: '/v1/catalog/products?channel=GL&limit=10&q=beras' });
    expect(r.statusCode).toBe(200);
    expect(r.headers['cache-control']).toBe('public, max-age=60');
    expect(catalog.products).toHaveBeenCalledWith('GL', { category: null, q: 'beras', cursor: null, limit: 10 });
    const img = await app.inject({ method: 'GET', url: `/v1/catalog/images/${'a'.repeat(40)}?size=1024` });
    expect(img.statusCode).toBe(200);
    expect(img.headers['content-type']).toBe('image/jpeg');
    expect(img.headers['cache-control']).toBe('public, max-age=31536000, immutable');
    for (const url of ['/v1/catalog/products?channel=gl', '/v1/catalog/products?channel=GL&limit=51', `/v1/catalog/stock?channel=GL&outlet=TOKO-01&ids=${'1,'.repeat(50)}1`, '/v1/catalog/images/x?size=99']) {
      expect((await app.inject({ method: 'GET', url })).statusCode).toBe(400);
    }
    expect((await app.inject({ method: 'GET', url: '/v1/catalog/stock?channel=GL&outlet=TOKO-01&ids=1,2' })).headers['cache-control']).toBe('no-store');
    await app.close();
  });

  it('rate limit katalog per IP (120/menit default) → 429 + Retry-After', async () => {
    const { app } = await boot({ ONLINE_RL_CATALOG_LIMIT: '2' });
    for (let i = 0; i < 2; i += 1) expect((await app.inject({ method: 'GET', url: '/v1/catalog/channels' })).statusCode).toBe(200);
    const r = await app.inject({ method: 'GET', url: '/v1/catalog/channels' });
    expect(r.statusCode).toBe(429);
    expect(r.headers['retry-after']).toBeDefined();
    await app.close();
  });

  it('cart/quote/order wajib Bearer; no-store; kill switch ONLINE_ENABLED=false → 503 online_unavailable', async () => {
    const { app, bearer } = await boot();
    expect((await app.inject({ method: 'GET', url: '/v1/cart' })).statusCode).toBe(401);
    const r = await app.inject({ method: 'GET', url: '/v1/cart', headers: bearer });
    expect(r.statusCode).toBe(200);
    expect(r.headers['cache-control']).toBe('no-store');
    await app.close();
    const off = await boot({ ONLINE_ENABLED: 'false', ONLINE_CATALOG_ENABLED: 'false' });
    for (const [method, url] of [['GET', '/v1/cart'], ['POST', '/v1/cart/quote'], ['GET', '/v1/orders'], ['GET', '/v1/catalog/channels']] as const) {
      const res = await off.app.inject({ method, url, headers: off.bearer, payload: method === 'POST' ? {} : undefined });
      expect(res.statusCode).toBe(503);
      expect(res.json()).toEqual({ error: { code: 'online_unavailable', message: expect.any(String) } });
    }
    await off.app.close();
  });

  it('checkout: Idempotency-Key diteruskan; body > 16 KB → 413; checkout 5/10 menit/member → 429; harga dari klien ditolak', async () => {
    const { app, bearer, orders } = await boot();
    const key = 'chk1_' + 'A'.repeat(43);
    const body = { quote_id: '5e0c3a2b-1d4f-4e6a-8b7c-9d0e1f2a3b4c', accept_total: 39_800 };
    const ok = await app.inject({ method: 'POST', url: '/v1/orders', headers: { ...bearer, 'idempotency-key': key }, payload: body });
    expect(ok.statusCode).toBe(201);
    expect(orders.checkout).toHaveBeenCalledWith(MEMBER, key, { quoteId: body.quote_id, acceptTotal: 39_800, paymentMethod: 'qris' });
    expect((await app.inject({ method: 'POST', url: '/v1/orders', headers: bearer, payload: { ...body, price: 1 } })).statusCode).toBe(400);
    const big = await app.inject({ method: 'POST', url: '/v1/orders', headers: { ...bearer, 'content-type': 'application/json' }, payload: JSON.stringify({ ...body, pad: 'x'.repeat(17_000) }) });
    expect(big.statusCode).toBe(413);
    for (let i = 0; i < 3; i += 1) await app.inject({ method: 'POST', url: '/v1/orders', headers: { ...bearer, 'idempotency-key': key }, payload: body });
    const limited = await app.inject({ method: 'POST', url: '/v1/orders', headers: { ...bearer, 'idempotency-key': key }, payload: body });
    expect(limited.statusCode).toBe(429);
    await app.close();
  });

  it('G20 §12.2: payment_method qris|counter (default qris) diteruskan; nilai lain → 400 invalid_request', async () => {
    const { app, bearer, orders } = await boot({ ONLINE_RL_CHECKOUT_LIMIT: '50' });
    const key = 'chk1_' + 'B'.repeat(43);
    const body = { quote_id: '5e0c3a2b-1d4f-4e6a-8b7c-9d0e1f2a3b4c', accept_total: 39_800 };
    const ok = await app.inject({ method: 'POST', url: '/v1/orders', headers: { ...bearer, 'idempotency-key': key }, payload: { ...body, payment_method: 'counter' } });
    expect(ok.statusCode).toBe(201);
    expect(orders.checkout).toHaveBeenLastCalledWith(MEMBER, key, { quoteId: body.quote_id, acceptTotal: 39_800, paymentMethod: 'counter' });
    await app.inject({ method: 'POST', url: '/v1/orders', headers: { ...bearer, 'idempotency-key': key }, payload: { ...body, payment_method: 'qris' } });
    expect(orders.checkout).toHaveBeenLastCalledWith(MEMBER, key, { quoteId: body.quote_id, acceptTotal: 39_800, paymentMethod: 'qris' });
    const calls = orders.checkout.mock.calls.length;
    for (const pm of ['cash', 'COUNTER', 1, null, '']) {
      const bad = await app.inject({ method: 'POST', url: '/v1/orders', headers: { ...bearer, 'idempotency-key': key }, payload: { ...body, payment_method: pm } });
      expect(bad.statusCode).toBe(400);
      expect(bad.json()).toMatchObject({ error: { code: 'invalid_request' } });
    }
    expect(orders.checkout.mock.calls.length).toBe(calls);
    await app.close();
  });

  it('order id bukan UUID → 404 (anti-enumeration); simulate-payment 404 di production', async () => {
    const { app, bearer, orders } = await boot();
    expect((await app.inject({ method: 'GET', url: '/v1/orders/123', headers: bearer })).statusCode).toBe(404);
    expect((await app.inject({ method: 'GET', url: `/v1/orders/${ORDER}/pickup-code`, headers: bearer })).headers['cache-control']).toBe('no-store');
    expect(orders.pickupCodes).toHaveBeenCalledWith(MEMBER, ORDER);
    await app.close();
    const prod = await boot({ APP_ENV: 'production' });
    const r = await prod.app.inject({ method: 'POST', url: `/v1/orders/${ORDER}/simulate-payment`, headers: prod.bearer, payload: { status: 'paid' } });
    expect(r.statusCode).toBe(404);
    expect(prod.orders.simulatePayment).not.toHaveBeenCalled();
    await prod.app.close();
  });

  it('event Odoo: service key + HMAC valid → 200; HMAC salah / replay > 300 s / tanpa service key → 401', async () => {
    const { app, config, events } = await boot();
    const body = Buffer.from(
      JSON.stringify({ schema_version: 1, idempotency_key: 'online:7:ready:2', seq: 2, platform_order_ref: ORDER, checkout_ref: MEMBER, channel_code: 'GL', company_code: 'GL', event: 'ready', state: 'ready', occurred_at: null, refund: null }),
    );
    const now = Math.floor(Date.now() / 1000);
    const sig = (t: number, secret = HMAC) => `t=${t},v1=${computeWebhookSignature(secret, t, body)}`;
    const send = (headers: Record<string, string>) =>
      app.inject({ method: 'POST', url: '/v1/pos/online/order-events', headers: { 'content-type': 'application/json', ...headers }, payload: body });
    const good = await send({ 'x-efn-service-key': config.odooBffApiKey, 'x-efn-signature': sig(now) });
    expect(good.statusCode).toBe(200);
    expect(good.json()).toEqual({ ok: true, status: 'applied' });
    expect(events.handle).toHaveBeenCalledWith(expect.objectContaining({ seq: 2, event: 'ready' }));
    expect((await send({ 'x-efn-service-key': config.odooBffApiKey, 'x-efn-signature': sig(now, randomSecret()) })).json()).toMatchObject({ error: { code: 'invalid_signature' } });
    expect((await send({ 'x-efn-service-key': config.odooBffApiKey, 'x-efn-signature': sig(now - 301) })).statusCode).toBe(401);
    expect((await send({ 'x-efn-signature': sig(now) })).statusCode).toBe(401);
    expect((await send({ 'x-efn-service-key': config.odooBffApiKey })).statusCode).toBe(401);
    expect(events.handle).toHaveBeenCalledTimes(1);
    await app.close();
  });

  it('R2: parameter URL online hanya id opak/enum/teks pencarian — tanpa PII', () => {
    // G23 §15.4: `date` (YYYY-MM-DD, GET /v1/catalog/slots) — tanggal kalender, bukan PII
    const allowedQuery = new Set(['channel', 'category', 'q', 'cursor', 'limit', 'size', 'outlet', 'ids', 'brand', 'date']);
    const allowedParam = new Set(['product_id', 'image_sha', 'order_id']);
    for (const ctrl of [CatalogController, CartController, OrdersController, OrderEventsController]) {
      for (const m of Object.getOwnPropertyNames(ctrl.prototype)) {
        const args = (Reflect.getMetadata(ROUTE_ARGS_METADATA, ctrl, m) ?? {}) as Record<string, { data?: unknown }>;
        for (const [k, meta] of Object.entries(args)) {
          const type = Number(k.split(':')[0]);
          if (type === RouteParamtypes.QUERY) expect(allowedQuery.has(String(meta.data))).toBe(true);
          if (type === RouteParamtypes.PARAM) expect(allowedParam.has(String(meta.data))).toBe(true);
        }
      }
    }
  });
});
