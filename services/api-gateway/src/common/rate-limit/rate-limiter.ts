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

  /** Seperti `hit`, tetapi melempar 429 (format kontrak + Retry-After) bila melewati batas. */
  async enforce(key: string, limit: number, windowSeconds: number, offsetSeconds = 0): Promise<RateLimitResult> {
    const res = await this.hit(key, limit, windowSeconds, offsetSeconds);
    if (!res.allowed) throw Errors.rateLimited(res.retryAfterSeconds);
    return res;
  }
}
