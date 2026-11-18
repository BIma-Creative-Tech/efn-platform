/**
 * Rate limit jendela tetap per kunci (IP sumber) di memori. Cukup untuk replika tunggal (L-124);
 * multi-replika butuh Redis (gelombang berikut).
 */
export interface RateResult {
  allowed: boolean;
  retryAfterSec: number;
}

export class RateLimiter {
  private readonly buckets = new Map<string, { start: number; count: number }>();

  constructor(
    private readonly limit: number,
    private readonly windowMs: number,
    private readonly maxKeys = 10_000,
  ) {}

  hit(key: string, nowMs: number): RateResult {
    if (this.buckets.size >= this.maxKeys) this.purge(nowMs);
    const b = this.buckets.get(key);
    if (!b || nowMs - b.start >= this.windowMs) {
      this.buckets.set(key, { start: nowMs, count: 1 });
      return { allowed: true, retryAfterSec: 0 };
    }
    b.count += 1;
    if (b.count > this.limit) {
      return { allowed: false, retryAfterSec: Math.max(1, Math.ceil((b.start + this.windowMs - nowMs) / 1000)) };
    }
    return { allowed: true, retryAfterSec: 0 };
  }

  private purge(nowMs: number): void {
    for (const [k, b] of this.buckets) {
      if (nowMs - b.start >= this.windowMs) this.buckets.delete(k);
    }
    if (this.buckets.size >= this.maxKeys) this.buckets.clear();
  }
}

/**
 * IP sumber di belakang Traefik: entri TERKANAN X-Forwarded-For = alamat yang dilihat proxy tepercaya terdekat
 * (entri kiri bisa dipalsukan klien). Tanpa header -> 'unknown' (satu ember bersama, lebih ketat).
 */
export function clientIp(xForwardedFor: string | null | undefined): string {
  if (!xForwardedFor) return 'unknown';
  const parts = xForwardedFor.split(',').map((s) => s.trim()).filter(Boolean);
  const last = parts[parts.length - 1];
  return last ? last.slice(0, 64) : 'unknown';
}
