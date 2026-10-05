import { readFileSync } from 'node:fs';
import { posix } from 'node:path';

/**
 * Pemuat secret (R-S01). Dua mekanisme, berurutan:
 *
 * 1. Pola `X_FILE`: setiap env var `X_FILE` yang menunjuk ke file (Docker secret) dibaca menjadi `X`.
 *    PENGECUALIAN: `*_CA_FILE`, `TLS_CERT_FILE`, `TLS_KEY_FILE` adalah PATH material TLS yang dibaca langsung oleh
 *    AppConfig (sebagai Buffer, gagal keras bila hilang) — isinya (terutama kunci privat server) tidak disalin ke env.
 * 2. Fallback nama kontrak (docs/04 §2, §7.3): bila `X` dan `X_FILE` sama-sama tidak ada,
 *    coba baca `${SECRETS_DIR:-/run/secrets}/<nama_secret>` untuk secret yang dikenal.
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
  PII_ENCRYPTION_KEY: 'pii_encryption_key',
  // v3 (docs/04 §8.1, §8.3)
  ALTCHA_HMAC_KEY: 'altcha_hmac_key',
  PAYMENT_PROVIDER_API_KEY: 'payment_provider_api_key',
  // Kredensial per service (docs/04 §7.3, SEC-02) — bukan lagi kredensial bersama/superuser
  PG_PASSWORD: 'pg_password_bff',
  RABBITMQ_PASSWORD: 'rabbitmq_password_bff',
  REDIS_PASSWORD: 'redis_password_bff',
  MINIO_SECRET_KEY: 'minio_bff_secret',
};

/** Env *_FILE yang berisi path material TLS, bukan secret yang di-resolve ke env. */
export function isTlsMaterialFileVar(key: string): boolean {
  return key.endsWith('_CA_FILE') || key === 'TLS_CERT_FILE' || key === 'TLS_KEY_FILE';
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
    if (!key.endsWith('_FILE') || !value || isTlsMaterialFileVar(key)) continue;
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
