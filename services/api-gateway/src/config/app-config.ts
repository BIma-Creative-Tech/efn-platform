import { hkdfSync } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { isIP } from 'node:net';
import { createSecureContext } from 'node:tls';
import { posix } from 'node:path';
import { DIRECT_SECRET_FILES, EnvRecord, KNOWN_SECRET_FILES, isSecretEnvName } from './secrets.loader';

/**
 * Konfigurasi terketik untuk seluruh BFF. SEC-29: secret dibaca LANGSUNG dari file (`<NAMA>_FILE` → fallback
 * `${SECRETS_DIR:-/run/secrets}/<nama_secret>`) ke memori objek ini (cache privat) dan TIDAK PERNAH disalin ke
 * `process.env`. Env literal secret hanya diterima bila `APP_ENV=dev` (lihat `secrets.loader.ts`).
 * Nilai secret tidak pernah dimasukkan ke pesan error/log.
 *
 * Enkripsi in transit (docs/04 §7.1, ADR-022): setiap koneksi keluar (PostgreSQL, Redis, RabbitMQ, MinIO) dan
 * server HTTP BFF memakai TLS ≥ 1.2 dengan verifikasi sertifikat terhadap CA internal (`/run/secrets/efn_ca_crt`).
 * Bila TLS diminta tetapi CA/sertifikat tidak terbaca → `ConfigError` (gagal start, tidak ada fallback diam-diam).
 * Plaintext hanya lewat flag eksplisit `EFN_ALLOW_PLAINTEXT=true`, yang DITOLAK bila NODE_ENV/APP_ENV=production.
 *
 * Dibuat lewat factory di CommonModule (`AppConfig.fromProcessEnv()`); di test dibuat langsung
 * dengan record env sintetis dan pembaca file palsu.
 */
export class ConfigError extends Error {}

/**
 * Secret yang wajib ada agar BFF boleh start (fail fast, R-S01).
 * v3: `ALTCHA_HMAC_KEY` (secret `altcha_hmac_key`, docs/04 §8.1) wajib karena `POST /v1/auth/otp/request` selalu
 * memverifikasi ALTCHA.
 */
export const REQUIRED_SECRETS = ['JWT_SECRET', 'OTP_PEPPER', 'DISCOUNT_TOKEN_SECRET', 'ODOO_BFF_API_KEY', 'ALTCHA_HMAC_KEY'] as const;

/** docs/04 §8.3: adapter payment. `disabled` = default production sampai ADR-016 diputuskan. */
export const PAYMENT_PROVIDERS = ['mock', 'xendit', 'midtrans', 'disabled'] as const;
export type PaymentProviderName = (typeof PAYMENT_PROVIDERS)[number];
const MIN_SECRET_LENGTH = 16;

/** CA internal (docs/04 §7.1). Semua env `*_CA_FILE` default ke file ini (bisa dioverride `EFN_CA_FILE`). */
export const DEFAULT_CA_FILE = '/run/secrets/efn_ca_crt';
export const TLS_MIN_VERSION = 'TLSv1.2' as const;
export const PII_KEY_BYTES = 32;

/**
 * Default trust proxy: loopback + RFC1918 (10/8, 172.16/12, 192.168/16) + IPv6 ULA — yaitu Traefik dan
 * Next.js di network docker. Produksi: `TRUSTED_PROXIES` = subnet docker + rentang Cloudflare (SEC-03, docs/04 §7.5),
 * daftar disuplai DevSecOps. Pemisah: koma, titik koma, spasi, atau baris baru (daftar panjang didukung).
 */
const DEFAULT_TRUSTED_PROXIES = '127.0.0.0/8,::1/128,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,fc00::/7';
const PROXY_KEYWORDS = new Set(['loopback', 'linklocal', 'uniquelocal']);

/**
 * Validasi pasangan sertifikat/kunci server (OpenSSL via createSecureContext). Objek mutable agar unit test bisa
 * menggantinya tanpa menaruh kunci privat (meski dummy) di repo — gitleaks & kebijakan "tanpa secret di fixture".
 */
export const tlsKeyPairCheck = {
  verify: (cert: Buffer, key: Buffer): void => {
    createSecureContext({ cert, key, minVersion: TLS_MIN_VERSION });
  },
};

export type BinaryFileReader = (path: string) => Buffer;
const defaultBinaryReader: BinaryFileReader = (path) => readFileSync(path);

/** Opsi klien TLS terverifikasi (dipakai pg, ioredis, amqplib, https.Agent MinIO). */
export interface TlsClientOptions {
  ca: Buffer;
  rejectUnauthorized: true;
  minVersion: typeof TLS_MIN_VERSION;
  /** SNI + hostname verifikasi; tidak diisi bila host berupa alamat IP (SNI tidak boleh IP). */
  servername?: string;
}

export interface HttpsServerOptions {
  key: Buffer;
  cert: Buffer;
  minVersion: typeof TLS_MIN_VERSION;
}

export interface PgConnectionConfig {
  host: string;
  port: number;
  database: string;
  user: string;
  password: string | undefined;
  max: number;
  /** `false` hanya bila PG_SSLMODE=disable + EFN_ALLOW_PLAINTEXT (non-production). */
  ssl: TlsClientOptions | false;
}

export interface RedisConnectionConfig {
  /** URL tanpa kredensial. */
  url: string;
  username: string | undefined;
  password: string | undefined;
  tls: TlsClientOptions | undefined;
}

export interface AmqpConnectionConfig {
  /** URL tanpa kredensial. */
  url: string;
  username: string;
  password: string;
  tls: TlsClientOptions | undefined;
}

export interface MinioTransportConfig {
  endPoint: string;
  port: number;
  useSSL: boolean;
  tls: TlsClientOptions | undefined;
}

export interface MinioConnectionConfig extends MinioTransportConfig {
  accessKey: string;
  secretKey: string;
}

export class AppConfig {
  constructor(
    private readonly env: EnvRecord,
    private readonly readFile: BinaryFileReader = defaultBinaryReader,
  ) {}

  /**
   * SEC-29: `process.env` hanya DIBACA (nama/path non-rahasia + literal dev). Tidak ada lagi pemuat yang menulis
   * secret ke env; snapshot salinan dangkal agar perubahan env setelah start tidak memengaruhi config.
   */
  static fromProcessEnv(): AppConfig {
    return new AppConfig({ ...process.env });
  }

  /** Dipanggil di main.ts sebelum aplikasi dibuat. Pesan hanya memuat NAMA secret. */
  assertStartupSecrets(): void {
    const problems: string[] = [];
    const attempt = (fn: () => void, fallback: string): void => {
      try {
        fn();
      } catch (err) {
        problems.push(err instanceof Error ? err.message : fallback);
      }
    };
    // SEC-29: di luar APP_ENV=dev, env literal berisi secret = sudah bocor ke environ proses → tolak start.
    if (!this.isDevEnv) {
      for (const name of Object.keys(this.env)) {
        if (isSecretEnvName(name) && this.str(name) !== undefined) {
          problems.push(`${name} sebagai env literal ditolak di luar APP_ENV=dev; pakai ${name}_FILE (SEC-29)`);
        }
      }
      for (const name of ['DATABASE_URL', 'REDIS_URL', 'AMQP_URL', 'RABBITMQ_URL']) {
        attempt(() => this.assertNoUrlPassword(name), `${name} tidak valid`);
      }
    }
    const check = (name: string, alias?: string): void => {
      let v: string | undefined;
      try {
        v = this.secretValue(name) ?? (alias ? this.secretValue(alias) : undefined);
      } catch (err) {
        problems.push(err instanceof Error ? err.message : `${name} tidak valid`);
        return;
      }
      if (!v) problems.push(`${name} belum dikonfigurasi`);
      else if (v.length < MIN_SECRET_LENGTH) problems.push(`${name} kurang dari ${MIN_SECRET_LENGTH} karakter`);
    };
    for (const name of REQUIRED_SECRETS) {
      check(name, name === 'ODOO_BFF_API_KEY' ? 'EFN_BFF_API_KEY' : undefined);
    }
    attempt(() => void this.piiActiveKeyVersion, 'PII_ENCRYPTION_KEY tidak valid'); // seluruh key ring + versi aktif
    attempt(() => void this.paymentProvider, 'PAYMENT_PROVIDER tidak valid');
    attempt(() => void this.posOtpAmountTolerancePct, 'POS_OTP_AMOUNT_TOLERANCE_PCT tidak valid');
    attempt(() => void this.altchaMaxNumberCap, 'ALTCHA_MAX_NUMBER_CAP tidak valid');
    // Fase 1 loyalty POS (docs/04 §9.1): salah konfigurasi = gagal start, bukan gagal di tengah transaksi kasir
    for (const getter of [
      () => this.loyaltyEarnIdrPerPoint,
      () => this.loyaltyPointValueIdr,
      () => this.loyaltyRedeemMinPoints,
      () => this.loyaltyRedeemMaxPctOfOrder,
      () => this.loyaltyPointExpiryDays,
      () => this.loyaltyPosMaxEarnPoints,
      () => this.loyaltyReservationTtlSeconds,
      () => this.loyaltyEarnDailyMaxPoints,
      () => this.loyaltyEarnPosConfigHourlyMaxPoints,
      () => this.loyaltyEarnCompanyHourlyMaxPoints,
      () => this.loyaltyReconIntervalMs,
      () => this.loyaltyReconLookbackHours,
      () => this.loyaltyMigrationAnomalyPoints,
    ]) {
      attempt(() => void getter(), 'parameter LOYALTY_* tidak valid');
    }
    attempt(() => void this.erasureSuppressionDays, 'ERASURE_SUPPRESSION_DAYS tidak valid');
    attempt(() => void this.dsarOpenOrderMaxDeferDays, 'DSAR_OPEN_ORDER_MAX_DEFER_DAYS tidak valid (1..14, Q-P18)');
    attempt(() => void this.memberMinAge, 'MEMBER_MIN_AGE tidak valid (17..21, Q-P3)');
    attempt(() => void this.priceHunterGeoRetentionDays, 'PRICE_HUNTER_GEO_RETENTION_DAYS tidak valid');
    attempt(() => void this.altchaFailMinSample, 'ALTCHA_FAIL_MIN_SAMPLE tidak valid');
    if (this.migrationEnabled) {
      check('MIGRATION_API_KEY');
      attempt(() => {
        if (this.migrationApiKey === this.odooBffApiKey) throw new ConfigError('MIGRATION_API_KEY harus berbeda dari ODOO_BFF_API_KEY (K-7)');
      }, 'MIGRATION_API_KEY tidak valid');
    }
    if (this.whatsappEnabled) {
      check('WHATSAPP_TOKEN');
      if (!this.str('WHATSAPP_PHONE_NUMBER_ID')) problems.push('WHATSAPP_PHONE_NUMBER_ID belum dikonfigurasi');
    }
    if (!this.str('PG_HOST') && !this.str('DATABASE_URL')) problems.push('PG_HOST (atau DATABASE_URL) belum dikonfigurasi');
    // Fase 3 (docs/04 §10.13): mock json2 ditolak di production; secret kanal & HMAC event wajib bila online aktif
    problems.push(...this.onlineStartupProblems());
    if (problems.length > 0) throw new ConfigError(`Konfigurasi tidak lengkap: ${problems.join('; ')}`);
  }

