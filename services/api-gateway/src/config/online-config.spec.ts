import { randomSecret, testConfig } from '../common/testing/fakes';
import { ConfigError, ONLINE_CHANNEL_RE, onlineApiKeyEnvName, onlineApiKeySecretName } from './app-config';

/** G19 docs/04 §11.1 & §11.3 — default env online dan secret API key per kanal berhifen. */
describe('Config online G19', () => {
  it('§11.1: default ONLINE_PAYMENT_TIMEOUT_SECONDS 3600 (batas 60..3600 tetap), ONLINE_MAX_OPEN_ORDERS_PER_MEMBER 2', () => {
    const c = testConfig();
    expect(c.onlinePaymentTimeoutSeconds).toBe(3600);
    expect(c.onlineMaxOpenOrdersPerMember).toBe(2);
    expect(testConfig({ ONLINE_PAYMENT_TIMEOUT_SECONDS: '900' }).onlinePaymentTimeoutSeconds).toBe(900);
    expect(() => testConfig({ ONLINE_PAYMENT_TIMEOUT_SECONDS: '3601' }).onlinePaymentTimeoutSeconds).toThrow(ConfigError);
    expect(() => testConfig({ ONLINE_PAYMENT_TIMEOUT_SECONDS: '59' }).onlinePaymentTimeoutSeconds).toThrow(ConfigError);
    expect(testConfig({ ONLINE_MAX_OPEN_ORDERS_PER_MEMBER: '3' }).onlineMaxOpenOrdersPerMember).toBe(3);
  });

  it('§11.3: ONLINE_CHANNELS menerima kode berhifen; selain regex §10.2 ditolak', () => {
    expect(testConfig({ ONLINE_CHANNELS: 'RTL-GL,RTL-BACHA' }).onlineChannels).toEqual(['RTL-GL', 'RTL-BACHA']);
    expect(testConfig({ ONLINE_CHANNELS: 'RTL, FNB-PB;FNB-BACHA' }).onlineChannels).toEqual(['RTL', 'FNB-PB', 'FNB-BACHA']);
    for (const bad of ['RTL_GL', 'rtl-gl', 'R', '../RTL', 'RTL/GL', 'RTL.GL', '-RTL', 'A'.repeat(17)]) {
      expect(() => testConfig({ ONLINE_CHANNELS: bad }).onlineChannels).toThrow(ConfigError);
    }
  });

  it('§11.3: nama env/secret per kanal: "-" → "_", huruf kecil untuk nama secret', () => {
    expect(onlineApiKeyEnvName('RTL-GL')).toBe('ODOO_API_KEY_ONLINE_RTL_GL');
    expect(onlineApiKeyEnvName('FNB-BACHA')).toBe('ODOO_API_KEY_ONLINE_FNB_BACHA');
    expect(onlineApiKeySecretName('RTL-GL')).toBe('odoo_api_key_online_rtl_gl');
    expect(onlineApiKeySecretName('FNB-PB')).toBe('odoo_api_key_online_fnb_pb');
    expect(onlineApiKeySecretName('RTL')).toBe('odoo_api_key_online_rtl');
  });

  it('API key kanal berhifen: dibaca dari ODOO_API_KEY_ONLINE_<CHANNEL>_FILE, fallback ${SECRETS_DIR}/odoo_api_key_online_<channel>', () => {
    const key = randomSecret();
    const reads: string[] = [];
    const reader = (expected: string) => (p: string): Buffer => {
      reads.push(p);
      if (p === expected) return Buffer.from(`${key}\n`);
      throw new Error('ENOENT');
    };
    expect(testConfig({ ODOO_API_KEY_ONLINE_RTL_GL_FILE: '/s/gl' }, reader('/s/gl')).odooApiKeyOnline('RTL-GL')).toBe(key);
    expect(testConfig({ SECRETS_DIR: '/run/secrets' }, reader('/run/secrets/odoo_api_key_online_fnb_bacha')).odooApiKeyOnline('FNB-BACHA')).toBe(key);
    expect(reads).toContain('/run/secrets/odoo_api_key_online_fnb_bacha');
    // placeholder gen-secrets ditolak untuk kanal berhifen juga
    expect(() => testConfig({ ODOO_API_KEY_ONLINE_RTL_BACHA_FILE: '/s/b' }, () => Buffer.from('disabled')).odooApiKeyOnline('RTL-BACHA')).toThrow(/placeholder 'disabled'/);
  });

  it('tanpa path traversal: kode kanal tak valid ditolak SEBELUM membentuk nama env/path (reader tidak pernah dipanggil)', () => {
    const reads: string[] = [];
    const c = testConfig({}, (p) => {
      reads.push(p);
      throw new Error('ENOENT');
    });
    for (const bad of ['../../etc/passwd', 'RTL/../../x', 'RTL..GL', '..', 'RTL GL', 'rtl', 'RTL_GL', 'RTL\u0000', 'R', '']) {
      expect(ONLINE_CHANNEL_RE.test(bad)).toBe(false);
      expect(() => c.odooApiKeyOnline(bad)).toThrow(ConfigError);
      expect(() => onlineApiKeySecretName(bad)).toThrow(ConfigError);
      expect(() => onlineApiKeyEnvName(bad)).toThrow(ConfigError);
    }
    expect(reads.filter((p) => !p.startsWith('/test-secrets/'))).toEqual([]);
  });

  it('start: kanal berhifen aktif tanpa secret → gagal start menyebut NAMA env saja (tanpa nilai)', () => {
    const c = testConfig({ ONLINE_ENABLED: 'true', ONLINE_CHANNELS: 'RTL-GL', ODOO_ONLINE_EVENT_HMAC_FILE: '/s/hmac' }, (p) => {
      if (p === '/s/hmac') return Buffer.from(randomSecret());
      throw new Error('ENOENT');
    });
    expect(() => c.assertStartupSecrets()).toThrow(/ODOO_API_KEY_ONLINE_RTL_GL/);
    // env literal kanal berhifen di luar APP_ENV=dev ditolak (SEC-29)
    const lit = testConfig({ ONLINE_ENABLED: 'true', ONLINE_CHANNELS: 'RTL-GL', ODOO_API_KEY_ONLINE_RTL_GL: randomSecret() });
    expect(() => lit.assertStartupSecrets()).toThrow(/ODOO_API_KEY_ONLINE_RTL_GL sebagai env literal ditolak/);
  });
});
