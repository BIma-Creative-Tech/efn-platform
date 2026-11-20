import { randomBytes } from 'node:crypto';
import { ChannelConfigError, ChannelEnv } from './channel-env';
import { EmailConfig } from './email.config';
import { smtpTransportOptions } from './smtp-transport';

const SECRETS = '/test-secrets';

function cfg(env: Record<string, string>, files: Record<string, string> = {}): EmailConfig {
  return new EmailConfig(
    new ChannelEnv({ NODE_ENV: 'test', ...env }, (path) => {
      if (path in files) return Buffer.from(files[path], 'utf8');
      throw new Error('ENOENT');
    }),
  );
}

const BASE = {
  EMAIL_ENABLED: 'true',
  EMAIL_SMTP_HOST: 'mailpit',
  EMAIL_FROM: 'EFN Promo <promo@mail.contoh.id>',
  EMAIL_UNSUBSCRIBE_URL: 'https://superapp.contoh.id/berhenti-langganan',
  EMAIL_UNSUBSCRIBE_KEY_FILE: `${SECRETS}/email_unsubscribe_key`,
};
const KEYFILE = { [`${SECRETS}/email_unsubscribe_key`]: randomBytes(32).toString('hex') };

describe('EmailConfig (ADR-048 §3.2: TLS wajib, tanpa plaintext)', () => {
  it('default mati; assertValid tidak memeriksa apa pun saat mati', () => {
    const c = cfg({});
    expect(c.enabled).toBe(false);
    expect(() => c.assertValid()).not.toThrow();
  });

  it.each(['none', 'plain', 'plaintext', 'false', 'tls', 'STARTTLS'])('EMAIL_SMTP_TLS_MODE=%s ditolak (gagal start)', (mode) => {
    const c = cfg({ ...BASE, EMAIL_SMTP_TLS_MODE: mode }, KEYFILE);
    expect(() => c.assertValid()).toThrow(ChannelConfigError);
  });

  it('TLS mode kosong ditolak', () => {
    expect(() => cfg(BASE, KEYFILE).assertValid()).toThrow(/implicit atau starttls/);
  });

  it('starttls → requireTLS, bukan secure, verifikasi sertifikat + servername; port default 587', () => {
    const c = cfg({ ...BASE, EMAIL_SMTP_TLS_MODE: 'starttls' }, KEYFILE);
    c.assertValid();
    const o = smtpTransportOptions(c.smtp);
    expect(o).toMatchObject({ host: 'mailpit', port: 587, secure: false, requireTLS: true, ignoreTLS: false });
    expect(o.tls).toMatchObject({ rejectUnauthorized: true, minVersion: 'TLSv1.2', servername: 'mailpit' });
    expect(o.auth).toBeUndefined();
  });

  it('implicit → secure (465); CA opsional dipakai bila diisi; IP tanpa servername', () => {
    const ca = '-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n';
    const c = cfg({ ...BASE, EMAIL_SMTP_HOST: '10.0.0.5', EMAIL_SMTP_TLS_MODE: 'implicit', EMAIL_SMTP_CA_FILE: '/ca.pem' }, { ...KEYFILE, '/ca.pem': ca });
    const o = smtpTransportOptions(c.smtp);
    expect(o).toMatchObject({ port: 465, secure: true, requireTLS: false });
    expect(o.tls).toMatchObject({ rejectUnauthorized: true });
    expect((o.tls as { servername?: string }).servername).toBeUndefined();
    expect(Buffer.isBuffer((o.tls as { ca?: unknown }).ca)).toBe(true);
  });

  it('CA file diisi tetapi bukan PEM / tak terbaca → error (tanpa fallback)', () => {
    expect(() => cfg({ ...BASE, EMAIL_SMTP_TLS_MODE: 'implicit', EMAIL_SMTP_CA_FILE: '/x' }, KEYFILE).smtp).toThrow(ChannelConfigError);
    expect(() => cfg({ ...BASE, EMAIL_SMTP_TLS_MODE: 'implicit', EMAIL_SMTP_CA_FILE: '/x' }, { ...KEYFILE, '/x': 'bukan pem' }).smtp).toThrow(/PEM/);
  });

  it('password SMTP: literal env ditolak di luar dev; dibaca dari file secret', () => {
    const pw = randomBytes(16).toString('hex');
    expect(() => cfg({ ...BASE, EMAIL_SMTP_TLS_MODE: 'starttls', EMAIL_SMTP_USER: 'u', EMAIL_SMTP_PASSWORD: pw, APP_ENV: 'staging' }, KEYFILE).smtp).toThrow(/SEC-29/);
    const c = cfg(
      { ...BASE, EMAIL_SMTP_TLS_MODE: 'starttls', EMAIL_SMTP_USER: 'u', EMAIL_SMTP_PASSWORD_FILE: '/pw' },
      { ...KEYFILE, '/pw': `${pw}\n` },
    );
    expect(smtpTransportOptions(c.smtp).auth).toEqual({ user: 'u', pass: pw });
  });

  it('user tanpa password → error; unsubscribe URL wajib https tanpa query/fragmen; kunci unsubscribe wajib saat aktif', () => {
    expect(() => cfg({ ...BASE, EMAIL_SMTP_TLS_MODE: 'starttls', EMAIL_SMTP_USER: 'u' }, KEYFILE).smtp).toThrow(/PASSWORD/);
    expect(() => cfg({ ...BASE, EMAIL_SMTP_TLS_MODE: 'starttls', EMAIL_UNSUBSCRIBE_URL: 'http://x.id/u' }, KEYFILE).assertValid()).toThrow(/https/);
    expect(() => cfg({ ...BASE, EMAIL_SMTP_TLS_MODE: 'starttls', EMAIL_UNSUBSCRIBE_URL: 'https://x.id/u?t=1' }, KEYFILE).assertValid()).toThrow(/https/);
    expect(() => cfg({ ...BASE, EMAIL_SMTP_TLS_MODE: 'starttls' }, {}).assertValid()).toThrow(/UNSUBSCRIBE_KEY/);
  });
});
