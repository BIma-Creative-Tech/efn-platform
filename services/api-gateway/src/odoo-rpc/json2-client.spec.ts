import { MetricsService } from '../common/metrics/metrics.service';
import { randomSecret, testConfig } from '../common/testing/fakes';
import { ConfigError } from '../config/app-config';
import { CircuitBreaker } from './circuit-breaker';
import { Json2OdooOnlineClient, Json2Transport, TransportResponse, businessCode } from './json2-client';
import { parseCatalogPage, parsePlace, parseQuote } from './odoo-online.parse';
import { ODOO_ONLINE_PATHS, OdooRpcError } from './odoo-online.types';
import { OnlineMetrics } from './online-metrics';

const RTL_KEY = randomSecret();
const FNB_KEY = randomSecret();

function setup(responses: (TransportResponse | Error)[]) {
  const config = testConfig(
    { ODOO_API_KEY_ONLINE_RTL_FILE: '/k/rtl', ODOO_API_KEY_ONLINE_FNB_FILE: '/k/fnb', ODOO_DB: 'efn_odoo' },
    (p) => {
      if (p === '/k/rtl') return Buffer.from(RTL_KEY);
      if (p === '/k/fnb') return Buffer.from(FNB_KEY);
      throw new Error('ENOENT');
    },
  );
  const calls: { path: string; body: unknown; headers: Record<string, string>; timeout: number }[] = [];
  const transport: Json2Transport = async (path, body, headers, timeout) => {
    calls.push({ path, body: JSON.parse(body), headers, timeout });
    const r = responses.shift();
    if (!r) throw new Error('tidak ada respons');
    if (r instanceof Error) throw r;
    return r;
  };
  const metrics = new OnlineMetrics(new MetricsService());
  const client = new Json2OdooOnlineClient(config, transport, metrics);
  const sleeps: number[] = [];
  client.sleep = async (ms) => {
    sleeps.push(ms);
  };
  client.random = () => 0;
  return { client, calls, metrics, sleeps };
}

const ok = (v: unknown): TransportResponse => ({ status: 200, body: JSON.stringify(v) });
const userError = (code: string, status = 422): TransportResponse => ({
  status,
  body: JSON.stringify({ name: 'odoo.exceptions.UserError', message: `efn_online:${code}`, debug: 'Traceback (most recent call last): rahasia' }),
});
const STATUS_ROW = [{ platform_order_ref: 'x', state: 'confirmed', seq: 3, ready_at: null, picked_up_at: null }];

