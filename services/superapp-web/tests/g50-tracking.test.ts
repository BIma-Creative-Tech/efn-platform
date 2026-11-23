import { describe, expect, it } from 'vitest';
import {
  buildBatchBody,
  buildEvent,
  byteLength,
  clampRetryAfter,
  coerceFeatures,
  EventQueue,
  isMemberOnlyEvent,
  isUuidV4,
  MAX_BODY_BYTES,
  needsDecision,
  NO_FEATURES,
  PAGES,
  pageForPath,
  parseBatchResponse,
  parseChoice,
  parseFeatures,
  pickTracking,
  sanitizeEvents,
  serializeChoice,
  TRACKING_TEXT,
  TRACKING_VERSION_RE,
  trackingActive,
  TrackingSender,
  VISIT_LOG_TEXT,
  VisibleTimer,
  type SendResult,
  type TrackedEvent,
} from '../lib/tracking';

const DEVICE = '3f2b8c1e-5d4a-4b7e-9c3a-1a2b3c4d5e6f';
let n = 0;
function uuid(): string {
  n += 1;
  return `00000000-0000-4000-8000-${String(n).padStart(12, '0')}`;
}
const NOW = new Date('2026-11-20T03:00:00.000Z');
function ev(type: 'product_view' | 'search' = 'product_view'): TrackedEvent {
  const e =
    type === 'search'
      ? buildEvent({ type, channelCode: 'GL', query: 'susu', resultCount: 3 }, uuid(), NOW)
      : buildEvent({ type, channelCode: 'GL', outletCode: 'TOKO-01', productId: 7, durationS: 12 }, uuid(), NOW);
  if (!e) throw new Error('event tidak valid');
  return e;
}

describe('teks consent tracking', () => {
  it('versi cocok regex BFF dan teks DRAFT tidak kosong', () => {
    expect(TRACKING_TEXT.version).toMatch(TRACKING_VERSION_RE);
    expect(TRACKING_TEXT.body.length).toBeGreaterThan(50);
  });
});

describe('device id & pilihan', () => {
  it('UUID v4 saja', () => {
    expect(isUuidV4(DEVICE)).toBe(true);
    expect(isUuidV4('3f2b8c1e-5d4a-1b7e-9c3a-1a2b3c4d5e6f')).toBe(false);
    expect(isUuidV4(null)).toBe(false);
    expect(isUuidV4('../../etc')).toBe(false);
  });
  it('serialize/parse pilihan', () => {
    const raw = serializeChoice({ granted: true, version: TRACKING_TEXT.version });
    expect(parseChoice(raw)).toEqual({ granted: true, version: TRACKING_TEXT.version });
    expect(parseChoice('2.x')).toBeNull();
    expect(parseChoice('1.<script>')).toBeNull();
    expect(parseChoice(null)).toBeNull();
  });
  it('banner tampil bila belum memilih atau versi lama; tidak bila sudah memilih versi terbaru', () => {
    expect(needsDecision(null)).toBe(true);
    expect(needsDecision({ granted: false, version: 'tracking@2020-01-01' })).toBe(true);
    expect(needsDecision({ granted: false, version: TRACKING_TEXT.version })).toBe(false);
  });
  it('tracking aktif hanya bila setuju versi terbaru DAN ada ID', () => {
    const c = { granted: true, version: TRACKING_TEXT.version };
    expect(trackingActive(c, DEVICE)).toBe(true);
    expect(trackingActive(c, null)).toBe(false);
    expect(trackingActive({ ...c, granted: false }, DEVICE)).toBe(false);
    expect(trackingActive({ ...c, version: 'tracking@2020-01-01' }, DEVICE)).toBe(false);
    expect(trackingActive(null, DEVICE)).toBe(false);
  });
  it('pickTracking: hanya boolean true = aktif; purpose lain diabaikan', () => {
    expect(pickTracking([{ purpose: 'marketing', granted: true }])).toMatchObject({ granted: false });
    expect(pickTracking({ items: [{ purpose: 'tracking', granted: true, version: 'v' }] })).toMatchObject({ granted: true, version: 'v' });
    expect(pickTracking([{ purpose: 'tracking', granted: 'true' }])).toMatchObject({ granted: false });
    expect(pickTracking(null)).toMatchObject({ granted: false });
  });
});

