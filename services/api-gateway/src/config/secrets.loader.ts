import { readFileSync } from 'node:fs';
import { posix } from 'node:path';

/**
 * Pemuat secret (R-S01). Dua mekanisme, berurutan:
 *
 * 1. Pola `X_FILE`: setiap env var `X_FILE` yang menunjuk ke file (Docker secret) dibaca menjadi `X`.
 *    PENGECUALIAN (`isDirectReadFileVar`) — file dibaca langsung oleh AppConfig sebagai Buffer dan TIDAK PERNAH
 *    disalin ke `process.env` (SEC-29; env mudah bocor lewat /proc/<pid>/environ, crash dump, child process):
 *      - `*_CA_FILE`, `TLS_CERT_FILE`, `TLS_KEY_FILE` (material TLS),
 *      - `PII_ENCRYPTION_KEY_FILE`, `PII_ENCRYPTION_KEY_V<N>_FILE` (key ring PII, docs/04 §7.2),
 *      - `MINIO_ACCESS_KEY_FILE` (docs/04 §8.12/§8.13: literal `MINIO_ACCESS_KEY` hanya untuk APP_ENV=dev, sehingga
 *        AppConfig harus bisa membedakan asal nilainya).
 * 2. Fallback nama kontrak (docs/04 §2, §7.3): bila `X` dan `X_FILE` sama-sama tidak ada,
 *    coba baca `${SECRETS_DIR:-/run/secrets}/<nama_secret>` untuk secret yang dikenal. Untuk kunci PII v1
 *    (`pii_encryption_key`) dan access key MinIO (`minio_bff_access_key`) fallback ini dilakukan AppConfig (Buffer).
 *
 * Secret tidak pernah ada di environment compose/CI sebagai plaintext. Nilai tidak pernah di-log.
 * Fungsi ini idempoten: variabel yang sudah terisi tidak ditimpa.
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
  // Kredensial per service (docs/04 §7.3, SEC-02) — bukan lagi kredensial bersama/superuser
  PG_PASSWORD: 'pg_password_bff',
  RABBITMQ_PASSWORD: 'rabbitmq_password_bff',
  REDIS_PASSWORD: 'redis_password_bff',
  MINIO_SECRET_KEY: 'minio_bff_secret',
};

/** Nama secret Docker default untuk file yang dibaca langsung oleh AppConfig (tanpa env). */
export const DIRECT_SECRET_FILES = {
  PII_ENCRYPTION_KEY: 'pii_encryption_key',
  MINIO_ACCESS_KEY: 'minio_bff_access_key',
} as const;

/** Env *_FILE yang berisi path material TLS, bukan secret yang di-resolve ke env. */
export function isTlsMaterialFileVar(key: string): boolean {
  return key.endsWith('_CA_FILE') || key === 'TLS_CERT_FILE' || key === 'TLS_KEY_FILE';
}

/** SEC-29: env *_FILE yang dibaca langsung oleh AppConfig ke Buffer (tidak disalin ke env). */
export function isDirectReadFileVar(key: string): boolean {
  return isTlsMaterialFileVar(key) || /^PII_ENCRYPTION_KEY(_V\d{1,3})?_FILE$/.test(key) || key === 'MINIO_ACCESS_KEY_FILE';
}

export type EnvRecord = Record<string, string | undefined>;
export type FileReader = (path: string) => string;

const defaultReader: FileReader = (path) => readFileSync(path, 'utf8');

export function secretsLoader(
  env: EnvRecord = process.env,
  readFile: FileReader = defaultReader,
): Record<string, string> {
  const out: Record<string, string> = {};

  for (const [key, value] of Object.entries(env)) {
    if (!key.endsWith('_FILE') || !value || isDirectReadFileVar(key)) continue;
    const target = key.slice(0, -'_FILE'.length);
    if (env[target]) continue;
    try {
      out[target] = readFile(value).trim();
      env[target] = out[target];
    } catch {
      // Biarkan AppConfig gagal dengan pesan jelas (nama secret, bukan nilainya) saat start
    }
  }

  const dir = env.SECRETS_DIR && env.SECRETS_DIR.length > 0 ? env.SECRETS_DIR : '/run/secrets';
  for (const [target, fileName] of Object.entries(KNOWN_SECRET_FILES)) {
    if (env[target] || env[`${target}_FILE`]) continue;
    try {
      const v = readFile(posix.join(dir, fileName)).trim();
      if (v.length > 0) {
        out[target] = v;
        env[target] = v;
      }
    } catch {
      // Secret opsional (mis. whatsapp_token di dev) boleh tidak ada
    }
  }
  return out;
}