describe('Klien json2 (docs/04 §10.4)', () => {
  it('POST ke 10 path persis, argumen bernama + context lang saja, bearer key PER KANAL, X-Odoo-Database', async () => {
    const ctx = setup([ok(STATUS_ROW), ok(STATUS_ROW)]);
    await ctx.client.status('RTL', ['x']);
    await ctx.client.status('FNB', ['x']);
    expect(ctx.calls[0]).toMatchObject({
      path: 'efn.online.order/efn_online_status',
      body: { channel_code: 'RTL', platform_order_refs: ['x'], context: { lang: 'id_ID' } },
      headers: { Authorization: `bearer ${RTL_KEY}`, 'X-Odoo-Database': 'efn_odoo', 'Content-Type': 'application/json' },
    });
    expect(ctx.calls[1].headers.Authorization).toBe(`bearer ${FNB_KEY}`);
    expect(Object.values(ODOO_ONLINE_PATHS)).toHaveLength(10);
    for (const p of Object.values(ODOO_ONLINE_PATHS)) expect(p).toMatch(/^efn\.online\.(channel|order)\/efn_online_[a-z_]+$/);
  });

  it('retry hanya transport/5xx/429 (0,5/1/2 s), maks 3×; UserError tidak di-retry dan memetakan kode', async () => {
    const ctx = setup([{ status: 502, body: '' }, { status: 429, body: '' }, new Error('ECONNRESET'), ok(STATUS_ROW)]);
    expect(await ctx.client.status('RTL', ['x'])).toHaveLength(1);
    expect(ctx.sleeps).toEqual([500, 1000, 2000]);
    const biz = setup([userError('price_changed')]);
    const err = await biz.client.status('RTL', ['x']).catch((e: unknown) => e);
    expect(err).toMatchObject({ kind: 'business', code: 'price_changed' });
    expect(biz.calls).toHaveLength(1);
    expect(String((err as Error).message)).not.toContain('Traceback');
    const many = setup([{ status: 503, body: '' }, { status: 503, body: '' }, { status: 503, body: '' }, { status: 503, body: '' }]);
    await expect(many.client.status('RTL', ['x'])).rejects.toMatchObject({ kind: 'transport' });
    expect(many.calls).toHaveLength(4);
  });

  it('401/403 → auth (tanpa retry) + metrik auth failure per kanal; 404 → not_found', async () => {
    const ctx = setup([{ status: 401, body: '{}' }, { status: 403, body: 'forbidden' }, { status: 404, body: '' }]);
    await expect(ctx.client.status('RTL', ['x'])).rejects.toMatchObject({ kind: 'auth' });
    await expect(ctx.client.status('RTL', ['x'])).rejects.toMatchObject({ kind: 'auth' });
    await expect(ctx.client.status('RTL', ['x'])).rejects.toMatchObject({ kind: 'not_found' });
    expect((await ctx.metrics.odooAuthFailures.get()).values[0]).toMatchObject({ labels: { channel: 'RTL' }, value: 2 });
    expect(ctx.calls).toHaveLength(3);
  });

  it('circuit breaker: 5 kegagalan transport beruntun → terbuka 30 s (langsung circuit_open), lalu half-open', async () => {
    const ctx = setup(Array.from({ length: 5 }, () => ({ status: 500, body: '' })).concat([ok(STATUS_ROW)]));
    let t = 0;
    ctx.client.now = () => t;
    await expect(ctx.client.status('RTL', ['x'])).rejects.toMatchObject({ kind: 'transport' }); // 4 percobaan
    await expect(ctx.client.status('RTL', ['x'])).rejects.toMatchObject({ kind: 'transport' }); // ke-5 → terbuka
    expect(ctx.client.isCircuitOpen('RTL')).toBe(true);
    expect(ctx.client.isCircuitOpen('FNB')).toBe(false);
    await expect(ctx.client.status('RTL', ['x'])).rejects.toMatchObject({ kind: 'circuit_open' });
    expect((await ctx.metrics.circuitOpen.get()).values[0]).toMatchObject({ value: 1 });
    t = 31_000;
    expect(await ctx.client.status('RTL', ['x'])).toHaveLength(1);
    expect(ctx.client.isCircuitOpen('RTL')).toBe(false);
    const b = new CircuitBreaker(2, 1000, () => 0);
    b.failure();
    b.neutral();
    b.failure();
    expect(b.isOpen()).toBe(false);
  });

  it('API key kanal tidak dikonfigurasi → auth (tanpa panggilan); literal di luar dev ditolak; mock ditolak di production', async () => {
    const ctx = setup([]);
    await expect(ctx.client.status('FNB-BAKSO', ['x'])).rejects.toMatchObject({ kind: 'auth' });
    expect(ctx.calls).toHaveLength(0);
    expect(() => testConfig({ ODOO_API_KEY_ONLINE_RTL: randomSecret() }).odooApiKeyOnline('RTL')).toThrow(ConfigError);
    expect(testConfig({ APP_ENV: 'dev', ODOO_API_KEY_ONLINE_RTL: 'k'.repeat(20) }).odooApiKeyOnline('RTL')).toBe('k'.repeat(20));
    expect(testConfig({ ODOO_ONLINE_MOCK: 'true' }).odooOnlineMock).toBe(true);
    expect(testConfig({ ODOO_ONLINE_MOCK: 'true', APP_ENV: 'dev' }).odooOnlineMock).toBe(true);
    const envs: Record<string, string>[] = [{ APP_ENV: 'production' }, { APP_ENV: 'staging' }, { NODE_ENV: 'production' }, { NODE_ENV: '' }];
    for (const env of envs) {
      expect(() => testConfig({ ODOO_ONLINE_MOCK: 'true', ...env }).odooOnlineMock).toThrow(/ditolak di staging\/production/);
    }
    expect(() => testConfig({ ODOO_ONLINE_MOCK: 'true', APP_ENV: 'production', ONLINE_ENABLED: 'true' }).assertStartupSecrets()).toThrow(/ODOO_ONLINE_MOCK/);
  });

  it('businessCode hanya menerima pesan persis efn_online:<kode dikenal>', () => {
    expect(businessCode({ name: 'x', message: 'efn_online:out_of_stock' })).toBe('out_of_stock');
    expect(businessCode({ error: { data: { message: 'efn_online:not_found' } } })).toBe('not_found');
    expect(businessCode({ message: 'efn_online:hack' })).toBeNull();
    expect(businessCode({ message: 'ValidationError efn_online:price_changed' })).toBeNull();
  });
});

