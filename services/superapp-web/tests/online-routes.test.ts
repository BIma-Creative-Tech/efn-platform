/**
 * Route Handler belanja online (G17): proxy katalog/cart/orders ke BFF (di-mock — BFF belum ada),
 * Idempotency-Key server-side, no-store, guard CSRF/lintas situs, peta error K-ON5.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { NextRequest } from 'next/server';

const { bffFetch, bffBaseUrl } = vi.hoisted(() => ({ bffFetch: vi.fn(), bffBaseUrl: vi.fn() }));
vi.mock('server-only', () => ({}));
vi.mock('@/lib/bff', () => ({ bffFetch, bffBaseUrl }));

import { GET as getChannels } from '@/app/api/catalog/channels/route';
import { GET as getProducts } from '@/app/api/catalog/products/route';
import { GET as getProduct } from '@/app/api/catalog/products/[id]/route';
import { GET as getImage } from '@/app/api/catalog/images/[sha]/route';
import { GET as getStock } from '@/app/api/catalog/stock/route';
import { DELETE as deleteCart, GET as getCart, PUT as putCart } from '@/app/api/cart/route';
import { POST as postQuote } from '@/app/api/cart/quote/route';
import { GET as listOrders, POST as postOrder } from '@/app/api/orders/route';
import { GET as getOrder } from '@/app/api/orders/[id]/route';
import { POST as cancelOrder } from '@/app/api/orders/[id]/cancel/route';
import { GET as getPickup } from '@/app/api/orders/[id]/pickup-code/route';
import { POST as simulate } from '@/app/api/orders/[id]/simulate-payment/route';
import { limitStream, MAX_IMAGE_BYTES } from '@/lib/catalog-route';
import { checkoutIdempotencyKey, IDEMPOTENCY_KEY_RE } from '@/lib/orders-route';
import { paymentSimulatorEnabled } from '@/lib/orders-server';

const HOST = 'app.efn.example';
const ID = '3f2b8c1e-9a4d-4c7e-8b21-5d6e7f8a9b0c';
const QUOTE_ID = '9b1d2c3e-4f5a-4b6c-8d7e-0f1a2b3c4d5e';
const SHA = 'c'.repeat(64);

function req(path: string, init: { method?: string; body?: unknown; cookie?: string | null; origin?: string | null; site?: string } = {}) {
  const headers: Record<string, string> = { host: HOST };
  if (init.cookie !== null) headers.cookie = init.cookie ?? 'efn_session=tok';
  if (init.origin !== null && init.method && init.method !== 'GET') headers.origin = init.origin ?? `https://${HOST}`;
  if (init.site) headers['sec-fetch-site'] = init.site;
  if (init.body !== undefined) headers['content-type'] = 'application/json';
  return new NextRequest(`https://${HOST}${path}`, {
    method: init.method ?? 'GET',
    headers,
    body: init.body === undefined ? undefined : typeof init.body === 'string' ? init.body : JSON.stringify(init.body),
  });
}
const params = <T extends Record<string, string>>(p: T) => ({ params: Promise.resolve(p) });
const fail = (status: number, code: string) => ({ ok: false, status, code, message: 'pesan internal BFF', body: null });

beforeEach(() => {
  bffFetch.mockReset();
  bffBaseUrl.mockReset();
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('katalog publik', () => {
  it('channels: tanpa Bearer, allowlist, cache publik 60 dtk', async () => {
    bffFetch.mockResolvedValue({
      ok: true,
      status: 200,
      data: { items: [{ channel_code: 'RTL', company_code: 'RTL', name: 'Supermarket', fulfilment: { pickup: true }, outlets: [], secret: 1 }] },
    });
    const res = await getChannels(req('/api/catalog/channels'));
    expect(res.status).toBe(200);
    expect(res.headers.get('cache-control')).toBe('public, max-age=60');
    expect(JSON.stringify(await res.json())).not.toContain('secret');
    const [path, opts] = bffFetch.mock.calls[0]!;
    expect(path).toBe('/v1/catalog/channels');
    expect(opts.token).toBeUndefined();
  });
  it('products: query dibangun ulang; parameter sisipan tidak diteruskan', async () => {
    bffFetch.mockResolvedValue({ ok: true, status: 200, data: { items: [], next_cursor: null, catalog_synced_at: null } });
    await getProducts(req('/api/catalog/products?channel=RTL&q=kopi&limit=999&member_id=x&cursor=a%26b'));
    expect(bffFetch.mock.calls[0]![0]).toBe('/v1/catalog/products?channel=RTL&q=kopi&limit=50');
    expect((await getProducts(req('/api/catalog/products?channel=rtl'))).status).toBe(400);
  });
  it('detail: id bukan integer -> 404 tanpa BFF; BFF 404 -> 404', async () => {
    expect((await getProduct(req('/api/catalog/products/abc?channel=RTL'), params({ id: 'abc' }))).status).toBe(404);
    expect(bffFetch).not.toHaveBeenCalled();
    bffFetch.mockResolvedValue(fail(404, 'not_found'));
    const res = await getProduct(req('/api/catalog/products/11?channel=RTL'), params({ id: '11' }));
    expect(res.status).toBe(404);
    expect((await res.json()).error.message).toBe('Data tidak ditemukan.');
  });
  it('kill switch katalog: 503 online_unavailable -> 503 pesan ramah', async () => {
    bffFetch.mockResolvedValue(fail(503, 'online_unavailable'));
    const res = await getChannels(req('/api/catalog/channels'));
    expect(res.status).toBe(503);
    const body = await res.json();
    expect(body.error.code).toBe('online_unavailable');
    expect(body.error.message).toMatch(/Belanja online sedang tidak tersedia/);
  });
  it('stok: no-store, validasi ids', async () => {
    bffFetch.mockResolvedValue({ ok: true, status: 200, data: { items: [{ product_id: 1, status: 'in_stock', qty: 9 }] } });
    const res = await getStock(req('/api/catalog/stock?channel=RTL&outlet=TOKO-01&ids=1,1,2'));
    expect(res.headers.get('cache-control')).toBe('no-store');
    expect(await res.json()).toEqual({ items: [{ product_id: 1, status: 'in_stock' }] });
    expect(bffFetch.mock.calls[0]![0]).toBe('/v1/catalog/stock?channel=RTL&outlet=TOKO-01&ids=1%2C2');
    expect((await getStock(req('/api/catalog/stock?channel=RTL&outlet=TOKO-01&ids=1,x'))).status).toBe(400);
  });
  it('gambar: stream image/jpeg dari BFF dengan cache immutable', async () => {
    bffBaseUrl.mockReturnValue('https://api-gateway:3000');
    const fetchMock = vi.fn().mockResolvedValue(new Response(new Uint8Array([0xff, 0xd8, 0xff]), { status: 200, headers: { 'content-type': 'image/jpeg' } }));
    vi.stubGlobal('fetch', fetchMock);
    const res = await getImage(req(`/api/catalog/images/${SHA}?size=1024`), params({ sha: SHA }));
    expect(res.status).toBe(200);
    expect(res.headers.get('content-type')).toBe('image/jpeg');
    expect(res.headers.get('cache-control')).toBe('public, max-age=31536000, immutable');
    expect(res.headers.get('x-content-type-options')).toBe('nosniff');
    expect(new Uint8Array(await res.arrayBuffer())).toEqual(new Uint8Array([0xff, 0xd8, 0xff]));
    expect(fetchMock.mock.calls[0]![0]).toBe(`https://api-gateway:3000/v1/catalog/images/${SHA}?size=1024`);
    expect(fetchMock.mock.calls[0]![1].redirect).toBe('manual');
  });
  it('gambar: sha tidak valid -> 404 tanpa fetch; tipe bukan jpeg -> 502', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response('<svg/>', { status: 200, headers: { 'content-type': 'image/svg+xml' } }));
    vi.stubGlobal('fetch', fetchMock);
    bffBaseUrl.mockReturnValue('https://api-gateway:3000');
    expect((await getImage(req('/api/catalog/images/..%2F..'), params({ sha: '../..' }))).status).toBe(404);
    expect(fetchMock).not.toHaveBeenCalled();
    expect((await getImage(req(`/api/catalog/images/${SHA}`), params({ sha: SHA }))).status).toBe(502);
  });
  it('gambar: content-length > 5 MB -> 502 sebelum stream', async () => {
    bffBaseUrl.mockReturnValue('https://api-gateway:3000');
    const body = new ReadableStream<Uint8Array>({ start: (c) => c.close() });
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(new Response(body, { status: 200, headers: { 'content-type': 'image/jpeg', 'content-length': String(MAX_IMAGE_BYTES + 1) } })),
    );
    expect((await getImage(req(`/api/catalog/images/${SHA}`), params({ sha: SHA }))).status).toBe(502);
  });
  it('gambar tanpa content-length: stream diputus setelah 5 MB, upstream dibatalkan', async () => {
    bffBaseUrl.mockReturnValue('https://api-gateway:3000');
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    const chunk = new Uint8Array(1024 * 1024).fill(0xff);
    let produced = 0;
    let cancelled = false;
    const upstream = new ReadableStream<Uint8Array>({
      pull(c) {
        produced += chunk.byteLength;
        c.enqueue(chunk); // tak berujung: hanya batas yang menghentikan
      },
      cancel() {
        cancelled = true;
      },
    });
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(upstream, { status: 200, headers: { 'content-type': 'image/jpeg' } })));
    const res = await getImage(req(`/api/catalog/images/${SHA}`), params({ sha: SHA }));
    expect(res.status).toBe(200);
    expect(res.headers.get('content-length')).toBeNull();
    await expect(res.arrayBuffer()).rejects.toThrow();
    expect(cancelled).toBe(true);
    expect(produced).toBeLessThanOrEqual(MAX_IMAGE_BYTES + 3 * chunk.byteLength);
    expect(warn).toHaveBeenCalled();
  });
  it('limitStream meneruskan utuh bila di bawah batas', async () => {
    const src = new Response(new Uint8Array(10).fill(7)).body!;
    const out = await new Response(limitStream(src, 10)).arrayBuffer();
    expect(out.byteLength).toBe(10);
    await expect(new Response(limitStream(new Response(new Uint8Array(11)).body!, 10)).arrayBuffer()).rejects.toThrow();
  });
  it('gambar: konfigurasi transport ditolak -> 503 (fail-closed)', async () => {
    bffBaseUrl.mockImplementation(() => {
      throw new Error('BFF_INTERNAL_URL wajib https://');
    });
    expect((await getImage(req(`/api/catalog/images/${SHA}`), params({ sha: SHA }))).status).toBe(503);
  });
});

describe('keranjang', () => {
  it('GET: Bearer dari cookie, no-store; tanpa sesi -> 401 tanpa BFF', async () => {
    bffFetch.mockResolvedValue({ ok: true, status: 200, data: { version: 0, groups: [] } });
    const res = await getCart(req('/api/cart'));
    expect(res.headers.get('cache-control')).toBe('no-store');
    expect(bffFetch.mock.calls[0]![1].token).toBe('tok');
    bffFetch.mockClear();
    expect((await getCart(req('/api/cart', { cookie: null }))).status).toBe(401);
    expect(bffFetch).not.toHaveBeenCalled();
  });
  it('GET lintas situs -> 403', async () => {
    expect((await getCart(req('/api/cart', { site: 'cross-site' }))).status).toBe(403);
    expect(bffFetch).not.toHaveBeenCalled();
  });
  it('PUT: harga dari klien dibuang sebelum ke BFF', async () => {
    bffFetch.mockResolvedValue({ ok: true, status: 200, data: { version: 2, groups: [], warnings: [] } });
    const body = {
      version: 1,
      amount_payable_total: 1,
      groups: [{ channel_code: 'RTL', outlet_code: 'TOKO-01', fulfilment: 'pickup', lines: [{ product_id: 11, qty: 2, price_unit_incl: 1 }] }],
    };
    const res = await putCart(req('/api/cart', { method: 'PUT', body }));
    expect(res.status).toBe(200);
    const [path, opts] = bffFetch.mock.calls[0]!;
    expect(path).toBe('/v1/cart');
    expect(opts.json).toEqual({ version: 1, groups: [{ channel_code: 'RTL', outlet_code: 'TOKO-01', fulfilment: 'pickup', lines: [{ product_id: 11, qty: 2 }] }] });
  });
  it('PUT: tanpa/lain Origin -> 403; body > 16 KB -> 413; JSON rusak -> 400', async () => {
    expect((await putCart(req('/api/cart', { method: 'PUT', body: { version: 0, groups: [] }, origin: null }))).status).toBe(403);
    expect((await putCart(req('/api/cart', { method: 'PUT', body: { version: 0, groups: [] }, origin: 'https://evil.example' }))).status).toBe(403);
    expect((await putCart(req('/api/cart', { method: 'PUT', body: 'x'.repeat(17 * 1024) }))).status).toBe(413);
    expect((await putCart(req('/api/cart', { method: 'PUT', body: '{rusak' }))).status).toBe(400);
    expect(bffFetch).not.toHaveBeenCalled();
  });
  it('409 cart_version_conflict diteruskan dengan pesan Indonesia; 401 menghapus cookie sesi', async () => {
    bffFetch.mockResolvedValueOnce(fail(409, 'cart_version_conflict'));
    const res = await putCart(req('/api/cart', { method: 'PUT', body: { version: 0, groups: [] } }));
    expect(res.status).toBe(409);
    const b = await res.json();
    expect(b.error.code).toBe('cart_version_conflict');
    expect(b.error.message).not.toBe('pesan internal BFF');
    bffFetch.mockResolvedValueOnce(fail(401, 'unauthenticated'));
    const r401 = await deleteCart(req('/api/cart', { method: 'DELETE' }));
    expect(r401.status).toBe(401);
    expect(r401.headers.getSetCookie().find((c) => c.startsWith('efn_session='))).toMatch(/Max-Age=0/i);
  });
  it('kill switch ONLINE_ENABLED=false: 503 online_unavailable', async () => {
    bffFetch.mockResolvedValue(fail(503, 'online_unavailable'));
    const res = await getCart(req('/api/cart'));
    expect(res.status).toBe(503);
    expect((await res.json()).error.message).toMatch(/tidak tersedia/);
  });
  it('quote: body klien diabaikan, quote_hash tidak diteruskan', async () => {
    bffFetch.mockResolvedValue({
      ok: true,
      status: 200,
      data: {
        quote_id: QUOTE_ID,
        valid_until: null,
        cart_version: 1,
        quote_hash: 'h',
        groups: [{ channel_code: 'RTL', company_code: 'RTL', outlet_code: 'TOKO-01', fulfilment: 'pickup', quote_hash: 'h', lines: [], tax_groups: [], amount_total: 0, amount_payable: 0, issues: [] }],
        amount_payable_total: 0,
      },
    });
    const res = await postQuote(req('/api/cart/quote', { method: 'POST', body: { amount_payable_total: 1 } }));
    expect(res.status).toBe(200);
    expect(res.headers.get('cache-control')).toBe('no-store');
    expect(JSON.stringify(await res.json())).not.toContain('quote_hash');
    expect(bffFetch.mock.calls[0]![1].json).toEqual({});
  });
});

describe('checkout & Idempotency-Key server-side', () => {
  it('key deterministik per quote, format kontrak 16–64 [A-Za-z0-9_-]', () => {
    const k = checkoutIdempotencyKey(QUOTE_ID);
    expect(k).toMatch(IDEMPOTENCY_KEY_RE);
    expect(k).toBe(checkoutIdempotencyKey(QUOTE_ID.toUpperCase()));
    expect(k).not.toBe(checkoutIdempotencyKey(ID));
    expect(k).not.toContain(QUOTE_ID);
  });
  it('POST meneruskan header Idempotency-Key buatan server; klik ganda = key sama', async () => {
    bffFetch.mockResolvedValue({ ok: true, status: 201, data: { order_id: ID, status: 'awaiting_payment', payment: { qr_string: 'q' } } });
    const body = { quote_id: QUOTE_ID, accept_total: 39800 };
    const r1 = await postOrder(req('/api/orders', { method: 'POST', body }));
    const r2 = await postOrder(req('/api/orders', { method: 'POST', body }));
    expect(r1.status).toBe(201);
    expect(r1.headers.get('cache-control')).toBe('no-store');
    expect(await r1.json()).toEqual({ order_id: ID, status: 'awaiting_payment' });
    const k1 = bffFetch.mock.calls[0]![1].headers['idempotency-key'];
    const k2 = bffFetch.mock.calls[1]![1].headers['idempotency-key'];
    expect(k1).toBe(checkoutIdempotencyKey(QUOTE_ID));
    expect(k1).toBe(k2);
    expect(bffFetch.mock.calls[0]![1].json).toEqual(body);
    expect(r2.status).toBe(201);
  });
  it('Idempotency-Key dari browser diabaikan', async () => {
    bffFetch.mockResolvedValue({ ok: true, status: 201, data: { order_id: ID, status: 'awaiting_payment' } });
    const r = new NextRequest(`https://${HOST}/api/orders`, {
      method: 'POST',
      headers: { host: HOST, origin: `https://${HOST}`, cookie: 'efn_session=tok', 'idempotency-key': 'dipilih-klien-1234567' }, // gitleaks:allow (fixture, bukan secret)
      body: JSON.stringify({ quote_id: QUOTE_ID, accept_total: 1 }),
    });
    await postOrder(r);
    expect(bffFetch.mock.calls[0]![1].headers['idempotency-key']).toBe(checkoutIdempotencyKey(QUOTE_ID));
  });
  it('validasi body: quote_id UUID, accept_total bilangan bulat ≥ 0', async () => {
    for (const body of [{}, { quote_id: 'x', accept_total: 1 }, { quote_id: QUOTE_ID, accept_total: 1.5 }, { quote_id: QUOTE_ID, accept_total: '1' }, { quote_id: QUOTE_ID, accept_total: -1 }]) {
      expect((await postOrder(req('/api/orders', { method: 'POST', body }))).status).toBe(400);
    }
    expect(bffFetch).not.toHaveBeenCalled();
  });
  it.each([
    [409, 'price_changed', 409, /Harga berubah/],
    [409, 'quote_expired', 409, /kedaluwarsa/],
    [409, 'cart_changed', 409, /Keranjang berubah/],
    [409, 'out_of_stock', 409, /habis/],
    [409, 'idempotency_conflict', 409, /sedang diproses/],
    [429, 'too_many_open_orders', 429, /belum dibayar/],
    [429, 'rate_limited', 429, /Terlalu banyak/],
    [422, 'fulfilment_unavailable', 422, /ambil di toko/],
    [409, 'kode_baru_tak_dikenal', 409, /tidak valid/],
    [502, 'payment_provider_error', 502, /Pembayaran belum dapat diproses/],
    [503, 'payment_provider_unavailable', 503, /pembayaran sedang tidak tersedia/],
    [503, 'online_unavailable', 503, /Belanja online sedang tidak tersedia/],
    [503, 'bff_unavailable', 503, /Layanan sedang tidak tersedia/],
    [500, 'internal', 502, /Terjadi kendala/],
  ])('BFF %i %s -> %i pesan Indonesia', async (status, code, expected, msg) => {
    bffFetch.mockResolvedValue(fail(status, code));
    const res = await postOrder(req('/api/orders', { method: 'POST', body: { quote_id: QUOTE_ID, accept_total: 1 } }));
    expect(res.status).toBe(expected);
    expect(res.headers.get('cache-control')).toBe('no-store');
    const b = await res.json();
    expect(b.error.message).toMatch(msg);
    expect(JSON.stringify(b)).not.toContain('pesan internal BFF');
  });
  it('GET daftar: limit ≤ 20, cursor tervalidasi', async () => {
    bffFetch.mockResolvedValue({ ok: true, status: 200, data: { items: [], next_cursor: null } });
    await listOrders(req('/api/orders?limit=100&cursor=abc'));
    expect(bffFetch.mock.calls[0]![0]).toBe('/v1/orders?limit=20&cursor=abc');
    expect((await listOrders(req('/api/orders?cursor=a%26member%3D1'))).status).toBe(400);
  });
});

describe('status, batal, kode ambil', () => {
  it('id bukan UUID -> 404 tanpa BFF (status, batal, kode ambil)', async () => {
    expect((await getOrder(req('/api/orders/1'), params({ id: '1' }))).status).toBe(404);
    expect((await cancelOrder(req('/api/orders/1/cancel', { method: 'POST', body: {} }), params({ id: '1' }))).status).toBe(404);
    expect((await getPickup(req('/api/orders/1/pickup-code'), params({ id: '1' }))).status).toBe(404);
    expect(bffFetch).not.toHaveBeenCalled();
  });
  it('status: no-store, allowlist (kode ambil tidak ikut)', async () => {
    bffFetch.mockResolvedValue({
      ok: true,
      status: 200,
      data: { order_id: ID, status: 'fulfilling', amount_payable: 1, pickup_code: 'ABCD-EFGH', payment: { status: 'paid', qr_string: 'q' }, parts: [] },
    });
    const res = await getOrder(req(`/api/orders/${ID}`), params({ id: ID }));
    expect(res.headers.get('cache-control')).toBe('no-store');
    const s = JSON.stringify(await res.json());
    expect(s).not.toContain('ABCD-EFGH');
    expect(s).not.toContain('"qr_string":"q"');
  });
  it('order milik orang lain: BFF 404 -> 404', async () => {
    bffFetch.mockResolvedValue(fail(404, 'not_found'));
    expect((await getOrder(req(`/api/orders/${ID}`), params({ id: ID }))).status).toBe(404);
  });
  it('batal: wajib Origin sama; sudah dibayar -> 409 order_not_cancellable', async () => {
    expect((await cancelOrder(req(`/api/orders/${ID}/cancel`, { method: 'POST', body: {}, origin: null }), params({ id: ID }))).status).toBe(403);
    bffFetch.mockResolvedValueOnce({ ok: true, status: 200, data: { status: 'cancelled' } });
    expect((await cancelOrder(req(`/api/orders/${ID}/cancel`, { method: 'POST', body: {} }), params({ id: ID }))).status).toBe(200);
    expect(bffFetch.mock.calls[0]![0]).toBe(`/v1/orders/${ID}/cancel`);
    bffFetch.mockResolvedValueOnce(fail(409, 'order_not_cancellable'));
    const res = await cancelOrder(req(`/api/orders/${ID}/cancel`, { method: 'POST', body: {} }), params({ id: ID }));
    expect(res.status).toBe(409);
    expect((await res.json()).error.message).toMatch(/layanan pelanggan/);
  });
  it('kode ambil: no-store + pragma + no-referrer; lintas situs ditolak; tidak di-log', async () => {
    const log = vi.spyOn(console, 'log');
    const err = vi.spyOn(console, 'error');
    const warn = vi.spyOn(console, 'warn');
    bffFetch.mockResolvedValue({ ok: true, status: 200, data: { items: [{ channel_code: 'RTL', outlet_code: 'TOKO-01', outlet_name: 'Toko', pickup_code: 'ABCD-EFGH' }] } });
    const res = await getPickup(req(`/api/orders/${ID}/pickup-code`, { site: 'same-origin' }), params({ id: ID }));
    expect(res.status).toBe(200);
    expect(res.headers.get('cache-control')).toMatch(/^no-store/);
    expect(res.headers.get('pragma')).toBe('no-cache');
    expect(res.headers.get('referrer-policy')).toBe('no-referrer');
    expect((await res.json()).items[0].pickup_code).toBe('ABCD-EFGH');
    const logged = [...log.mock.calls, ...err.mock.calls, ...warn.mock.calls].flat().join(' ');
    expect(logged).not.toContain('ABCD-EFGH');
    expect((await getPickup(req(`/api/orders/${ID}/pickup-code`, { site: 'cross-site' }), params({ id: ID }))).status).toBe(403);
    bffFetch.mockResolvedValue(fail(409, 'pickup_code_unavailable'));
    const r409 = await getPickup(req(`/api/orders/${ID}/pickup-code`), params({ id: ID }));
    expect(r409.status).toBe(409);
    expect((await r409.json()).error.message).toMatch(/setelah pesanan dikonfirmasi/);
  });
});

describe('simulator pembayaran mock', () => {
  it('nonaktif secara default -> 404 tanpa BFF', async () => {
    const res = await simulate(req(`/api/orders/${ID}/simulate-payment`, { method: 'POST', body: { status: 'paid' } }), params({ id: ID }));
    expect(res.status).toBe(404);
    expect(bffFetch).not.toHaveBeenCalled();
  });
  it('aturan env: production selalu mati', () => {
    expect(paymentSimulatorEnabled({ EFN_PAYMENT_SIMULATOR: 'true', NODE_ENV: 'development' })).toBe(true);
    expect(paymentSimulatorEnabled({ EFN_PAYMENT_SIMULATOR: 'true', NODE_ENV: 'production', APP_ENV: 'staging' })).toBe(true);
    expect(paymentSimulatorEnabled({ EFN_PAYMENT_SIMULATOR: 'true', NODE_ENV: 'production' })).toBe(false);
    expect(paymentSimulatorEnabled({ EFN_PAYMENT_SIMULATOR: 'true', APP_ENV: 'production' })).toBe(false);
    expect(paymentSimulatorEnabled({ NODE_ENV: 'development' })).toBe(false);
  });
});
