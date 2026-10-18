import { createHash } from 'node:crypto';
import { Logger, RawBodyRequest } from '@nestjs/common';
import { FastifyReply, FastifyRequest } from 'fastify';
import { MetricsService } from '../common/metrics/metrics.service';
import { RateLimiter } from '../common/rate-limit/rate-limiter';
import { FakeKvStore, catchApiError, testConfig } from '../common/testing/fakes';
import { CatalogService, ChannelInfo, ProductSummary } from '../catalog/catalog.service';
import { MockOdooOnline } from '../odoo-rpc/odoo-online.mock';
import { OnlineMetrics } from '../odoo-rpc/online-metrics';
import * as PromoModule from '../odoo-rpc/promo-codes';
import { MemoryCartStore } from './cart-store';
import { CartController, parseQuoteBody } from './cart.controller';
import { CartService } from './cart.service';
import { QuoteService, StoredQuote, placeLines, quoteKey } from './quote.service';

const { PromoCodesInvalid, normalizePromoCodes } = PromoModule;

/** G19 docs/04 §11.2 — promo online di quote (BFF). Semua Odoo = stub `MockOdooOnline` (promo demo DEMO5K / DEMOGL10). */

const M = '8d7b5a1e-2c3f-4a5b-9c8d-7e6f5a4b3c2d';
const M2 = '00000000-0000-4000-8000-000000000002';

function channel(code: string, brand: string | null): ChannelInfo {
  return {
    channel_code: code,
    company_code: 'RTL',
    brand_code: brand,
    name: code,
    fulfilment: { pickup: true, delivery: false },
    outlets: [{ outlet_code: 'TOKO-01', public_name: 'Toko 1', pickup_enabled: true, prep_minutes: 10 }],
  };
}
const CHANNELS = [channel('RTL', null), channel('RTL-GL', 'GL')];
const catalog = {
  channel: async (c: string) => CHANNELS.find((x) => x.channel_code === c) ?? null,
  productsByIds: async (_c: string, ids: readonly number[]) =>
    new Map(
      ids
        .filter((i) => i >= 11 && i <= 14)
        .map((i): [number, ProductSummary] => [i, { product_id: i, name: `P${i}`, uom: '', category_id: 1, image_sha: null, price_incl: 1, price_excl: 1, tax_labels: [], max_qty: 20 }]),
    ),
} as unknown as CatalogService;

function setup(over: Record<string, string> = {}) {
  const config = testConfig({ ONLINE_ENABLED: 'true', ONLINE_CHANNELS: 'RTL,RTL-GL', ...over });
  const cart = new CartService(config, new MemoryCartStore(), catalog);
  const kv = new FakeKvStore();
  const odoo = new MockOdooOnline();
  const metrics = new OnlineMetrics(new MetricsService());
  const quotes = new QuoteService(config, cart, catalog, odoo, kv, metrics);
  const limiter = new RateLimiter(kv);
  limiter.now = () => Date.UTC(2026, 9, 18, 3, 0, 1); // waktu tetap: tidak melintasi batas window di tengah test
  const controller = new CartController(config, cart, quotes, limiter);
  const put = (groups: { channel: string; lines: { product_id: number; qty: number }[] }[], member = M) =>
    cart.put(member, { version: 0, groups: groups.map((g) => ({ channel_code: g.channel, outlet_code: 'TOKO-01', fulfilment: 'pickup' as const, lines: g.lines })) });
  return { config, cart, kv, odoo, metrics, quotes, limiter, controller, put };
}

async function promoMetric(metrics: OnlineMetrics, result: string): Promise<number> {
  return (await metrics.promoCodes.get()).values.find((v) => v.labels.result === result)?.value ?? 0;
}