  /**
   * Validasi seluruh konfigurasi transport (docs/04 §7.1) saat start: CA terbaca, URL memakai skema TLS,
   * sertifikat server valid & cocok dengan kunci. Mengembalikan daftar kanal yang berjalan PLAINTEXT
   * (hanya mungkin dengan EFN_ALLOW_PLAINTEXT di non-production) agar main.ts mencetak peringatan keras.
   */
  assertTransportSecurity(): { plaintextChannels: string[] } {
    const problems: string[] = [];
    const plaintext: string[] = [];
    const attempt = (fn: () => void): void => {
      try {
        fn();
      } catch (err) {
        problems.push(err instanceof Error ? err.message : String(err));
      }
    };
    attempt(() => void this.allowPlaintext);
    attempt(() => {
      if (this.pgConnection.ssl === false) plaintext.push('PostgreSQL');
    });
    attempt(() => {
      if (!this.redisConnection.tls) plaintext.push('Redis');
    });
    attempt(() => {
      if (!this.amqpConnection.tls) plaintext.push('RabbitMQ');
    });
    attempt(() => {
      if (!this.minioTransport.useSSL) plaintext.push('MinIO');
    });
    attempt(() => {
      if (this.httpsServerOptions === null) plaintext.push('BFF HTTP server');
    });
    // §10.4.1: mTLS ke odoo-rpc-gateway wajib bila kanal online/katalog aktif dan bukan stub dev (tanpa fallback plaintext)
    attempt(() => {
      if ((this.onlineEnabled || this.onlineCatalogEnabled) && !this.odooOnlineMock) void this.odooRpcTls;
    });
    if (problems.length > 0) throw new ConfigError(`Konfigurasi TLS tidak valid: ${problems.join('; ')}`);
    return { plaintextChannels: plaintext };
  }

  // ───────────── helper ─────────────
  private str(name: string): string | undefined {
    const v = this.env[name];
    return v === undefined || v === '' ? undefined : v;
  }

  /** SEC-29: env literal secret & URL berpassword hanya untuk APP_ENV=dev. */
  private get isDevEnv(): boolean {
    return this.str('APP_ENV') === 'dev';
  }

  private readonly secretCache = new Map<string, string | null>();

  /**
   * SEC-29: nilai secret `name` (string, di-trim) atau `undefined` bila tidak dikonfigurasi. Sumber berurutan:
   * `<name>_FILE` (wajib terbaca) → `${SECRETS_DIR:-/run/secrets}/<nama kontrak>` → env literal `<name>` (HANYA
   * APP_ENV=dev). Hasil di-cache di memori objek ini; buffer mentah file di-nol-kan. Tidak pernah menulis env.
   */
  private secretValue(name: string): string | undefined {
    if (this.secretCache.has(name)) return this.secretCache.get(name) ?? undefined;
    const literal = this.str(name);
    if (literal !== undefined && !this.isDevEnv) {
      throw new ConfigError(`${name} sebagai env literal ditolak di luar APP_ENV=dev; pakai ${name}_FILE (SEC-29)`);
    }
    let v: string | undefined;
    const file = this.str(`${name}_FILE`);
    if (file) {
      v = this.readSecretFile(file, true, name);
    } else {
      const contractName = KNOWN_SECRET_FILES[name];
      if (contractName) v = this.readSecretFile(posix.join(this.str('SECRETS_DIR') ?? '/run/secrets', contractName), false, name);
      if (!v) v = literal;
    }
    this.secretCache.set(name, v && v.length > 0 ? v : null);
    return v && v.length > 0 ? v : undefined;
  }

  private readSecretFile(path: string, required: boolean, name: string): string | undefined {
    let raw: Buffer;
    try {
      raw = this.readFile(path);
    } catch {
      if (required) throw new ConfigError(`${name}_FILE (${path}) tidak dapat dibaca`);
      return undefined;
    }
    try {
      const s = raw.toString('utf8').trim();
      return s.length > 0 ? s : undefined;
    } finally {
      raw.fill(0);
    }
  }

  private secret(name: string, fallbackName?: string): string {
    const v = this.secretValue(name) ?? (fallbackName ? this.secretValue(fallbackName) : undefined);
    if (!v) throw new ConfigError(`Secret ${name} belum dikonfigurasi`);
    if (v.length < MIN_SECRET_LENGTH) throw new ConfigError(`Secret ${name} terlalu pendek`);
    return v;
  }

  /** SEC-29: password di URL koneksi (DATABASE_URL, REDIS_URL, AMQP_URL) = secret di env → hanya APP_ENV=dev. */
  private assertNoUrlPassword(name: string): void {
    const raw = this.str(name);
    if (raw === undefined || this.isDevEnv) return;
    let hasPassword: boolean;
    try {
      hasPassword = new URL(raw).password.length > 0;
    } catch {
      throw new ConfigError(`${name} bukan URL yang valid`);
    }
    if (hasPassword) {
      throw new ConfigError(`${name} tidak boleh memuat password di luar APP_ENV=dev; pakai *_PASSWORD_FILE (SEC-29)`);
    }
  }

  private urlPassword(name: string, u: URL): string | undefined {
    if (!u.password) return undefined;
    this.assertNoUrlPassword(name);
    return decodeURIComponent(u.password);
  }

  private int(name: string, def: number, min = 0, max = Number.MAX_SAFE_INTEGER): number {
    const raw = this.str(name);
    if (raw === undefined) return def;
    const n = Number(raw);
    if (!Number.isInteger(n) || n < min || n > max) throw new ConfigError(`${name} harus bilangan bulat ${min}..${max}`);
    return n;
  }

  private bool(name: string, def: boolean): boolean {
    const raw = this.str(name);
    if (raw === undefined) return def;
    return ['1', 'true', 'yes', 'on'].includes(raw.toLowerCase());
  }

  // ───────────── runtime ─────────────
  get nodeEnv(): string {
    return this.str('NODE_ENV') ?? 'production';
  }
  get isDevelopment(): boolean {
    return this.nodeEnv === 'development';
  }
  /** Production bila NODE_ENV atau APP_ENV = production (NODE_ENV kosong dianggap production — fail safe). */
  get isProduction(): boolean {
    return this.nodeEnv === 'production' || this.str('APP_ENV') === 'production';
  }
  /**
   * G6 QA: lingkungan DEPLOYMENT production. Staging menjalankan image production (`NODE_ENV=production`, ditulis
   * deploy.sh) dengan `APP_ENV=staging` dan kontrak §8.3 mengizinkan `PAYMENT_PROVIDER=mock` di staging. Bila `APP_ENV`
   * di-set, ia yang menentukan; bila kosong → `isProduction` (fail safe). Hanya dipakai untuk pilihan provider payment;
   * kontrol keamanan lain (plaintext, simulate) tetap memakai `isProduction` yang lebih ketat.
   */
  get isProductionDeployment(): boolean {
    const appEnv = this.str('APP_ENV');
    return appEnv !== undefined ? appEnv === 'production' : this.isProduction;
  }
  get port(): number {
    return this.int('PORT', 3000, 1, 65535);
  }
  /**
   * Nilai untuk Fastify `trustProxy` (proxy-addr). Hanya hop yang terdaftar yang dipercaya untuk X-Forwarded-For
   * sehingga XFF palsu dari internet tidak bisa memalsukan IP untuk rate limit (SEC-03).
   * Setiap entri divalidasi (IPv4/IPv6 dengan prefix opsional, atau kata kunci proxy-addr); entri rusak → gagal start.
   */
  get trustProxy(): string[] {
    const raw = this.str('TRUSTED_PROXIES') ?? this.str('TRUST_PROXY') ?? DEFAULT_TRUSTED_PROXIES;
    const entries = raw
      .split(/[\s,;]+/)
      .map((s) => s.trim())
      .filter((s) => s.length > 0);
    const out: string[] = [];
    for (const e of entries) {
      if (!isValidProxyEntry(e)) throw new ConfigError(`TRUSTED_PROXIES berisi entri tidak valid: ${e}`);
      if (!out.includes(e)) out.push(e);
    }
    if (out.length === 0) throw new ConfigError('TRUSTED_PROXIES kosong');
    return out;
  }

  // ───────────── transport security (docs/04 §7.1) ─────────────
  /** `EFN_ALLOW_PLAINTEXT=true` hanya untuk dev/test; ditolak saat production. */
  get allowPlaintext(): boolean {
    const on = this.bool('EFN_ALLOW_PLAINTEXT', false);
    if (on && this.isProduction) {
      throw new ConfigError('EFN_ALLOW_PLAINTEXT=true ditolak saat NODE_ENV/APP_ENV=production');
    }
    return on;
  }

  private requirePlaintextAllowed(what: string): void {
    if (!this.allowPlaintext) {
      throw new ConfigError(
        `${what}: koneksi tanpa TLS ditolak (docs/04 §7.1). EFN_ALLOW_PLAINTEXT=true hanya untuk non-production`,
      );
    }
  }

  private caPath(envName: string): string {
    return this.str(envName) ?? this.str('EFN_CA_FILE') ?? DEFAULT_CA_FILE;
  }

  /** Opsi klien TLS verify-full. Melempar ConfigError bila CA tidak terbaca / bukan PEM. */
  tlsClientOptions(caEnvName: string, host: string, what: string): TlsClientOptions {
    const path = this.caPath(caEnvName);
    let ca: Buffer;
    try {
      ca = this.readFile(path);
    } catch {
      throw new ConfigError(`${what}: CA (${caEnvName}=${path}) tidak dapat dibaca`);
    }
    if (!ca.toString('utf8').includes('-----BEGIN CERTIFICATE-----')) {
      throw new ConfigError(`${what}: CA (${caEnvName}=${path}) bukan sertifikat PEM`);
    }
    const bareHost = host.replace(/^\[|\]$/g, '');
    return {
      ca,
      rejectUnauthorized: true,
      minVersion: TLS_MIN_VERSION,
      ...(isIP(bareHost) ? {} : { servername: bareHost }),
    };
  }

  /**
   * Sertifikat server HTTPS BFF (`TLS_CERT_FILE`, `TLS_KEY_FILE`). `null` = HTTP plaintext (hanya dengan
   * EFN_ALLOW_PLAINTEXT di non-production). Pasangan cert/key divalidasi via createSecureContext.
   */
  get httpsServerOptions(): HttpsServerOptions | null {
    const certPath = this.str('TLS_CERT_FILE');
    const keyPath = this.str('TLS_KEY_FILE');
    if (!certPath && !keyPath) {
      this.requirePlaintextAllowed('Server BFF (TLS_CERT_FILE/TLS_KEY_FILE kosong)');
      return null;
    }
    if (!certPath || !keyPath) throw new ConfigError('TLS_CERT_FILE dan TLS_KEY_FILE harus diisi berpasangan');
    let cert: Buffer;
    let key: Buffer;
    try {
      cert = this.readFile(certPath);
    } catch {
      throw new ConfigError(`Sertifikat server (TLS_CERT_FILE=${certPath}) tidak dapat dibaca`);
    }
    try {
      key = this.readFile(keyPath);
    } catch {
      throw new ConfigError(`Kunci server (TLS_KEY_FILE=${keyPath}) tidak dapat dibaca`);
    }
    if (!cert.toString('utf8').includes('-----BEGIN CERTIFICATE-----') || !key.toString('utf8').includes('PRIVATE KEY-----')) {
      throw new ConfigError('TLS_CERT_FILE/TLS_KEY_FILE bukan PEM yang valid');
    }
    try {
      tlsKeyPairCheck.verify(cert, key);
    } catch {
      // Pesan asli OpenSSL tidak diteruskan (bisa memuat detail kunci); cukup nama env
      throw new ConfigError('TLS_CERT_FILE/TLS_KEY_FILE tidak valid atau tidak berpasangan');
    }
    return { cert, key, minVersion: TLS_MIN_VERSION };
  }