describe('features', () => {
  it('hilang/rusak -> semua mati', () => {
    expect(parseFeatures(null)).toEqual({ ...NO_FEATURES, channels: { push: true, email: false, wa_unofficial: false } });
    expect(parseFeatures({ features: { tracking: 'true' } }).tracking).toBe(false);
  });
  it('membaca flag dan nama alternatif', () => {
    const f = parseFeatures({ features: { tracking: true, wishlist: true, restock_alerts: true, price_alert: true, email: true } });
    expect(f).toMatchObject({ tracking: true, wishlist: true, restock: true, priceAlert: true });
    expect(f.channels).toEqual({ push: true, email: true, wa_unofficial: false });
    expect(parseFeatures({ features: { push: false } }).channels.push).toBe(false);
  });
  it('coerceFeatures hanya menerima boolean true', () => {
    expect(coerceFeatures({ tracking: 1, wishlist: true, channels: { push: true } })).toMatchObject({ tracking: false, wishlist: true });
    expect(coerceFeatures('x')).toEqual(NO_FEATURES);
  });
});

describe('buildEvent', () => {
  it('product_view: durasi dipotong 0..600 dan hanya field kontrak', () => {
    const e = buildEvent({ type: 'product_view', channelCode: 'GL', outletCode: 'TOKO-01', productId: 7, durationS: 9999 }, uuid(), NOW)!;
    expect(e.duration_s).toBe(600);
    expect(Object.keys(e).sort()).toEqual(['channel_code', 'client_ts', 'duration_s', 'event_id', 'outlet_code', 'product_id', 'type']);
    expect(e.client_ts).toBe('2026-11-20T03:00:00.000Z');
  });
  it('channel_code > 8 karakter ditolak; outlet_code > 16 menjadi null', () => {
    expect(buildEvent({ type: 'product_view', channelCode: 'TERLALUPANJANG', productId: 1 }, uuid(), NOW)).toBeNull();
    expect(buildEvent({ type: 'product_view', channelCode: 'GL', outletCode: 'X'.repeat(17), productId: 1 }, uuid(), NOW)!.outlet_code).toBeNull();
  });
  it('cart_add wajib qty positif & product_id; checkout_start tanpa product_id', () => {
    expect(buildEvent({ type: 'cart_add', channelCode: 'GL', productId: 1 }, uuid(), NOW)).toBeNull();
    expect(buildEvent({ type: 'cart_add', channelCode: 'GL', productId: 1, qty: 2 }, uuid(), NOW)).toMatchObject({ qty: 2 });
    const c = buildEvent({ type: 'checkout_start', channelCode: 'GL' }, uuid(), NOW)!;
    expect(c).not.toHaveProperty('product_id');
  });
  it('search: query apa adanya (dipotong 64), kosong ditolak, tanpa product_id', () => {
    const e = buildEvent({ type: 'search', channelCode: 'GL', query: '  Susu UHT 6281234567890  ', resultCount: 2 }, uuid(), NOW)!;
    expect(e.query).toBe('Susu UHT 6281234567890'); // redaksi = tugas server
    expect(e).not.toHaveProperty('product_id');
    expect(buildEvent({ type: 'search', channelCode: 'GL', query: '   ' }, uuid(), NOW)).toBeNull();
    expect(buildEvent({ type: 'search', channelCode: 'GL', query: 'a'.repeat(100) }, uuid(), NOW)!.query).toHaveLength(64);
  });
  it('tipe tak dikenal / event_id bukan uuid ditolak', () => {
    // @ts-expect-error tipe sengaja salah
    expect(buildEvent({ type: 'bukan_tipe', channelCode: 'GL' }, uuid(), NOW)).toBeNull();
    expect(buildEvent({ type: 'checkout_start', channelCode: 'GL' }, 'bukan-uuid', NOW)).toBeNull();
  });
});

