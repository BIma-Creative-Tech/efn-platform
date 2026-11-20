import { isIP } from 'node:net';
import { ChannelConfigError, ChannelEnv, MIN_CHANNEL_KEY_LENGTH } from './channel-env';

/**
 * Konfigurasi kanal email (ADR-048 Proposed, docs/04 §28.9/§28.10). Semua default aman: `EMAIL_ENABLED=false`.
 *
 * | Env | Default | Catatan |
 * |---|---|---|
 * | `EMAIL_ENABLED` | `false` | Production boleh `true` setelah Q-SM19 (penyedia + DPA + SPF/DKIM/DMARC) |
 * | `EMAIL_SMTP_HOST`, `EMAIL_SMTP_PORT` | — / 465 (implicit) atau 587 (starttls) | |
 * | `EMAIL_SMTP_TLS_MODE` | — (wajib) | `implicit` \| `starttls`; nilai lain (termasuk plaintext) → gagal start |
 * | `EMAIL_SMTP_USER` | — | opsional (relai internal tanpa AUTH) |
 * | `EMAIL_SMTP_PASSWORD_FILE` | `/run/secrets/email_smtp_password` | secret |
 * | `EMAIL_SMTP_CA_FILE` | — | opsional; CA internal untuk relai/Mailpit, selain itu CA sistem |
 * | `EMAIL_FROM` | — (wajib) | mis. `EFN Promo <promo@mail.contoh.id>` |
 * | `EMAIL_RATE_PER_MIN` | 60 | global lintas replika (Redis) |
 * | `EMAIL_UNSUBSCRIBE_KEY_FILE` | `/run/secrets/email_unsubscribe_key` | secret HMAC token berhenti berlangganan |
 * | `EMAIL_UNSUBSCRIBE_URL` | — (wajib bila aktif) | halaman web HTTPS; token di fragmen `#t=` (tidak masuk access log) |
 * | `EMAIL_UNSUBSCRIBE_MAILTO` | — | opsional, ditambahkan ke `List-Unsubscribe` |
 * | `EMAIL_UNSUBSCRIBE_TTL_DAYS` | 30 | umur token |
 * | `EMAIL_UNSUBSCRIBE_RATE_PER_MIN` | 10 | per IP, endpoint publik |
 * | `EMAIL_BRAND_NAME` | `EFN` | nama di salam/footer |
 * | `CHANNEL_RETENTION_INTERVAL_MS` | 21600000 | 0 = job purge mati (test) |
 */
export type SmtpTlsMode = 'implicit' | 'starttls';

export interface SmtpSettings {
  host: string;
  port: number;
  tlsMode: SmtpTlsMode;
  user: string | undefined;
  password: string | undefined;
  /** CA tambahan (PEM). `undefined` = CA sistem. */
  ca: Buffer | undefined;
  /** SNI + verifikasi hostname; tidak diisi bila host berupa IP. */
  servername: string | undefined;
}