  // ───────────── Metrics mTLS (docs/04 §8.5, §8.10) ─────────────
  /** Listener HTTPS terpisah untuk `/metrics` (wajib sertifikat klien). 0 = mati. Default 9464. */
  get metricsPort(): number {
    return this.int('METRICS_PORT', 9464, 0, 65535);
  }
  /** CN/SAN DNS sertifikat klien yang boleh scrape (default `prometheus`). */
  get metricsAllowedClientNames(): string[] {
    const raw = this.str('METRICS_TLS_CLIENT_ALLOWED_CN') ?? 'prometheus';
    const out = raw.split(/[\s,;]+/).filter((s) => s.length > 0);
    if (out.length === 0 || out.some((s) => !/^[A-Za-z0-9.-]{1,253}$/.test(s))) {
      throw new ConfigError('METRICS_TLS_CLIENT_ALLOWED_CN tidak valid');
    }
    return out;
  }
  /** CA untuk memverifikasi sertifikat klien metrics (`METRICS_CLIENT_CA_FILE`, default CA internal). */
  get metricsClientCa(): Buffer {
    const path = this.caPath('METRICS_CLIENT_CA_FILE');
    let ca: Buffer;
    try {
      ca = this.readFile(path);
    } catch {
      throw new ConfigError(`Metrics: CA klien (METRICS_CLIENT_CA_FILE=${path}) tidak dapat dibaca`);
    }
    if (!ca.toString('utf8').includes('-----BEGIN CERTIFICATE-----')) throw new ConfigError('Metrics: CA klien bukan PEM');
    return ca;
  }

  // ───────────── secrets ─────────────
  get jwtSecret(): string {
    return this.secret('JWT_SECRET');
  }
  /** Secret mentah `otp_pepper` — JANGAN dipakai langsung; gunakan turunan HKDF di bawah. */
  get otpPepper(): string {
    return this.secret('OTP_PEPPER');
  }
  /**
   * Kunci HMAC untuk hash kode OTP = HKDF-SHA256(ikm=otp_pepper, salt="", info="efn:otp_code:v1", 32 byte).
   * Dipisah dari pepper member_hash (domain separation): bocornya satu turunan tidak membuka yang lain,
   * dan member_hash yang tersebar di DB/analitik tidak berbagi kunci dengan hash OTP.
   */
  get otpCodeKey(): Buffer {
    return this.derive('efn:otp_code:v1');
  }
  /**
   * Pepper member_hash (kontrak §1: sha256(pepper + member_ref)) dengan
   * pepper = hex(HKDF-SHA256(ikm=otp_pepper, salt="", info="efn:member_hash:v1", 32 byte)).
   * Service lain yang perlu menghitung member_hash yang sama WAJIB memakai derivasi ini.
   */
  get memberHashPepper(): string {
    return this.derive('efn:member_hash:v1').toString('hex');
  }

  private readonly derived = new Map<string, Buffer>();
  private derive(info: string): Buffer {
    let v = this.derived.get(info);
    if (!v) {
      v = Buffer.from(hkdfSync('sha256', this.otpPepper, Buffer.alloc(0), info, 32));
      this.derived.set(info, v);
    }
    return v;
  }
  /**
   * SEC-24: kunci HMAC untuk nama key Redis yang memuat identitas (IP klien, dsb.) =
   * HKDF-SHA256(ikm=otp_pepper, salt="", info="efn:redis_key:v1", 32 byte). Nama key tidak pernah memuat IP/HP mentah.
   */
  get redisKeyPepper(): Buffer {
    return this.derive('efn:redis_key:v1');
  }
  /**
   * SEC-65 (Gelombang 13c): kunci HMAC daftar tolak migrasi pasca-erasure =
   * HKDF-SHA256(ikm=otp_pepper, salt="", info="efn:erasure_suppression:v1", 32 byte). Domain terpisah dari member_hash:
   * tag di `platform.erasure_suppression` tidak dapat di-JOIN ke kolom member_hash mana pun.
   */
  get erasureSuppressionKey(): Buffer {
    return this.derive('efn:erasure_suppression:v1');
  }
  get discountTokenSecret(): string {
    return this.secret('DISCOUNT_TOKEN_SECRET');
  }
  /** docs/04 §8.1: kunci HMAC challenge ALTCHA (secret `altcha_hmac_key`). */
  get altchaHmacKey(): string {
    return this.secret('ALTCHA_HMAC_KEY');
  }
  /** Kontrak §2: secret `odoo_bff_api_key`. `EFN_BFF_API_KEY` diterima sebagai alias. */
  get odooBffApiKey(): string {
    return this.secret('ODOO_BFF_API_KEY', 'EFN_BFF_API_KEY');
  }
  get webhookPaymentSecret(): string {
    return this.secret('WEBHOOK_PAYMENT_SECRET');
  }
  /**
   * Kunci AES-256-GCM kolom PII v1 (docs/04 §7.2): secret `pii_encryption_key` = 32 byte acak, base64
   * (`PII_ENCRYPTION_KEY_FILE`). Wajib kecuali v1 sudah dipensiunkan (`PII_RETIRED_VERSIONS` memuat 1).
   */
  get piiEncryptionKey(): Buffer {
    return this.decodePiiKey('PII_ENCRYPTION_KEY');
  }

  /**
   * U2 (rotasi kunci): versi kunci yang SUDAH dipensiunkan (`PII_RETIRED_VERSIONS`, mis. `1` atau `1,2`).
   * Kunci versi ini tidak dimuat (walau file-nya masih ada) dan tidak wajib. Saat start, `PiiKeyRingCheck` memastikan
   * tidak ada lagi ciphertext dengan versi pensiun di DB (bila ada → gagal start; jalankan `pii-reencrypt` dulu).
   */
  get piiRetiredVersions(): ReadonlySet<number> {
    const raw = this.str('PII_RETIRED_VERSIONS');
    const out = new Set<number>();
    if (!raw) return out;
    for (const part of raw.split(/[\s,;]+/).filter((s) => s.length > 0)) {
      if (!/^\d{1,3}$/.test(part) || Number(part) < 1 || Number(part) > 255) {
        throw new ConfigError('PII_RETIRED_VERSIONS harus daftar angka 1..255');
      }
      out.add(Number(part));
    }
    return out;
  }

  /**
   * SEC-29: kunci PII hanya hidup di memori objek ini (Buffer), tidak pernah di `process.env`.
   * Sumber (berurutan): `${name}_FILE` (dibaca langsung) → untuk v1 fallback
   * `${SECRETS_DIR:-/run/secrets}/pii_encryption_key` → env literal `${name}` (HANYA APP_ENV=dev).
   * Hasil decode di-cache; buffer mentah file di-nol-kan setelah decode.
   */
  private readonly piiKeys = new Map<string, Buffer | null>();

  private piiKeyIfConfigured(name: string): Buffer | null {
    if (this.piiKeys.has(name)) return this.piiKeys.get(name) ?? null;
    let key: Buffer | null = null;
    const file = this.str(`${name}_FILE`);
    if (file) {
      let raw: Buffer;
      try {
        raw = this.readFile(file);
      } catch {
        throw new ConfigError(`${name}_FILE (${file}) tidak dapat dibaca`);
      }
      key = decodeBase64Key(name, raw);
    } else if (this.str(name) !== undefined) {
      if (!this.isDevEnv) {
        throw new ConfigError(`${name} sebagai env literal ditolak di luar APP_ENV=dev; pakai ${name}_FILE (SEC-29)`);
      }
      key = decodeBase64Key(name, Buffer.from(this.str(name) ?? '', 'utf8'));
    } else if (name === 'PII_ENCRYPTION_KEY') {
      const dir = this.str('SECRETS_DIR') ?? '/run/secrets';
      let raw: Buffer | null = null;
      try {
        raw = this.readFile(posix.join(dir, DIRECT_SECRET_FILES.PII_ENCRYPTION_KEY));
      } catch {
        raw = null; // tidak ada → v1 dianggap tidak dikonfigurasi
      }
      if (raw) key = decodeBase64Key(name, raw);
    }
    this.piiKeys.set(name, key);
    return key;
  }

  private decodePiiKey(name: string): Buffer {
    const key = this.piiKeyIfConfigured(name);
    if (!key) throw new ConfigError(`${name} belum dikonfigurasi`);
    return key;
  }

  /**
   * Key ring PII (docs/04 §7.2): byte versi ciphertext = versi kunci.
   * v1 = `PII_ENCRYPTION_KEY` (← `PII_ENCRYPTION_KEY_FILE`, secret `pii_encryption_key`);
   * vN (N ≥ 2, opsional) = `PII_ENCRYPTION_KEY_V<N>` (← `PII_ENCRYPTION_KEY_V<N>_FILE`).
   * U2: v1 TIDAK wajib bila dipensiunkan (`PII_RETIRED_VERSIONS` memuat 1) — syaratnya versi aktif ≠ 1.
   * Versi pensiun tidak dimuat walau kuncinya masih dikonfigurasi.
   */
  get piiKeyRing(): Map<number, Buffer> {
    const retired = this.piiRetiredVersions;
    const ring = new Map<number, Buffer>();
    if (!retired.has(1)) {
      const v1 = this.piiKeyIfConfigured('PII_ENCRYPTION_KEY');
      if (!v1) {
        throw new ConfigError(
          'PII_ENCRYPTION_KEY (v1) belum dikonfigurasi; bila v1 sudah dipensiunkan setelah re-encrypt, set PII_RETIRED_VERSIONS=1',
        );
      }
      ring.set(1, v1);
    }
    const versions = new Set<number>();
    for (const envName of Object.keys(this.env)) {
      const m = /^PII_ENCRYPTION_KEY_V(\d{1,3})(_FILE)?$/.exec(envName);
      if (!m || this.str(envName) === undefined) continue;
      versions.add(Number(m[1]));
    }
    for (const version of [...versions].sort((a, b) => a - b)) {
      const name = `PII_ENCRYPTION_KEY_V${version}`;
      if (version < 2 || version > 255) throw new ConfigError(`${name}: versi kunci PII harus 2..255`);
      if (retired.has(version)) continue;
      ring.set(version, this.decodePiiKey(name));
    }
    if (ring.size === 0) throw new ConfigError('Key ring PII kosong');
    return ring;
  }

  /** Versi kunci untuk enkripsi baru (`PII_ACTIVE_KEY_VERSION`, default 1); harus ada di key ring & tidak pensiun. */
  get piiActiveKeyVersion(): number {
    const v = this.int('PII_ACTIVE_KEY_VERSION', 1, 1, 255);
    if (this.piiRetiredVersions.has(v)) throw new ConfigError(`PII_ACTIVE_KEY_VERSION=${v} ada di PII_RETIRED_VERSIONS`);
    if (!this.piiKeyRing.has(v)) throw new ConfigError(`PII_ACTIVE_KEY_VERSION=${v} tidak punya kunci (PII_ENCRYPTION_KEY_V${v}_FILE)`);
    return v;
  }

  /** Ukuran batch job re-encrypt PII (CLI `pii-reencrypt`). */
  get piiReencryptBatchSize(): number {
    return this.int('PII_REENCRYPT_BATCH_SIZE', 500, 1, 10_000);
  }