describe('Body quote promo_codes (§11.2 no. 1)', () => {
  it('opsional; dinormalisasi huruf besar, duplikat dibuang; {} / tanpa body tetap sah', () => {
    expect(parseQuoteBody(undefined)).toEqual([]);
    expect(parseQuoteBody(null)).toEqual([]);
    expect(parseQuoteBody({})).toEqual([]);
    expect(parseQuoteBody({ promo_codes: [] })).toEqual([]);
    expect(parseQuoteBody({ promo_codes: null })).toEqual([]);
    expect(parseQuoteBody({ promo_codes: ['demo5k', 'DEMO5K', 'x_1-b'] })).toEqual(['DEMO5K', 'X_1-B']);
    expect(parseQuoteBody({ promo_codes: ['A'.repeat(40)] })).toEqual(['A'.repeat(40)]);
  });

  it('format salah → 400 invalid_request (maks 3, regex, tipe, kunci lain)', async () => {
    const bad: unknown[] = [
      { promo_codes: ['A', 'B', 'C', 'D'] },
      { promo_codes: ['A', 'a', 'B', 'C'] }, // > 3 elemen sebelum dedupe tetap ditolak
      { promo_codes: 'DEMO5K' },
      { promo_codes: [5] },
      { promo_codes: [''] },
      { promo_codes: ['DEMO 5K'] },
      { promo_codes: ['DEMO5K;DROP'] },
      { promo_codes: ['A'.repeat(41)] },
      { promo_codes: ['KODÉ'] },
      { promo_codes: [null] },
      { promo_codes: ['DEMO5K'], total: 1 },
      { lines: [] },
      [],
      'DEMO5K',
    ];
    for (const b of bad) {
      const err = await catchApiError(() => parseQuoteBody(b));
      expect(err.getStatus()).toBe(400);
      expect(err.body).toEqual({ error: { code: 'invalid_request', message: expect.any(String) } });
      expect(JSON.stringify(err.body)).not.toMatch(/DEMO|DROP|KOD/);
    }
    expect(() => normalizePromoCodes({})).toThrow(PromoCodesInvalid);
  });

  it('SEC-132: modul promo-codes tidak lagi mengekspor tag hash kode (promoCodeTag dihapus)', () => {
    expect((PromoModule as unknown as Record<string, unknown>).promoCodeTag).toBeUndefined();
  });
});

