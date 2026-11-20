import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { isIP } from 'node:net';

/**
 * Konfigurasi wa-unofficial (ADR-044 Proposed, docs/04 §28.9/§28.10). Secret HANYA dari file (`<NAMA>_FILE` atau
 * default `/run/secrets/<nama>`); env literal berisi secret tidak pernah dibaca. Nilai secret tidak masuk log/error.
 *
 * Gerbang start (`decideStartup`):
 *  - `WA_UNOFFICIAL_ENABLED` bukan persis `true` → mode **idle** (hanya `/health`, tidak konek RabbitMQ/WhatsApp);
 *  - `APP_ENV=production` + aktif + `WA_UNOFFICIAL_RISK_ACCEPTANCE_REF` kosong → **menolak start** (exit ≠ 0, Q-SM14);
 *  - selain itu → mode **run**.
 * TLS: RabbitMQ `amqps://` dengan CA internal; server admin HTTPS. Plaintext hanya bila `EFN_ALLOW_PLAINTEXT=true`
 * DAN `APP_ENV` ∈ {dev, test} (tanpa fallback diam-diam).
 */
export class ConfigError extends Error {}

export type Env = Record<string, string | undefined>;
export type Reader = (path: string) => Buffer;

export interface TlsClient {
  ca: Buffer;
  rejectUnauthorized: true;
  minVersion: 'TLSv1.2';
  servername?: string;
}

export interface ServerTls {
  cert: Buffer;
  key: Buffer;
  minVersion: 'TLSv1.2';
}

export interface Limits {
  ratePerMin: number;
  maxPerDay: number;
  maxPerMemberPerDay: number;
  jitterMinMs: number;
  jitterMaxMs: number;
}

export interface RunConfig {
  appEnv: string;
  riskRef: string | null;
  amqp: { url: string; username: string; password: string; tls: TlsClient | undefined; prefetch: 1 };
  sessionKey: Buffer;
  hashKey: Buffer;
  adminTokenDigest: Buffer;
  metricsTokenDigest: Buffer | null;
  stateDir: string;
  limits: Limits;
  port: number;
  serverTls: ServerTls | null;
  senderLabel: string;
  proxyUrl: string | null;
  logLevel: 'debug' | 'info' | 'warn' | 'error';
}

export interface IdleConfig {
  port: number;
  serverTls: ServerTls | null;
  reason: string;
}

export type StartupDecision = { mode: 'idle'; config: IdleConfig } | { mode: 'run'; config: RunConfig };

const MIN_TOKEN = 32;
const RISK_REF_RE = /^[A-Za-z0-9._/-]{3,64}$/;

class EnvReader {
  constructor(
    private readonly env: Env,
    private readonly read: Reader,
  ) {}

  str(n: string): string | undefined {
    const v = this.env[n];
    return v === undefined || v === '' ? undefined : v;
  }

  int(n: string, def: number, min: number, max: number): number {
    const raw = this.str(n);
    if (raw === undefined) return def;
    const v = Number(raw);
    if (!Number.isInteger(v) || v < min || v > max) throw new ConfigError(`${n} harus bilangan bulat ${min}..${max}`);
    return v;
  }

  get appEnv(): string {
    return this.str('APP_ENV') ?? 'production'; // kosong = production (fail safe)
  }

  get plaintextAllowed(): boolean {
    return this.str('EFN_ALLOW_PLAINTEXT') === 'true' && (this.appEnv === 'dev' || this.appEnv === 'test');
  }

  file(envName: string, defPath: string | null, required: boolean): Buffer | null {
    const path = this.str(envName) ?? defPath;
    if (!path) {
      if (required) throw new ConfigError(`${envName} belum dikonfigurasi`);
      return null;
    }
    try {
      return this.read(path);
    } catch {
      if (required || this.str(envName)) throw new ConfigError(`${envName} (${path}) tidak dapat dibaca`);
      return null;
    }
  }