describe('V-01 page_view & log kunjungan anonim', () => {
  it('page_view wajib page enum, tanpa product_id/query, hanya field kontrak', () => {
    const e = buildEvent({ type: 'page_view', channelCode: 'SMD', page: 'home' }, uuid(), NOW)!;
    expect(Object.keys(e).sort()).toEqual(['channel_code', 'client_ts', 'event_id', 'outlet_code', 'page', 'type']);
    expect(buildEvent({ type: 'page_view', channelCode: 'SMD' }, uuid(), NOW)).toBeNull();
    // @ts-expect-error nilai di luar enum
    expect(buildEvent({ type: 'page_view', channelCode: 'SMD', page: '/belanja?q=susu' }, uuid(), NOW)).toBeNull();
    for (const p of PAGES) expect(buildEvent({ type: 'page_view', channelCode: 'PB', page: p }, uuid(), NOW)).not.toBeNull();
  });
  it('page dilarang untuk tipe lain', () => {
    expect(buildEvent({ type: 'checkout_start', channelCode: 'GL', page: 'cart' }, uuid(), NOW)).toBeNull();
    expect(buildEvent({ type: 'product_view', channelCode: 'GL', productId: 1, page: 'product' }, uuid(), NOW)).toBeNull();
  });
  it('sanitizeEvents: page_view valid lolos, page palsu / kunci asing ditolak', () => {
    const pv = buildEvent({ type: 'page_view', channelCode: 'SMD', page: 'cart' }, uuid(), NOW)!;
    expect(sanitizeEvents([pv])).toHaveLength(1);
    expect(sanitizeEvents([{ ...pv, page: 'https://x.test/a?b=1' }])).toBeNull();
    expect(sanitizeEvents([{ ...pv, url: '/keranjang' }])).toBeNull();
    expect(sanitizeEvents([{ ...ev(), page: 'home' }])).toBeNull();
  });
  it('pageForPath: pemetaan route -> enum, tidak pernah mengembalikan path mentah', () => {
    expect(pageForPath('/')).toBe('home');
    expect(pageForPath('/belanja')).toBe('category');
    expect(pageForPath('/belanja', true)).toBe('search');
    expect(pageForPath('/belanja/123')).toBe('product');
    expect(pageForPath('/keranjang')).toBe('cart');
    expect(pageForPath('/checkout')).toBe('checkout');
    expect(pageForPath('/promo/lebaran')).toBe('promo');
    expect(pageForPath('/pesanan')).toBe('order');
    expect(pageForPath('/pesanan/55')).toBe('order');
    for (const p of ['/akun', '/akun/privasi', '/masuk', '/keluar', '/inbox', '/wishlist']) expect(pageForPath(p)).toBe('account');
    expect(pageForPath('/meja')).toBe('store');
    expect(pageForPath('/reservasi/saya')).toBe('store');
    expect(pageForPath('/pantau-harga/9')).toBe('other');
    expect(pageForPath('/privasi/pelacakan')).toBe('other');
    expect(pageForPath('/BELANJA/1')).toBe('product');
    for (const p of ['/x', '//', '/belanja/../akun', '/belanja?q=a']) expect(PAGES).toContain(pageForPath(p));
  });
  it('fitur member tidak pernah anonim; event lain boleh', () => {
    for (const t of ['wishlist_add', 'wishlist_remove', 'notify_me_request', 'price_alert_set'] as const) expect(isMemberOnlyEvent(t)).toBe(true);
    for (const t of ['page_view', 'product_view', 'cart_add', 'cart_remove', 'search', 'checkout_start'] as const) expect(isMemberOnlyEvent(t)).toBe(false);
  });
  it('features.visit_log: hanya boolean true; hilang = mati; coerceFeatures konsisten', () => {
    expect(parseFeatures({ features: { tracking: true, visit_log: true } }).visitLog).toBe(true);
    expect(parseFeatures({ features: { visit_log: 'true' } }).visitLog).toBe(false);
    expect(parseFeatures({ features: { tracking: true } }).visitLog).toBe(false);
    expect(parseFeatures(null).visitLog).toBe(false);
    expect(coerceFeatures({ visitLog: true }).visitLog).toBe(true);
    expect(coerceFeatures({ visitLog: 1 }).visitLog).toBe(false);
    expect(NO_FEATURES.visitLog).toBe(false);
  });
  it('teks log anonim terpisah dari teks consent (versi/hash consent tidak berubah)', () => {
    expect(TRACKING_TEXT.version).toBe('tracking@2026-11-20');
    expect(VISIT_LOG_TEXT.notice).toMatch(/anonim/);
    expect(VISIT_LOG_TEXT.notice).toMatch(/IP/);
    expect(VISIT_LOG_TEXT.consentHint).toMatch(/Menolak tidak menghentikan/);
  });
});

