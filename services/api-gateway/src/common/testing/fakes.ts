/**
 * Fake in-memory untuk unit test (bukan untuk runtime). Tidak memuat secret nyata:
 * nilai secret dibangkitkan acak per test run.
 */
import { ExecutionContext } from '@nestjs/common';
import { randomBytes } from 'node:crypto';
import { AppConfig } from '../../config/app-config';
import { isSecretEnvName } from '../../config/secrets.loader';
import { WhatsAppSender } from '../../otp/whatsapp/whatsapp-sender';
import { Db, Queryable, QueryResultLike } from '../db/db';
import { ApiError } from '../errors/api-error';
import { PiiCipher } from '../pii/crypto';
import { KvStore } from '../redis/kv-store';

export class FakeClock {
  constructor(public ms: number = Date.UTC(2026, 9, 5, 3, 0, 0)) {}
  now = (): number => this.ms;
  advanceSeconds(s: number): void {
    this.ms += s * 1000;
  }
}

interface Entry {
  value: string | Map<string, string>;
  expiresAt: number | null;
}

export class FakeKvStore implements KvStore {
  private readonly data = new Map<string, Entry>();

  constructor(private readonly clock: () => number = () => Date.now()) {}

  private live(key: string): Entry | undefined {
    const e = this.data.get(key);
    if (!e) return undefined;
    if (e.expiresAt !== null && this.clock() >= e.expiresAt) {
      this.data.delete(key);
      return undefined;
    }
    return e;
  }

  private exp(ttlSeconds: number): number {
    return this.clock() + ttlSeconds * 1000;
  }

  keys(): string[] {
    return [...this.data.keys()].filter((k) => this.live(k) !== undefined);
  }

  /** Untuk asersi: isi mentah (string atau object hash). */
  raw(key: string): string | Record<string, string> | undefined {
    const e = this.live(key);
    if (!e) return undefined;
    return typeof e.value === 'string' ? e.value : Object.fromEntries(e.value);
  }

  async get(key: string): Promise<string | null> {
    const e = this.live(key);
    return e && typeof e.value === 'string' ? e.value : null;
  }

  async setEx(key: string, value: string, ttlSeconds: number): Promise<void> {
    this.data.set(key, { value, expiresAt: this.exp(ttlSeconds) });
  }

  async setNxEx(key: string, value: string, ttlSeconds: number): Promise<boolean> {
    if (this.live(key)) return false;
    this.data.set(key, { value, expiresAt: this.exp(ttlSeconds) });
    return true;
  }

  async del(key: string): Promise<number> {
    if (!this.live(key)) return 0;
    this.data.delete(key);
    return 1;
  }

  async incrWindow(key: string, windowSeconds: number): Promise<number> {
    const e = this.live(key);
    if (!e) {
      this.data.set(key, { value: '1', expiresAt: this.exp(windowSeconds) });
      return 1;
    }
    const n = Number(e.value) + 1;
    e.value = String(n);
    return n;
  }

  async incrByWindow(key: string, by: number, windowSeconds: number): Promise<number> {
    const e = this.live(key);
    if (!e) {
      this.data.set(key, { value: String(by), expiresAt: this.exp(windowSeconds) });
      return by;
    }
    const n = Number(e.value) + by;
    e.value = String(n);
    return n;
  }

  async ttl(key: string): Promise<number> {
    const e = this.live(key);
    if (!e) return -2;
    if (e.expiresAt === null) return -1;
    return Math.ceil((e.expiresAt - this.clock()) / 1000);
  }

  async hsetEx(key: string, fields: Record<string, string>, ttlSeconds: number): Promise<void> {
    this.data.set(key, { value: new Map(Object.entries(fields)), expiresAt: this.exp(ttlSeconds) });
  }

  async hgetall(key: string): Promise<Record<string, string>> {
    const e = this.live(key);
    return e && e.value instanceof Map ? Object.fromEntries(e.value) : {};
  }

