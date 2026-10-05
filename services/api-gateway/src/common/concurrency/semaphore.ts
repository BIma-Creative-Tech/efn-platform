/**
 * Semaphore per proses dengan antrean terbatas + batas waktu tunggu (SEC-19).
 * `acquire()` → fungsi `release` (panggil tepat sekali); melempar `SemaphoreFullError` bila antrean penuh dan
 * `SemaphoreTimeoutError` bila menunggu melewati `timeoutMs`. FIFO.
 */
export class SemaphoreFullError extends Error {}
export class SemaphoreTimeoutError extends Error {}

interface Waiter {
  resolve: (release: () => void) => void;
  reject: (err: Error) => void;
  timer: NodeJS.Timeout;
}

export class Semaphore {
  private active = 0;
  private readonly queue: Waiter[] = [];

  constructor(
    private readonly max: number,
    private readonly maxQueue: number,
    private readonly timeoutMs: number,
  ) {
    if (!Number.isInteger(max) || max < 1) throw new Error('max semaphore harus ≥ 1');
  }

  get inUse(): number {
    return this.active;
  }

  get waiting(): number {
    return this.queue.length;
  }

  acquire(): Promise<() => void> {
    if (this.active < this.max) {
      this.active += 1;
      return Promise.resolve(this.releaser());
    }
    if (this.queue.length >= this.maxQueue) return Promise.reject(new SemaphoreFullError('antrean penuh'));
    return new Promise((resolve, reject) => {
      const waiter: Waiter = {
        resolve,
        reject,
        timer: setTimeout(() => {
          const i = this.queue.indexOf(waiter);
          if (i >= 0) this.queue.splice(i, 1);
          reject(new SemaphoreTimeoutError('menunggu terlalu lama'));
        }, this.timeoutMs),
      };
      waiter.timer.unref();
      this.queue.push(waiter);
    });
  }

  async run<T>(fn: () => Promise<T>): Promise<T> {
    const release = await this.acquire();
    try {
      return await fn();
    } finally {
      release();
    }
  }

  private releaser(): () => void {
    let done = false;
    return () => {
      if (done) return;
      done = true;
      const next = this.queue.shift();
      if (next) {
        clearTimeout(next.timer);
        next.resolve(this.releaser()); // slot dipindahkan langsung (active tetap)
      } else {
        this.active -= 1;
      }
    };
  }
}
