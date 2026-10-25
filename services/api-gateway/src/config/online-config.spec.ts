import { randomSecret, testConfig } from '../common/testing/fakes';
import { ConfigError, ONLINE_CHANNEL_RE, onlineApiKeyEnvName, onlineApiKeySecretName } from './app-config';

/**
 * G19 docs/04 §11.1 & §11.3 + G20 §12.1/§12.2 (L-48, L-55) — default env online, secret API key per kanal, metode bayar.
 * Kanal G20 = kode PT (GL/PB/BCR/BCF); kode berhifen tetap didukung regex §10.2 (diuji dengan kode generik `AB-CD`).
 */
describe('Config online G19/G20', () => {
  it('§11.1: default ONLINE_PAYMENT_TIMEOUT_SECONDS 3600 (batas 60..3600 tetap), ONLINE_MAX_OPEN_ORDERS_PER_MEMBER 2', () => {
    const c = testConfig();
    expect(c.onlinePaymentTimeoutSeconds).toBe(3600);
    expect(c.onlineMaxOpenOrdersPerMember).toBe(2);
    expect(testConfig({ ONLINE_PAYMENT_TIMEOUT_SECONDS: '900' }).onlinePaymentTimeoutSeconds).toBe(900);
    expect(() => testConfig({ ONLINE_PAYMENT_TIMEOUT_SECONDS: '3601' }).onlinePaymentTimeoutSeconds).toThrow(ConfigError);
    expect(() => testConfig({ ONLINE_PAYMENT_TIMEOUT_SECONDS: '59' }).onlinePaymentTimeoutSeconds).toThrow(ConfigError);
    expect(testConfig({ ONLINE_MAX_OPEN_ORDERS_PER_MEMBER: '3' }).onlineMaxOpenOrdersPerMember).toBe(3);
  });

  it('G20 §12.1: default ONLINE_CHANNELS = GL; daftar GL,PB,BCR,BCF diterima; selain regex §10.2 ditolak', () => {
    expect(testConfig().onlineChannels).toEqual(['GL']);
    expect(testConfig({ ONLINE_CHANNELS: 'GL, PB;BCR,BCF' }).onlineChannels).toEqual(['GL', 'PB', 'BCR', 'BCF']);
    // G24 §16.1 (L-93): WTZ diterima bila dinyalakan eksplisit, tidak masuk default
    expect(testConfig({ ONLINE_CHANNELS: 'GL,WTZ' }).onlineChannels).toEqual(['GL', 'WTZ']);
    expect(testConfig({ ONLINE_CHANNELS: 'AB-CD' }).onlineChannels).toEqual(['AB-CD']); // berhifen tetap valid secara format
    for (const bad of ['GL_X', 'gl', 'G', '../GL', 'GL/PB', 'GL.PB', '-GL', 'A'.repeat(17), 'GL-FILE']) { // GL-FILE: L-46
      expect(() => testConfig({ ONLINE_CHANNELS: bad }).onlineChannels).toThrow(ConfigError);
    }
  });

  it('G20 §12.2: ONLINE_MAX_OPEN_COUNTER_ORDERS_PER_MEMBER default 1 (1..10)', () => {
    expect(testConfig().onlineMaxOpenCounterOrdersPerMember).toBe(1);
    expect(testConfig({ ONLINE_MAX_OPEN_COUNTER_ORDERS_PER_MEMBER: '2' }).onlineMaxOpenCounterOrdersPerMember).toBe(2);
    for (const bad of ['0', '11', 'x']) {
      expect(() => testConfig({ ONLINE_MAX_OPEN_COUNTER_ORDERS_PER_MEMBER: bad }).onlineMaxOpenCounterOrdersPerMember).toThrow(ConfigError);
    }
  });

  it('nama env/secret per kanal: GL → ODOO_API_KEY_ONLINE_GL / odoo_api_key_online_gl; "-" → "_"', () => {
    expect(onlineApiKeyEnvName('GL')).toBe('ODOO_API_KEY_ONLINE_GL');
    expect(onlineApiKeyEnvName('BCF')).toBe('ODOO_API_KEY_ONLINE_BCF');
    expect(onlineApiKeyEnvName('AB-CD')).toBe('ODOO_API_KEY_ONLINE_AB_CD');
    expect(onlineApiKeySecretName('GL')).toBe('odoo_api_key_online_gl');
    expect(onlineApiKeySecretName('PB')).toBe('odoo_api_key_online_pb');
    expect(onlineApiKeySecretName('BCR')).toBe('odoo_api_key_online_bcr');
    expect(onlineApiKeySecretName('BCF')).toBe('odoo_api_key_online_bcf');
    expect(onlineApiKeyEnvName('WTZ')).toBe('ODOO_API_KEY_ONLINE_WTZ'); // G24 §16.1
    expect(onlineApiKeySecretName('WTZ')).toBe('odoo_api_key_online_wtz');
    expect(onlineApiKeySecretName('AB-CD')).toBe('odoo_api_key_online_ab_cd');
  });

  it('API key kanal: dibaca dari ODOO_API_KEY_ONLINE_<KODE>_FILE, fallback ${SECRETS_DIR}/odoo_api_key_online_<kode>', () => {
    const key = randomSecret();
    const reads: string[] = [];
    const reader = (expected: string) => (p: string): Buffer => {
      reads.push(p);
      if (p === expected) return Buffer.from(`${key}\n`);
      throw new Error('ENOENT');
    };
    expect(testConfig({ ODOO_API_KEY_ONLINE_GL_FILE: '/s/gl' }, reader('/s/gl')).odooApiKeyOnline('GL')).toBe(key);
    expect(testConfig({ SECRETS_DIR: '/run/secrets' }, reader('/run/secrets/odoo_api_key_online_bcf')).odooApiKeyOnline('BCF')).toBe(key);
    expect(reads).toContain('/run/secrets/odoo_api_key_online_bcf');
    // placeholder gen-secrets ditolak
    expect(() => testConfig({ ODOO_API_KEY_ONLINE_BCR_FILE: '/s/b' }, () => Buffer.from('disabled')).odooApiKeyOnline('BCR')).toThrow(/placeholder 'disabled'/);
  });

  it('tanpa path traversal: kode kanal tak valid ditolak SEBELUM membentuk nama env/path (reader tidak pernah dipanggil)', () => {
    const reads: string[] = [];
    const c = testConfig({}, (p) => {
      reads.push(p);
      throw new Error('ENOENT');
    });
    for (const bad of ['../../etc/passwd', 'GL/../../x', 'GL..PB', '..', 'GL PB', 'gl', 'GL_PB', 'GL\u0000', 'G', '']) {
      expect(ONLINE_CHANNEL_RE.test(bad)).toBe(false);
      expect(() => c.odooApiKeyOnline(bad)).toThrow(ConfigError);
      expect(() => onlineApiKeySecretName(bad)).toThrow(ConfigError);
      expect(() => onlineApiKeyEnvName(bad)).toThrow(ConfigError);
    }
    expect(reads.filter((p) => !p.startsWith('/test-secrets/'))).toEqual([]);
  });

  it('start: kanal aktif tanpa secret → gagal start menyebut NAMA env saja (tanpa nilai)', () => {
    const c = testConfig({ ONLINE_ENABLED: 'true', ONLINE_CHANNELS: 'BCR', ODOO_ONLINE_EVENT_HMAC_FILE: '/s/hmac' }, (p) => {
      if (p === '/s/hmac') return Buffer.from(randomSecret());
      throw new Error('ENOENT');
    });
    expect(() => c.assertStartupSecrets()).toThrow(/ODOO_API_KEY_ONLINE_BCR/);
    // env literal di luar APP_ENV=dev ditolak (SEC-29)
    const lit = testConfig({ ONLINE_ENABLED: 'true', ONLINE_CHANNELS: 'BCR', ODOO_API_KEY_ONLINE_BCR: randomSecret() });
    expect(() => lit.assertStartupSecrets()).toThrow(/ODOO_API_KEY_ONLINE_BCR sebagai env literal ditolak/);
  });

  describe('SEC-133: pemeriksaan env literal tidak menolak `ODOO_API_KEY_ONLINE_*_FILE`', () => {
    const fileEnv = { ODOO_API_KEY_ONLINE_GL_FILE: '/s/gl', ODOO_API_KEY_ONLINE_BCR_FILE: '/s/bcr' };
    const keys: Record<string, string> = { '/s/gl': randomSecret(), '/s/bcr': randomSecret(), '/s/hmac': randomSecret() };
    const reader = (p: string): Buffer => {
      const v = keys[p];
      if (v === undefined) throw new Error('ENOENT');
      return Buffer.from(v);
    };

    it('APP_ENV=production + GL_FILE + BCR_FILE (online OFF, seperti base compose) → start valid', () => {
      expect(() => testConfig({ APP_ENV: 'production', ...fileEnv }, reader).assertStartupSecrets()).not.toThrow();
    });

    it('APP_ENV=production + online ON kanal GL,BCR via *_FILE (pilot counter, L-55) → start valid dan key terbaca dari file', () => {
      const c = testConfig(
        {
          APP_ENV: 'production',
          ONLINE_ENABLED: 'true',
          ONLINE_CHANNELS: 'GL,BCR',
          ONLINE_PAYMENT_METHODS: 'bank_transfer', // G26 L-103: pilot tanpa vendor = transfer bank (counter dibekukan)
          ODOO_ONLINE_EVENT_HMAC_FILE: '/s/hmac',
          ...fileEnv,
        },
        reader,
      );
      expect(() => c.assertStartupSecrets()).not.toThrow();
      expect(c.odooApiKeyOnline('GL')).toBe(keys['/s/gl']);
      expect(c.odooApiKeyOnline('BCR')).toBe(keys['/s/bcr']);
    });

    it('APP_ENV=production + literal ODOO_API_KEY_ONLINE_GL → ditolak (pesan menyebut nama, bukan nilai)', () => {
      const literal = randomSecret();
      const c = testConfig({ APP_ENV: 'production', ODOO_API_KEY_ONLINE_GL: literal, ...fileEnv }, reader);
      let msg = '';
      try {
        c.assertStartupSecrets();
      } catch (err) {
        msg = err instanceof Error ? err.message : String(err);
      }
      expect(msg).toMatch(/ODOO_API_KEY_ONLINE_GL sebagai env literal ditolak/);
      expect(msg).not.toMatch(/ODOO_API_KEY_ONLINE_GL_FILE sebagai env literal/);
      expect(msg).not.toMatch(/ODOO_API_KEY_ONLINE_BCR_FILE sebagai env literal/);
      expect(msg).not.toContain(literal);
      expect(() => testConfig({ APP_ENV: 'production', ODOO_API_KEY_ONLINE_GL: 'xxx' }).assertStartupSecrets()).toThrow(
        /ODOO_API_KEY_ONLINE_GL sebagai env literal ditolak/,
      );
    });
  });

  describe('L-55: gerbang deploy pilot bayar di kasir (ONLINE_PAYMENT_METHODS × PAYMENT_PROVIDER)', () => {
    const hmac = randomSecret();
    const glKey = randomSecret();
    const reader = (p: string): Buffer => {
      if (p === '/s/hmac') return Buffer.from(hmac);
      if (p === '/s/gl') return Buffer.from(glKey);
      throw new Error('ENOENT');
    };
    const prod = (over: Record<string, string>) =>
      testConfig(
        { APP_ENV: 'production', ONLINE_ENABLED: 'true', ONLINE_CHANNELS: 'GL', ODOO_ONLINE_EVENT_HMAC_FILE: '/s/hmac', ODOO_API_KEY_ONLINE_GL_FILE: '/s/gl', ...over },
        reader,
      );

    it('G26 default: dev/test qris,bank_transfer (+counter hanya ONLINE_COUNTER_LEGACY); staging/production qris; nilai asing → ConfigError', () => {
      expect(testConfig().onlinePaymentMethods).toEqual(['qris', 'bank_transfer']);
      expect(testConfig({ APP_ENV: 'dev' }).onlinePaymentMethods).toEqual(['qris', 'bank_transfer']);
      expect(testConfig({ ONLINE_COUNTER_LEGACY: 'true' }).onlinePaymentMethods).toEqual(['qris', 'counter', 'bank_transfer']);
      expect(testConfig({ APP_ENV: 'staging' }).onlinePaymentMethods).toEqual(['qris']);
      expect(testConfig({ APP_ENV: 'production' }).onlinePaymentMethods).toEqual(['qris']);
      expect(testConfig({ ONLINE_PAYMENT_METHODS: 'bank_transfer' }).onlinePaymentMethods).toEqual(['bank_transfer']);
      expect(testConfig({ ONLINE_PAYMENT_METHODS: 'bank_transfer, qris;bank_transfer' }).onlinePaymentMethods).toEqual(['bank_transfer', 'qris']);
      expect(testConfig({ ONLINE_PAYMENT_METHODS: 'counter, qris', ONLINE_COUNTER_LEGACY: 'true' }).onlinePaymentMethods).toEqual(['counter', 'qris']);
      for (const bad of ['cash', 'QRIS', ',', 'qris,va', 'counter']) {
        expect(() => testConfig({ ONLINE_PAYMENT_METHODS: bad }).onlinePaymentMethods).toThrow(/ONLINE_PAYMENT_METHODS/);
      }
    });

    it('G26 L-103: production + bank_transfer saja + PAYMENT_PROVIDER=disabled (default production) → start valid; counter → gagal', () => {
      expect(() => prod({ ONLINE_PAYMENT_METHODS: 'bank_transfer' }).assertStartupSecrets()).not.toThrow();
      expect(() => prod({ ONLINE_PAYMENT_METHODS: 'bank_transfer', PAYMENT_PROVIDER: 'disabled' }).assertStartupSecrets()).not.toThrow();
      expect(() => prod({ ONLINE_PAYMENT_METHODS: 'counter' }).assertStartupSecrets()).toThrow(/L-95/);
      expect(() => prod({ ONLINE_PAYMENT_METHODS: 'counter', ONLINE_COUNTER_LEGACY: 'true' }).assertStartupSecrets()).toThrow(/ONLINE_COUNTER_LEGACY/);
    });

    it('production + qris tanpa provider nyata (disabled / default) → gagal start dengan pesan L-103 tanpa nilai rahasia', () => {
      const cases: Record<string, string>[] = [{}, { ONLINE_PAYMENT_METHODS: 'qris' }, { ONLINE_PAYMENT_METHODS: 'qris,bank_transfer', PAYMENT_PROVIDER: 'disabled' }];
      for (const over of cases) {
        let msg = '';
        try {
          prod(over).assertStartupSecrets();
        } catch (err) {
          msg = err instanceof Error ? err.message : String(err);
        }
        expect(msg).toMatch(/ONLINE_PAYMENT_METHODS memuat qris tetapi PAYMENT_PROVIDER bukan xendit\|midtrans/);
        expect(msg).not.toContain(hmac);
        expect(msg).not.toContain(glKey);
      }
    });

    it('production tetap melarang PAYMENT_PROVIDER=mock (juga dengan bank_transfer saja)', () => {
      expect(() => prod({ ONLINE_PAYMENT_METHODS: 'bank_transfer', PAYMENT_PROVIDER: 'mock' }).assertStartupSecrets()).toThrow(/PAYMENT_PROVIDER/);
    });

    it('online OFF di production: qris + disabled tidak memblokir start (gerbang hanya saat ONLINE_ENABLED=true)', () => {
      expect(() => prod({ ONLINE_ENABLED: 'false' }).assertStartupSecrets()).not.toThrow();
    });

    it('staging/dev: qris + mock tetap boleh (aturan L-55 hanya production)', () => {
      expect(testConfig({ APP_ENV: 'staging', PAYMENT_PROVIDER: 'mock' }).onlinePaymentMethods).toEqual(['qris']);
      expect(testConfig({ APP_ENV: 'staging', PAYMENT_PROVIDER: 'mock' }).paymentProvider).toBe('mock');
    });
  });
});