describe('Validasi respons Odoo (§10.4.3)', () => {
  it('amount_payable non-bulat → invalid_response (alert online_config_error)', () => {
    const base = { order_state: 'placed', sale_order_name: 'S1', amount_total: 100, amount_payable: 100, expires_at: '2026-10-09 03:25:00' };
    expect(parsePlace(base).expires_at?.toISOString()).toBe('2026-10-09T03:25:00.000Z');
    expect(() => parsePlace({ ...base, amount_payable: 100.5 })).toThrow(OdooRpcError);
    expect(() => parseQuote({ lines: [], amount_total: 1, amount_payable: 0.4, quote_hash: 'h' })).toThrow(/bilangan bulat/);
  });

  it('quote: issues tingkat order (product_id null) diterima; price_unit_incl dihitung bila tidak ada', () => {
    const q = parseQuote({
      lines: [{ product_id: 11, name: 'Beras', qty: 2, subtotal_incl: 39_800, tax_labels: ['PPN 11%'], available: true }],
      tax_groups: [{ label: 'PPN 11%', base: 35_855.86, amount: 3_944.14 }],
      amount_total: 39_800,
      amount_payable: 39_800,
      quote_hash: 'abc',
      valid_until: '2026-10-09T03:15:00Z',
      issues: [{ product_id: null, code: 'order_limit' }],
    });
    expect(q.lines[0]).toMatchObject({ price_unit_incl: 19_900, name: 'Beras' });
    expect(q.issues).toEqual([{ product_id: null, code: 'order_limit' }]);
  });

  it('katalog: image_sha hanya hex 40/64 (L-9); deskripsi dipotong 2000; blok channel opsional', () => {
    const item = { product_id: 1, tmpl_id: 2, name: 'A', description: 'x'.repeat(3000), categ: null, uom: 'Unit', sequence: 1, prices: [], max_qty: 5 };
    const p = parseCatalogPage({ items: [{ ...item, image_sha: 'a'.repeat(40) }, { ...item, product_id: 3, image_sha: 'b'.repeat(32) }], deleted: [9], next_cursor: '3', server_time: 'now' });
    expect(p.items.map((i) => i.image_sha)).toEqual(['a'.repeat(40), null]);
    expect(p.items[0].description).toHaveLength(2000);
    expect(p.channel).toBeNull();
    expect(() => parseCatalogPage({ items: [{ ...item, product_id: 'x' }], server_time: 'now' })).toThrow(OdooRpcError);
    // L-11: bentuk blok channel dari E-28 (outlet berkunci `code`)
    const withCh = parseCatalogPage({
      items: [],
      server_time: 'now',
      channel: { code: 'RTL', name: 'Retail', company_code: 'RTL', currency: 'IDR', outlets: [{ code: 'TOKO-01', public_name: 'Toko 1', pickup_enabled: true, prep_minutes: 15, active: true }] },
    });
    expect(withCh.channel).toEqual({
      code: 'RTL', name: 'Retail', company_code: 'RTL', brand_code: null, currency: 'IDR', active: true, fulfilment: { pickup: true, delivery: false },
      outlets: [{ outlet_code: 'TOKO-01', public_name: 'Toko 1', pickup_enabled: true, prep_minutes: 15, active: true }],
    });
    expect(() => parseCatalogPage({ items: [], server_time: 'now', channel: { code: 'RTL', name: 'x', company_code: 'RTL', outlets: [] } })).toThrow(OdooRpcError);
  });
});

