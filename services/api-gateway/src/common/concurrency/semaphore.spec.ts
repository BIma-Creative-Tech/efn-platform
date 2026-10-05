import { Semaphore, SemaphoreFullError, SemaphoreTimeoutError } from './semaphore';

describe('Semaphore (SEC-19)', () => {
  it('membatasi konkurensi, FIFO, dan slot berpindah ke penunggu', async () => {
    const s = new Semaphore(2, 5, 1000);
    const r1 = await s.acquire();
    const r2 = await s.acquire();
    expect(s.inUse).toBe(2);
    const order: number[] = [];
    const p3 = s.acquire().then((r) => {
      order.push(3);
      return r;
    });
    const p4 = s.acquire().then((r) => {
      order.push(4);
      return r;
    });
    expect(s.waiting).toBe(2);
    r1();
    r1(); // release ganda diabaikan
    const r3 = await p3;
    expect(s.inUse).toBe(2);
    r2();
    const r4 = await p4;
    expect(order).toEqual([3, 4]);
    r3();
    r4();
    expect(s.inUse).toBe(0);
  });

  it('antrean penuh → SemaphoreFullError; tunggu terlalu lama → SemaphoreTimeoutError', async () => {
    jest.useFakeTimers();
    try {
      const s = new Semaphore(1, 1, 50);
      await s.acquire();
      const waiting = s.acquire();
      await expect(s.acquire()).rejects.toBeInstanceOf(SemaphoreFullError);
      jest.advanceTimersByTime(60);
      await expect(waiting).rejects.toBeInstanceOf(SemaphoreTimeoutError);
      expect(s.waiting).toBe(0);
    } finally {
      jest.useRealTimers();
    }
  });

  it('run() melepas slot walau fn melempar', async () => {
    const s = new Semaphore(1, 0, 10);
    await expect(s.run(async () => Promise.reject(new Error('x')))).rejects.toThrow('x');
    expect(s.inUse).toBe(0);
    await expect(s.run(async () => 7)).resolves.toBe(7);
  });
});
