/**
 * Route ekspor CSV (SEC-189 batas 5 / 10 menit per sub, pool 'export'; SEC-194 log audit). auth, loaders, runtime di-mock.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';

const { loaderFn, authFn } = vi.hoisted(() => ({ loaderFn: vi.fn(), authFn: vi.fn() }));

vi.mock('@/lib/auth', () => ({ authFromRequest: authFn }));
vi.mock('@/lib/runtime', async () => {
  const { RateLimiter } = await import('@/lib/rate-limit');
  const l = new RateLimiter(5, 600_000);
  return { getExportLimiter: () => l };
});
vi.mock('@/lib/loaders', () => ({
  TABLE_LOADERS: { 'branch-rank': (c: unknown) => loaderFn(c) },
  TABLE_QUERIES: { 'branch-rank': 'exec_branch_rank' },
  settle: async (p: Promise<unknown>) => ({ ok: true, data: await p }),
}));

import { GET } from '@/app/api/export/[name]/route';

const session = (sub: number) => ({ sub, name: 'Budi Rahasia', codes: ['PB'], cids: [1], roles: ['user'], iat: 1, exp: 2 });
const asUser = (sub: number) => authFn.mockReturnValue({ status: 'ok', session: session(sub) });
const call = (name = 'branch-rank') =>
  GET(new Request(`https://insight.test/api/export/${name}?from=2020-01-01&to=2020-01-31`, { headers: { cookie: '__Host-eis=RAHASIA' } }), {
    params: Promise.resolve({ name }),
  });

afterEach(() => {
  vi.restoreAllMocks();
  loaderFn.mockReset();
  authFn.mockReset();
});

function rowsFixture() {
  loaderFn.mockResolvedValue([
    { branch_key: 'pb:1', branch_label: '=Cabang A', company_code: 'PB', net_sales: 1000, trx: 2, avg_trx: 500, hpp_pct: 30 },
  ]);
}

describe('GET /api/export/<tabel>', () => {
  it('5 ekspor lolos, ke-6 -> 429 + Retry-After; pengguna lain tidak terdampak', async () => {
    rowsFixture();
    asUser(101);
    for (let i = 0; i < 5; i += 1) expect((await call()).status).toBe(200);
    const over = await call();
    expect(over.status).toBe(429);
    expect(Number(over.headers.get('retry-after'))).toBeGreaterThanOrEqual(1);
    expect(await over.text()).toBe('');
    expect(loaderFn).toHaveBeenCalledTimes(5);
    asUser(102);
    expect((await call()).status).toBe(200);
  });

  it('CSV: header, escape formula (C3), pool ekspor dipakai pemuat', async () => {
    rowsFixture();
    asUser(103);
    const res = await call();
    expect(res.headers.get('content-type')).toBe('text/csv; charset=utf-8');
    expect(res.headers.get('content-disposition')).toContain('efn-insight-peringkat-cabang-2020-01-01_2020-01-31.csv');
    expect(res.headers.get('cache-control')).toBe('no-store');
    expect(await res.text()).toContain("'=Cabang A");
    expect(loaderFn.mock.calls[0][0]).toMatchObject({ pool: 'export' });
  });

  it('tidak berwenang / dimatikan / tabel tak dikenal ditolak SEBELUM menghabiskan jatah', async () => {
    rowsFixture();
    authFn.mockReturnValue({ status: 'unauthenticated' });
    for (let i = 0; i < 7; i += 1) expect((await call()).status).toBe(401);
    authFn.mockReturnValue({ status: 'disabled' });
    expect((await call()).status).toBe(503);
    asUser(104);
    for (let i = 0; i < 7; i += 1) expect((await call('tidak-ada')).status).toBe(404);
    for (let i = 0; i < 5; i += 1) expect((await call()).status).toBe(200);
  });

  it('log audit memuat pt, rentang, nama query, rows; tanpa nama user atau cookie (SEC-194)', async () => {
    rowsFixture();
    asUser(105);
    const spy = vi.spyOn(console, 'info').mockImplementation(() => undefined);
    await call();
    const lines = spy.mock.calls.map((c) => String(c[0]));
    const line = lines.find((l) => l.includes('"event":"export"'));
    expect(line).toBeDefined();
    const rec = JSON.parse(line as string) as Record<string, unknown>;
    expect(rec).toMatchObject({ table: 'branch-rank', query: 'exec_branch_rank', pt: 'PB', from: '2020-01-01', to: '2020-01-31', rows: 1, sub: 105 });
    expect(line).not.toContain('Budi');
    expect(line).not.toContain('RAHASIA');
    expect(line).not.toContain('name');
  });
});
