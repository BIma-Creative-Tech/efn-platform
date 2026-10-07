import { ApiError } from '../errors/api-error';
import { FakeClock, FakeKvStore, catchApiError } from '../testing/fakes';
import { RateLimiter, WIB_OFFSET_SECONDS } from './rate-limiter';

function setup(startMs?: number) {
  const clock = new FakeClock(startMs);
  const kv = new FakeKvStore(clock.now);
  const limiter = new RateLimiter(kv);
  limiter.now = clock.now;
  return { clock, kv, limiter };
}

describe('RateLimiter (fixed window)', () => {
  it('mengizinkan sampai limit lalu menolak dalam window yang sama', async () => {
    const { limiter } = setup(Date.UTC(2026, 9, 5, 3, 0, 0));
    const results: { allowed: boolean; count: number }[] = [];
    for (let i = 0; i < 4; i += 1) results.push(await limiter.hit('k', 3, 900));
    expect(results.map((r) => r.allowed)).toEqual([true, true, true, false]);
    expect(results.map((r) => r.count)).toEqual([1, 2, 3, 4]);
  });

  it('window baru → counter reset', async () => {
    const { limiter, clock } = setup(Date.UTC(2026, 9, 5, 3, 0, 0));
    for (let i = 0; i < 3; i += 1) await limiter.hit('k', 3, 900);
    expect((await limiter.hit('k', 3, 900)).allowed).toBe(false);
    clock.advanceSeconds(900);
    expect((await limiter.hit('k', 3, 900)).allowed).toBe(true);
  });

  it('retryAfterSeconds = sisa window', async () => {
    // 03:05:00 UTC → window 900 s dimulai 03:00:00 → sisa 600 s
    const { limiter } = setup(Date.UTC(2026, 9, 5, 3, 5, 0));
    expect((await limiter.hit('k', 1, 900)).retryAfterSeconds).toBe(600);
  });

  it('key berbeda independen', async () => {
    const { limiter } = setup();
    await limiter.hit('a', 1, 60);
    expect((await limiter.hit('a', 1, 60)).allowed).toBe(false);
    expect((await limiter.hit('b', 1, 60)).allowed).toBe(true);
  });

  it('enforce melempar 429 format kontrak + Retry-After', async () => {
    const { limiter } = setup();
    await limiter.enforce('x', 1, 60);
    const err = await catchApiError(() => limiter.enforce('x', 1, 60));
    expect(err).toBeInstanceOf(ApiError);
    expect(err.getStatus()).toBe(429);
    expect(err.body).toEqual({ error: { code: 'rate_limited', message: expect.any(String) } });
    expect(Number(err.headers['Retry-After'])).toBeGreaterThan(0);
  });

  it('SEC-61 consume berbobot: melewati batas → ditolak & dikompensasi; release mengembalikan; window baru reset', async () => {
    const { limiter, kv, clock } = setup(Date.UTC(2026, 9, 5, 3, 0, 0));
    expect((await limiter.consume('p', 60, 100, 3600)).allowed).toBe(true);
    const over = await limiter.consume('p', 50, 100, 3600);
    expect(over).toMatchObject({ allowed: false, count: 110, limit: 100 });
    const key = kv.keys().find((k) => k.startsWith('rl:p:'))!;
    expect(kv.raw(key)).toBe('60');
    expect((await limiter.consume('p', 40, 100, 3600)).allowed).toBe(true);
    await limiter.release('p', 30, 3600);
    expect(kv.raw(key)).toBe('70');
    expect((await limiter.consume('p', 0, 100, 3600)).count).toBe(0);
    clock.advanceSeconds(3600);
    expect((await limiter.consume('p', 100, 100, 3600)).allowed).toBe(true);
  });

  it('offset WIB: window harian reset pada 00:00 WIB (17:00 UTC)', async () => {
    const { limiter, clock } = setup(Date.UTC(2026, 9, 5, 16, 59, 0)); // 23:59 WIB
    expect((await limiter.hit('d', 1, 86_400, WIB_OFFSET_SECONDS)).allowed).toBe(true);
    expect((await limiter.hit('d', 1, 86_400, WIB_OFFSET_SECONDS)).allowed).toBe(false);
    clock.advanceSeconds(61); // 00:00:01 WIB
    expect((await limiter.hit('d', 1, 86_400, WIB_OFFSET_SECONDS)).allowed).toBe(true);
  });
});
