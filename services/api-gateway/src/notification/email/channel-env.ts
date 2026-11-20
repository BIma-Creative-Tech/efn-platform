import { readFileSync } from 'node:fs';
import { posix } from 'node:path';

/**
 * Pembaca env + secret untuk kanal G50 (email ADR-048, WA unofficial ADR-044) — dipakai `EmailConfig` dan
 * `WaUnofficialConfig`. Aturan SEC-29 sama dengan `AppConfig` (yang tidak diubah gelombang ini):
 *   1. `<NAMA>_FILE` (wajib terbaca bila di-set);
 *   2. fallback `${SECRETS_DIR:-/run/secrets}/<nama kontrak>`;
 *   3. env literal `<NAMA>` HANYA bila `APP_ENV=dev`; di luar dev literal → `ChannelConfigError`.
 * Nilai secret tidak pernah masuk pesan error/log dan tidak pernah ditulis ke `process.env`.
 * Buffer mentah file di-nol-kan setelah dibaca.
 */
export class ChannelConfigError extends Error {}

export type ChannelEnvRecord = Record<string, string | undefined>;
export type ChannelFileReader = (path: string) => Buffer;

const defaultReader: ChannelFileReader = (path) => readFileSync(path);

export class ChannelEnv {
  constructor(
    private readonly env: ChannelEnvRecord,
    private readonly readFile: ChannelFileReader = defaultReader,
  ) {}

  static fromProcessEnv(): ChannelEnv {
    return new ChannelEnv({ ...process.env });
  }

  str(name: string): string | undefined {
    const v = this.env[name];
    return v === undefined || v === '' ? undefined : v;
  }

  /** Flag boolean longgar (`1|true|yes|on`), sama dengan `AppConfig.bool`. */
  bool(name: string, def: boolean): boolean {
    const raw = this.str(name);
    if (raw === undefined) return def;
    return ['1', 'true', 'yes', 'on'].includes(raw.toLowerCase());
  }

  /** Flag ketat: hanya string persis `true` yang menyalakan (dipakai gerbang WA unofficial, ADR-044 §3.4). */
  strictTrue(name: string): boolean {
    return this.str(name) === 'true';
  }

  int(name: string, def: number, min: number, max: number): number {
    const raw = this.str(name);
    if (raw === undefined) return def;
    const n = Number(raw);
    if (!Number.isInteger(n) || n < min || n > max) throw new ChannelConfigError(`${name} harus bilangan bulat ${min}..${max}`);
    return n;
  }

  get appEnv(): string | undefined {
    return this.str('APP_ENV');
  }

  get isDevEnv(): boolean {
    return this.appEnv === 'dev';
  }

  /** Sama dengan `AppConfig.isProductionDeployment`: APP_ENV menentukan; kosong → NODE_ENV (kosong = production). */
  get isProductionDeployment(): boolean {
    const appEnv = this.appEnv;
    if (appEnv !== undefined) return appEnv === 'production';
    const nodeEnv = this.str('NODE_ENV') ?? 'production';
    return nodeEnv === 'production';
  }

  /** Secret string (trim) atau `undefined` bila tidak dikonfigurasi. */
  secret(name: string, contractFile: string): string | undefined {
    const literal = this.str(name);
    if (literal !== undefined && !this.isDevEnv) {
      throw new ChannelConfigError(`${name} sebagai env literal ditolak di luar APP_ENV=dev; pakai ${name}_FILE (SEC-29)`);
    }
    const file = this.str(`${name}_FILE`);
    if (file) return this.readSecretFile(file, true, name);
    const fromDir = this.readSecretFile(posix.join(this.str('SECRETS_DIR') ?? '/run/secrets', contractFile), false, name);
    return fromDir ?? literal;
  }

  /** Material non-rahasia (CA PEM). `undefined` bila env tidak di-set; file di-set tapi tak terbaca → error. */
  pemFile(envName: string): Buffer | undefined {
    const path = this.str(envName);
    if (!path) return undefined;
    let raw: Buffer;
    try {
      raw = this.readFile(path);
    } catch {
      throw new ChannelConfigError(`${envName} (${path}) tidak dapat dibaca`);
    }
    if (!raw.toString('utf8').includes('-----BEGIN CERTIFICATE-----')) throw new ChannelConfigError(`${envName} bukan sertifikat PEM`);
    return raw;
  }

  private readSecretFile(path: string, required: boolean, name: string): string | undefined {
    let raw: Buffer;
    try {
      raw = this.readFile(path);
    } catch {
      if (required) throw new ChannelConfigError(`${name}_FILE (${path}) tidak dapat dibaca`);
      return undefined;
    }
    try {
      const s = raw.toString('utf8').trim();
      return s.length > 0 ? s : undefined;
    } finally {
      raw.fill(0);
    }
  }
}

/** Panjang minimum kunci HMAC kanal (≥ 256 bit dalam hex/base64 ≈ 32 karakter). */
export const MIN_CHANNEL_KEY_LENGTH = 32;
