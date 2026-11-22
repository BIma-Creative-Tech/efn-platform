import { describe, expect, it } from 'vitest';
import { describeProblems, loadConfig, parseHttpsUrl } from '@/lib/config';

describe('parseHttpsUrl', () => {
  it('menerima URL https absolut', () => {
    expect(parseHttpsUrl('https://erp.efn.local:9443/web/login')).toBe('https://erp.efn.local:9443/web/login');
  });
  it.each([
    undefined,
    '',
    '   ',
    'http://erp.efn.local/web/login',
    '//erp.efn.local/web/login',
    '/web/login',
    'javascript:alert(1)',
    'https://user:pw@erp.efn.local/',
    'not a url',
  ])('menolak %s', (v) => {
    expect(parseHttpsUrl(v)).toBeNull();
  });
});

describe('loadConfig', () => {
  it('kedua tujuan aktif bila env valid', () => {
    const c = loadConfig({
      LOGIN_GATEWAY_ERP_URL: 'https://erp.efn.local:9443/web/login',
      LOGIN_GATEWAY_INSIGHT_URL: 'https://insight.efn.local:9443/',
    });
    expect(c.erp).toEqual({ enabled: true, url: 'https://erp.efn.local:9443/web/login' });
    expect(c.insight).toEqual({ enabled: true, url: 'https://insight.efn.local:9443/' });
  });
  it('unset dan invalid dibedakan, tanpa melempar', () => {
    const c = loadConfig({ LOGIN_GATEWAY_ERP_URL: 'http://x.local/' });
    expect(c.erp).toEqual({ enabled: false, reason: 'invalid' });
    expect(c.insight).toEqual({ enabled: false, reason: 'unset' });
  });
  it('log masalah memuat nama variabel, bukan nilai', () => {
    const msgs = describeProblems(loadConfig({ LOGIN_GATEWAY_ERP_URL: 'http://rahasia.local/' }));
    expect(msgs.join('\n')).toContain('LOGIN_GATEWAY_ERP_URL');
    expect(msgs.join('\n')).not.toContain('rahasia');
  });
});