describe('Quote berpromo (§11.2 no. 2–5)', () => {
  it('kode terpakai: baris reward `reward: true` diteruskan apa adanya, total turun, promo.applied; oq menyimpan promo_codes + promo_applied grup', async () => {
    const ctx = setup();
    await ctx.put([{ channel: 'RTL', lines: [{ product_id: 11, qty: 1 }] }]);
    const q = await ctx.quotes.quote(M, ['DEMO5K']);
    expect(q.promo).toEqual({ applied: ['DEMO5K'], invalid: [] });
    const g = q.groups[0];
    expect(g.lines).toEqual([
      { product_id: 11, name: 'Beras Premium 5 kg', qty: 1, price_unit_incl: 79_900, subtotal_incl: 79_900, tax_labels: ['PPN (demo)'], available: true },
      { product_id: 9001, name: 'Promo DEMO5K (demo)', qty: 1, price_unit_incl: -5_000, subtotal_incl: -5_000, tax_labels: ['PPN (demo)'], available: true, reward: true },
    ]);
    expect(g.amount_payable).toBe(74_900);
    expect(q.amount_payable_total).toBe(74_900);
    // tidak ada data internal ke klien
    expect(JSON.stringify(q)).not.toMatch(/quote_hash|promo_applied|promo_rejected/);
    const stored = JSON.parse((await ctx.kv.get(quoteKey(q.quote_id)))!) as StoredQuote;
    expect(stored.promo_codes).toEqual(['DEMO5K']);
    expect(stored.groups[0].promo_applied).toEqual(['DEMO5K']);
    // place hanya mengirim baris produk cart (tanpa reward)
    expect(placeLines(stored.groups[0])).toEqual([{ product_id: 11, qty: 1 }]);
    expect(await promoMetric(ctx.metrics, 'applied')).toBe(1);
    expect(await promoMetric(ctx.metrics, 'invalid')).toBe(0);
  });

  it('tanpa kode: promo {applied:[], invalid:[]}, Odoo tetap menerima promo_codes [] (eksplisit), tanpa metrik promo', async () => {
    const ctx = setup();
    await ctx.put([{ channel: 'RTL', lines: [{ product_id: 11, qty: 2 }] }]);
    const q = await ctx.quotes.quote(M);
    expect(q.promo).toEqual({ applied: [], invalid: [] });
    expect(ctx.odoo.quoteArgs).toEqual([{ channel: 'RTL', promoCodes: [] }]);
    expect(q.groups[0].lines.every((l) => l.reward === undefined)).toBe(true);
    expect((await ctx.metrics.promoCodes.get()).values).toEqual([]);
  });

  it('multi-kanal: kode yang sama dikirim ke SETIAP kanal; invalid = tidak terpakai di grup MANA PUN', async () => {
    const ctx = setup();
    await ctx.put([
      { channel: 'RTL', lines: [{ product_id: 11, qty: 1 }] },
      { channel: 'RTL-GL', lines: [{ product_id: 12, qty: 2 }] },
    ]);
    const q = await ctx.quotes.quote(M, ['DEMOGL10', 'NOPE42']);
    expect(ctx.odoo.quoteArgs.map((a) => a.channel).sort()).toEqual(['RTL', 'RTL-GL']);
    for (const a of ctx.odoo.quoteArgs) expect(a.promoCodes).toEqual(['DEMOGL10', 'NOPE42']);
    // DEMOGL10 hanya berlaku di kanal brand GL → tetap "applied" (terpakai di ≥ 1 grup)
    expect(q.promo).toEqual({ applied: ['DEMOGL10'], invalid: ['NOPE42'] });
    const rtl = q.groups.find((g) => g.channel_code === 'RTL')!;
    const gl = q.groups.find((g) => g.channel_code === 'RTL-GL')!;
    expect(rtl.lines.some((l) => l.reward)).toBe(false);
    expect(gl.lines.filter((l) => l.reward)).toEqual([expect.objectContaining({ subtotal_incl: -7_700, reward: true })]);
    const stored = JSON.parse((await ctx.kv.get(quoteKey(q.quote_id)))!) as StoredQuote;
    expect(stored.groups.find((g) => g.channel_code === 'RTL')!.promo_applied).toEqual([]);
    expect(stored.groups.find((g) => g.channel_code === 'RTL-GL')!.promo_applied).toEqual(['DEMOGL10']);
    expect(await promoMetric(ctx.metrics, 'applied')).toBe(1);
    expect(await promoMetric(ctx.metrics, 'invalid')).toBe(1);
  });

  it('anti-enumerasi: kode tak dikenal vs kedaluwarsa vs bukan brand ini → respons IDENTIK (selain teks kode), bukan issues, checkout tidak diblokir', async () => {
    const run = async (code: string, prep: (o: MockOdooOnline) => void) => {
      const ctx = setup();
      prep(ctx.odoo);
      await ctx.put([{ channel: 'RTL', lines: [{ product_id: 11, qty: 1 }] }]);
      const q = await ctx.quotes.quote(M, [code]);
      return JSON.parse(JSON.stringify({ ...q, quote_id: '<id>', valid_until: '<t>' }).split(code).join('<KODE>'));
    };
    const unknown = await run('NOPE42', () => undefined);
    const expired = await run('DEMO5K', (o) => o.expiredPromos.add('DEMO5K'));
    const otherBrand = await run('DEMOGL10', () => undefined); // kanal RTL bukan brand GL
    expect(expired).toEqual(unknown);
    expect(otherBrand).toEqual(unknown);
    expect(unknown.promo).toEqual({ applied: [], invalid: ['<KODE>'] });
    expect(unknown.groups[0].issues).toEqual([]);
    expect(Object.keys(unknown.promo)).toEqual(['applied', 'invalid']);
  });

  it('SEC-132: kode promo maupun hash/tag-nya tidak pernah muncul di log BFF (hanya jumlah)', async () => {
    const logs: string[] = [];
    const capture = (msg: unknown): void => void logs.push(String(msg));
    const spies = [
      jest.spyOn(Logger.prototype, 'log').mockImplementation(capture),
      jest.spyOn(Logger.prototype, 'warn').mockImplementation(capture),
      jest.spyOn(Logger.prototype, 'error').mockImplementation(capture),
      jest.spyOn(Logger.prototype, 'debug').mockImplementation(capture),
      jest.spyOn(Logger.prototype, 'verbose').mockImplementation(capture),
    ];
    try {
      const ctx = setup();
      await ctx.put([{ channel: 'RTL', lines: [{ product_id: 11, qty: 1 }] }]);
      await ctx.quotes.quote(M, ['DEMO5K', 'RAHASIA99']);
      jest.spyOn(ctx.odoo, 'quote').mockResolvedValueOnce({
        company_code: 'RTL', lines: [], tax_groups: [], amount_total: 0, amount_payable: 0, quote_hash: 'h', valid_until: null,
        issues: [{ product_id: 11, code: 'out_of_stock' }], promo_applied: ['BOCOR77'], promo_rejected: [],
      });
      await catchApiError(() => ctx.quotes.quote(M, ['RAHASIA99']));
    } finally {
      spies.forEach((s) => s.mockRestore());
    }
    const all = logs.join('\n');
    expect(all).not.toMatch(/DEMO5K|RAHASIA99|BOCOR77/i);
    // turunan kode (SHA-256 penuh / 8 heks pertama, huruf besar & kecil) juga tidak boleh ada
    for (const code of ['DEMO5K', 'RAHASIA99', 'BOCOR77', 'demo5k', 'rahasia99']) {
      const hex = createHash('sha256').update(code, 'utf8').digest('hex');
      expect(all).not.toContain(hex.slice(0, 8));
      expect(all).not.toContain(hex);
    }
    // log debug hanya memuat jumlah
    expect(all).toContain('Quote promo: terpakai 1, tidak terpakai 1');
  });

  it('Odoo melaporkan kode yang TIDAK diminta → 503 online_unavailable (fail-closed) + anomali online_config_error', async () => {
    const ctx = setup();
    await ctx.put([{ channel: 'RTL', lines: [{ product_id: 11, qty: 1 }] }]);
    const warn = jest.spyOn(Logger.prototype, 'warn').mockImplementation(() => undefined);
    try {
      jest.spyOn(ctx.odoo, 'quote').mockResolvedValueOnce({
        company_code: 'RTL',
        lines: [{ product_id: 11, name: 'x', qty: 1, price_unit_incl: 1, subtotal_incl: 1, tax_labels: [], available: true, reward: false }],
        tax_groups: [], amount_total: 1, amount_payable: 1, quote_hash: 'h', valid_until: null, issues: [],
        promo_applied: ['LAIN'], promo_rejected: [],
      });
      expect((await catchApiError(() => ctx.quotes.quote(M, ['DEMO5K']))).body).toMatchObject({ error: { code: 'online_unavailable' } });
      expect(warn.mock.calls.some((c) => String(c[0]).includes('online_config_error'))).toBe(true);
    } finally {
      warn.mockRestore();
    }
  });

  it('baris reward boleh memakai produk cart (buy_x_get_y) tanpa merusak validasi baris; baris produk tetap harus = cart', async () => {
    const ctx = setup();
    await ctx.put([{ channel: 'RTL', lines: [{ product_id: 11, qty: 2 }] }]);
    const base = {
      company_code: 'RTL', tax_groups: [], amount_total: 79_900, amount_payable: 79_900, quote_hash: 'h', valid_until: null, issues: [],
      promo_applied: [], promo_rejected: [],
    };
    jest.spyOn(ctx.odoo, 'quote').mockResolvedValueOnce({
      ...base,
      lines: [
        { product_id: 11, name: 'Beras', qty: 2, price_unit_incl: 79_900, subtotal_incl: 159_800, tax_labels: [], available: true, reward: false },
        { product_id: 11, name: 'Beli 1 gratis 1', qty: 1, price_unit_incl: -79_900, subtotal_incl: -79_900, tax_labels: [], available: true, reward: true },
      ],
    });
    const q = await ctx.quotes.quote(M);
    expect(q.groups[0].lines.map((l) => [l.product_id, l.reward ?? false])).toEqual([[11, false], [11, true]]);
    // baris produk ≠ cart (qty beda) tetap ditolak walau ada reward
    jest.spyOn(ctx.odoo, 'quote').mockResolvedValueOnce({
      ...base,
      lines: [{ product_id: 11, name: 'Beras', qty: 1, price_unit_incl: 79_900, subtotal_incl: 79_900, tax_labels: [], available: true, reward: false }],
    });
    expect((await catchApiError(() => ctx.quotes.quote(M))).getStatus()).toBe(503);
  });
});

