import { randomBytes } from 'node:crypto';
import { ConfigError, decideStartup } from './config';

const PEM = '-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n';

function files(over: Record<string, string> = {}): Record<string, string> {
  return {
    '/run/secrets/efn_ca_crt': PEM,
    '/run/secrets/rabbitmq_password_wa_unofficial': randomBytes(16).toString('hex'),
    '/run/secrets/wa_unofficial_session_key': randomBytes(32).toString('base64'),
    '/run/secrets/wa_unofficial_hash_key': randomBytes(32).toString('hex'),
    '/run/secrets/wa_unofficial_admin_token': randomBytes(32).toString('hex'),
    '/run/secrets/wa_unofficial_tls_crt': PEM,
    '/run/secrets/wa_unofficial_tls_key': 'kunci-dummy-bukan-rahasia',
    ...over,
  };
}

function decide(env: Record<string, string>, f: Record<string, string> = files()) {
  return decideStartup(env, (p) => {
    if (p in f) return Buffer.from(f[p], 'utf8');
    throw new Error('ENOENT');
  });
}

describe('decideStartup (gerbang ADR-044)', () => {
  it.each([undefined, 'false', 'TRUE', '1', 'yes'])('WA_UNOFFICIAL_ENABLED=%s → idle (tidak konek apa pun)', (v) => {
    const d = decide(v === undefined ? { APP_ENV: 'staging' } : { APP_ENV: 'staging', WA_UNOFFICIAL_ENABLED: v });
    expect(d.mode).toBe('idle');
  });

  it('production + aktif tanpa WA_UNOFFICIAL_RISK_ACCEPTANCE_REF → menolak start', () => {
    expect(() => decide({ APP_ENV: 'production', WA_UNOFFICIAL_ENABLED: 'true' })).toThrow(/RISK_ACCEPTANCE_REF/);
    // APP_ENV kosong = production (fail safe)
    expect(() => decide({ WA_UNOFFICIAL_ENABLED: 'true' })).toThrow(ConfigError);
  });

  it('production + ref → run; default batas kontrak (10/menit, 300/hari, 1/member/hari, jitter 2–8 dtk)', () => {
    const d = decide({ APP_ENV: 'production', WA_UNOFFICIAL_ENABLED: 'true', WA_UNOFFICIAL_RISK_ACCEPTANCE_REF: 'LGL/2027/001' });
    expect(d.mode).toBe('run');
    if (d.mode !== 'run') return;
    expect(d.config.limits).toEqual({ ratePerMin: 10, maxPerDay: 300, maxPerMemberPerDay: 1, jitterMinMs: 2000, jitterMaxMs: 8000 });
    expect(d.config.amqp).toMatchObject({ username: 'efn-wa-unofficial', prefetch: 1 });
    expect(d.config.amqp.tls).toMatchObject({ rejectUnauthorized: true, minVersion: 'TLSv1.2', servername: 'rabbitmq' });
    expect(d.config.amqp.url).not.toContain('efn-wa-unofficial@');
    expect(d.config.sessionKey).toHaveLength(32);
  });

  it('RabbitMQ plaintext ditolak kecuali EFN_ALLOW_PLAINTEXT di dev/test; password di URL ditolak', () => {
    const base = { APP_ENV: 'staging', WA_UNOFFICIAL_ENABLED: 'true' };
    expect(() => decide({ ...base, AMQP_URL: 'amqp://efn-wa-unofficial@rabbitmq:5672' })).toThrow(/tanpa TLS/);
    expect(() => decide({ ...base, AMQP_URL: 'amqp://efn-wa-unofficial@rabbitmq:5672', EFN_ALLOW_PLAINTEXT: 'true' })).toThrow(/tanpa TLS/);
    expect(decide({ APP_ENV: 'dev', WA_UNOFFICIAL_ENABLED: 'true', AMQP_URL: 'amqp://efn-wa-unofficial@rabbitmq:5672', EFN_ALLOW_PLAINTEXT: 'true' }).mode).toBe('run');
    expect(() => decide({ ...base, AMQP_URL: 'amqps://efn-wa-unofficial:x@rabbitmq:5671' })).toThrow(/password/);
  });

  it('secret wajib: kunci sesi 32 byte, token admin ≥ 32, token metrik ≠ admin', () => {
    const base = { APP_ENV: 'staging', WA_UNOFFICIAL_ENABLED: 'true' };
    expect(() => decide(base, files({ '/run/secrets/wa_unofficial_session_key': randomBytes(16).toString('base64') }))).toThrow(/32 byte/);
    expect(() => decide(base, files({ '/run/secrets/wa_unofficial_admin_token': 'pendek' }))).toThrow(/admin_token/);
    const f = files();
    expect(() => decide(base, { ...f, '/run/secrets/wa_unofficial_metrics_token': f['/run/secrets/wa_unofficial_admin_token'] })).toThrow(/berbeda/);
    const noKey = files();
    delete noKey['/run/secrets/wa_unofficial_hash_key'];
    expect(() => decide(base, noKey)).toThrow(ConfigError);
  });

  it('jitter tidak boleh di bawah 2 dtk; TLS server wajib di luar dev/test', () => {
    const base = { APP_ENV: 'staging', WA_UNOFFICIAL_ENABLED: 'true' };
    expect(() => decide({ ...base, WA_UNOFFICIAL_JITTER_MIN_MS: '500' })).toThrow(ConfigError);
    const noTls = files();
    delete noTls['/run/secrets/wa_unofficial_tls_crt'];
    expect(() => decide(base, noTls)).toThrow(/TLS_CERT_FILE/);
    expect(() => decide({ APP_ENV: 'staging' }, noTls)).toThrow(/TLS_CERT_FILE/); // idle juga HTTPS
  });
});