  // ───────────── OTP / token ─────────────
  get otpTtlSeconds(): number {
    return this.int('OTP_TTL_SECONDS', 180, 30, 900);
  }
  get otpMaxAttempts(): number {
    return this.int('OTP_MAX_ATTEMPTS', 3, 1, 10);
  }
  get otpRateWindowSeconds(): number {
    return this.int('OTP_RL_WINDOW_SECONDS', 900, 60, 86_400);
  }
  /**
   * POS: per member_ref 3 / 15 menit (kontrak §3.1).
   * Login: per (nomor, IP klien) 3 / 15 menit — bucket pengguna sah tidak bisa dihabiskan penyerang dari IP lain (SEC-06).
   */
  get otpRateMemberLimit(): number {
    return this.int('OTP_RL_MEMBER_LIMIT', 3, 1, 1000);
  }
  /** Login: plafon per nomor lintas IP (anti spam WA ke korban), default 10 / jam. */
  get otpRateMemberGlobalLimit(): number {
    return this.int('OTP_RL_MEMBER_GLOBAL_LIMIT', 10, 1, 10_000);
  }
  get otpRateMemberGlobalWindowSeconds(): number {
    return this.int('OTP_RL_MEMBER_GLOBAL_WINDOW_SECONDS', 3600, 60, 86_400);
  }
  /** Per IP (login) atau per pos_config (POS): 20 / 15 menit (kontrak §3.1). */
  get otpRateSourceLimit(): number {
    return this.int('OTP_RL_SOURCE_LIMIT', 20, 1, 100_000);
  }
  /** Batas percobaan verify per IP (login) per window — lapisan tambahan anti brute force. */
  get otpRateVerifyLimit(): number {
    return this.int('OTP_RL_VERIFY_LIMIT', 60, 1, 100_000);
  }
  /** SEC-06: budget global pengiriman OTP WhatsApp per jam untuk kanal publik (login Super-App). */
  get otpGlobalHourlyBudget(): number {
    return this.int('OTP_GLOBAL_HOURLY_BUDGET', 1000, 1, 10_000_000);
  }
  /** Budget terpisah untuk OTP POS agar serangan pumping pada login publik tidak mematikan promo di kasir. */
  get otpPosHourlyBudget(): number {
    return this.int('OTP_POS_HOURLY_BUDGET', 5000, 1, 10_000_000);
  }
  get discountTokenTtlSeconds(): number {
    return this.int('DISCOUNT_TOKEN_TTL_SECONDS', 600, 60, 3600);
  }
  get accessTokenTtlSeconds(): number {
    return this.int('ACCESS_TOKEN_TTL_SECONDS', 3600, 300, 86_400);
  }
  /**
   * SEC-07 sisa: toleransi kenaikan total order terhadap `amount` saat OTP diminta. Consume ditolak
   * `409 amount_mismatch` bila `amount_total` < 50% atau > amount × (1 + pct/100). Default 20.
   */
  get posOtpAmountTolerancePct(): number {
    return this.int('POS_OTP_AMOUNT_TOLERANCE_PCT', 20, 0, 1000);
  }

  // ───────────── ALTCHA (docs/04 §8.1, SEC-06) ─────────────
  /**
   * Rentang angka proof-of-work (0..maxnumber). Makin besar makin mahal bagi bot (dan perangkat pelanggan).
   * SEC-34 (r3): default dinaikkan 100 000 → 500 000. Rata-rata kerja = maxnumber/2 = 250 000 SHA-256:
   * ponsel kelas bawah (WebCrypto di worker ±100–250 rb hash/s) ±1–2,5 s rata-rata, kasus terburuk ±5 s; ponsel
   * menengah/desktop < 1 s. Bot native tetap murah per challenge (±25 ms), sehingga kontrol utama tetap rate limit
   * challenge per IP + plafon per nomor + budget global (ADR-028). Bila alert budget OTP menyala, operator dapat
   * menaikkan sementara ke 1 000 000 (rata-rata ±2–5 s di ponsel bawah). Batas atas 10 000 000.
   */
  get altchaMaxNumber(): number {
    return this.int('ALTCHA_MAX_NUMBER', 500_000, 1000, 10_000_000);
  }
  /**
   * SEC-34 (Gelombang 12): biaya adaptif. `maxnumber` challenge = `ALTCHA_MAX_NUMBER` × 2^level (level 0..3,
   * lihat `adaptiveAltchaMaxNumber`), dipotong ke batas atas ini. Default max(4 000 000, ALTCHA_MAX_NUMBER):
   * level 3 dari base 500 000 = 4 000 000 → rata-rata 2 jt SHA-256 ≈ 8–20 s di ponsel kelas bawah, < 4 s desktop.
   * Harus ≥ ALTCHA_MAX_NUMBER; maksimum 10 000 000 (juga batas NUMBER_RE verifikasi).
   */
  get altchaMaxNumberCap(): number {
    const base = this.altchaMaxNumber;
    const cap = this.int('ALTCHA_MAX_NUMBER_CAP', Math.max(4_000_000, base), 1000, 10_000_000);
    if (cap < base) throw new ConfigError('ALTCHA_MAX_NUMBER_CAP harus ≥ ALTCHA_MAX_NUMBER');
    return cap;
  }
  /** SEC-34: `ALTCHA_ADAPTIVE=false` mematikan kenaikan biaya (maxnumber tetap = ALTCHA_MAX_NUMBER). Default aktif. */
  get altchaAdaptive(): boolean {
    return this.bool('ALTCHA_ADAPTIVE', true);
  }
  /** Kontrak: TTL 5 menit. */
  get altchaTtlSeconds(): number {
    return this.int('ALTCHA_TTL_SECONDS', 300, 60, 900);
  }
  /** Batas `GET /v1/auth/challenge` per IP (hash) per `OTP_RL_WINDOW_SECONDS`. */
  get altchaChallengeRateLimit(): number {
    return this.int('ALTCHA_CHALLENGE_RL_LIMIT', 120, 1, 100_000);
  }
  /**
   * SEC-54 (Gelombang 13c): jumlah percobaan minimum per IP (per `OTP_RL_WINDOW_SECONDS`) sebelum rasio gagal
   * (ALTCHA tidak valid / kode OTP salah) ikut menaikkan level biaya. Mencegah 1 salah ketik = level tinggi.
   */
  get altchaFailMinSample(): number {
    return this.int('ALTCHA_FAIL_MIN_SAMPLE', 5, 1, 10_000);
  }

  // ───────────── Privasi / DSAR (docs/04 §8.4) ─────────────
  get dsarDeleteGraceDays(): number {
    return this.int('DSAR_DELETE_GRACE_DAYS', 7, 0, 90);
  }
  /** Interval job penjadwal erasure; 0 = mati (test). Default 60 s. */
  get dsarJobIntervalMs(): number {
    return this.int('DSAR_JOB_INTERVAL_MS', 60_000, 0, 24 * 3600 * 1000);
  }
  /** Batas export `GET /v1/me/data` per member per jam. */
  get dsarExportHourlyLimit(): number {
    return this.int('DSAR_EXPORT_HOURLY_LIMIT', 10, 1, 1000);
  }
  /**
   * W-18 (Q-P18, keputusan pemilik proyek 10 Okt 2026): batas maksimum penundaan erasure karena pesanan online
   * terbuka, dihitung dari `requested_at`. Setelah lewat, erasure tetap dijalankan (tautan member di order dilepas;
   * CS menyelesaikan pesanan tanpa tautan akun). Plafon 14 = keputusan; nilai lebih kecil boleh (mis. uji).
   */
  get dsarOpenOrderMaxDeferDays(): number {
    return this.int('DSAR_OPEN_ORDER_MAX_DEFER_DAYS', 14, 1, 14);
  }
  /**
   * SEC-65 (Gelombang 13c): lama (hari) tag HMAC subjek yang sudah dihapus disimpan di `platform.erasure_suppression`
   * agar impor migrasi K-7 tidak menghidupkannya kembali. Default **365** (W-19, Q-P12 diputuskan pemilik proyek
   * 10 Okt 2026; risiko uji keanggotaan oleh pemegang pepper diterima). 0 = tidak menyimpan tag (migrasi hanya
   * ditolak selama erasure masih `scheduled`/`processing`).
   */
  get erasureSuppressionDays(): number {
    return this.int('ERASURE_SUPPRESSION_DAYS', 365, 0, 730);
  }
  /**
   * W-19 (Q-P3): usia minimum member Super-App. Pendaftaran (= login OTP pertama) wajib menyertakan pernyataan
   * `age_confirmed: true`. Hanya untuk teks pesan; tidak ada tanggal lahir yang disimpan. Rentang 17..21.
   */
  get memberMinAge(): number {
    return this.int('MEMBER_MIN_AGE', 17, 17, 21);
  }
  /**
   * docs/04 §8.7/§8.9: versi & SHA-256 teks consent AKTIF per purpose: `CONSENT_<PURPOSE>_VERSION`,
   * `CONSENT_<PURPOSE>_TEXT_SHA256` (mis. `CONSENT_LOCATION_VERSION=v1`). `null` bila belum dikonfigurasi
   * (grant ditolak 503; penarikan tetap diterima).
   */
  consentActiveText(purpose: 'location' | 'marketing'): { version: string; textSha256: string } | null {
    const p = purpose.toUpperCase();
    const version = this.str(`CONSENT_${p}_VERSION`);
    const textSha256 = this.str(`CONSENT_${p}_TEXT_SHA256`)?.toLowerCase();
    if (!version && !textSha256) return null;
    // `@` diizinkan: format versi frontend `<purpose>@<tanggal>` (superapp-web lib/consent.ts). Sama dengan privacy.dto.
    if (!version || !/^[A-Za-z0-9._@-]{1,32}$/.test(version)) throw new ConfigError(`CONSENT_${p}_VERSION tidak valid`);
    if (!textSha256 || !/^[0-9a-f]{64}$/.test(textSha256)) throw new ConfigError(`CONSENT_${p}_TEXT_SHA256 harus hex 64`);
    return { version, textSha256 };
  }
  /**
   * R6 (docs/04 §8.13): kunci HMAC `receipt_token` DSAR = HKDF-SHA256(ikm=jwt_secret, salt="", info="efn:dsar_receipt:v1",
   * 32 byte). Domain terpisah dari JWT (token receipt tidak bisa dipakai sebagai JWT dan sebaliknya).
   */
  get dsarReceiptKey(): Buffer {
    if (!this.receiptKey) this.receiptKey = Buffer.from(hkdfSync('sha256', this.jwtSecret, Buffer.alloc(0), 'efn:dsar_receipt:v1', 32));
    return this.receiptKey;
  }
  private receiptKey: Buffer | undefined;
  /** Masa berlaku `receipt_token` (hari), default 30 (kontrak §8.13). */
  get dsarReceiptTtlDays(): number {
    return this.int('DSAR_RECEIPT_TTL_DAYS', 30, 1, 90);
  }
  /** Batas `GET /v1/dsar/requests/:id` per IP (hash) per 15 menit. */
  get dsarReceiptRateLimit(): number {
    return this.int('DSAR_RECEIPT_RL_LIMIT', 30, 1, 10_000);
  }
  /** Batas re-OTP `POST /v1/me/reauth` (permintaan kode) per member per `OTP_RL_WINDOW_SECONDS`. */
  get dsarReauthLimit(): number {
    return this.int('DSAR_REAUTH_LIMIT', 3, 1, 100);
  }

  // ───────────── Loyalty ─────────────
  /** SEC-08: plafon poin per pesan `loyalty.earn.requested` (kontrak §4, default 100). */
  get loyaltyMaxPointsPerEvent(): number {
    return this.int('LOYALTY_MAX_POINTS_PER_EVENT', 100, 1, 1_000_000);
  }