describe('Rate limit promo terpisah (§11.2 no. 6)', () => {
  const reply = { header: jest.fn().mockReturnThis() } as unknown as FastifyReply;
  const req = (ip: string) => ({ ip, headers: {}, rawBody: Buffer.from('{}') }) as unknown as RawBodyRequest<FastifyRequest>;
  const user = (memberId: string) => ({ memberId, memberHash: 'h'.repeat(64) });

  it('default PROMO_MEMBER 10 / 15 menit, PROMO_IP 30 / 15 menit (configurable)', () => {
    const c = testConfig();
    expect(c.onlineRateLimit('PROMO_MEMBER')).toEqual({ limit: 10, windowSeconds: 900 });
    expect(c.onlineRateLimit('PROMO_IP')).toEqual({ limit: 30, windowSeconds: 900 });
    const o = testConfig({ ONLINE_RL_PROMO_MEMBER_LIMIT: '4', ONLINE_RL_PROMO_IP_WINDOW_SECONDS: '60' });
    expect(o.onlineRateLimit('PROMO_MEMBER')).toEqual({ limit: 4, windowSeconds: 900 });
    expect(o.onlineRateLimit('PROMO_IP')).toEqual({ limit: 30, windowSeconds: 60 });
  });

  it('per member: quote berpromo ke-3 → 429 rate_limited; quote tanpa promo tidak memakan kuota promo', async () => {
    const ctx = setup({ ONLINE_RL_PROMO_MEMBER_LIMIT: '2' });
    await ctx.put([{ channel: 'RTL', lines: [{ product_id: 11, qty: 1 }] }]);
    for (let i = 0; i < 3; i += 1) await ctx.controller.quote({}, user(M), req('203.0.113.5'), reply); // tanpa promo
    await ctx.controller.quote({ promo_codes: ['NOPE1'] }, user(M), req('203.0.113.5'), reply);
    await ctx.controller.quote({ promo_codes: ['nope2'] }, user(M), req('203.0.113.5'), reply);
    const err = await catchApiError(() => ctx.controller.quote({ promo_codes: ['NOPE3'] }, user(M), req('203.0.113.5'), reply));
    expect(err.getStatus()).toBe(429);
    expect(err.body).toMatchObject({ error: { code: 'rate_limited' } });
    expect(ctx.odoo.quoteArgs.filter((a) => a.promoCodes.length > 0)).toHaveLength(2); // yang ditolak tidak sampai Odoo
    // quote tanpa promo tetap jalan (batas QUOTE terpisah)
    expect((await ctx.controller.quote({}, user(M), req('203.0.113.5'), reply)).promo).toEqual({ applied: [], invalid: [] });
    // kunci Redis memakai HMAC IP, tidak memuat IP mentah
    expect(ctx.kv.keys().some((k) => k.includes('203.0.113.5'))).toBe(false);
  });

  it('per IP: banyak member dari satu IP → 429 setelah ONLINE_RL_PROMO_IP; IP lain tidak terdampak', async () => {
    const ctx = setup({ ONLINE_RL_PROMO_IP_LIMIT: '2' });
    await ctx.put([{ channel: 'RTL', lines: [{ product_id: 11, qty: 1 }] }]);
    await ctx.put([{ channel: 'RTL', lines: [{ product_id: 11, qty: 1 }] }], M2);
    await ctx.controller.quote({ promo_codes: ['A1'] }, user(M), req('198.51.100.7'), reply);
    await ctx.controller.quote({ promo_codes: ['A2'] }, user(M2), req('198.51.100.7'), reply);
    expect((await catchApiError(() => ctx.controller.quote({ promo_codes: ['A3'] }, user(M2), req('198.51.100.7'), reply))).getStatus()).toBe(429);
    expect((await ctx.controller.quote({ promo_codes: ['A3'] }, user(M2), req('198.51.100.8'), reply)).promo.invalid).toEqual(['A3']);
  });

  it('SEC-134: kuota member dihitung per KODE — 3 kode = 3 unit; default 10 → quote ke-4 berisi 3 kode ditolak, 1 kode masih boleh', async () => {
    const ctx = setup();
    await ctx.put([{ channel: 'RTL', lines: [{ product_id: 11, qty: 1 }] }]);
    const ip = (n: number) => req(`203.0.113.${n}`); // IP berbeda per quote: hanya limiter member yang diuji
    for (let i = 0; i < 3; i += 1) {
      await ctx.controller.quote({ promo_codes: [`A${i}`, `B${i}`, `C${i}`] }, user(M), ip(i), reply); // 9 unit
    }
    const err = await catchApiError(() => ctx.controller.quote({ promo_codes: ['X1', 'X2', 'X3'] }, user(M), ip(10), reply)); // 12 > 10
    expect(err.getStatus()).toBe(429);
    expect(err.body).toMatchObject({ error: { code: 'rate_limited' } });
    // permintaan yang ditolak tidak memakan kuota: sisa tepat 1 kode
    await ctx.controller.quote({ promo_codes: ['Y1'] }, user(M), ip(11), reply);
    expect((await catchApiError(() => ctx.controller.quote({ promo_codes: ['Z1'] }, user(M), ip(12), reply))).getStatus()).toBe(429);
    // total kode yang sampai Odoo = 10 = ONLINE_RL_PROMO_MEMBER_LIMIT
    expect(ctx.odoo.quoteArgs.reduce((s, a) => s + a.promoCodes.length, 0)).toBe(10);
    // member lain tidak terdampak
    await ctx.put([{ channel: 'RTL', lines: [{ product_id: 11, qty: 1 }] }], M2);
    expect((await ctx.controller.quote({ promo_codes: ['Q1', 'Q2', 'Q3'] }, user(M2), ip(13), reply)).promo.invalid).toEqual(['Q1', 'Q2', 'Q3']);
  });

  it('SEC-134: kuota IP dihitung per KODE (default 30) lintas member; IP menolak → unit member dikembalikan', async () => {
    const ctx = setup({ ONLINE_RL_PROMO_MEMBER_LIMIT: '100', ONLINE_RL_QUOTE_LIMIT: '100' });
    const members = Array.from({ length: 11 }, (_, i) => `00000000-0000-4000-8000-0000000001${String(i).padStart(2, '0')}`);
    for (const m of members) await ctx.put([{ channel: 'RTL', lines: [{ product_id: 11, qty: 1 }] }], m);
    const shared = req('198.51.100.9');
    for (let i = 0; i < 10; i += 1) {
      await ctx.controller.quote({ promo_codes: [`K${i}A`, `K${i}B`, `K${i}C`] }, user(members[i]), shared, reply); // 30 unit IP
    }
    const err = await catchApiError(() => ctx.controller.quote({ promo_codes: ['L1'] }, user(members[10]), shared, reply));
    expect(err.getStatus()).toBe(429);
    expect(ctx.odoo.quoteArgs.reduce((s, a) => s + a.promoCodes.length, 0)).toBe(30);
    // unit member yang sempat dikonsumsi dikembalikan: member ke-11 tetap punya kuota penuh dari IP lain
    const memberCounter = ctx.kv.keys().filter((k) => k.includes(`promo_member:m:${members[10]}`));
    for (const k of memberCounter) expect(Number((await ctx.kv.get(k)) ?? '0')).toBe(0);
    expect((await ctx.controller.quote({ promo_codes: ['L1', 'L2', 'L3'] }, user(members[10]), req('198.51.100.10'), reply)).promo.invalid).toEqual(['L1', 'L2', 'L3']);
  });

  it('SEC-134: duplikat kode (huruf besar/kecil) dihitung sekali; quote tanpa kode = 0 unit', async () => {
    const ctx = setup({ ONLINE_RL_PROMO_MEMBER_LIMIT: '2' });
    await ctx.put([{ channel: 'RTL', lines: [{ product_id: 11, qty: 1 }] }]);
    await ctx.controller.quote({}, user(M), req('203.0.113.5'), reply);
    await ctx.controller.quote({ promo_codes: ['dup', 'DUP'] }, user(M), req('203.0.113.5'), reply); // 1 unit
    await ctx.controller.quote({ promo_codes: ['ONE'] }, user(M), req('203.0.113.5'), reply); // 2 unit
    expect((await catchApiError(() => ctx.controller.quote({ promo_codes: ['TWO'] }, user(M), req('203.0.113.5'), reply))).getStatus()).toBe(429);
  });

  it('format salah ditolak 400 invalid_request SEBELUM rate limit/Odoo', async () => {
    const ctx = setup();
    await ctx.put([{ channel: 'RTL', lines: [{ product_id: 11, qty: 1 }] }]);
    const err = await catchApiError(() => ctx.controller.quote({ promo_codes: ['A', 'B', 'C', 'D'] }, user(M), req('203.0.113.5'), reply));
    expect(err.body).toMatchObject({ error: { code: 'invalid_request' } });
    expect(ctx.odoo.quoteArgs).toHaveLength(0);
  });
});
