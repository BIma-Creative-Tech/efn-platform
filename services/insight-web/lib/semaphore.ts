/**
 * Semaphore sederhana (FIFO) untuk membatasi query ClickHouse yang berjalan bersamaan per proses.
 * Semua staf memakai SATU user ClickHouse `efn_insight`; satu halaman memicu 9-13 query paralel, sehingga tanpa
 * pembatas di aplikasi beberapa pengguna sekaligus menembus `max_concurrent_queries_for_user` (galat 202,
 * ditemukan uji E2E G42). Antrean > batas tunggu -> ditolak (dianggap timeout oleh pemanggil).
 */
export class Semaphore {
  private active = 0;
  private readonly queue: Array<() => void> = [];

  constructor(
    private readonly limit: number,
    private readonly maxQueue = 200,
  ) {
    if (!Number.isInteger(limit) || limit < 1) throw new Error('limit semaphore tidak valid');
  }

  get inFlight(): number {
    return this.active;
  }

  get waiting(): number {
    return this.queue.length;
  }

  /** Menunggu slot; `timeoutMs` habis -> reject Error('semaphore_timeout'). */
  acquire(timeoutMs: number): Promise<() => void> {
    if (this.active < this.limit) {
      this.active += 1;
      return Promise.resolve(this.releaser());
    }
    if (this.queue.length >= this.maxQueue) return Promise.reject(new Error('semaphore_full'));
    return new Promise((resolve, reject) => {
      const grant = () => {
        clearTimeout(timer);
        this.active += 1;
        resolve(this.releaser());
      };
      const timer = setTimeout(() => {
        const i = this.queue.indexOf(grant);
        if (i >= 0) this.queue.splice(i, 1);
        reject(new Error('semaphore_timeout'));
      }, timeoutMs);
      this.queue.push(grant);
    });
  }

  private releaser(): () => void {
    let done = false;
    return () => {
      if (done) return;
      done = true;
      this.active -= 1;
      const next = this.queue.shift();
      if (next) next();
    };
  }
}

/**
 * Batas dari env `INSIGHT_CH_MAX_CONCURRENCY` (1..24, default 12). Batas atas = `max_concurrent_queries_for_user` profil
 * ClickHouse `insight_readonly` (24, insight-access.sql; dijaga test_insight_access.py): nilai di atasnya pasti memicu
 * galat 202 dari ClickHouse. Default 12 < 24 agar ekspor CSV tetap muat.
 */
export function concurrencyFromEnv(raw: string | undefined): number {
  const n = raw === undefined || raw === '' ? 12 : Number(raw);
  return Number.isInteger(n) && n >= 1 && n <= 24 ? n : 12;
}
