import { Provider } from '@nestjs/common';
import type Redis from 'ioredis';
import { REDIS_CLIENT } from '../common/redis/kv-store';

export interface CartLine {
  product_id: number;
  qty: number;
}
export interface CartGroup {
  channel_code: string;
  outlet_code: string;
  fulfilment: 'pickup' | 'delivery';
  lines: CartLine[];
}
/** Isi cart (docs/04 §10.3.3): HANYA `product_id` + `qty` per grup kanal — tanpa harga. */
export interface Cart {
  version: number;
  groups: CartGroup[];
}

export const CART_STORE = Symbol('CART_STORE');
export const CART_TTL_SECONDS = 7 * 86_400;

/**
 * Penyimpanan cart per member: Redis hash `cart:<member_id>` (`v` = versi, `b` = JSON grup), TTL 7 hari diperpanjang
 * setiap tulis (`member_id` UUID opak, bukan PII). Tulis = compare-and-set atomik (Lua) terhadap versi → konflik
 * dua perangkat menjadi `409 cart_version_conflict`, bukan tulisan yang hilang.
 */
export interface CartStore {
  load(memberId: string): Promise<Cart>;
  /** true bila versi tersimpan = `expected` dan cart ditulis dengan versi `expected + 1`. */
  save(memberId: string, expected: number, groups: CartGroup[]): Promise<boolean>;
  clear(memberId: string): Promise<void>;
  /** Hapus hanya bila versi masih `version` (checkout tidak menghapus perubahan cart setelah quote). */
  clearIfVersion(memberId: string, version: number): Promise<void>;
}

const CAS_SCRIPT = `
local v = redis.call('HGET', KEYS[1], 'v')
if not v then v = '0' end
if v ~= ARGV[1] then return 0 end
redis.call('HSET', KEYS[1], 'v', ARGV[2], 'b', ARGV[3])
redis.call('EXPIRE', KEYS[1], ARGV[4])
return 1`;
const CLEAR_IF_SCRIPT = `
local v = redis.call('HGET', KEYS[1], 'v')
if v == ARGV[1] then redis.call('DEL', KEYS[1]) return 1 end
return 0`;

export function cartKey(memberId: string): string {
  return `cart:${memberId}`;
}

export class RedisCartStore implements CartStore {
  constructor(private readonly redis: Redis) {}

  async load(memberId: string): Promise<Cart> {
    const h = await this.redis.hgetall(cartKey(memberId));
    return parseStored(h.v, h.b);
  }
  async save(memberId: string, expected: number, groups: CartGroup[]): Promise<boolean> {
    const r = await this.redis.eval(CAS_SCRIPT, 1, cartKey(memberId), String(expected), String(expected + 1), JSON.stringify(groups), String(CART_TTL_SECONDS));
    return Number(r) === 1;
  }
  async clear(memberId: string): Promise<void> {
    await this.redis.del(cartKey(memberId));
  }
  async clearIfVersion(memberId: string, version: number): Promise<void> {
    await this.redis.eval(CLEAR_IF_SCRIPT, 1, cartKey(memberId), String(version));
  }
}

export function parseStored(v: string | undefined, b: string | undefined): Cart {
  const version = v !== undefined && /^\d{1,9}$/.test(v) ? Number(v) : 0;
  if (!b) return { version, groups: [] };
  try {
    const groups = JSON.parse(b) as CartGroup[];
    return { version, groups: Array.isArray(groups) ? groups : [] };
  } catch {
    return { version, groups: [] };
  }
}

/** Fake in-memory (test). */
export class MemoryCartStore implements CartStore {
  readonly data = new Map<string, Cart>();
  async load(memberId: string): Promise<Cart> {
    const c = this.data.get(memberId);
    return c ? { version: c.version, groups: JSON.parse(JSON.stringify(c.groups)) as CartGroup[] } : { version: 0, groups: [] };
  }
  async save(memberId: string, expected: number, groups: CartGroup[]): Promise<boolean> {
    const cur = this.data.get(memberId)?.version ?? 0;
    if (cur !== expected) return false;
    this.data.set(memberId, { version: expected + 1, groups: JSON.parse(JSON.stringify(groups)) as CartGroup[] });
    return true;
  }
  async clear(memberId: string): Promise<void> {
    this.data.delete(memberId);
  }
  async clearIfVersion(memberId: string, version: number): Promise<void> {
    if (this.data.get(memberId)?.version === version) this.data.delete(memberId);
  }
}

export const cartStoreProvider: Provider = {
  provide: CART_STORE,
  inject: [REDIS_CLIENT],
  useFactory: (redis: Redis): CartStore => new RedisCartStore(redis),
};