  // Fase 1 — loyalty di POS (docs/04 §9.1). Semua nilai default adalah PLACEHOLDER menunggu Q-13/Finance.
  /** Rupiah eligible (setelah diskon, sebelum pajak) per 1 poin; poin = floor(eligible / nilai ini). */
  get loyaltyEarnIdrPerPoint(): number {
    return this.int('LOYALTY_EARN_IDR_PER_POINT', 10_000, 1, 1_000_000_000);
  }
  /** Nilai tukar 1 poin saat redeem (Rupiah). */
  get loyaltyPointValueIdr(): number {
    return this.int('LOYALTY_POINT_VALUE_IDR', 100, 1, 1_000_000_000);
  }
  get loyaltyRedeemMinPoints(): number {
    return this.int('LOYALTY_REDEEM_MIN_POINTS', 50, 1, 1_000_000);
  }
  /** Nilai redeem maksimum sebagai persen `amount_total` order (1..100). */
  /**
   * G18 kontrak §9.9 K-18-4 (default sementara, keputusan final Finance): true = porsi yang dibayar poin TIDAK
   * menghasilkan poin — Odoo menghitung `eligible_amount = Σ eligible tanpa pajak × (1 − bayar_poin ÷ amount_total)`
   * (nilai flag dibaca Odoo dari `GET /v1/pos/loyalty/params`), BFF menolak earn yang melebihi porsi uang order.
   * false = earn atas seluruh eligible (tanpa faktor; pagar porsi uang BFF tidak dijalankan).
   */
  get loyaltyEarnExcludePointPayment(): boolean {
    return this.bool('LOYALTY_EARN_EXCLUDE_POINT_PAYMENT', true);
  }
  get loyaltyRedeemMaxPctOfOrder(): number {
    return this.int('LOYALTY_REDEEM_MAX_PCT_OF_ORDER', 50, 1, 100);
  }
  // K-2 (docs/04 §9.6): kategori tanpa poin dikelola di Odoo (flag kategori produk) — BFF tidak punya parameter ini.
  /** Masa berlaku poin earn POS (hari). 0 = tidak kedaluwarsa. */
  get loyaltyPointExpiryDays(): number {
    return this.int('LOYALTY_POINT_EXPIRY_DAYS', 365, 0, 3650);
  }
  /**
   * Plafon poin satu earn POS (pagar fraud/salah hitung; batas teknis ledger 1 000 000). SEC-61 (Gelombang 13c):
   * default turun 100 000 → 5 000 (= basket Rp50 juta pada 1 poin/Rp10.000). PLACEHOLDER menunggu Finance.
   */
  get loyaltyPosMaxEarnPoints(): number {
    return this.int('LOYALTY_POS_MAX_EARN_POINTS', 5_000, 1, 1_000_000);
  }
  /**
   * SEC-61: total poin earn POS per member per hari kalender WIB (dihitung dari `loyalty_pos_earn` di bawah kunci
   * member — tepat, tanpa balapan). Default 2 000 (= belanja Rp20 juta/hari). 0 = mati. PLACEHOLDER Finance.
   */
  get loyaltyEarnDailyMaxPoints(): number {
    return this.int('LOYALTY_EARN_DAILY_MAX_POINTS', 2_000, 0, 1_000_000);
  }
  /**
   * SEC-61: total poin earn POS per pos_config (kasir) per jam (counter Redis). Berlaku hanya bila Odoo mengirim
   * `pos_config_id` di body earn (opsional). Default 10 000. 0 = mati.
   */
  get loyaltyEarnPosConfigHourlyMaxPoints(): number {
    return this.int('LOYALTY_EARN_POS_CONFIG_HOURLY_MAX_POINTS', 10_000, 0, 100_000_000);
  }
  /**
   * SEC-61: total poin earn POS per PT per jam (counter Redis, selalu berlaku — `pos_config_id` kiriman pemanggil
   * bisa dipalsukan, PT tidak menambah kombinasi). Default 50 000. 0 = mati.
   */
  get loyaltyEarnCompanyHourlyMaxPoints(): number {
    return this.int('LOYALTY_EARN_COMPANY_HOURLY_MAX_POINTS', 50_000, 0, 1_000_000_000);
  }
  /** SEC-61: interval job rekonsiliasi ledger ↔ aturan earn. 0 = mati. Default 1 jam. */
  get loyaltyReconIntervalMs(): number {
    return this.int('LOYALTY_RECON_INTERVAL_MS', 3_600_000, 0, 24 * 3600 * 1000);
  }
  /** SEC-61: jendela data yang diperiksa tiap siklus rekonsiliasi (jam). Default 26 (≥ 1 hari + jeda). */
  get loyaltyReconLookbackHours(): number {
    return this.int('LOYALTY_RECON_LOOKBACK_HOURS', 26, 1, 24 * 14);
  }
  /** SEC-61: saldo pembuka migrasi di atas ambang ini dicatat sebagai anomali (tidak ditolak). Default 5 000. */
  get loyaltyMigrationAnomalyPoints(): number {
    return this.int('LOYALTY_MIGRATION_ANOMALY_POINTS', 5_000, 1, 1_000_000);
  }
  /** TTL reservasi redeem yang ditampilkan ke UI kasir (kontrak §9.3: 15 menit; K-3: hanya informasi UI). */
  get loyaltyReservationTtlSeconds(): number {
    return this.int('LOYALTY_RESERVATION_TTL_SECONDS', 900, 60, 3600);
  }
  /**
   * K-3 (docs/04 §9.6): reservasi `reserved` tetap menahan poin dan bisa di-commit sampai N detik sejak dibuat
   * (default 24 jam) — order offline-sync terlambat tidak kehilangan poin. Setelahnya job melepas (status `expired`).
   */
  get loyaltyReservationCommitWindowSeconds(): number {
    return this.int('LOYALTY_RESERVATION_COMMIT_WINDOW_SECONDS', 86_400, 900, 7 * 86_400);
  }
  /** K-8: rate limit `POST /v1/pos/loyalty/balance` per member dan per pos_config per `LOYALTY_RL_WINDOW_SECONDS`. */
  get loyaltyBalanceRateMemberLimit(): number {
    return this.int('LOYALTY_BALANCE_RL_MEMBER_LIMIT', 30, 1, 100_000);
  }
  get loyaltyBalanceRateSourceLimit(): number {
    return this.int('LOYALTY_BALANCE_RL_SOURCE_LIMIT', 600, 1, 1_000_000);
  }
  /** K-7: endpoint migrasi `/internal/v1/migration/*` aktif hanya bila true (default false → 404). */
  get migrationEnabled(): boolean {
    return this.bool('MIGRATION_ENABLED', false);
  }
  /** K-7: kunci terpisah dari `odoo_bff_api_key` (secret `migration_api_key`). */
  get migrationApiKey(): string {
    return this.secret('MIGRATION_API_KEY');
  }
  /** Interval job pelepas reservasi kedaluwarsa. 0 = dimatikan (test). Default 60 s. */
  get loyaltyReservationSweepIntervalMs(): number {
    return this.int('LOYALTY_RESERVATION_SWEEP_INTERVAL_MS', 60_000, 0, 24 * 3600 * 1000);
  }
  /** Reservasi `released`/`expired` (tanpa nilai keuangan) dihapus setelah N hari. */
  get loyaltyReservationRetentionDays(): number {
    return this.int('LOYALTY_RESERVATION_RETENTION_DAYS', 30, 1, 3650);
  }
  /**
   * SEC-124 (L-25l): reservasi `committed` = nilai keuangan redeem (sisi BFF rekonsiliasi K-18-6) → disimpan seperti data
   * keuangan (Q-P2, 10 th). Minimum 3650.
   */
  get loyaltyCommittedReservationRetentionDays(): number {
    return this.int('LOYALTY_COMMITTED_RESERVATION_RETENTION_DAYS', 3650, 3650, 7300);
  }
  /**
   * SEC-118 (L-25f): toleransi selisih `points_restored_value` Odoo terhadap restore yang diharapkan BFF
   * (`nilai redeem × rasio refund kumulatif` − restore sebelumnya), dalam % nilai redeem order asal; minimum 1 nilai
   * poin (pembulatan). Rasio BFF berbasis eligible (tanpa pajak) sedangkan alokasi Odoo berbasis nilai incl pajak →
   * toleransi menampung beda campuran tarif pajak/kategori dikecualikan. Default 10.
   */
  get loyaltyRestoreTolerancePct(): number {
    return this.int('LOYALTY_RESTORE_TOLERANCE_PCT', 10, 0, 100);
  }
  /** K-6: catatan earn per order (`loyalty_pos_earn`) dihapus setelah N hari (≥ masa retur). Default 400. */
  get loyaltyPosEarnRetentionDays(): number {
    return this.int('LOYALTY_POS_EARN_RETENTION_DAYS', 400, 30, 3650);
  }
  /** Rate limit per member per operasi (earn / reserve / reverse) per `LOYALTY_RL_WINDOW_SECONDS`. */
  get loyaltyRateMemberLimit(): number {
    return this.int('LOYALTY_RL_MEMBER_LIMIT', 30, 1, 100_000);
  }
  get loyaltyRateWindowSeconds(): number {
    return this.int('LOYALTY_RL_WINDOW_SECONDS', 900, 60, 86_400);
  }

  // ───────────── Retensi (UU PDP, SEC-11) ─────────────
  get otpAuditRetentionDays(): number {
    return this.int('OTP_AUDIT_RETENTION_DAYS', 90, 1, 3650);
  }
  get processedMessageRetentionDays(): number {
    return this.int('PROCESSED_MESSAGE_RETENTION_DAYS', 14, 1, 3650);
  }
  get outboxRetentionDays(): number {
    return this.int('OUTBOX_RETENTION_DAYS', 14, 1, 3650);
  }
  /** docs/04 §8.4: `price_hunter_report.user_enc` di-null-kan setelah N hari (default 90). */
  get priceHunterUserEncRetentionDays(): number {
    return this.int('PRICE_HUNTER_USER_ENC_RETENTION_DAYS', 90, 1, 3650);
  }
  /**
   * W-19 (Q-P8, keputusan pemilik proyek 10 Okt 2026): `price_hunter_report.geo_lat/geo_lng` di-NULL-kan setelah N hari
   * (default 90; sebelumnya ikut umur baris 730 h). `updated_at` ikut diperbarui agar ETL membawa versi tanpa lokasi.
   */
  get priceHunterGeoRetentionDays(): number {
    return this.int('PRICE_HUNTER_GEO_RETENTION_DAYS', 90, 1, 3650);
  }
  /** docs/04 §8.4: baris `price_hunter_report` dihapus setelah N hari (default 730 = 2 th). */
  get priceHunterReportRetentionDays(): number {
    return this.int('PRICE_HUNTER_REPORT_RETENTION_DAYS', 730, 30, 7300);
  }
  /** 0 = job purge dimatikan (test). Default 6 jam. */
  get retentionPurgeIntervalMs(): number {
    return this.int('RETENTION_PURGE_INTERVAL_MS', 6 * 3600 * 1000, 0, 7 * 24 * 3600 * 1000);
  }

  // ───────────── WhatsApp Cloud API ─────────────
  get whatsappEnabled(): boolean {
    return this.bool('WHATSAPP_ENABLED', false);
  }
  get whatsappToken(): string {
    return this.secret('WHATSAPP_TOKEN');
  }
  get whatsappPhoneNumberId(): string {
    const v = this.str('WHATSAPP_PHONE_NUMBER_ID');
    if (!v) throw new ConfigError('WHATSAPP_PHONE_NUMBER_ID belum dikonfigurasi');
    return v;
  }
  get whatsappOtpTemplate(): string {
    return this.str('WHATSAPP_OTP_TEMPLATE') ?? 'efn_otp';
  }
  get whatsappTemplateLanguage(): string {
    return this.str('WHATSAPP_TEMPLATE_LANG') ?? 'id';
  }
  get whatsappApiVersion(): string {
    return this.str('WHATSAPP_API_VERSION') ?? 'v21.0';
  }
  /** Graph API publik: wajib https; verifikasi sertifikat memakai trust store default Node (tidak diubah). */
  get whatsappGraphBaseUrl(): string {
    const v = this.str('WHATSAPP_GRAPH_BASE_URL') ?? 'https://graph.facebook.com';
    if (!v.startsWith('https://')) throw new ConfigError('WHATSAPP_GRAPH_BASE_URL harus https://');
    return v;
  }
  /** Template kategori AUTHENTICATION dengan tombol copy-code butuh komponen button berisi kode. */
  get whatsappOtpCopyCodeButton(): boolean {
    return this.bool('WHATSAPP_OTP_COPY_CODE_BUTTON', true);
  }
  get whatsappTimeoutMs(): number {
    return this.int('WHATSAPP_TIMEOUT_MS', 4000, 500, 30_000);
  }

  // ───────────── PostgreSQL ─────────────
  /** `verify-full` (default) atau `disable` (hanya dengan EFN_ALLOW_PLAINTEXT). Mode lain ditolak. */
  get pgSslMode(): 'verify-full' | 'disable' {
    const m = (this.str('PG_SSLMODE') ?? 'verify-full').toLowerCase();
    if (m === 'verify-full') return m;
    if (m === 'disable') {
      this.requirePlaintextAllowed('PostgreSQL (PG_SSLMODE=disable)');
      return m;
    }
    throw new ConfigError('PG_SSLMODE harus verify-full (disable hanya dengan EFN_ALLOW_PLAINTEXT di non-production)');
  }

