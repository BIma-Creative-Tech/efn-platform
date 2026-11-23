/**
 * V-01 / SEC-V01-01: ID anonim `efn_vid` terpisah dari ID consent `efn_did`. Menguji lib/tracking-client.ts dengan
 * document/window/fetch palsu (runner Vitest berjalan di Node).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { pickEventChannel, parseMode, anonymousModeLeaked, serializeChoice, TRACKING_TEXT } from '../lib/tracking';

const OLD_D = '3f2b8c1e-5d4a-4b7e-9c3a-1a2b3c4d5e6f';

function installBrowser(cookies: Record<string, string> = {}) {
  const jar = new Map<string, string>(Object.entries(cookies));
  const store = new Map<string, string>();
  const doc = {
    get cookie() {
      return [...jar].map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join('; ');
    },
    set cookie(s: string) {
      const [pair, ...attrs] = s.split('; ');
      const i = pair!.indexOf('=');
      const k = pair!.slice(0, i);
      const v = decodeURIComponent(pair!.slice(i + 1));
      if (attrs.some((a) => a === 'Max-Age=0')) jar.delete(k);
      else jar.set(k, v);
    },
    visibilityState: 'visible',
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
  };
  const win = {
    location: { protocol: 'https:', search: '' },
    localStorage: {
      getItem: (k: string) => store.get(k) ?? null,
      setItem: (k: string, v: string) => void store.set(k, v),
      removeItem: (k: string) => void store.delete(k),
    },
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
  };
  vi.stubGlobal('document', doc);
  vi.stubGlobal('window', win);
  return { jar, store };
}

function mockFetch(payload: unknown = { accepted: 1 }) {
  const calls: { url: string; body: { device_id: string; events: unknown[] } }[] = [];
  const f = vi.fn(async (url: string, init: { body: string }) => {
    calls.push({ url, body: JSON.parse(init.body) });
    return { ok: true, status: 202, json: async () => payload, headers: new Headers() };
  });
  vi.stubGlobal('fetch', f);
  return calls;
}

beforeEach(() => {
  vi.resetModules();
});
afterEach(() => {
  vi.unstubAllGlobals();
});

describe('SEC-V01-01: efn_vid vs efn_did', () => {
  it('tanpa consent + visit_log: ID anonim di efn_vid, efn_did tidak dibuat', async () => {
    const { jar } = installBrowser();
    const calls = mockFetch();
    const c = await import('../lib/tracking-client');
    c.setVisitLog(true);
    c.trackEvent({ type: 'page_view', channelCode: 'SMD', page: 'home' });
    await c.flushNow();
    expect(jar.has('efn_did')).toBe(false);
    const vid = jar.get('efn_vid');
    expect(vid).toBeTruthy();
    expect(calls[0]!.body.device_id).toBe(vid);
    expect(calls[0]!.url).toBe('/api/events');
  });

  it('consent versi lama (versi naik) + efn_did ada: event anonim memakai efn_vid, BUKAN efn_did, tanpa ?link=1', async () => {
    const { jar } = installBrowser({ efn_did: OLD_D, efn_tc: serializeChoice({ granted: true, version: 'tracking@2020-01-01' }) });
    const calls = mockFetch();
    const c = await import('../lib/tracking-client');
    c.setVisitLog(true);
    c.setLinked(true);
    c.trackEvent({ type: 'page_view', channelCode: 'SMD', page: 'cart' });
    await c.flushNow();
    expect(calls).toHaveLength(1);
    expect(calls[0]!.body.device_id).not.toBe(OLD_D);
    expect(calls[0]!.body.device_id).toBe(jar.get('efn_vid'));
    expect(calls[0]!.url).toBe('/api/events');
    expect(jar.get('efn_did')).toBe(OLD_D); // tidak disentuh
  });

  it('efn_tc hilang tetapi efn_did ada: tetap anonim dengan efn_vid', async () => {
    const { jar } = installBrowser({ efn_did: OLD_D });
    const calls = mockFetch();
    const c = await import('../lib/tracking-client');
    c.setVisitLog(true);
    c.trackEvent({ type: 'search', channelCode: 'SMD', query: 'susu', resultCount: 1 });
    await c.flushNow();
    expect(calls[0]!.body.device_id).toBe(jar.get('efn_vid'));
    expect(calls[0]!.body.device_id).not.toBe(OLD_D);
  });

  it('consent aktif: memakai efn_did; event fitur member diizinkan', async () => {
    installBrowser({ efn_did: OLD_D, efn_tc: serializeChoice({ granted: true, version: TRACKING_TEXT.version }) });
    const calls = mockFetch({ accepted: 1, mode: 'consented' });
    const c = await import('../lib/tracking-client');
    c.setVisitLog(true);
    c.trackEvent({ type: 'wishlist_add', channelCode: 'SMD', productId: 5 });
    await c.flushNow();
    expect(calls[0]!.body.device_id).toBe(OLD_D);
  });

  it('anonim + fitur member: tidak dikirim', async () => {
    installBrowser();
    const calls = mockFetch();
    const c = await import('../lib/tracking-client');
    c.setVisitLog(true);
    c.trackEvent({ type: 'wishlist_add', channelCode: 'SMD', productId: 5 });
    c.trackEvent({ type: 'price_alert_set', channelCode: 'SMD', productId: 5 });
    await c.flushNow();
    expect(calls).toHaveLength(0);
  });

  it('visit_log mati dan tanpa consent: tidak ada ID dan tidak ada pengiriman', async () => {
    const { jar } = installBrowser();
    const calls = mockFetch();
    const c = await import('../lib/tracking-client');
    c.trackEvent({ type: 'page_view', channelCode: 'SMD', page: 'home' });
    await c.flushNow();
    expect(calls).toHaveLength(0);
    expect(jar.size).toBe(0);
  });

  it('server membalas mode consented untuk event anonim: efn_vid diganti UUID baru', async () => {
    const { jar } = installBrowser();
    const calls = mockFetch({ accepted: 1, mode: 'consented' });
    const c = await import('../lib/tracking-client');
    c.setVisitLog(true);
    c.trackEvent({ type: 'page_view', channelCode: 'SMD', page: 'home' });
    await c.flushNow();
    expect(jar.get('efn_vid')).toBeTruthy();
    expect(jar.get('efn_vid')).not.toBe(calls[0]!.body.device_id);
  });

  it('setuju consent membuat efn_did BARU (bukan efn_vid) dan efn_vid tidak disentuh', async () => {
    const { jar } = installBrowser();
    mockFetch({ accepted: 1 });
    const c = await import('../lib/tracking-client');
    c.setVisitLog(true);
    c.trackEvent({ type: 'page_view', channelCode: 'SMD', page: 'home' });
    const vid = jar.get('efn_vid');
    const r = await c.grantDeviceConsent();
    expect(r.ok).toBe(true);
    expect(jar.get('efn_did')).toBeTruthy();
    expect(jar.get('efn_did')).not.toBe(vid);
    expect(jar.get('efn_vid')).toBe(vid);
  });

  it('tolak tanpa consent sebelumnya: tidak ada panggilan server, efn_vid dipertahankan', async () => {
    const { jar } = installBrowser();
    const calls = mockFetch();
    const c = await import('../lib/tracking-client');
    c.setVisitLog(true);
    c.trackEvent({ type: 'page_view', channelCode: 'SMD', page: 'home' });
    const vid = jar.get('efn_vid');
    const r = await c.declineDeviceConsent();
    expect(r.ok).toBe(true);
    expect(calls).toHaveLength(0);
    expect(jar.get('efn_vid')).toBe(vid);
  });
});

describe('fungsi murni V-01', () => {
  it('pickEventChannel: ?channel= valid menang; brand satu kanal; brand multi-kanal/holding -> null', () => {
    expect(pickEventChannel('PB', [])).toBe('PB');
    expect(pickEventChannel(null, ['SMD'])).toBe('SMD');
    expect(pickEventChannel('PB', ['SMD'])).toBe('PB');
    expect(pickEventChannel(null, ['BCR', 'BCF'])).toBeNull(); // Bacha: dua kanal
    expect(pickEventChannel(null, [])).toBeNull(); // holding
    expect(pickEventChannel('pb', ['SMD'])).toBe('SMD'); // bentuk tidak valid diabaikan
    expect(pickEventChannel('<script>', [])).toBeNull();
    expect(pickEventChannel('TERLALUPANJANG', [])).toBeNull(); // > 8 karakter
    expect(pickEventChannel(undefined, ['TERLALUPANJANG'])).toBeNull();
  });
  it('parseMode & anonymousModeLeaked', () => {
    expect(parseMode({ mode: 'anonymous' })).toBe('anonymous');
    expect(parseMode({ mode: 'consented' })).toBe('consented');
    expect(parseMode({ mode: 'x' })).toBeNull();
    expect(parseMode(null)).toBeNull();
    expect(anonymousModeLeaked('anonymous', 'consented')).toBe(true);
    expect(anonymousModeLeaked('anonymous', 'anonymous')).toBe(false);
    expect(anonymousModeLeaked('anonymous', null)).toBe(false);
    expect(anonymousModeLeaked('consented', 'consented')).toBe(false);
  });
});
