import { randomSecret, testConfig } from '../common/testing/fakes';
import { isSecretEnvName } from './secrets.loader';

/** G50-A (docs/04 §28.10): flag default false, validasi start, secret per file. */
describe('AppConfig G50-A', () => {
  it('semua flag G50 default false', () => {
    const c = testConfig();
    expect([
      c.behaviorTrackingEnabled,
      c.wishlistEnabled,
      c.restockAlertEnabled,
      c.priceAlertEnabled,
      c.promoCampaignEnabled,
      c.campaignFrequencyCapEnabled,
      c.campaignBudgetCapEnabled,
      c.emailEnabled,
      c.waUnofficialEnabled,
    ]).toEqual([false, false, false, false, false, false, false, false, false]);
    expect(c.behaviorRateDevicePerMinute).toBe(120);
    expect(c.behaviorRateIpPerMinute).toBe(600);
    expect(c.behaviorEventRetentionDays).toBe(7);
    expect(c.campaignCompanies).toEqual(['SMD']);
    expect(c.segmentAllowedClientNames).toEqual(['segment-worker']);
  });

  it('pepper adalah secret (ditolak sebagai env literal di luar dev) dan wajib berbeda saat tracking aktif', () => {
    expect(isSecretEnvName('BEHAVIOR_SUBJECT_PEPPER')).toBe(true);
    expect(isSecretEnvName('BEHAVIOR_DEVICE_PEPPER')).toBe(true);
    const same = randomSecret();
    expect(() => testConfig({ BEHAVIOR_TRACKING_ENABLED: 'true', BEHAVIOR_SUBJECT_PEPPER: same, BEHAVIOR_DEVICE_PEPPER: same }).assertStartupSecrets()).toThrow(/harus berbeda/);
    expect(() =>
      testConfig({ BEHAVIOR_TRACKING_ENABLED: 'true', BEHAVIOR_SUBJECT_PEPPER: randomSecret(), BEHAVIOR_DEVICE_PEPPER: randomSecret() }).assertStartupSecrets(),
    ).not.toThrow();
    expect(() => testConfig({ BEHAVIOR_TRACKING_ENABLED: 'true' }).assertStartupSecrets()).toThrow(/BEHAVIOR_SUBJECT_PEPPER/);
  });

  it('retensi staging maks 7 hari; kode PT kampanye tervalidasi; API key kampanye literal ditolak di luar dev', () => {
    expect(() => testConfig({ BEHAVIOR_EVENT_RETENTION_DAYS: '30' }).assertStartupSecrets()).toThrow(/BEHAVIOR_EVENT_RETENTION_DAYS/);
    expect(() => testConfig({ CAMPAIGN_COMPANIES: 'smd' }).assertStartupSecrets()).toThrow(/CAMPAIGN_COMPANIES/);
    expect(() => testConfig({ ODOO_API_KEY_CAMPAIGN_SMD: 'x'.repeat(32) }).assertStartupSecrets()).toThrow(/ODOO_API_KEY_CAMPAIGN_SMD/);
  });

  it('kampanye aktif tanpa mock → API key per PT wajib', () => {
    expect(() => testConfig({ PROMO_CAMPAIGN_ENABLED: 'true', CAMPAIGN_COMPANIES: 'SMD' }).assertStartupSecrets()).toThrow(/ODOO_API_KEY_CAMPAIGN_SMD/);
  });

  it('V-01: BEHAVIOR_ANON_VISIT_ENABLED default false dan efektif hanya bersama BEHAVIOR_TRACKING_ENABLED', () => {
    expect(testConfig().behaviorAnonVisitEnabled).toBe(false);
    expect(testConfig().behaviorVisitLogEnabled).toBe(false);
    expect(testConfig({ BEHAVIOR_ANON_VISIT_ENABLED: 'true' }).behaviorAnonVisitEnabled).toBe(true);
    expect(testConfig({ BEHAVIOR_ANON_VISIT_ENABLED: 'true' }).behaviorVisitLogEnabled).toBe(false);
    expect(testConfig({ BEHAVIOR_TRACKING_ENABLED: 'true' }).behaviorVisitLogEnabled).toBe(false);
    expect(testConfig({ BEHAVIOR_TRACKING_ENABLED: 'true', BEHAVIOR_ANON_VISIT_ENABLED: 'true' }).behaviorVisitLogEnabled).toBe(true);
  });

  it('consent purpose tracking dibaca dari CONSENT_TRACKING_*', () => {
    expect(testConfig({ CONSENT_TRACKING_VERSION: 'tracking@1', CONSENT_TRACKING_TEXT_SHA256: 'b'.repeat(64) }).consentActiveText('tracking')).toEqual({
      version: 'tracking@1',
      textSha256: 'b'.repeat(64),
    });
  });
});