describe('sanitizeEvents (route handler)', () => {
  it('menerima event valid dan menolak kunci tambahan', () => {
    const e = ev();
    expect(sanitizeEvents([e])).toHaveLength(1);
    expect(sanitizeEvents([{ ...e, ip: '1.2.3.4' }])).toBeNull();
    expect(sanitizeEvents([{ ...e, user_agent: 'x' }])).toBeNull();
  });
  it('menolak batch kosong, > 50, dan elemen bukan objek', () => {
    expect(sanitizeEvents([])).toBeNull();
    expect(sanitizeEvents(Array.from({ length: 51 }, () => ev()))).toBeNull();
    expect(sanitizeEvents(Array.from({ length: 50 }, () => ev()))).toHaveLength(50);
    expect(sanitizeEvents(['x'])).toBeNull();
    expect(sanitizeEvents('x')).toBeNull();
  });
});

describe('EventQueue', () => {
  it('batas antrean membuang yang terlama', () => {
    const q = new EventQueue(3);
    const evs = [ev(), ev(), ev(), ev()];
    for (const e of evs) q.push(e);
    expect(q.size()).toBe(3);
    expect(q.take(DEVICE)[0]).toEqual(evs[1]);
  });
  it('take <= 50 dan body <= 32 KB', () => {
    const q = new EventQueue(500);
    for (let i = 0; i < 120; i += 1) q.push(ev('search'));
    const batch = q.take(DEVICE);
    expect(batch.length).toBeLessThanOrEqual(50);
    expect(byteLength(buildBatchBody(DEVICE, batch))).toBeLessThanOrEqual(MAX_BODY_BYTES);
    expect(q.size()).toBe(120 - batch.length);
  });
  it('requeue menjaga urutan', () => {
    const q = new EventQueue();
    const a = ev();
    const b = ev();
    q.push(a);
    q.push(b);
    const first = q.take(DEVICE, 1);
    q.requeue(first);
    expect(q.take(DEVICE, 2)).toEqual([a, b]);
  });
});