const EMAIL_FROM_RE = /^(?:[^<>\r\n"]{1,64} )?<?[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,253}\.[A-Za-z]{2,24}>?$/;
const HOST_RE = /^[A-Za-z0-9.-]{1,253}$/;

export class EmailConfig {
  constructor(private readonly env: ChannelEnv) {}

  static fromProcessEnv(): EmailConfig {
    return new EmailConfig(ChannelEnv.fromProcessEnv());
  }

  get enabled(): boolean {
    return this.env.bool('EMAIL_ENABLED', false);
  }

  /** Validasi penuh saat start bila kanal aktif (ADR-048 §3.2: konfigurasi tanpa TLS → gagal start). */
  assertValid(): void {
    if (!this.enabled) return;
    void this.smtp;
    void this.from;
    void this.ratePerMin;
    void this.unsubscribeUrl;
    void this.unsubscribeTtlSeconds;
    if (!this.unsubscribeKey) throw new ChannelConfigError('EMAIL_UNSUBSCRIBE_KEY belum dikonfigurasi (wajib bila EMAIL_ENABLED=true)');
  }

  get smtp(): SmtpSettings {
    const host = this.env.str('EMAIL_SMTP_HOST');
    if (!host || !HOST_RE.test(host)) throw new ChannelConfigError('EMAIL_SMTP_HOST belum dikonfigurasi atau tidak valid');
    const modeRaw = this.env.str('EMAIL_SMTP_TLS_MODE');
    if (modeRaw !== 'implicit' && modeRaw !== 'starttls') {
      // Tidak ada nilai plaintext (L-160): `none`/`plain`/`false`/kosong semuanya ditolak.
      throw new ChannelConfigError('EMAIL_SMTP_TLS_MODE harus implicit atau starttls (tanpa mode plaintext, L-160)');
    }
    const port = this.env.int('EMAIL_SMTP_PORT', modeRaw === 'implicit' ? 465 : 587, 1, 65535);
    const user = this.env.str('EMAIL_SMTP_USER');
    const password = this.env.secret('EMAIL_SMTP_PASSWORD', 'email_smtp_password');
    if (user && !password) throw new ChannelConfigError('EMAIL_SMTP_PASSWORD belum dikonfigurasi (EMAIL_SMTP_USER diisi)');
    return {
      host,
      port,
      tlsMode: modeRaw,
      user,
      password,
      ca: this.env.pemFile('EMAIL_SMTP_CA_FILE'),
      servername: isIP(host) ? undefined : host,
    };
  }

  get from(): string {
    const v = this.env.str('EMAIL_FROM');
    if (!v || !EMAIL_FROM_RE.test(v)) throw new ChannelConfigError('EMAIL_FROM belum dikonfigurasi atau tidak valid');
    return v;
  }

  get ratePerMin(): number {
    return this.env.int('EMAIL_RATE_PER_MIN', 60, 1, 10_000);
  }

  get brandName(): string {
    const v = this.env.str('EMAIL_BRAND_NAME') ?? 'EFN';
    return v.replace(/[\r\n<>]/g, '').slice(0, 40) || 'EFN';
  }

  /** Kunci HMAC token berhenti berlangganan; `null` bila belum dikonfigurasi (endpoint → 503). */
  get unsubscribeKey(): Buffer | null {
    const v = this.env.secret('EMAIL_UNSUBSCRIBE_KEY', 'email_unsubscribe_key');
    if (!v) return null;
    if (v.length < MIN_CHANNEL_KEY_LENGTH) throw new ChannelConfigError('EMAIL_UNSUBSCRIBE_KEY terlalu pendek');
    return Buffer.from(v, 'utf8');
  }

  /** URL halaman berhenti berlangganan (HTTPS, tanpa query/fragmen). */
  get unsubscribeUrl(): string {
    const raw = this.env.str('EMAIL_UNSUBSCRIBE_URL');
    if (!raw) throw new ChannelConfigError('EMAIL_UNSUBSCRIBE_URL belum dikonfigurasi');
    let u: URL;
    try {
      u = new URL(raw);
    } catch {
      throw new ChannelConfigError('EMAIL_UNSUBSCRIBE_URL bukan URL valid');
    }
    if (u.protocol !== 'https:' || u.search || u.hash || u.username || u.password) {
      throw new ChannelConfigError('EMAIL_UNSUBSCRIBE_URL harus https:// tanpa query/fragmen/kredensial');
    }
    return u.toString();
  }

  get unsubscribeMailto(): string | undefined {
    const v = this.env.str('EMAIL_UNSUBSCRIBE_MAILTO');
    if (v === undefined) return undefined;
    if (!/^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,253}\.[A-Za-z]{2,24}$/.test(v)) {
      throw new ChannelConfigError('EMAIL_UNSUBSCRIBE_MAILTO bukan alamat email valid');
    }
    return v;
  }

  get unsubscribeTtlSeconds(): number {
    return this.env.int('EMAIL_UNSUBSCRIBE_TTL_DAYS', 30, 1, 365) * 86_400;
  }

  get unsubscribeRatePerMin(): number {
    return this.env.int('EMAIL_UNSUBSCRIBE_RATE_PER_MIN', 10, 1, 1000);
  }

  get retentionIntervalMs(): number {
    return this.env.int('CHANNEL_RETENTION_INTERVAL_MS', 21_600_000, 0, 86_400_000);
  }
}
