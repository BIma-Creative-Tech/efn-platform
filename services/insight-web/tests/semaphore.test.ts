import { describe, expect, it } from 'vitest';
import { Semaphore, concurrencyFromEnv } from '@/lib/semaphore';

describe('Semaphore (pembatas query ClickHouse)', () => {
  it('membatasi jumlah in-flight dan melayani antrean FIFO', async () => {
    const s = new Semaphore(2);
    const r1 = await s.acquire(1000);
    const r2 = await s.acquire(1000);
    expect(s.inFlight).toBe(2);
    const order: number[] = [];
    const p3 = s.acquire(1000).then((r) => {
      order.push(3);
      return r;
    });
    const p4 = s.acquire(1000).then((r) => {
      order.push(4);
      return r;
    });
    expect(s.waiting).toBe(2);
    r1();
    const r3 = await p3;
    r2();
    const r4 = await p4;
    expect(order).toEqual([3, 4]);
    r3();
    r4();
    r4(); // rilis ganda diabaikan
    expect(s.inFlight).toBe(0);
  });

  it('antrean melewati batas waktu -> ditolak dan dikeluarkan dari antrean', async () => {
    const s = new Semaphore(1);
    const r1 = await s.acquire(1000);
    await expect(s.acquire(20)).rejects.toThrow('semaphore_timeout');
    expect(s.waiting).toBe(0);
    r1();
    expect(s.inFlight).toBe(0);
  });

  it('antrean penuh -> ditolak segera', async () => {
    const s = new Semaphore(1, 1);
    await s.acquire(1000);
    void s.acquire(1000).catch(() => undefined);
    await expect(s.acquire(1000)).rejects.toThrow('semaphore_full');
  });

  it('concurrencyFromEnv: default 12, rentang 1..24 (= max_concurrent_queries_for_user profil ClickHouse)', () => {
    expect(concurrencyFromEnv(undefined)).toBe(12);
    expect(concurrencyFromEnv('')).toBe(12);
    expect(concurrencyFromEnv('8')).toBe(8);
    expect(concurrencyFromEnv('1')).toBe(1);
    expect(concurrencyFromEnv('24')).toBe(24);
    expect(concurrencyFromEnv('25')).toBe(12); // di atas batas profil ClickHouse -> galat 202; ditolak, pakai default
    expect(concurrencyFromEnv('0')).toBe(12);
    expect(concurrencyFromEnv('65')).toBe(12);
    expect(concurrencyFromEnv('x')).toBe(12);
    expect(concurrencyFromEnv('3.5')).toBe(12);
    expect(concurrencyFromEnv('-2')).toBe(12);
  });

  it('release saat antrean menunggu menyerahkan slot ke penunggu tertua, in-flight tidak pernah melebihi batas', async () => {
    const s = new Semaphore(3);
    let peak = 0;
    const work = async (): Promise<void> => {
      const release = await s.acquire(5000);
      peak = Math.max(peak, s.inFlight);
      await new Promise((r) => setTimeout(r, 5));
      release();
    };
    await Promise.all(Array.from({ length: 20 }, work));
    expect(peak).toBeLessThanOrEqual(3);
    expect(s.inFlight).toBe(0);
    expect(s.waiting).toBe(0);
  });

  it('batas tidak valid (0, negatif, pecahan) ditolak saat konstruksi', () => {
    for (const n of [0, -1, 1.5, Number.NaN]) expect(() => new Semaphore(n)).toThrow();
  });
});
