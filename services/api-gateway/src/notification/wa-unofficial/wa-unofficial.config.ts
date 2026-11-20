import { ChannelConfigError, ChannelEnv, MIN_CHANNEL_KEY_LENGTH } from '../email/channel-env';

/**
 * Konfigurasi sisi BFF kanal WhatsApp tidak resmi (ADR-044 Proposed, docs/04 §28.7/§28.10, L-158).
 *
 * | Env | Default | Catatan |
 * |---|---|---|
 * | `WA_UNOFFICIAL_ENABLED` | `false` | hanya string persis `true` yang menyalakan |
 * | `WA_UNOFFICIAL_RISK_ACCEPTANCE_REF` | — | WAJIB di `APP_ENV=production` (Q-SM14); tanpa ini kanal dianggap mati |
 * | `WA_UNOFFICIAL_HASH_KEY_FILE` | `/run/secrets/wa_unofficial_hash_key` | kunci HMAC `to_hash` (hanya BFF + wa-unofficial) |
 * | `CHANNEL_RETENTION_INTERVAL_MS` | 21600000 | 0 = job purge mati |
 *
 * Production tanpa ref: BFF TIDAK gagal start (kanal promo tidak boleh menjatuhkan inti), tetapi kanal mati dan log
 * error tiap start; `deploy-validate` (devsecops) yang menolak konfigurasi itu sebelum deploy.
 */
const RISK_REF_RE = /^[A-Za-z0-9._/-]{3,64}$/;

export type WaDisabledReason = 'flag_off' | 'production_without_risk_ref' | 'hash_key_missing' | null;

export class WaUnofficialConfig {
  constructor(private readonly env: ChannelEnv) {}

  static fromProcessEnv(): WaUnofficialConfig {
    return new WaUnofficialConfig(ChannelEnv.fromProcessEnv());
  }

  get flagOn(): boolean {
    return this.env.strictTrue('WA_UNOFFICIAL_ENABLED');
  }

  get riskAcceptanceRef(): string | undefined {
    const v = this.env.str('WA_UNOFFICIAL_RISK_ACCEPTANCE_REF');
    if (v === undefined) return undefined;
    if (!RISK_REF_RE.test(v)) throw new ChannelConfigError('WA_UNOFFICIAL_RISK_ACCEPTANCE_REF tidak valid');
    return v;
  }

  get isProductionDeployment(): boolean {
    return this.env.isProductionDeployment;
  }

  /** Alasan kanal mati (`null` = aktif). Dihitung tanpa melempar. */
  get disabledReason(): WaDisabledReason {
    if (!this.flagOn) return 'flag_off';
    let ref: string | undefined;
    try {
      ref = this.riskAcceptanceRef;
    } catch {
      ref = undefined;
    }
    if (this.isProductionDeployment && !ref) return 'production_without_risk_ref';
    try {
      if (!this.hashKey) return 'hash_key_missing';
    } catch {
      return 'hash_key_missing';
    }
    return null;
  }

  get enabled(): boolean {
    return this.disabledReason === null;
  }

  /** Kunci HMAC `to_hash = HMAC-SHA256(key, to_e164)`; `null` bila belum dikonfigurasi. */
  get hashKey(): Buffer | null {
    const v = this.env.secret('WA_UNOFFICIAL_HASH_KEY', 'wa_unofficial_hash_key');
    if (!v) return null;
    if (v.length < MIN_CHANNEL_KEY_LENGTH) throw new ChannelConfigError('WA_UNOFFICIAL_HASH_KEY terlalu pendek');
    return Buffer.from(v, 'utf8');
  }

  get retentionIntervalMs(): number {
    return this.env.int('CHANNEL_RETENTION_INTERVAL_MS', 21_600_000, 0, 86_400_000);
  }
}
