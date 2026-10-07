import { Inject, Injectable } from '@nestjs/common';
import { Errors } from '../errors/api-error';
import { KV_STORE, KvStore } from '../redis/kv-store';

export interface RateLimitResult {
  allowed: boolean;
  count: number;
  limit: number;
  /** Sisa detik sampai window berikutnya (untuk header Retry-After). */
  retryAfterSeconds: number;
}

/** Offset WIB (UTC+7) agar kuota harian reset pada tengah malam Jakarta. */
export const WIB_OFFSET_SECONDS = 7 * 3600;

/**
 * Rate limiter fixed window berbasis Redis (R-S04, R-B05).
 * Key: `rl:<key>:<bucket>` dengan bucket = floor((now + offset) / window). INCR + EXPIRE atomik (Lua).
 * Kegagalan Redis dilempar (fail closed) — endpoint yang dilindungi (OTP) memang butuh Redis.
 * Key yang memuat identitas pengguna harus memakai hash, bukan nomor HP plaintext.
 */
@Injectable()
export class RateLimiter {
  now: () => number = () => Date.now();

  constructor(@Inject(KV_STORE) private readonly kv: KvStore) {}

  async hit(key: string, limit: number, windowSeconds: number, offsetSeconds = 0): Promise<RateLimitResult> {
    const nowSec = Math.floor(this.now() / 1000) + offsetSeconds;
    const bucket = Math.floor(nowSec / windowSeconds);
    const remaining = (bucket + 1) * windowSeconds - nowSec;
    const count = await this.kv.incrWindow(`rl:${key}:${bucket}`, windowSeconds);
    return { allowed: count <= limit, count, limit, retryAfterSeconds: Math.max(1, remaining) };
  }

  /** Baca counter window berjalan TANPA menambah (0 bila belum ada). Dipakai biaya ALTCHA adaptif (SEC-34). */
  async peek(key: string, windowSeconds: number, offsetSeconds = 0): Promise<number> {
    const nowSec = Math.floor(this.now() / 1000) + offsetSeconds;
    const bucket = Math.floor(nowSec / windowSeconds);
    const raw = await this.kv.get(`rl:${key}:${bucket}`);
    const n = raw === null ? 0 : Number(raw);
    return Number.isFinite(n) && n > 0 ? n : 0;
  }

  /**
   * SEC-61: kuota berbobot (mis. poin per jam). Menambah `amount` ke counter window berjalan; bila hasilnya melewati
   * `limit`, penambahan DIKOMPENSASI (dikurangi lagi) sehingga percobaan yang ditolak tidak memakan kuota sah, lalu
   * `allowed=false`. `amount` ≤ 0 → tidak menyentuh Redis.
   */
  async consume(key: string, amount: number, limit: number, windowSeconds: number, offsetSeconds = 0): Promise<RateLimitResult> {
    const nowSec = Math.floor(this.now() / 1000) + offsetSeconds;
    const bucket = Math.floor(nowSec / windowSeconds);
    const remaining = Math.max(1, (bucket + 1) * windowSeconds - nowSec);
    if (amount <= 0) return { allowed: true, count: 0, limit, retryAfterSeconds: remaining };
    const k = `rl:${key}:${bucket}`;
    const count = await this.kv.incrByWindow(k, amount, windowSeconds);
    if (count <= limit) return { allowed: true, count, limit, retryAfterSeconds: remaining };
    await this.kv.incrByWindow(k, -amount, windowSeconds);
    return { allowed: false, count, limit, retryAfterSeconds: remaining };
  }

  /** Kembalikan `amount` yang sudah di-`consume` di window berjalan (kompensasi lintas-kunci). */
  async release(key: string, amount: number, windowSeconds: number, offsetSeconds = 0): Promise<void> {
    if (amount <= 0) return;
    const nowSec = Math.floor(this.now() / 1000) + offsetSeconds;
    await this.kv.incrByWindow(`rl:${key}:${Math.floor(nowSec / windowSeconds)}`, -amount, windowSeconds);
  }

  /** Seperti `hit`, tetapi melempar 429 (format kontrak + Retry-After) bila melewati batas. */
  async enforce(key: string, limit: number, windowSeconds: number, offsetSeconds = 0): Promise<RateLimitResult> {
    const res = await this.hit(key, limit, windowSeconds, offsetSeconds);
    if (!res.allowed) throw Errors.rateLimited(res.retryAfterSeconds);
    return res;
  }
}
