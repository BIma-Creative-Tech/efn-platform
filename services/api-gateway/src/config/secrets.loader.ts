/**
 * Daftar secret BFF dan nama file Docker secret-nya (R-S01, SEC-29).
 *
 * SEC-29 (Gelombang 12): TIDAK ADA secret yang disalin ke `process.env`. Env mudah bocor lewat child process
 * (mewarisi environ), `process.report` / diagnostic report (memuat `environmentVariables`), heap/crash dump, dan
 * library pihak ketiga yang men-dump env saat error. Semua secret dibaca oleh `AppConfig` langsung dari file ke
 * memori objek config (string/Buffer privat, di-cache), dengan urutan sumber:
 *
 *   1. `<NAMA>_FILE` (path Docker secret, mis. `JWT_SECRET_FILE=/run/secrets/jwt_secret`) — file wajib terbaca;
 *   2. fallback nama kontrak `${SECRETS_DIR:-/run/secrets}/<nama_secret>` (docs/04 §2, §7.3);
 *   3. env literal `<NAMA>` — HANYA bila `APP_ENV=dev`. Di luar dev, keberadaan env literal secret saja sudah
 *      membuat BFF gagal start (`assertStartupSecrets`), karena nilainya sudah ada di environ proses.
 *
 * Fungsi lama `secretsLoader()` (yang mengisi `process.env[X]`) DIHAPUS. Spec `no-secret-env-write.spec.ts`
 * gagal bila ada kode yang menulis `process.env.<SECRET> = ...`.
 */
export const KNOWN_SECRET_FILES: Readonly<Record<string, string>> = {
  JWT_SECRET: 'jwt_secret',
  OTP_PEPPER: 'otp_pepper',
  DISCOUNT_TOKEN_SECRET: 'discount_token_secret',
  ODOO_BFF_API_KEY: 'odoo_bff_api_key',
  WHATSAPP_TOKEN: 'whatsapp_token',
  WEBHOOK_PAYMENT_SECRET: 'webhook_payment_secret',
  // v3 (docs/04 §8.1, §8.3)
  ALTCHA_HMAC_KEY: 'altcha_hmac_key',
  PAYMENT_PROVIDER_API_KEY: 'payment_provider_api_key',
  // v4.1 (docs/04 §9.6 K-7): kunci endpoint migrasi, TERPISAH dari odoo_bff_api_key; wajib hanya bila MIGRATION_ENABLED
  MIGRATION_API_KEY: 'migration_api_key',
  // G21 docs/04 §13.1 no. 3 (L-61): kunci HMAC publikasi kebijakan poin Odoo → BFF; TIDAK wajib saat start
  // (endpoint `PUT /v1/pos/loyalty/policies/*` menjawab 503 bila belum ada)
  LOYALTY_POLICY_SIGNING_KEY: 'loyalty_policy_signing_key',
  // G22 docs/04 §14.A.9 (QR meja): kunci HKDF token sesi meja `fts1` (+ kunci lama opsional untuk verifikasi saat rotasi)
  // dan HMAC event Odoo → BFF `/v1/pos/fnb/events`. Wajib hanya bila FNB_QR_ENABLED=true. `ODOO_API_KEY_QR_<PT>` bernama
  // dinamis per PT (pola ODOO_API_KEY_ONLINE_<KANAL>) → tidak di daftar ini.
  FNB_TABLE_TOKEN_KEY: 'fnb_table_token_key',
  FNB_TABLE_TOKEN_KEY_PREV: 'fnb_table_token_key_prev',
  ODOO_FNB_EVENT_HMAC: 'odoo_fnb_event_hmac',
  // G23 docs/04 §15.5/§15.8: HMAC event reservasi Odoo → BFF `/v1/pos/booking/events`. Wajib hanya bila BOOKING_ENABLED=true.
  // `ODOO_API_KEY_BOOKING_<PT>` bernama dinamis per PT (pola QR) → tidak di daftar ini.
  ODOO_BOOKING_EVENT_HMAC: 'odoo_booking_event_hmac',
  // Kredensial per service (docs/04 §7.3, SEC-02) — bukan lagi kredensial bersama/superuser
  PG_PASSWORD: 'pg_password_bff',
  RABBITMQ_PASSWORD: 'rabbitmq_password_bff',
  REDIS_PASSWORD: 'redis_password_bff',
  MINIO_SECRET_KEY: 'minio_bff_secret',
};

/** Alias nama env lama yang juga diperlakukan sebagai secret (nama kontrak §2). */
export const SECRET_ALIASES: Readonly<Record<string, string>> = {
  EFN_BFF_API_KEY: 'ODOO_BFF_API_KEY',
};

/** Nama secret Docker default untuk file yang dibaca langsung oleh AppConfig sebagai Buffer. */
export const DIRECT_SECRET_FILES = {
  PII_ENCRYPTION_KEY: 'pii_encryption_key',
  MINIO_ACCESS_KEY: 'minio_bff_access_key',
} as const;

/**
 * Semua nama env yang BERISI nilai secret (bukan path). Dipakai `AppConfig.assertStartupSecrets` (tolak literal di
 * luar dev) dan spec statis. `PII_ENCRYPTION_KEY_V<N>` dicocokkan lewat `isSecretEnvName`.
 */
export const SECRET_ENV_NAMES: readonly string[] = [
  ...Object.keys(KNOWN_SECRET_FILES),
  ...Object.keys(SECRET_ALIASES),
  'PII_ENCRYPTION_KEY',
  'MINIO_ACCESS_KEY',
];

export function isSecretEnvName(name: string): boolean {
  return SECRET_ENV_NAMES.includes(name) || /^PII_ENCRYPTION_KEY_V\d{1,3}$/.test(name);
}

/** Env *_FILE yang berisi path material TLS. */
export function isTlsMaterialFileVar(key: string): boolean {
  return key.endsWith('_CA_FILE') || key === 'TLS_CERT_FILE' || key === 'TLS_KEY_FILE';
}

export type EnvRecord = Record<string, string | undefined>;