  secretString(envName: string, defPath: string, required: boolean): string | null {
    const raw = this.file(envName, defPath, required);
    if (!raw) return null;
    try {
      const s = raw.toString('utf8').trim();
      if (!s) {
        if (required) throw new ConfigError(`${envName} kosong`);
        return null;
      }
      return s;
    } finally {
      raw.fill(0);
    }
  }

  serverTls(): ServerTls | null {
    const cert = this.file('TLS_CERT_FILE', '/run/secrets/wa_unofficial_tls_crt', false);
    const key = this.file('TLS_KEY_FILE', '/run/secrets/wa_unofficial_tls_key', false);
    if (cert && key) return { cert, key, minVersion: 'TLSv1.2' };
    if (this.plaintextAllowed) return null;
    throw new ConfigError('TLS_CERT_FILE/TLS_KEY_FILE wajib (server admin HTTPS); plaintext hanya EFN_ALLOW_PLAINTEXT=true di APP_ENV dev/test');
  }
}

/** Kunci sesi: base64 (32 byte) atau hex 64. */
export function decodeKey32(s: string, name: string): Buffer {
  const hex = /^[0-9a-fA-F]{64}$/.test(s) ? Buffer.from(s, 'hex') : null;
  const key = hex ?? Buffer.from(s, 'base64');
  if (key.length !== 32) throw new ConfigError(`${name} harus 32 byte (base64 atau hex 64)`);
  return key;
}

export function tokenDigest(token: string): Buffer {
  return createHash('sha256').update(token, 'utf8').digest();
}

