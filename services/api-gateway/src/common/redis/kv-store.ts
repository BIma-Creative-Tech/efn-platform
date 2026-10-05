import type Redis from 'ioredis';

export const KV_STORE = Symbol('KV_STORE');
export const REDIS_CLIENT = Symbol('REDIS_CLIENT');

/**
 * Abstraksi sempit di atas Redis. Service domain hanya bergantung pada interface ini sehingga
 * test cukup memakai fake in-memory (src/common/testing/fakes.ts).
 */
export interface KvStore {
  get(key: string): Promise<string | null>;
  setEx(key: string, value: string, ttlSeconds: number): Promise<void>;
  /** SET key value EX ttl NX — true bila key baru dibuat (pemenang). */
  setNxEx(key: string, value: string, ttlSeconds: number): Promise<boolean>;
  del(key: string): Promise<number>;
  /** INCR atomik; TTL dipasang saat hit pertama (fixed window). */
  incrWindow(key: string, windowSeconds: number): Promise<number>;
  ttl(key: string): Promise<number>;
  /** HSET banyak field + EXPIRE dalam satu MULTI. */
  hsetEx(key: string, fields: Record<string, string>, ttlSeconds: number): Promise<void>;
  hgetall(key: string): Promise<Record<string, string>>;
  hincrby(key: string, field: string, by: number): Promise<number>;
  /** HSET satu field tanpa mengubah TTL. */
  hset(key: string, field: string, value: string): Promise<void>;
}

const INCR_WINDOW_SCRIPT = `
local c = redis.call('INCR', KEYS[1])
if c == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
return c`;

export class IoRedisKvStore implements KvStore {
  constructor(private readonly redis: Redis) {}

  get(key: string): Promise<string | null> {
    return this.redis.get(key);
  }

  async setEx(key: string, value: string, ttlSeconds: number): Promise<void> {
    await this.redis.set(key, value, 'EX', ttlSeconds);
  }

  async setNxEx(key: string, value: string, ttlSeconds: number): Promise<boolean> {
    const res = await this.redis.set(key, value, 'EX', ttlSeconds, 'NX');
    return res === 'OK';
  }

  del(key: string): Promise<number> {
    return this.redis.del(key);
  }

  async incrWindow(key: string, windowSeconds: number): Promise<number> {
    const res = await this.redis.eval(INCR_WINDOW_SCRIPT, 1, key, String(windowSeconds));
    return Number(res);
  }

  ttl(key: string): Promise<number> {
    return this.redis.ttl(key);
  }

  async hsetEx(key: string, fields: Record<string, string>, ttlSeconds: number): Promise<void> {
    const results = await this.redis.multi().hset(key, fields).expire(key, ttlSeconds).exec();
    const failed = results?.find(([err]) => err !== null);
    if (!results || failed) throw new Error('Redis MULTI hset/expire gagal');
  }

  hgetall(key: string): Promise<Record<string, string>> {
    return this.redis.hgetall(key);
  }

  hincrby(key: string, field: string, by: number): Promise<number> {
    return this.redis.hincrby(key, field, by);
  }

  async hset(key: string, field: string, value: string): Promise<void> {
    await this.redis.hset(key, field, value);
  }
}
