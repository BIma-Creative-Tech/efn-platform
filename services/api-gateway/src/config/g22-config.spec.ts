import { randomSecret, testConfig } from '../common/testing/fakes';
import { ConfigError } from './app-config';

/** G22 docs/04 §14.A.9, §14.C.1, §14.D.8 + L-72 (D-22-18), L-73, L-76. */
describe('AppConfig G22', () => {
  it('default aman: QR, poin online, job hangus, anonimisasi OFF; batas kiriman 10 (L-76); MAX_AGE 0', () => {
    const c = testConfig();
    expect([c.fnbQrEnabled, c.onlineLoyaltyRedeemEnabled, c.onlineLoyaltyEarnEnabled, c.loyaltyExpiryEnabled, c.loyaltyInactiveAnonymize, c.fnbQrPointsEnabled]).toEqual([
      false,
      false,
      false,
      false,
      false,
      false,
    ]);
    expect(c.loyaltyExpiryDryRun).toBe(true);
    expect(c.fnbMaxSubmissionsPerSession).toBe(10);
    expect(c.fnbTableSessionTtlSeconds).toBe(14_400);
    expect(c.loyaltyPointMaxAgeDays).toBe(0);
    expect(c.fnbCaptchaMode).toBe('off');
    expect(() => testConfig().assertStartupSecrets()).not.toThrow();
  });

  it('D-22-18: LOYALTY_POINT_EXPIRY_DAYS diset → gagal start di production, peringatan di dev/test', () => {
    expect(() => testConfig({ LOYALTY_POINT_EXPIRY_DAYS: '365', APP_ENV: 'production' }).assertStartupSecrets()).toThrow(/LOYALTY_POINT_EXPIRY_DAYS/);
    const dev = testConfig({ LOYALTY_POINT_EXPIRY_DAYS: '365' });
    expect(() => dev.assertStartupSecrets()).not.toThrow();
    expect(dev.startupWarnings()).toHaveLength(1);
  });

  it('FNB_QR_ENABLED=true (API8): wajib kunci token ≥ 32 byte, HMAC event ≠ service key, URL tenant https per PT', () => {
    const base = { FNB_QR_ENABLED: 'true', ODOO_ONLINE_MOCK: 'true', FNB_QR_BASE_URL_PB: 'https://pb.example', FNB_QR_BASE_URL_BCF: 'https://bcf.example' };
    expect(() => testConfig(base).assertStartupSecrets()).toThrow(/FNB_TABLE_TOKEN_KEY/);
    const ok = { ...base, FNB_TABLE_TOKEN_KEY: randomSecret(), ODOO_FNB_EVENT_HMAC: randomSecret() };
    expect(() => testConfig(ok).assertStartupSecrets()).not.toThrow();
    expect(() => testConfig({ ...ok, FNB_QR_BASE_URL_BCF: 'http://bcf.example' }).assertStartupSecrets()).toThrow(/FNB_QR_BASE_URL_BCF/);
    expect(() => testConfig({ ...ok, FNB_QR_BASE_URL_BCF: '' }).assertStartupSecrets()).toThrow(/FNB_QR_BASE_URL_BCF/);
    expect(() => testConfig({ ...ok, FNB_TABLE_TOKEN_KEY: 'k'.repeat(20) }).fnbTableTokenKey).toThrow(ConfigError);
    const same = randomSecret();
    expect(() => testConfig({ ...ok, ODOO_FNB_EVENT_HMAC: same, ODOO_BFF_API_KEY: same }).odooFnbEventHmac).toThrow(/berbeda/);
    // kunci token = turunan HKDF (bukan secret mentah), 32 byte
    expect(testConfig(ok).fnbTableTokenKey).toHaveLength(32);
  });

  it('FNB_QR_COMPANIES hanya PB|BCF; literal API key QR di luar dev ditolak; katalog disalin juga untuk PT QR', () => {
    expect(() => testConfig({ FNB_QR_COMPANIES: 'PB,GL' }).fnbQrCompanies).toThrow(ConfigError);
    expect(() => testConfig({ ODOO_API_KEY_QR_PB: randomSecret() }).assertStartupSecrets()).toThrow(/ODOO_API_KEY_QR_PB/);
    expect(testConfig({ FNB_QR_ENABLED: 'true', ONLINE_CHANNELS: 'GL' }).catalogSyncChannels).toEqual(['GL', 'PB', 'BCF']);
    expect(testConfig({ ONLINE_CHANNELS: 'GL' }).catalogSyncChannels).toEqual(['GL']);
  });

  it('job hangus: epoch wajib bila aktif; rentang bulan mengikuti plafon 6..36', () => {
    expect(() => testConfig({ LOYALTY_EXPIRY_ENABLED: 'true' }).assertStartupSecrets()).toThrow(/LOYALTY_ACTIVITY_EPOCH/);
    expect(() => testConfig({ LOYALTY_EXPIRY_ENABLED: 'true', LOYALTY_ACTIVITY_EPOCH: '2026-11-01' }).assertStartupSecrets()).not.toThrow();
    expect(() => testConfig({ LOYALTY_ACTIVITY_EPOCH: '2026-02-30' }).loyaltyActivityEpoch).toThrow(ConfigError);
    expect(testConfig({ LOYALTY_ACTIVITY_EPOCH: '2026-11-01' }).loyaltyActivityEpoch?.toISOString()).toBe('2026-10-31T17:00:00.000Z');
    expect(() => testConfig({ LOYALTY_INACTIVITY_EXPIRY_MONTHS: '5' }).loyaltyInactivityExpiryMonths).toThrow(ConfigError);
    expect(() => testConfig({ LOYALTY_EXPIRY_NOTICE_DAYS: '61' }).loyaltyExpiryNoticeDays).toThrow(ConfigError);
  });
});