export function decideStartup(env: Env, read: Reader = (p) => readFileSync(p)): StartupDecision {
  const r = new EnvReader(env, read);
  const port = r.int('PORT', 8443, 1, 65535);
  if (r.str('WA_UNOFFICIAL_ENABLED') !== 'true') {
    return { mode: 'idle', config: { port, serverTls: r.serverTls(), reason: 'WA_UNOFFICIAL_ENABLED bukan "true"' } };
  }
  const refRaw = r.str('WA_UNOFFICIAL_RISK_ACCEPTANCE_REF');
  if (refRaw !== undefined && !RISK_REF_RE.test(refRaw)) throw new ConfigError('WA_UNOFFICIAL_RISK_ACCEPTANCE_REF tidak valid');
  if (r.appEnv === 'production' && !refRaw) {
    throw new ConfigError(
      'APP_ENV=production dengan WA_UNOFFICIAL_ENABLED=true ditolak tanpa WA_UNOFFICIAL_RISK_ACCEPTANCE_REF (penerimaan risiko tertulis pemilik proyek + Legal, Q-SM14)',
    );
  }

  // RabbitMQ (user efn-wa-unofficial, amqps + CA internal)
  const u = new URL(r.str('AMQP_URL') ?? 'amqps://efn-wa-unofficial@rabbitmq:5671');
  const username = decodeURIComponent(u.username);
  if (!username) throw new ConfigError('AMQP_URL harus memuat username (efn-wa-unofficial)');
  if (u.password) throw new ConfigError('AMQP_URL tidak boleh memuat password; pakai RABBITMQ_PASSWORD_FILE');
  u.username = '';
  const password = r.secretString('RABBITMQ_PASSWORD_FILE', '/run/secrets/rabbitmq_password_wa_unofficial', true) as string;
  let tls: TlsClient | undefined;
  if (u.protocol === 'amqps:') {
    const ca = r.file('AMQP_TLS_CA_FILE', '/run/secrets/efn_ca_crt', true) as Buffer;
    if (!ca.toString('utf8').includes('-----BEGIN CERTIFICATE-----')) throw new ConfigError('AMQP_TLS_CA_FILE bukan PEM');
    tls = { ca, rejectUnauthorized: true, minVersion: 'TLSv1.2', ...(isIP(u.hostname) ? {} : { servername: u.hostname }) };
  } else if (u.protocol === 'amqp:') {
    if (!r.plaintextAllowed) throw new ConfigError('RabbitMQ tanpa TLS (amqp://) ditolak; plaintext hanya EFN_ALLOW_PLAINTEXT=true di APP_ENV dev/test');
  } else {
    throw new ConfigError('AMQP_URL harus amqps://');
  }

  const sessionKey = decodeKey32(r.secretString('WA_UNOFFICIAL_SESSION_KEY_FILE', '/run/secrets/wa_unofficial_session_key', true) as string, 'wa_unofficial_session_key');
  const hashKeyStr = r.secretString('WA_UNOFFICIAL_HASH_KEY_FILE', '/run/secrets/wa_unofficial_hash_key', true) as string;
  if (hashKeyStr.length < MIN_TOKEN) throw new ConfigError('wa_unofficial_hash_key terlalu pendek');
  const admin = r.secretString('WA_UNOFFICIAL_ADMIN_TOKEN_FILE', '/run/secrets/wa_unofficial_admin_token', true) as string;
  if (admin.length < MIN_TOKEN) throw new ConfigError('wa_unofficial_admin_token terlalu pendek (min 32 karakter)');
  const metrics = r.secretString('WA_UNOFFICIAL_METRICS_TOKEN_FILE', '/run/secrets/wa_unofficial_metrics_token', false);
  if (metrics !== null && metrics.length < MIN_TOKEN) throw new ConfigError('wa_unofficial_metrics_token terlalu pendek');
  if (metrics !== null && metrics === admin) throw new ConfigError('wa_unofficial_metrics_token harus berbeda dari token admin');

  const limits: Limits = {
    ratePerMin: r.int('WA_UNOFFICIAL_RATE_PER_MIN', 10, 1, 60),
    maxPerDay: r.int('WA_UNOFFICIAL_MAX_PER_DAY', 300, 1, 5000),
    maxPerMemberPerDay: r.int('WA_UNOFFICIAL_MAX_PER_MEMBER_PER_DAY', 1, 1, 10),
    // Jitter kontrak 2–8 dtk; boleh dinaikkan, tidak boleh di bawah 2 dtk.
    jitterMinMs: r.int('WA_UNOFFICIAL_JITTER_MIN_MS', 2000, 2000, 60_000),
    jitterMaxMs: r.int('WA_UNOFFICIAL_JITTER_MAX_MS', 8000, 2000, 120_000),
  };
  if (limits.jitterMaxMs < limits.jitterMinMs) throw new ConfigError('WA_UNOFFICIAL_JITTER_MAX_MS < WA_UNOFFICIAL_JITTER_MIN_MS');

  let proxyUrl: string | null = null;
  const proxyRaw = r.str('WA_UNOFFICIAL_PROXY_URL');
  if (proxyRaw) {
    let p: URL;
    try {
      p = new URL(proxyRaw);
    } catch {
      throw new ConfigError('WA_UNOFFICIAL_PROXY_URL bukan URL valid');
    }
    if (p.protocol !== 'http:' && p.protocol !== 'https:') throw new ConfigError('WA_UNOFFICIAL_PROXY_URL harus http(s)://');
    if (p.username || p.password) throw new ConfigError('WA_UNOFFICIAL_PROXY_URL tidak boleh memuat kredensial');
    proxyUrl = p.toString();
  }

  const label = (r.str('WA_UNOFFICIAL_SENDER_LABEL') ?? 'Promo EFN').replace(/[\r\n*_~`]/g, '').slice(0, 40) || 'Promo EFN';
  const lvl = r.str('LOG_LEVEL');
  const logLevel = lvl === 'debug' || lvl === 'warn' || lvl === 'error' ? lvl : 'info';

  return {
    mode: 'run',
    config: {
      appEnv: r.appEnv,
      riskRef: refRaw ?? null,
      amqp: { url: u.toString(), username, password, tls, prefetch: 1 },
      sessionKey,
      hashKey: Buffer.from(hashKeyStr, 'utf8'),
      adminTokenDigest: tokenDigest(admin),
      metricsTokenDigest: metrics ? tokenDigest(metrics) : null,
      stateDir: r.str('WA_UNOFFICIAL_STATE_DIR') ?? '/var/lib/wa-unofficial',
      limits,
      port,
      serverTls: r.serverTls(),
      senderLabel: label,
      proxyUrl,
      logLevel,
    },
  };
}