  private hash(key: string): Map<string, string> {
    const e = this.live(key);
    if (e && e.value instanceof Map) return e.value;
    const m = new Map<string, string>();
    this.data.set(key, { value: m, expiresAt: null });
    return m;
  }

  async hincrby(key: string, field: string, by: number): Promise<number> {
    const m = this.hash(key);
    const n = Number(m.get(field) ?? '0') + by;
    m.set(field, String(n));
    return n;
  }

  async hset(key: string, field: string, value: string): Promise<void> {
    this.hash(key).set(field, value);
  }
}

export interface RecordedQuery {
  text: string;
  params: readonly unknown[];
}

export type QueryHandler = (text: string, params: readonly unknown[]) => QueryResultLike<Record<string, unknown>> | undefined;

/** Fake Db: mencatat query; hasil ditentukan handler. `tx` menjalankan fn langsung (tanpa rollback). */
export class FakeDb implements Db {
  readonly queries: RecordedQuery[] = [];

  constructor(private readonly handler: QueryHandler = () => undefined) {}

  async query<R = Record<string, unknown>>(text: string, params: readonly unknown[] = []): Promise<QueryResultLike<R>> {
    this.queries.push({ text, params });
    const res = this.handler(text, params) ?? { rows: [], rowCount: 0 };
    return res as unknown as QueryResultLike<R>;
  }

  async tx<T>(fn: (q: Queryable) => Promise<T>): Promise<T> {
    return fn(this);
  }

  find(fragment: string): RecordedQuery[] {
    return this.queries.filter((q) => q.text.includes(fragment));
  }
}

export interface FakeMemberRow {
  id: string;
  member_hash: string;
  member_ref_enc: Buffer;
  created_at: Date;
  deleted_at: Date | null;
}

/**
 * Emulasi minimal `platform.member` untuk FakeDb (query MemberService). Mengembalikan `undefined` untuk query lain
 * sehingga bisa dirangkai dengan handler lain: `(t, p) => members.handle(t, p) ?? other(t, p)`.
 */
export class FakeMemberTable {
  readonly rows = new Map<string, FakeMemberRow>();

  byHash(hash: string): FakeMemberRow | undefined {
    return [...this.rows.values()].find((r) => r.member_hash === hash);
  }

  handle(text: string, params: readonly unknown[]): QueryResultLike<Record<string, unknown>> | undefined {
    if (text.includes('INSERT INTO platform.member')) {
      const [id, hash, enc] = params as [string, string, Buffer];
      if (this.byHash(hash)) return { rows: [], rowCount: 0 };
      this.rows.set(id, { id, member_hash: hash, member_ref_enc: enc, created_at: new Date(), deleted_at: null });
      return { rows: [{ id }], rowCount: 1 };
    }
    if (text.includes('FROM platform.member WHERE member_hash')) {
      const r = this.byHash(params[0] as string);
      return r ? { rows: [{ id: r.id, deleted_at: r.deleted_at }], rowCount: 1 } : { rows: [], rowCount: 0 };
    }
    if (text.includes('FROM platform.member WHERE id') && text.includes('deleted_at IS NULL')) {
      const r = this.rows.get(params[0] as string);
      if (!r || r.deleted_at) return { rows: [], rowCount: 0 };
      return { rows: [{ id: r.id, member_hash: r.member_hash, member_ref_enc: r.member_ref_enc }], rowCount: 1 };
    }
    if (text.includes('SELECT created_at FROM platform.member')) {
      const r = this.rows.get(params[0] as string);
      return r ? { rows: [{ created_at: r.created_at }], rowCount: 1 } : { rows: [], rowCount: 0 };
    }
    return undefined;
  }
}

export class FakeWhatsAppSender implements WhatsAppSender {
  readonly otps: { to: string; code: string }[] = [];
  readonly templates: { to: string; name: string; params: readonly string[] }[] = [];
  failNext = false;