describe('TrackingSender', () => {
  function make(results: SendResult[], deviceId: string | null = DEVICE) {
    let t = 1_000_000;
    const sent: string[] = [];
    const sender = new TrackingSender({
      getDeviceId: () => deviceId,
      now: () => t,
      send: async (body) => {
        sent.push(body);
        return results.shift() ?? { kind: 'ok', disabled: false };
      },
    });
    return { sender, sent, advance: (ms: number) => (t += ms) };
  }

  it('mengirim batch dengan device_id, channel web, schema_version 1', async () => {
    const { sender, sent } = make([{ kind: 'ok', disabled: false }]);
    sender.enqueue(ev());
    expect(await sender.flush()).toBe(1);
    const body = JSON.parse(sent[0]!);
    expect(body).toMatchObject({ schema_version: 1, device_id: DEVICE, channel: 'web' });
    expect(body.events).toHaveLength(1);
    expect(sender.queue.size()).toBe(0);
  });

  it('429: menahan sampai Retry-After lewat, event tidak hilang', async () => {
    const { sender, sent, advance } = make([{ kind: 'rate_limited', retryAfterS: 30 }]);
    sender.enqueue(ev());
    expect(await sender.flush()).toBe(0);
    expect(sender.queue.size()).toBe(1);
    advance(10_000);
    expect(await sender.flush()).toBe(0); // masih dijeda
    expect(sent).toHaveLength(1);
    advance(21_000);
    expect(await sender.flush()).toBe(1);
    expect(sent).toHaveLength(2);
  });

  it('Retry-After dibatasi 1..900 dtk, default 60', () => {
    expect(clampRetryAfter(undefined)).toBe(60);
    expect(clampRetryAfter(0)).toBe(60);
    expect(clampRetryAfter(5)).toBe(5);
    expect(clampRetryAfter(99999)).toBe(900);
  });

  it('disabled: berhenti permanen dan antrean dikosongkan', async () => {
    const { sender, sent } = make([{ kind: 'ok', disabled: true }]);
    sender.enqueue(ev());
    await sender.flush();
    sender.enqueue(ev());
    expect(sender.queue.size()).toBe(0);
    expect(await sender.flush()).toBe(0);
    expect(sent).toHaveLength(1);
    expect(sender.isStopped()).toBe(true);
  });

  it('tracking_consent_required: berhenti sampai resume()', async () => {
    const { sender, sent } = make([{ kind: 'consent_required' }]);
    sender.enqueue(ev());
    await sender.flush();
    sender.enqueue(ev());
    expect(sender.queue.size()).toBe(0);
    sender.resume();
    sender.enqueue(ev());
    expect(await sender.flush()).toBe(1);
    expect(sent).toHaveLength(2);
  });

  it('rejected: batch dibuang tanpa retry', async () => {
    const { sender } = make([{ kind: 'rejected' }]);
    sender.enqueue(ev());
    expect(await sender.flush()).toBe(1);
    expect(sender.queue.size()).toBe(0);
  });

  it('gangguan jaringan: backoff, dibuang setelah 3 kegagalan beruntun', async () => {
    const { sender, advance } = make([{ kind: 'retry' }, { kind: 'retry' }, { kind: 'retry' }]);
    sender.enqueue(ev());
    expect(await sender.flush()).toBe(0);
    advance(60_000);
    expect(await sender.flush()).toBe(0);
    advance(60_000);
    expect(await sender.flush()).toBe(1); // percobaan ke-3 gagal -> dibuang
    expect(sender.queue.size()).toBe(0);
  });

  it('tanpa device id (consent dicabut) antrean dikosongkan dan tidak ada pengiriman', async () => {
    const { sender, sent } = make([], null);
    sender.enqueue(ev());
    expect(await sender.flush()).toBe(0);
    expect(sender.queue.size()).toBe(0);
    expect(sent).toHaveLength(0);
  });

  it('beacon: mengambil batch, dan mengembalikannya bila ditolak', () => {
    const { sender } = make([]);
    sender.enqueue(ev());
    const body = sender.takeForBeacon();
    expect(body).not.toBeNull();
    expect(sender.queue.size()).toBe(0);
    sender.beaconFailed();
    expect(sender.queue.size()).toBe(1);
  });

  it('beacon tidak mengirim saat dijeda 429', async () => {
    const { sender } = make([{ kind: 'rate_limited', retryAfterS: 30 }]);
    sender.enqueue(ev());
    await sender.flush();
    expect(sender.takeForBeacon()).toBeNull();
  });
});

describe('respons & timer', () => {
  it('parseBatchResponse', () => {
    expect(parseBatchResponse({ accepted: 0, disabled: true })).toEqual({ disabled: true, accepted: 0 });
    expect(parseBatchResponse({ accepted: 3, duplicates: 1, rejected: [] })).toEqual({ disabled: false, accepted: 3 });
    expect(parseBatchResponse(null)).toEqual({ disabled: false, accepted: 0 });
  });
  it('VisibleTimer berhenti saat tab tersembunyi', () => {
    let t = 0;
    const timer = new VisibleTimer(() => t, true);
    t = 5000;
    timer.setVisible(false);
    t = 50_000;
    expect(timer.seconds()).toBe(5);
    timer.setVisible(true);
    t = 52_000;
    expect(timer.seconds()).toBe(7);
  });
  it('VisibleTimer dibatasi 600 dtk', () => {
    let t = 0;
    const timer = new VisibleTimer(() => t, true);
    t = 3_600_000;
    expect(timer.seconds()).toBe(600);
  });
});
