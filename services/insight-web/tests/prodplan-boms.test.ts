/**
 * Pencarian BOM (docs/04 §30.8 `GET /v1/boms`): validasi query, rute baca `GET /api/prodplan/boms` (sesi, peran, batas,
 * hanya brand+q diteruskan), parser, dan pemanggil browser `searchBoms`.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const { state } = vi.hoisted(() => ({ state: { enabled: true } }));

vi.mock('@/lib/runtime', async () => {
  const { RateLimiter } = await import('@/lib/rate-limit');
  const mut = new RateLimiter(60, 60_000);
  let read = new RateLimiter(150, 60_000);
  (globalThis as unknown as { __resetPpRead: () => void }).__resetPpRead = () => {
    read = new RateLimiter(150, 60_000);
  };
  return {
    getConfig: () => ({ ppEnabled: state.enabled, ppUrl: 'http://127.0.0.1:7443' }),
    getKey: () => 'ab'.repeat(32),
    getPpMutationLimiter: () => mut,
    getPpReadLimiter: () => read,
  };
});

import { GET } from '@/app/api/prodplan/boms/route';
import { parseBomQuery } from '@/lib/prodplan-actions';
import { searchBoms } from '@/lib/prodplan-call';
import { parseBoms } from '@/lib/prodplan-types';

// Sesi = cookie `__Host-epp` (docs/04 §30.17); peran bukan urusan route baca ini (prodplan menegakkan).
const SID = 'B'.repeat(43);
const get = (qs: string, cookie: string | null = `__Host-epp=${SID}`): Promise<Response> =>
  GET(new Request(`https://insight.test/api/prodplan/boms${qs}`, { headers: cookie === null ? {} : { cookie } }));

type FetchCall = { url: string; init: RequestInit };
let calls: FetchCall[] = [];
function stubFetch(status: number, body: unknown): void {
  calls = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init: RequestInit) => {
      calls.push({ url, init });
      return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
    }),
  );
}

const ITEM = { bom_id: 55, bom_name: 'Roti Tawar', bom_code: 'BOM-55', product_detail_id: 101, product_code: 'FIN-A', product_name: 'Roti Tawar 400g', uom_name: 'PCS' };

beforeEach(() => {
  state.enabled = true;
  (globalThis as unknown as { __resetPpRead: () => void }).__resetPpRead();
  vi.spyOn(console, 'info').mockImplementation(() => undefined);
  vi.spyOn(console, 'warn').mockImplementation(() => undefined);
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('parseBomQuery', () => {
  it('brand huruf kecil + q 2..64 karakter (dipangkas, kontrol dibuang)', () => {
    expect(parseBomQuery('pb', '  roti  tawar ')).toEqual({ brand: 'pb', q: 'roti tawar' });
    expect(parseBomQuery('pb', 'ab')).toEqual({ brand: 'pb', q: 'ab' });
    expect(parseBomQuery('pb', 'a\u0000b')).toEqual({ brand: 'pb', q: 'a b' });
    expect(parseBomQuery('pb', 'x'.repeat(64))).not.toBeNull();
  });
  it('terlalu pendek/panjang, brand tidak sah, tipe salah -> null', () => {
    expect(parseBomQuery('pb', 'a')).toBeNull();
    expect(parseBomQuery('pb', '  a ')).toBeNull();
    expect(parseBomQuery('pb', '')).toBeNull();
    expect(parseBomQuery('pb', 'x'.repeat(65))).toBeNull();
    expect(parseBomQuery('PB', 'roti')).toBeNull();
    expect(parseBomQuery('../x', 'roti')).toBeNull();
    expect(parseBomQuery(null, 'roti')).toBeNull();
    expect(parseBomQuery('pb', null)).toBeNull();
  });
});

describe('parseBoms', () => {
  it('memetakan item sah, membuang id tidak sah, maks 50', () => {
    expect(parseBoms({ items: [ITEM, { ...ITEM, bom_id: 0 }, { ...ITEM, product_detail_id: 'x' }, 'x'] })).toEqual([ITEM]);
    expect(parseBoms(null)).toEqual([]);
    expect(parseBoms({ items: Array.from({ length: 60 }, (_, i) => ({ ...ITEM, bom_id: i + 1 })) })).toHaveLength(50);
  });
});

describe('GET /api/prodplan/boms', () => {
  it('meneruskan hanya brand + q + limit tetap; parameter lain diabaikan', async () => {
    stubFetch(200, { items: [ITEM], secret: 'x' });
    const res = await get('?brand=pb&q=%20roti%20&limit=500&evil=1&esb_brand=wtz');
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ ok: true, data: { items: [ITEM] } });
    expect(calls).toHaveLength(1);
    expect(calls[0].url).toBe('http://127.0.0.1:7443/v1/boms?brand=pb&q=roti&limit=20');
    expect(calls[0].init.method).toBe('GET');
    expect((calls[0].init.headers as Record<string, string>)['X-EFN-Actor']).toMatch(/^ppa2\./);
    expect(res.headers.get('cache-control')).toBe('no-store');
  });

  it('q < 2 karakter / brand tidak sah / tanpa parameter -> 422 tanpa memanggil prodplan', async () => {
    stubFetch(200, { items: [] });
    for (const qs of ['?brand=pb&q=a', '?brand=PB&q=roti', '', '?q=roti', '?brand=pb']) {
      const res = await get(qs);
      expect(res.status, qs).toBe(422);
      expect(((await res.json()) as { error: { code: string } }).error.code).toBe('bad_input');
    }
    expect(calls).toHaveLength(0);
  });

  it('flag mati 503; tanpa cookie sesi ESB / hanya cookie Odoo 401 session_expired', async () => {
    stubFetch(200, { items: [] });
    state.enabled = false;
    expect((await get('?brand=pb&q=roti')).status).toBe(503);
    state.enabled = true;
    expect((await get('?brand=pb&q=roti', null)).status).toBe(401);
    const odoo = await get('?brand=pb&q=roti', `__Host-eis=eis3.${'a'.repeat(30)}.${'b'.repeat(43)}`);
    expect(odoo.status).toBe(401);
    expect(((await odoo.json()) as { error: { code: string } }).error.code).toBe('session_expired');
    expect(calls).toHaveLength(0);
  });

  it('sesi habis di prodplan -> 401 + cookie dihapus', async () => {
    stubFetch(401, { error: { code: 'session_expired' } });
    const res = await get('?brand=pb&q=roti');
    expect(res.status).toBe(401);
    expect(res.headers.get('set-cookie')).toContain('__Host-epp=; Max-Age=0');
  });

  it('batas baca 150/menit per sesi -> 429', async () => {
    stubFetch(200, { items: [] });
    for (let i = 0; i < 150; i += 1) expect((await get('?brand=pb&q=roti')).status).toBe(200);
    const over = await get('?brand=pb&q=roti');
    expect(over.status).toBe(429);
    expect(Number(over.headers.get('retry-after'))).toBeGreaterThanOrEqual(1);
  });

  it('galat prodplan dipetakan tanpa detail internal (422 bad_input, 502)', async () => {
    stubFetch(422, { error: { code: 'bad_input', message: 'SELECT * FROM bom', details: { stack: 'x' } } });
    const a = await get('?brand=pb&q=roti');
    expect(a.status).toBe(422);
    expect(JSON.stringify(await a.json())).not.toMatch(/SELECT|stack/);
    stubFetch(500, '<html>psycopg2</html>');
    const b = await get('?brand=pb&q=roti');
    expect(b.status).toBe(502);
    expect(JSON.stringify(await b.json())).not.toMatch(/psycopg2/);
  });
});

describe('searchBoms (browser)', () => {
  it('memanggil rute sendiri dengan query ter-encode dan mengurai hasil', async () => {
    stubFetch(200, { ok: true, data: { items: [ITEM] } });
    const r = await searchBoms('pb', 'roti & susu');
    expect(r).toEqual({ ok: true, items: [ITEM] });
    expect(calls[0].url).toBe('/api/prodplan/boms?brand=pb&q=roti+%26+susu');
  });
  it('galat server -> pesan Indonesia; jaringan putus -> pesan tidak dapat dihubungi', async () => {
    stubFetch(422, { error: { code: 'bad_input', message: 'Isian tidak valid.' } });
    expect(await searchBoms('pb', 'roti')).toEqual({ ok: false, message: 'Isian tidak valid.' });
    vi.stubGlobal('fetch', vi.fn(async () => Promise.reject(new TypeError('x'))));
    const r = await searchBoms('pb', 'roti');
    expect(r.ok).toBe(false);
    expect(!r.ok && r.message).toContain('tidak dapat dihubungi');
  });
});
