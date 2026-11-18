/**
 * Cache `jti` token SSO di memori (replika = 1, L-124/C5). TTL 120 dtk (> umur token 60 dtk + skew 30 dtk).
 * Restart = cache hilang: token <= 60 dtk bisa dipakai ulang sekali (risiko diterima, C5).
 */
export class JtiCache {
  private readonly seen = new Map<string, number>();

  constructor(
    private readonly ttlMs = 120_000,
    private readonly maxEntries = 100_000,
  ) {}

  has(jti: string, nowMs: number): boolean {
    const exp = this.seen.get(jti);
    if (exp === undefined) return false;
    if (exp <= nowMs) {
      this.seen.delete(jti);
      return false;
    }
    return true;
  }

  add(jti: string, nowMs: number): void {
    if (this.seen.size >= this.maxEntries) this.purge(nowMs);
    if (this.seen.size >= this.maxEntries) {
      // Penuh oleh entri yang belum kedaluwarsa (tidak realistis di bawah rate limit): buang yang tertua.
      const oldest = this.seen.keys().next().value;
      if (oldest !== undefined) this.seen.delete(oldest);
    }
    this.seen.set(jti, nowMs + this.ttlMs);
  }

  size(): number {
    return this.seen.size;
  }

  private purge(nowMs: number): void {
    for (const [k, exp] of this.seen) {
      if (exp <= nowMs) this.seen.delete(k);
    }
  }
}
