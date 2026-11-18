/**
 * chQuery (SEC-189): batas per pengguna untuk query halaman dan semaphore ekspor yang TERPISAH dari semaphore halaman.
 * runtime.ts dan fetch di-mock; semaphore dibuat saat modul diimpor sehingga env diset sebelum import dinamis.
 */
import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest';

vi.mock('@/lib/runtime', async () => {
  const { RateLimiter } = await import('@/lib/rate-limit');
  const page = new RateLimiter(2, 60_000);
  return {
    getConfig: () => ({ enabled: true, chUrl: 'https://ch.test:8443', chUser: 'efn_insight', problems: [] }),
    getKey: () => 'ab'.repeat(32),
    getChPassword: () => 'pw',
    getPageQueryLimiter: () => page,
  };
});

type Mod = typeof import('@/lib/clickhouse');
let mod: Mod;
const fetchMock = vi.fn();
const ok = (): Response => new Response(JSON.stringify({ data: [] }), { status: 200 });
const sess = (sub: number) => ({ cids: [1], codes: ['PB' as const], sub });
const bag = { pt: ['PB'] };
const tick = (ms = 15) => new Promise<void>((r) => setTimeout(r, ms));

beforeAll(async () => {
  process.env.INSIGHT_CH_MAX_CONCURRENCY = '1';
  process.env.INSIGHT_CH_EXPORT_CONCURRENCY = '1';
  vi.stubGlobal('fetch', fetchMock);
  mod = await import('@/lib/clickhouse');
});

afterAll(() => {
  vi.unstubAllGlobals();
  delete process.env.INSIGHT_CH_MAX_CONCURRENCY;
  delete process.env.INSIGHT_CH_EXPORT_CONCURRENCY;
});

describe('batas per pengguna untuk query halaman', () => {
  it('query ke-3 sub yang sama ditolak rate_limited tanpa memanggil ClickHouse; sub lain dan pool ekspor tidak terdampak', async () => {
    fetchMock.mockReset();
    fetchMock.mockImplementation(async () => ok());
    await mod.chQuery('list_branches', sess(11), bag);
    await mod.chQuery('list_branches', sess(11), bag);
    await expect(mod.chQuery('list_branches', sess(11), bag)).rejects.toMatchObject({ name: 'ChError', kind: 'rate_limited' });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    await expect(mod.chQuery('list_branches', sess(12), bag)).resolves.toEqual([]);
    // ekspor dibatasi di route handler, bukan oleh batas query halaman
    await expect(mod.chQuery('list_branches', sess(11), bag, 'export')).resolves.toEqual([]);
  });
});

describe('semaphore ekspor terpisah dari semaphore halaman', () => {
  it('slot halaman penuh (1/1) tidak menghalangi ekspor; query halaman berikutnya mengantre sampai slot dilepas', async () => {
    let releaseFirst!: () => void;
    const gate = new Promise<void>((r) => {
      releaseFirst = r;
    });
    fetchMock.mockReset();
    fetchMock.mockImplementationOnce(async () => {
      await gate;
      return ok();
    });
    fetchMock.mockImplementation(async () => ok());

    const p1 = mod.chQuery('list_branches', sess(21), bag); // memegang satu-satunya slot halaman
    await tick();
    await expect(mod.chQuery('list_branches', sess(23), bag, 'export')).resolves.toEqual([]); // ekspor tetap jalan

    let p2Done = false;
    const p2 = mod.chQuery('list_branches', sess(22), bag).then((r) => {
      p2Done = true;
      return r;
    });
    await tick();
    expect(p2Done).toBe(false); // mengantre di semaphore halaman

    releaseFirst();
    await p1;
    await p2;
    expect(p2Done).toBe(true);
  });

  it('slot ekspor penuh (1/1) tidak menghalangi query halaman', async () => {
    let releaseExport!: () => void;
    const gate = new Promise<void>((r) => {
      releaseExport = r;
    });
    fetchMock.mockReset();
    fetchMock.mockImplementationOnce(async () => {
      await gate;
      return ok();
    });
    fetchMock.mockImplementation(async () => ok());

    const pe = mod.chQuery('list_branches', sess(31), bag, 'export'); // memegang satu-satunya slot ekspor
    await tick();
    await expect(mod.chQuery('list_branches', sess(32), bag)).resolves.toEqual([]); // halaman tetap jalan
    releaseExport();
    await pe;
  });
});
