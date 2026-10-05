import { hkdfSync } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { isIP } from 'node:net';
import { createSecureContext } from 'node:tls';
import { secretsLoader, EnvRecord } from './secrets.loader';

/**
 * Konfigurasi terketik untuk seluruh BFF. Secret dibaca dari env yang sudah di-resolve oleh
 * `secretsLoader` (pola *_FILE → /run/secrets/*). Nilai secret tidak pernah dimasukkan ke pesan error/log.
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

  static fromProcessEnv(): AppConfig {
    secretsLoader();
    return new AppConfig(process.env);
  }

  /** Dipanggil di main.ts sebelum aplikasi dibuat. Pesan hanya memuat NAMA secret. */
  assertStartupSecrets(): void {
    const problems: string[] = [];
    const check = (name: string): void => {
      const v = this.str(name);
      if (!v) problems.push(`${name} belum dikonfigurasi`);
      else if (v.length < MIN_SECRET_LENGTH) problems.push(`${name} kurang dari ${MIN_SECRET_LENGTH} karakter`);
    };
    for (const name of REQUIRED_SECRETS) {
      if (name === 'ODOO_BFF_API_KEY' && !this.str(name) && this.str('EFN_BFF_API_KEY')) continue;
      check(name);
    }
    const attempt = (fn: () => void, fallback: string): void => {
      try {
        fn();
      } catch (err) {
        problems.push(err instanceof Error ? err.message : fallback);
      }
    };
    attempt(() => void this.piiActiveKeyVersion, 'PII_ENCRYPTION_KEY tidak valid'); // seluruh key ring + versi aktif
    attempt(() => void this.paymentProvider, 'PAYMENT_PROVIDER tidak valid');
    attempt(() => void this.posOtpAmountTolerancePct, 'POS_OTP_AMOUNT_TOLERANCE_PCT tidak valid');
    if (this.whatsappEnabled) {
      check('WHATSAPP_TOKEN');
      if (!this.str('WHATSAPP_PHONE_NUMBER_ID')) problems.push('WHATSAPP_PHONE_NUMBER_ID belum dikonfigurasi');
    }
    if (!this.str('PG_HOST') && !this.str('DATABASE_URL')) problems.push('PG_HOST (atau DATABASE_URL) belum dikonfigurasi');
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
    if (problems.length > 0) throw new ConfigError(`Konfigurasi TLS tidak valid: ${problems.join('; ')}`);
    return { plaintextChannels: plaintext };
  }

  // ───────────── helper ─────────────
  private str(name: string): string | undefined {
    const v = this.env[name];
    return v === undefined || v === '' ? undefined : v;
  }

  private secret(name: string, fallbackName?: string): string {
    const v = this.str(name) ?? (fallbackName ? this.str(fallbackName) : undefined);
    if (!v) throw new ConfigError(`Secret ${name} belum dikonfigurasi`);
    if (v.length < MIN_SECRET_LENGTH) throw new ConfigError(`Secret ${name} terlalu pendek`);
    return v;
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

  private decodePiiKey(name: string): Buffer {
    const raw = this.str(name);
    if (!raw) throw new ConfigError(`${name} belum dikonfigurasi`);
    const s = raw.trim();
    if (!/^[A-Za-z0-9+/]+={0,2}$/.test(s)) throw new ConfigError(`${name} harus base64`);
    const key = Buffer.from(s, 'base64');
    if (key.length !== PII_KEY_BYTES) throw new ConfigError(`${name} harus ${PII_KEY_BYTES} byte (base64)`);
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
      if (this.str('PII_ENCRYPTION_KEY') === undefined) {
        throw new ConfigError(
          'PII_ENCRYPTION_KEY (v1) belum dikonfigurasi; bila v1 sudah dipensiunkan setelah re-encrypt, set PII_RETIRED_VERSIONS=1',
        );
      }
      ring.set(1, this.piiEncryptionKey);
    }
    for (const name of Object.keys(this.env)) {
      const m = /^PII_ENCRYPTION_KEY_V(\d{1,3})$/.exec(name);
      if (!m || this.str(name) === undefined) continue;
      const version = Number(m[1]);
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
  /** Rentang angka proof-of-work (0..maxnumber). Makin besar makin mahal bagi bot (dan perangkat pelanggan). */
  get altchaMaxNumber(): number {
    return this.int('ALTCHA_MAX_NUMBER', 100_000, 1000, 10_000_000);
  }
  /** Kontrak: TTL 5 menit. */
  get altchaTtlSeconds(): number {
    return this.int('ALTCHA_TTL_SECONDS', 300, 60, 900);
  }
  /** Batas `GET /v1/auth/challenge` per IP (hash) per `OTP_RL_WINDOW_SECONDS`. */
  get altchaChallengeRateLimit(): number {
    return this.int('ALTCHA_CHALLENGE_RL_LIMIT', 120, 1, 100_000);
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
  /** Batas re-OTP `POST /v1/me/reauth` (permintaan kode) per member per `OTP_RL_WINDOW_SECONDS`. */
  get dsarReauthLimit(): number {
    return this.int('DSAR_REAUTH_LIMIT', 3, 1, 100);
  }

  // ───────────── Loyalty ─────────────
  /** SEC-08: plafon poin per pesan `loyalty.earn.requested` (kontrak §4, default 100). */
  get loyaltyMaxPointsPerEvent(): number {
    return this.int('LOYALTY_MAX_POINTS_PER_EVENT', 100, 1, 1_000_000);
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
        password: this.str('PG_PASSWORD'),
        max: this.int('PG_POOL_MAX', 10, 1, 100),
      };
    } else {
      const raw = this.str('DATABASE_URL');
      if (!raw) throw new ConfigError('PG_HOST atau DATABASE_URL belum dikonfigurasi');
      const u = new URL(raw);
      const urlPassword = u.password ? decodeURIComponent(u.password) : undefined;
      base = {
        host: u.hostname,
        port: u.port ? Number(u.port) : 5432,
        database: decodeURIComponent(u.pathname.replace(/^\//, '')),
        user: decodeURIComponent(u.username),
        password: this.str('PG_PASSWORD') ?? urlPassword,
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
    const password = this.str('REDIS_PASSWORD') ?? (u.password ? decodeURIComponent(u.password) : undefined);
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
    const urlPassword = u.password ? decodeURIComponent(u.password) : undefined;
    u.username = '';
    u.password = '';
    const password = this.str('RABBITMQ_PASSWORD') ?? urlPassword ?? '';
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
  /** Kredensial user MinIO khusus BFF (`efn-bff`, secret `minio_bff_secret`), bukan root. Dibaca lazy. */
  get minioConnection(): MinioConnectionConfig {
    const accessKey = this.str('MINIO_ACCESS_KEY');
    const secretKey = this.str('MINIO_SECRET_KEY');
    if (!accessKey || !secretKey) {
      throw new ConfigError('Kredensial MinIO (MINIO_ACCESS_KEY / MINIO_SECRET_KEY_FILE) belum dikonfigurasi');
    }
    return { ...this.minioTransport, accessKey, secretKey };
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