describe('G19 §11.2: promo di json2 (quote/place) & baris reward', () => {
  const QUOTE_OK = {
    company_code: 'RTL',
    lines: [
      { product_id: 11, name: 'Beras', qty: 1, price_unit_incl: 79_900, subtotal_incl: 79_900, tax_labels: ['PPN 11%'], available: true },
      { product_id: 9001, name: 'Promo Gajian', qty: 1, price_unit_incl: -5_000, subtotal_incl: -5_000, tax_labels: ['PPN 11%'], available: true, reward: true, reward_id: 3 },
    ],
    tax_groups: [{ label: 'PPN 11%', base: 67_477.48, amount: 7_422.52 }],
    amount_total: 74_900,
    amount_payable: 74_900,
    quote_hash: 'h1',
    valid_until: false,
    issues: [],
    promo_applied: ['gajian'],
    promo_rejected: ['NOPE42'],
  };

  it('quote mengirim promo_codes (selalu, [] bila kosong); place mengirim order.promo_codes', async () => {
    const ctx = setup([ok(QUOTE_OK), ok({ ...QUOTE_OK, promo_applied: [], promo_rejected: [], lines: [QUOTE_OK.lines[0]], amount_total: 79_900, amount_payable: 79_900 }), ok({ order_state: 'placed', sale_order_name: 'S1', amount_total: 74_900, amount_payable: 74_900, expires_at: '2026-10-18 04:10:00' })]);
    await ctx.client.quote('RTL', 'TOKO-01', 'pickup', [{ product_id: 11, qty: 1 }], ['GAJIAN', 'NOPE42']);
    expect(ctx.calls[0].body).toEqual({
      channel_code: 'RTL', outlet_code: 'TOKO-01', fulfilment: 'pickup', lines: [{ product_id: 11, qty: 1 }], promo_codes: ['GAJIAN', 'NOPE42'], context: { lang: 'id_ID' },
    });
    await ctx.client.quote('RTL', 'TOKO-01', 'pickup', [{ product_id: 11, qty: 1 }], []);
    expect((ctx.calls[1].body as Record<string, unknown>).promo_codes).toEqual([]);
    await ctx.client.place('RTL', {
      idempotency_key: 'online:place:p', platform_order_ref: 'p', checkout_ref: 'o', outlet_code: 'TOKO-01', fulfilment: 'pickup',
      lines: [{ product_id: 11, qty: 1 }], quote_hash: 'h1', pickup_code: 'ABCDEFGH', promo_codes: ['GAJIAN'],
    });
    expect((ctx.calls[2].body as { order: Record<string, unknown> }).order.promo_codes).toEqual(['GAJIAN']);
  });

  it('kanal berhifen: bearer dari ODOO_API_KEY_ONLINE_RTL_GL_FILE, tidak pernah key kanal lain', async () => {
    const glKey = randomSecret();
    const config = testConfig({ ODOO_API_KEY_ONLINE_RTL_FILE: '/k/rtl', ODOO_API_KEY_ONLINE_RTL_GL_FILE: '/k/rtl-gl' }, (p) => {
      if (p === '/k/rtl') return Buffer.from(RTL_KEY);
      if (p === '/k/rtl-gl') return Buffer.from(glKey);
      throw new Error('ENOENT');
    });
    const seen: string[] = [];
    const transport: Json2Transport = async (_path, _body, headers) => {
      seen.push(headers.Authorization);
      return ok(STATUS_ROW);
    };
    const client = new Json2OdooOnlineClient(config, transport, new OnlineMetrics(new MetricsService()));
    await client.status('RTL-GL', ['x']);
    await client.status('RTL', ['x']);
    expect(seen).toEqual([`bearer ${glKey}`, `bearer ${RTL_KEY}`]);
    await expect(client.status('RTL-BACHA', ['x'])).rejects.toMatchObject({ kind: 'auth' }); // tanpa key → tidak memakai key lain
    expect(seen).toHaveLength(2);
  });

  it('parse: baris reward negatif diterima (reward:true, field lain dibuang), promo_applied dinormalisasi huruf besar', () => {
    const q = parseQuote(QUOTE_OK);
    expect(q.lines[1]).toEqual({ product_id: 9001, name: 'Promo Gajian', qty: 1, price_unit_incl: -5_000, subtotal_incl: -5_000, tax_labels: ['PPN 11%'], available: true, reward: true });
    expect(q.lines[0].reward).toBe(false);
    expect(q.promo_applied).toEqual(['GAJIAN']);
    expect(q.promo_rejected).toEqual(['NOPE42']);
    // Odoo lama tanpa field promo → [] (semua kode dianggap tidak terpakai)
    const legacy = parseQuote({ ...QUOTE_OK, promo_applied: undefined, promo_rejected: false, lines: [QUOTE_OK.lines[0]] });
    expect([legacy.promo_applied, legacy.promo_rejected]).toEqual([[], []]);
  });

  it('parse: bentuk salah → invalid_response (reward positif, harga negatif tanpa reward, kode berbentuk salah, applied ∩ rejected)', () => {
    const bad: Record<string, unknown>[] = [
      { ...QUOTE_OK, lines: [QUOTE_OK.lines[0], { ...QUOTE_OK.lines[1], subtotal_incl: 5_000, price_unit_incl: 5_000 }] },
      { ...QUOTE_OK, lines: [{ ...QUOTE_OK.lines[0], subtotal_incl: -1 }] },
      { ...QUOTE_OK, lines: [{ ...QUOTE_OK.lines[1], reward: 'yes' }] },
      { ...QUOTE_OK, promo_applied: ['KODE BOCOR'] },
      { ...QUOTE_OK, promo_applied: 'GAJIAN' },
      { ...QUOTE_OK, promo_applied: ['GAJIAN'], promo_rejected: ['gajian'] },
      { ...QUOTE_OK, promo_rejected: ['A'.repeat(41)] },
    ];
    for (const b of bad) expect(() => parseQuote(b)).toThrow(OdooRpcError);
  });
});