  /**
   * Prioritas: PG_HOST/PG_PORT/PG_DB/PG_USER (compose final) → fallback DATABASE_URL.
   * Password selalu dari PG_PASSWORD (PG_PASSWORD_FILE → secret `pg_password_bff`) bila ada.
   * TLS: `PG_SSLMODE=verify-full` + CA `PG_SSLROOTCERT` (default /run/secrets/efn_ca_crt).
   */
  get pgConnection(): PgConnectionConfig {
    const host = this.str('PG_HOST');
    let base: Omit<PgConnectionConfig, 'ssl'>;
    if (host) {
      base = {
        host,
        port: this.int('PG_PORT', 5432, 1, 65535),
        database: this.str('PG_DB') ?? 'efn_platform',
        user: this.str('PG_USER') ?? 'efn_bff',
        password: this.secretValue('PG_PASSWORD'),
        max: this.int('PG_POOL_MAX', 10, 1, 100),
      };
    } else {
      const raw = this.str('DATABASE_URL');
      if (!raw) throw new ConfigError('PG_HOST atau DATABASE_URL belum dikonfigurasi');
      const u = new URL(raw);
      const urlPassword = this.urlPassword('DATABASE_URL', u);
      base = {
        host: u.hostname,
        port: u.port ? Number(u.port) : 5432,
        database: decodeURIComponent(u.pathname.replace(/^\//, '')),
        user: decodeURIComponent(u.username),
        password: this.secretValue('PG_PASSWORD') ?? urlPassword,
        max: this.int('PG_POOL_MAX', 10, 1, 100),
      };
    }
    const ssl = this.pgSslMode === 'verify-full' ? this.tlsClientOptions('PG_SSLROOTCERT', base.host, 'PostgreSQL') : false;
    return { ...base, ssl };
  }

  // ───────────── Redis ─────────────
  /**
   * `REDIS_URL=rediss://redis:6379` (TLS, CA `REDIS_TLS_CA_FILE`), user ACL `REDIS_USERNAME`,
   * password `REDIS_PASSWORD` (← `REDIS_PASSWORD_FILE`, secret `redis_password_bff`).
   */
  get redisConnection(): RedisConnectionConfig {
    const u = new URL(this.str('REDIS_URL') ?? 'rediss://redis:6379');
    const username = this.str('REDIS_USERNAME') ?? (u.username ? decodeURIComponent(u.username) : undefined);
    const password = this.secretValue('REDIS_PASSWORD') ?? this.urlPassword('REDIS_URL', u);
    u.username = '';
    u.password = '';
    if (u.protocol === 'rediss:') {
      return { url: u.toString(), username, password, tls: this.tlsClientOptions('REDIS_TLS_CA_FILE', u.hostname, 'Redis') };
    }
    if (u.protocol === 'redis:') {
      this.requirePlaintextAllowed('Redis (redis://)');
      return { url: u.toString(), username, password, tls: undefined };
    }
    throw new ConfigError('REDIS_URL harus rediss://');
  }

  // ───────────── RabbitMQ ─────────────
  /**
   * `AMQP_URL=amqps://<user>@rabbitmq:5671` (tanpa password), password `RABBITMQ_PASSWORD`
   * (← `RABBITMQ_PASSWORD_FILE`, secret `rabbitmq_password_bff`), CA `AMQP_TLS_CA_FILE`.
   * Username juga dipakai sebagai properti AMQP `user_id` saat publish (SEC-08).
   */
  get amqpConnection(): AmqpConnectionConfig {
    const u = new URL(this.str('AMQP_URL') ?? this.str('RABBITMQ_URL') ?? 'amqps://efn-bff@rabbitmq:5671');
    const username = u.username ? decodeURIComponent(u.username) : '';
    if (!username) throw new ConfigError('AMQP_URL harus memuat username (mis. amqps://efn-bff@rabbitmq:5671)');
    const urlPassword = this.urlPassword(this.str('AMQP_URL') ? 'AMQP_URL' : 'RABBITMQ_URL', u);
    u.username = '';
    u.password = '';
    const password = this.secretValue('RABBITMQ_PASSWORD') ?? urlPassword ?? '';
    if (u.protocol === 'amqps:') {
      return { url: u.toString(), username, password, tls: this.tlsClientOptions('AMQP_TLS_CA_FILE', u.hostname, 'RabbitMQ') };
    }
    if (u.protocol === 'amqp:') {
      this.requirePlaintextAllowed('RabbitMQ (amqp://)');
      return { url: u.toString(), username, password, tls: undefined };
    }
    throw new ConfigError('AMQP_URL harus amqps://');
  }
  get amqpPrefetch(): number {
    return this.int('AMQP_PREFETCH', 10, 1, 1000);
  }
  get amqpConsumersEnabled(): boolean {
    return this.bool('AMQP_CONSUMERS_ENABLED', true);
  }
  /** 0 = relay outbox periodik dimatikan (dipakai di test). */
  get outboxRelayIntervalMs(): number {
    return this.int('OUTBOX_RELAY_INTERVAL_MS', 5000, 0, 3_600_000);
  }

  // ───────────── MinIO / Market Scout ─────────────
  /**
   * `MINIO_ENDPOINT=minio`, `MINIO_PORT=9000`, `MINIO_USE_SSL=true` (default), CA `MINIO_CA_FILE`.
   * Bentuk lama `MINIO_ENDPOINT=host:port` masih diterima bila MINIO_PORT kosong.
   */
  get minioTransport(): MinioTransportConfig {
    const endpoint = this.str('MINIO_ENDPOINT') ?? 'minio';
    let host = endpoint;
    let portRaw = this.str('MINIO_PORT');
    const idx = endpoint.lastIndexOf(':');
    if (!portRaw && idx > 0 && !endpoint.includes(']') && endpoint.indexOf(':') === idx) {
      host = endpoint.slice(0, idx);
      portRaw = endpoint.slice(idx + 1);
    }
    const port = portRaw ? Number(portRaw) : 9000;
    if (!Number.isInteger(port) || port < 1 || port > 65535) throw new ConfigError('MINIO_PORT tidak valid');
    const useSSL = this.bool('MINIO_USE_SSL', true);
    if (!useSSL) {
      this.requirePlaintextAllowed('MinIO (MINIO_USE_SSL=false)');
      return { endPoint: host, port, useSSL, tls: undefined };
    }
    return { endPoint: host, port, useSSL, tls: this.tlsClientOptions('MINIO_CA_FILE', host, 'MinIO') };
  }
  /** Kredensial user MinIO khusus BFF (secret `minio_bff_access_key` + `minio_bff_secret`), bukan root. Dibaca lazy. */
  get minioConnection(): MinioConnectionConfig {
    const accessKey = this.minioAccessKey;
    const secretKey = this.secretValue('MINIO_SECRET_KEY');
    if (!secretKey) throw new ConfigError('Kredensial MinIO (MINIO_SECRET_KEY_FILE) belum dikonfigurasi');
    return { ...this.minioTransport, accessKey, secretKey };
  }

  /**
   * docs/04 §8.12/§8.13 (ADR-030, CVE-2026-41145/40344): access key object storage diperlakukan sebagai secret.
   * Sumber: `MINIO_ACCESS_KEY_FILE` → fallback `${SECRETS_DIR:-/run/secrets}/minio_bff_access_key` → literal
   * `MINIO_ACCESS_KEY` HANYA bila `APP_ENV=dev`. Di luar dev panjang minimal 16 (bukan nama mudah ditebak).
   * Disimpan di memori config saja (tidak disalin ke env).
   */
  private cachedMinioAccessKey: string | undefined;
  get minioAccessKey(): string {
    if (this.cachedMinioAccessKey) return this.cachedMinioAccessKey;
    const dev = this.str('APP_ENV') === 'dev';
    const file = this.str('MINIO_ACCESS_KEY_FILE');
    let v: string | undefined;
    if (file) {
      try {
        v = this.readFile(file).toString('utf8').trim();
      } catch {
        throw new ConfigError(`MINIO_ACCESS_KEY_FILE (${file}) tidak dapat dibaca`);
      }
    } else {
      try {
        v = this.readFile(posix.join(this.str('SECRETS_DIR') ?? '/run/secrets', DIRECT_SECRET_FILES.MINIO_ACCESS_KEY))
          .toString('utf8')
          .trim();
      } catch {
        v = undefined;
      }
      if (!v) {
        const literal = this.str('MINIO_ACCESS_KEY');
        if (literal !== undefined && !dev) {
          throw new ConfigError('MINIO_ACCESS_KEY literal hanya untuk APP_ENV=dev; pakai MINIO_ACCESS_KEY_FILE (docs/04 §8.13)');
        }
        v = literal;
      }
    }
    if (!v) throw new ConfigError('Access key MinIO (MINIO_ACCESS_KEY_FILE) belum dikonfigurasi');
    if (!/^[A-Za-z0-9._~+=-]{3,128}$/.test(v)) throw new ConfigError('Access key MinIO memuat karakter/panjang tidak valid');
    if (!dev && v.length < 16) throw new ConfigError('Access key MinIO terlalu pendek (< 16) di luar APP_ENV=dev (docs/04 §8.12)');
    this.cachedMinioAccessKey = v;
    return v;
  }
  get marketScoutBucket(): string {
    return this.str('MINIO_BUCKET_MARKET_SCOUT') ?? this.str('MARKET_SCOUT_BUCKET') ?? 'market-scout';
  }
  get marketScoutDailyCap(): number {
    return this.int('MARKET_SCOUT_DAILY_CAP', 20, 1, 10_000);
  }
  get marketScoutMaxBytes(): number {
    return this.int('MARKET_SCOUT_MAX_BYTES', 8 * 1024 * 1024, 1024, 32 * 1024 * 1024);
  }

  // ───────────── Decode gambar (SEC-19) ─────────────
  /** Jumlah decode sharp paralel per proses (semaphore). */
  get imageMaxConcurrency(): number {
    return this.int('IMAGE_MAX_CONCURRENCY', 2, 1, 16);
  }
  /** Panjang antrean tunggu; penuh → 429. */
  get imageQueueMax(): number {
    return this.int('IMAGE_QUEUE_MAX', 8, 0, 1000);
  }
  get imageQueueTimeoutMs(): number {
    return this.int('IMAGE_QUEUE_TIMEOUT_MS', 10_000, 100, 120_000);
  }
  /** `sharp.concurrency()` = thread libvips per operasi. */
  get sharpThreads(): number {
    return this.int('SHARP_CONCURRENCY', 1, 1, 16);
  }

  // ───────────── Payment (docs/04 §8.3) ─────────────
  /**
   * `PAYMENT_PROVIDER=mock|xendit|midtrans|disabled`. Default: `mock` di non-production, `disabled` di production.
   * Production MENOLAK `mock` (gagal start). `xendit`/`midtrans` = kerangka yang belum diuji ke sandbox vendor →
   * wajib `PAYMENT_PROVIDER_EXPERIMENTAL=true` (ADR-016 belum diputuskan).
   */
  get paymentProvider(): PaymentProviderName {
    const prodDeployment = this.isProductionDeployment;
    const raw = (this.str('PAYMENT_PROVIDER') ?? (prodDeployment ? 'disabled' : 'mock')).toLowerCase();
    if (!(PAYMENT_PROVIDERS as readonly string[]).includes(raw)) {
      throw new ConfigError(`PAYMENT_PROVIDER harus salah satu dari ${PAYMENT_PROVIDERS.join('|')}`);
    }
    const p = raw as PaymentProviderName;
    if (p === 'mock' && prodDeployment) throw new ConfigError('PAYMENT_PROVIDER=mock ditolak saat production');
    if ((p === 'xendit' || p === 'midtrans') && !this.paymentProviderExperimental) {
      throw new ConfigError(`PAYMENT_PROVIDER=${p} belum diuji ke sandbox vendor; wajib PAYMENT_PROVIDER_EXPERIMENTAL=true`);
    }
    return p;
  }
  get paymentProviderExperimental(): boolean {
    return this.bool('PAYMENT_PROVIDER_EXPERIMENTAL', false);
  }
  /** Secret `payment_provider_api_key` — dibaca hanya bila provider ≠ mock (lazy). */
  get paymentProviderApiKey(): string {
    return this.secret('PAYMENT_PROVIDER_API_KEY');
  }
  /** Override base URL vendor (mis. sandbox). Wajib https. */
  get paymentProviderBaseUrl(): string | undefined {
    const v = this.str('PAYMENT_PROVIDER_BASE_URL');
    if (v !== undefined && !v.startsWith('https://')) throw new ConfigError('PAYMENT_PROVIDER_BASE_URL harus https://');
    return v;
  }
  get paymentQrTtlSeconds(): number {
    return this.int('PAYMENT_QR_TTL_SECONDS', 600, 60, 86_400);
  }
  get paymentTimeoutMs(): number {
    return this.int('PAYMENT_TIMEOUT_MS', 8000, 500, 60_000);
  }
  /** Jeda minimum antar-`getStatus` ke provider per intent saat Odoo polling. */
  get paymentStatusRefreshSeconds(): number {
    return this.int('PAYMENT_STATUS_REFRESH_SECONDS', 10, 1, 3600);
  }
  /** Midtrans QRIS acquirer (`gopay` | `airpay shopee`). */
  get midtransQrisAcquirer(): string {
    return this.str('MIDTRANS_QRIS_ACQUIRER') ?? 'gopay';
  }

  // ───────────── Fase 3 — penjualan online (docs/04 §10.13, ADR-034 Proposed) ─────────────
  /**
   * Kill switch kanal online (§10.1): `false` → cart/quote/order `503 online_unavailable`. Default `false` di SEMUA
   * lingkungan (kontrak: production default false; fail safe — dev/staging menyalakan eksplisit lewat env compose).
   */
  get onlineEnabled(): boolean {
    return this.bool('ONLINE_ENABLED', false);
  }
  /** Katalog publik diatur terpisah (§10.1). Default = nilai `ONLINE_ENABLED`. */
  get onlineCatalogEnabled(): boolean {
    return this.bool('ONLINE_CATALOG_ENABLED', this.onlineEnabled);
  }
  /** Kanal aktif di BFF (`ONLINE_CHANNELS=RTL`), format `channel_code` §10.2. */
  get onlineChannels(): string[] {
    const raw = this.str('ONLINE_CHANNELS') ?? 'RTL';
    const out = [...new Set(raw.split(/[\s,;]+/).filter((s) => s.length > 0))];
    if (out.length === 0 || out.length > 20 || out.some((c) => !ONLINE_CHANNEL_RE.test(c))) {
      throw new ConfigError('ONLINE_CHANNELS harus daftar kode kanal ^[A-Z][A-Z0-9-]{1,15}$');
    }
    return out;
  }
  /** Fulfilment yang diizinkan BFF (`ONLINE_FULFILMENTS=pickup`). `delivery` ditunda (Q-ON3). */
  get onlineFulfilments(): ('pickup' | 'delivery')[] {
    const raw = this.str('ONLINE_FULFILMENTS') ?? 'pickup';
    const out = [...new Set(raw.split(/[\s,;]+/).filter((s) => s.length > 0))];
    if (out.length === 0 || out.some((f) => f !== 'pickup' && f !== 'delivery')) {
      throw new ConfigError('ONLINE_FULFILMENTS hanya boleh berisi pickup|delivery');
    }
    return out as ('pickup' | 'delivery')[];
  }
  /**
   * Batas bayar = lama reservasi stok (G19 §11.1, W-02/E-35, keputusan Q-ON4 "1 jam"): default **3600** (batas 60..3600
   * tidak berubah). Odoo `payment_timeout_minutes` kanal ≥ nilai ini + 10 menit (default field 70). **Urutan aktivasi:
   * Odoo 70 dulu, baru BFF 3600** — pengaman tetap: checkout menolak bila `expires_at` SO < batas order + 5 menit
   * (§10.3.5 no. 5 → `503 online_unavailable` + anomali `odoo_expiry_too_short`).
   */
  get onlinePaymentTimeoutSeconds(): number {
    return this.int('ONLINE_PAYMENT_TIMEOUT_SECONDS', 3600, 60, 3600);
  }
  get onlineQuoteTtlSeconds(): number {
    return this.int('ONLINE_QUOTE_TTL_SECONDS', 600, 30, 1800);
  }
  /** G19 §11.1 / L-32: default **2** (dari 3) sejak reservasi stok aktif — anti *stock griefing*. */
  get onlineMaxOpenOrdersPerMember(): number {
    return this.int('ONLINE_MAX_OPEN_ORDERS_PER_MEMBER', 2, 1, 50);
  }
  /** Interval sinkron katalog (detik). 0 = job mati (test). */
  get onlineCatalogSyncSeconds(): number {
    return this.int('ONLINE_CATALOG_SYNC_SECONDS', 300, 0, 86_400);
  }
  get onlinePickupCodeMaxDays(): number {
    return this.int('ONLINE_PICKUP_CODE_MAX_DAYS', 7, 1, 90);
  }
  /** Retensi tautan member di order (§10.8.3, usulan menunggu DPO, L-5). */
  get onlineOrderMemberLinkDays(): number {
    return this.int('ONLINE_ORDER_MEMBER_LINK_DAYS', 730, 30, 3650);
  }
  /**
   * Retensi `platform.online_admin_audit` (audit CLI CS). Default **3650 hari (10 th)** — W-19, Q-P19 diputuskan
   * pemilik proyek 10 Okt 2026 (sama dengan dokumen keuangan). Baris memuat aktor staf `<user>/<host>` dan alasan
   * tersamarkan (SOP: alasan tanpa nama pelanggan).
   */
  get onlineAdminAuditRetentionDays(): number {
    return this.int('ONLINE_ADMIN_AUDIT_RETENTION_DAYS', 3650, 30, 3650);
  }
  get onlineEventRetentionDays(): number {
    return this.int('ONLINE_EVENT_RETENTION_DAYS', 180, 7, 3650);
  }
  /** Interval sweeper kedaluwarsa (§10.6.5: 30 s). 0 = mati (test). */
  get onlineSweepIntervalMs(): number {
    return this.int('ONLINE_SWEEP_INTERVAL_MS', 30_000, 0, 3_600_000);
  }
  /** Interval rekonsiliasi saga (§10.6.5: 5 menit). 0 = mati. */
  get onlineReconIntervalMs(): number {
    return this.int('ONLINE_RECON_INTERVAL_MS', 300_000, 0, 24 * 3_600_000);
  }
  /** Interval rekonsiliasi pull `efn_online_status` (§10.6.5: 15 menit). 0 = mati. */
  get onlineStatusReconIntervalMs(): number {
    return this.int('ONLINE_STATUS_RECON_INTERVAL_MS', 900_000, 0, 24 * 3_600_000);
  }
  /** Interval job retensi online (§10.8.3). 0 = mati. Default 1 jam. */
  get onlineRetentionIntervalMs(): number {
    return this.int('ONLINE_RETENTION_INTERVAL_MS', 3_600_000, 0, 24 * 3_600_000);
  }
  /**
   * Rate limit §10.3.1 (PLACEHOLDER): `ONLINE_RL_<NAMA>_LIMIT` & `ONLINE_RL_<NAMA>_WINDOW_SECONDS`.
   * Nama: CATALOG, STOCK, CART, QUOTE, CHECKOUT, ORDERS, PICKUP, CANCEL, EVENTS, PROMO_MEMBER, PROMO_IP.
   * G19 §11.2 no. 6: quote yang membawa `promo_codes` juga dihitung `PROMO_MEMBER` (10 / 15 menit / member) dan
   * `PROMO_IP` (30 / 15 menit / IP) — terpisah dari `QUOTE` (anti-enumerasi kupon).
   */
  onlineRateLimit(name: OnlineRateLimitName): { limit: number; windowSeconds: number } {
    const def = ONLINE_RL_DEFAULTS[name];
    return {
      limit: this.int(`ONLINE_RL_${name}_LIMIT`, def[0], 1, 1_000_000),
      windowSeconds: this.int(`ONLINE_RL_${name}_WINDOW_SECONDS`, def[1], 1, 86_400),
    };
  }
  get minioCatalogBucket(): string {
    const v = this.str('MINIO_CATALOG_BUCKET') ?? 'catalog';
    if (!/^[a-z0-9][a-z0-9.-]{2,62}$/.test(v)) throw new ConfigError('MINIO_CATALOG_BUCKET tidak valid');
    return v;
  }

  /**
   * L-7: stub json2 (`ODOO_ONLINE_MOCK=true`) HANYA untuk dev/test. Fail-closed: diizinkan bila `APP_ENV` ∈ {dev,test},
   * atau `APP_ENV` kosong dengan `NODE_ENV` ∈ {development,test}. Selain itu (production, staging, NODE_ENV kosong) →
   * ConfigError (gagal start).
   */
  get odooOnlineMock(): boolean {
    const on = this.bool('ODOO_ONLINE_MOCK', false);
    if (!on) return false;
    const appEnv = this.str('APP_ENV');
    const devLike = appEnv !== undefined ? appEnv === 'dev' || appEnv === 'test' : this.nodeEnv === 'development' || this.nodeEnv === 'test';
    if (!devLike || this.isProductionDeployment) {
      throw new ConfigError('ODOO_ONLINE_MOCK=true hanya untuk APP_ENV=dev/test; ditolak di staging/production (L-7)');
    }
    return true;
  }
  /** §10.4.1: `ODOO_RPC_URL=https://odoo-rpc-gateway:8443` — wajib https (tanpa fallback plaintext, §7.1). */
  get odooRpcUrl(): URL {
    const raw = this.str('ODOO_RPC_URL') ?? 'https://odoo-rpc-gateway:8443';
    let u: URL;
    try {
      u = new URL(raw);
    } catch {
      throw new ConfigError('ODOO_RPC_URL bukan URL valid');
    }
    if (u.protocol !== 'https:') throw new ConfigError('ODOO_RPC_URL harus https:// (docs/04 §10.4.1)');
    if (u.username || u.password || u.search || u.hash) throw new ConfigError('ODOO_RPC_URL tidak boleh memuat kredensial/query');
    return u;
  }
  /** Header `X-Odoo-Database` (opsional; gateway tetap memaksa nilainya). */
  get odooDb(): string | undefined {
    const v = this.str('ODOO_DB');
    if (v !== undefined && !/^[A-Za-z0-9_.-]{1,63}$/.test(v)) throw new ConfigError('ODOO_DB tidak valid');
    return v;
  }
  get odooRpcTimeoutMs(): number {
    return this.int('ODOO_RPC_TIMEOUT_MS', 8000, 500, 60_000);
  }
  get odooRpcCatalogTimeoutMs(): number {
    return this.int('ODOO_RPC_CATALOG_TIMEOUT_MS', 20_000, 1000, 120_000);
  }
  /**
   * SEC-96/SEC-109 (L-16, L-19f): batas konkurensi json2 BFF → Odoo per proses, DIPISAH untuk jalur publik
   * (katalog/gambar/stok/quote — dipicu pengunjung) dan saga (place/mark_paid/cancel/refund/status). Default 2 + 2
   * = 4 (≤ `ODOO_RPC_MAXCONN` gateway / worker Odoo untuk kanal online). Lonjakan quote tidak menghabiskan slot saga.
   * Antrean penuh/menunggu > 2 s → `busy` (503 / retry consumer).
   */
  get odooRpcPublicMaxConcurrency(): number {
    return this.int('ODOO_RPC_PUBLIC_MAX_CONCURRENCY', 2, 1, 64);
  }
  get odooRpcSagaMaxConcurrency(): number {
    return this.int('ODOO_RPC_SAGA_MAX_CONCURRENCY', 2, 1, 64);
  }
  /**
   * SEC-107 (L-19c): refund buatan CS dengan nominal ≥ ambang ini wajib persetujuan approver (`approval_required`).
   * Default 0 = SEMUA refund CS butuh persetujuan (PLACEHOLDER keputusan Finance).
   */
  get onlineCsRefundApprovalMinIdr(): number {
    return this.int('ONLINE_CS_REFUND_APPROVAL_MIN_IDR', 0, 0, 1_000_000_000);
  }
  get odooRpcImageTimeoutMs(): number {
    return this.int('ODOO_RPC_IMAGE_TIMEOUT_MS', 15_000, 1000, 120_000);
  }
  /**
   * mTLS ke `odoo-rpc-gateway` (§10.4.1, §8.5): CA `ODOO_RPC_CA_FILE` (default CA internal), sertifikat klien CN
   * `api-gateway` dari `ODOO_RPC_CLIENT_CERT_FILE`/`ODOO_RPC_CLIENT_KEY_FILE` (default `TLS_CERT_FILE`/`TLS_KEY_FILE`
   * server BFF bila ber-EKU clientAuth). Tidak terbaca → ConfigError (tanpa fallback tanpa sertifikat klien).
   */
  get odooRpcTls(): TlsClientOptions & { cert: Buffer; key: Buffer } {
    const url = this.odooRpcUrl;
    const base = this.tlsClientOptions('ODOO_RPC_CA_FILE', url.hostname, 'Odoo RPC gateway');
    const certPath = this.str('ODOO_RPC_CLIENT_CERT_FILE') ?? this.str('TLS_CERT_FILE');
    const keyPath = this.str('ODOO_RPC_CLIENT_KEY_FILE') ?? this.str('TLS_KEY_FILE');
    if (!certPath || !keyPath) throw new ConfigError('ODOO_RPC_CLIENT_CERT_FILE/ODOO_RPC_CLIENT_KEY_FILE belum dikonfigurasi (mTLS)');
    let cert: Buffer;
    let key: Buffer;
    try {
      cert = this.readFile(certPath);
      key = this.readFile(keyPath);
    } catch {
      throw new ConfigError('Sertifikat klien Odoo RPC (ODOO_RPC_CLIENT_CERT_FILE/KEY_FILE) tidak dapat dibaca');
    }
    if (!cert.toString('utf8').includes('-----BEGIN CERTIFICATE-----') || !key.toString('utf8').includes('PRIVATE KEY-----')) {
      throw new ConfigError('Sertifikat klien Odoo RPC bukan PEM yang valid');
    }
    return { ...base, cert, key };
  }
  /**
   * API key `/json/2` per kanal (§10.4.1): `ODOO_API_KEY_ONLINE_<CHANNEL>_FILE` → fallback
   * `${SECRETS_DIR:-/run/secrets}/odoo_api_key_online_<channel huruf kecil>` → literal hanya APP_ENV=dev.
   * Satu key = satu user teknis = satu PT; tidak pernah dipakai untuk kanal lain.
   */
  odooApiKeyOnline(channelCode: string): string {
    // G19 §11.3 no. 3: kode berhifen (`RTL-GL`) → env `ODOO_API_KEY_ONLINE_RTL_GL_FILE`, secret `odoo_api_key_online_rtl_gl`.
    // Kedua nama diturunkan HANYA dari kode yang lolos regex §10.2 (tanpa `/`, `.`, `_`) → tidak ada path traversal,
    // dan pemetaan `-`→`_` injektif (kode kanal tidak boleh memuat `_`).
    const envName = onlineApiKeyEnvName(channelCode);
    const contractName = onlineApiKeySecretName(channelCode);
    // gen-secrets menulis placeholder `disabled` untuk kanal yang belum punya user teknis; dicek sebelum aturan panjang
    const raw = this.onlineSecretRaw(envName, contractName);
    if (raw !== undefined && raw.trim().toLowerCase() === 'disabled') {
      throw new ConfigError(`${envName} masih placeholder 'disabled' — kanal ${channelCode} belum boleh aktif (runbook odoo-online-api-key)`);
    }
    return this.onlineSecret(envName, contractName);
  }
  /** L-2: secret HMAC event Odoo → BFF (`odoo_online_event_hmac`, env `ODOO_ONLINE_EVENT_HMAC_FILE`). */
  get odooOnlineEventHmac(): string {
    const v = this.onlineSecret('ODOO_ONLINE_EVENT_HMAC', 'odoo_online_event_hmac');
    if (v === this.secretValue('ODOO_BFF_API_KEY')) throw new ConfigError('ODOO_ONLINE_EVENT_HMAC harus berbeda dari ODOO_BFF_API_KEY');
    return v;
  }

  private onlineSecretRaw(envName: string, contractName: string): string | undefined {
    const file = this.str(`${envName}_FILE`);
    if (file) return this.readSecretFile(file, true, envName);
    return this.readSecretFile(posix.join(this.str('SECRETS_DIR') ?? '/run/secrets', contractName), false, envName) ?? (this.isDevEnv ? this.str(envName) : undefined);
  }

  private readonly onlineSecretCache = new Map<string, string>();
  /** Secret Fase 3 (nama dinamis per kanal — belum di KNOWN_SECRET_FILES); aturan SEC-29 sama dengan `secretValue`. */
  private onlineSecret(envName: string, contractName: string): string {
    const cached = this.onlineSecretCache.get(envName);
    if (cached) return cached;
    const literal = this.str(envName);
    if (literal !== undefined && !this.isDevEnv) {
      throw new ConfigError(`${envName} sebagai env literal ditolak di luar APP_ENV=dev; pakai ${envName}_FILE (SEC-29)`);
    }
    const file = this.str(`${envName}_FILE`);
    let v: string | undefined;
    if (file) v = this.readSecretFile(file, true, envName);
    else v = this.readSecretFile(posix.join(this.str('SECRETS_DIR') ?? '/run/secrets', contractName), false, envName) ?? literal;
    if (!v) throw new ConfigError(`Secret ${envName} belum dikonfigurasi`);
    if (v.length < MIN_SECRET_LENGTH) throw new ConfigError(`Secret ${envName} terlalu pendek`);
    this.onlineSecretCache.set(envName, v);
    return v;
  }

  /** Validasi konfigurasi online saat start (dipanggil `assertStartupSecrets`). */
  private onlineStartupProblems(): string[] {
    const problems: string[] = [];
    const attempt = (fn: () => void): void => {
      try {
        fn();
      } catch (err) {
        problems.push(err instanceof Error ? err.message : 'konfigurasi online tidak valid');
      }
    };
    if (!this.isDevEnv) {
      for (const name of Object.keys(this.env)) {
        if ((name === 'ODOO_ONLINE_EVENT_HMAC' || /^ODOO_API_KEY_ONLINE_[A-Z0-9_]+$/.test(name)) && this.str(name) !== undefined) {
          problems.push(`${name} sebagai env literal ditolak di luar APP_ENV=dev; pakai ${name}_FILE (SEC-29)`);
        }
      }
    }
    for (const getter of [
      () => this.onlineChannels,
      () => this.onlineFulfilments,
      () => this.onlinePaymentTimeoutSeconds,
      () => this.onlineQuoteTtlSeconds,
      () => this.onlineMaxOpenOrdersPerMember,
      () => this.onlineCatalogSyncSeconds,
      () => this.minioCatalogBucket,
      () => this.odooOnlineMock,
    ]) {
      attempt(() => void getter());
    }
    if (problems.length > 0) return problems;
    if (this.onlineEnabled || this.onlineCatalogEnabled) {
      attempt(() => void this.odooOnlineEventHmac);
      if (!this.odooOnlineMock) {
        attempt(() => void this.odooRpcUrl);
        for (const ch of this.onlineChannels) attempt(() => void this.odooApiKeyOnline(ch));
      }
    }
    return problems;
  }
}

/** `channel_code` (docs/04 §10.2). */
export const ONLINE_CHANNEL_RE = /^[A-Z][A-Z0-9-]{1,15}$/;
export type OnlineRateLimitName =
  | 'CATALOG'
  | 'STOCK'
  | 'CART'
  | 'QUOTE'
  | 'CHECKOUT'
  | 'ORDERS'
  | 'PICKUP'
  | 'CANCEL'
  | 'EVENTS'
  | 'PROMO_MEMBER'
  | 'PROMO_IP';
/** Default §10.3.1 (PLACEHOLDER): [batas, window detik]. EVENTS = pengaman endpoint event Odoo (bukan kontrak). */
const ONLINE_RL_DEFAULTS: Readonly<Record<OnlineRateLimitName, readonly [number, number]>> = {
  CATALOG: [120, 60],
  STOCK: [60, 60],
  CART: [60, 60],
  QUOTE: [20, 60],
  CHECKOUT: [5, 600],
  ORDERS: [60, 60],
  PICKUP: [10, 60],
  CANCEL: [10, 600],
  EVENTS: [1200, 60],
  PROMO_MEMBER: [10, 900],
  PROMO_IP: [30, 900],
};

/**
 * `RTL` → `ODOO_API_KEY_ONLINE_RTL`; `RTL-GL` → `ODOO_API_KEY_ONLINE_RTL_GL` (file: `…_FILE`). Kode yang tidak lolos
 * regex §10.2 → ConfigError (tidak pernah dipakai membentuk nama env/path).
 */
export function onlineApiKeyEnvName(channelCode: string): string {
  if (!ONLINE_CHANNEL_RE.test(channelCode)) throw new ConfigError('kode kanal tidak valid');
  return `ODOO_API_KEY_ONLINE_${channelCode.replace(/-/g, '_')}`;
}
/** Nama secret Docker per kanal: `RTL-GL` → `odoo_api_key_online_rtl_gl` (di bawah `${SECRETS_DIR:-/run/secrets}`). */
export function onlineApiKeySecretName(channelCode: string): string {
  if (!ONLINE_CHANNEL_RE.test(channelCode)) throw new ConfigError('kode kanal tidak valid');
  const name = `odoo_api_key_online_${channelCode.toLowerCase().replace(/-/g, '_')}`;
  // pertahanan berlapis: hanya [a-z0-9_] — tidak mungkin memuat pemisah path atau segmen titik
  if (!/^odoo_api_key_online_[a-z][a-z0-9_]{1,15}$/.test(name)) throw new ConfigError('kode kanal tidak valid');
  return name;
}

/** Entri TRUSTED_PROXIES: kata kunci proxy-addr, atau IPv4/IPv6 dengan prefix opsional (/0..32 atau /0..128). */
export function isValidProxyEntry(entry: string): boolean {
  if (PROXY_KEYWORDS.has(entry)) return true;
  const slash = entry.indexOf('/');
  const addr = slash === -1 ? entry : entry.slice(0, slash);
  const family = isIP(addr);
  if (family === 0) return false;
  if (slash === -1) return true;
  const bitsRaw = entry.slice(slash + 1);
  if (!/^\d{1,3}$/.test(bitsRaw)) return false;
  const bits = Number(bitsRaw);
  return family === 4 ? bits <= 32 : bits <= 128;
}

/**
 * Decode kunci base64 32 byte dari Buffer mentah (file/env) lalu nol-kan buffer mentah (SEC-29). Pesan error hanya
 * memuat NAMA kunci.
 */
function decodeBase64Key(name: string, raw: Buffer): Buffer {
  try {
    const s = raw.toString('ascii').trim();
    if (!/^[A-Za-z0-9+/]+={0,2}$/.test(s)) throw new ConfigError(`${name} harus base64`);
    const key = Buffer.from(s, 'base64');
    if (key.length !== PII_KEY_BYTES) throw new ConfigError(`${name} harus ${PII_KEY_BYTES} byte (base64)`);
    return key;
  } finally {
    raw.fill(0);
  }
}