  async sendOtp(to: string, code: string): Promise<void> {
    if (this.failNext) {
      this.failNext = false;
      throw new Error('simulated WA failure');
    }
    this.otps.push({ to, code });
  }

  async sendTemplate(to: string, name: string, params: readonly string[]): Promise<void> {
    if (this.failNext) {
      this.failNext = false;
      throw new Error('simulated WA failure');
    }
    this.templates.push({ to, name, params });
  }

  lastCode(): string {
    const last = this.otps[this.otps.length - 1];
    if (!last) throw new Error('belum ada OTP terkirim');
    return last.code;
  }
}

export function randomSecret(): string {
  return randomBytes(32).toString('hex');
}

/**
 * Config sintetis untuk unit test. Material TLS tidak disentuh kecuali test memanggil getter transport;
 * `readFile` dapat diganti untuk mensimulasikan CA/sertifikat.
 */
export function testConfig(
  overrides: Record<string, string> = {},
  readFile: (path: string) => Buffer = () => {
    throw new Error('ENOENT (testConfig)');
  },
): AppConfig {
  // SEC-29: secret disuplai sebagai FILE (`<NAMA>_FILE=/test-secrets/<NAMA>`), bukan env literal — jalur yang sama
  // dengan produksi. Override bernama secret ikut dipindah ke file; nilai '' = secret tidak dikonfigurasi.
  // Pengecualian: MINIO_ACCESS_KEY tetap literal karena test-nya justru menguji aturan literal (APP_ENV=dev).
  const secrets: Record<string, string> = {
    JWT_SECRET: randomSecret(),
    OTP_PEPPER: randomSecret(),
    DISCOUNT_TOKEN_SECRET: randomSecret(),
    ODOO_BFF_API_KEY: randomSecret(),
    WEBHOOK_PAYMENT_SECRET: randomSecret(),
    PII_ENCRYPTION_KEY: randomBytes(32).toString('base64'),
    ALTCHA_HMAC_KEY: randomSecret(),
  };
  const env: Record<string, string> = {
    NODE_ENV: 'test',
    OUTBOX_RELAY_INTERVAL_MS: '0',
    RETENTION_PURGE_INTERVAL_MS: '0',
    LOYALTY_RESERVATION_SWEEP_INTERVAL_MS: '0',
    DATABASE_URL: 'postgresql://efn_bff@localhost:5432/efn_platform',
  };
  for (const [k, v] of Object.entries(overrides)) {
    if (isSecretEnvName(k) && k !== 'MINIO_ACCESS_KEY') {
      if (v === '') delete secrets[k];
      else secrets[k] = v;
    } else {
      env[k] = v;
    }
  }
  for (const k of Object.keys(secrets)) env[`${k}_FILE`] ??= `${TEST_SECRETS_DIR}/${k}`;
  return new AppConfig(env, (path) => {
    if (path.startsWith(`${TEST_SECRETS_DIR}/`)) {
      const name = path.slice(TEST_SECRETS_DIR.length + 1);
      if (name in secrets) return Buffer.from(secrets[name], 'utf8'); // buffer baru: AppConfig me-nol-kannya
    }
    return readFile(path);
  });
}

const TEST_SECRETS_DIR = '/test-secrets';

/** Cipher PII dengan kunci dari config test. */
export function testCipher(config: AppConfig): PiiCipher {
  return PiiCipher.fromConfig(config);
}

export function httpContext(req: unknown): ExecutionContext {
  return {
    switchToHttp: () => ({ getRequest: () => req, getResponse: () => ({}), getNext: () => undefined }),
    getType: () => 'http',
  } as unknown as ExecutionContext;
}

/** Tangkap ApiError dari promise/fungsi; gagal bila tidak melempar ApiError. */
export async function catchApiError(fn: () => unknown): Promise<ApiError> {
  try {
    await fn();
  } catch (err) {
    if (err instanceof ApiError) return err;
    throw err;
  }
  throw new Error('diharapkan ApiError, tetapi tidak ada error');
}