describe('SEC-96/SEC-109: batas konkurensi json2 per pool (publik vs saga)', () => {
  function cfg(pub: string, saga: string) {
    return testConfig({ ODOO_API_KEY_ONLINE_RTL_FILE: '/k/rtl', ODOO_RPC_PUBLIC_MAX_CONCURRENCY: pub, ODOO_RPC_SAGA_MAX_CONCURRENCY: saga }, (path) => {
      if (path === '/k/rtl') return Buffer.from(RTL_KEY);
      throw new Error('ENOENT');
    });
  }
  const tick = () => new Promise((res) => setImmediate(res));

  it('default 2 + 2; pool penuh → busy (503), tidak membuka circuit', async () => {
    expect(testConfig().odooRpcPublicMaxConcurrency).toBe(2);
    expect(testConfig().odooRpcSagaMaxConcurrency).toBe(2);
    let inFlight = 0;
    let peak = 0;
    const waiters: (() => void)[] = [];
    const transport: Json2Transport = async () => {
      inFlight += 1;
      peak = Math.max(peak, inFlight);
      await new Promise<void>((res) => waiters.push(res));
      inFlight -= 1;
      return ok(STATUS_ROW);
    };
    const client = new Json2OdooOnlineClient(cfg('1', '1'), transport, new OnlineMetrics(new MetricsService()));
    const calls = Array.from({ length: 10 }, () => client.status('RTL', ['x']).then(() => 'ok', (e: unknown) => (e as OdooRpcError).kind));
    await tick();
    for (let k = 0; k < 20; k += 1) {
      waiters.shift()?.();
      await tick();
    }
    const out = await Promise.all(calls);
    expect(peak).toBe(1);
    expect(out.filter((x) => x === 'busy')).toHaveLength(1); // 1 jalan + 8 antre + 1 ditolak
    expect(client.isCircuitOpen('RTL')).toBe(false);
  });

  it('SEC-109: lonjakan stok/quote (pool publik penuh) tidak memblokir saga (mark_paid/status)', async () => {
    const hold: (() => void)[] = [];
    const transport: Json2Transport = async (path) => {
      if (path.endsWith('efn_online_stock')) await new Promise<void>((res) => hold.push(res));
      return path.endsWith('efn_online_stock') ? ok([]) : ok(STATUS_ROW);
    };
    const client = new Json2OdooOnlineClient(cfg('1', '1'), transport, new OnlineMetrics(new MetricsService()));
    const stocks = Array.from({ length: 3 }, () => client.stock('RTL', 'TOKO-01', [1]).catch(() => 'x'));
    await tick();
    expect(await client.status('RTL', ['x'])).toHaveLength(1); // saga tetap jalan
    for (let k = 0; k < 10; k += 1) {
      hold.splice(0).forEach((h) => h());
      await tick();
    }
    await Promise.all(stocks);
  });

  it('SEC-109: slot dilepas selama backoff retry — panggilan lain jalan di sela', async () => {
    const order: string[] = [];
    let first = true;
    const transport: Json2Transport = async (_path, body) => {
      const ref = (JSON.parse(body) as { platform_order_refs: string[] }).platform_order_refs[0];
      order.push(ref);
      if (ref === 'A' && first) {
        first = false;
        return { status: 503, body: '' };
      }
      return ok(STATUS_ROW);
    };
    const client = new Json2OdooOnlineClient(cfg('1', '1'), transport, new OnlineMetrics(new MetricsService()));
    let wake: (() => void) | null = null;
    client.sleep = () => new Promise<void>((res) => (wake = res));
    client.random = () => 0;
    const a = client.status('RTL', ['A']);
    await tick();
    expect(order).toEqual(['A']); // A gagal, sedang backoff
    await client.status('RTL', ['B']); // slot saga (1) bebas selama backoff A
    expect(order).toEqual(['A', 'B']);
    (wake as unknown as () => void)();
    await a;
    expect(order).toEqual(['A', 'B', 'A']);
  });
});
