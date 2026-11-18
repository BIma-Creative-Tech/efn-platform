import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { collectBootProblems, collectKeyProblems, parseConfig, odooOpenUrl } from '@/lib/config';
import { contrastRatio, textOn } from '@/lib/contrast';
import { authenticate } from '@/lib/gate';
import { clientIp, RateLimiter } from '@/lib/rate-limit';
import { JtiCache } from '@/lib/jti-cache';

const K1 = 'a1'.repeat(32);
const K2 = 'b2'.repeat(32);
const K3 = 'c3'.repeat(32);
const PEM = '-----BEGIN CERTIFICATE-----\nxx\n-----END CERTIFICATE-----';

const baseEnv = {
  INSIGHT_ENABLED: 'true',
  APP_ENV: 'production',
  NODE_ENV: 'production',
  CH_URL: 'https://clickhouse:8443',
  EFN_INSIGHT_ODOO_URL: 'https://erp.efn.test:9443',
  NODE_EXTRA_CA_CERTS: '/run/secrets/efn_ca_crt',
};

function files(over: Record<string, string | null> = {}): (p: string) => string | null {
  const m: Record<string, string | null> = {
    '/run/secrets/insight_sso_key': K1,
    '/run/secrets/insight_session_key': K2,
    '/run/secrets/insight_scope_key': K3,
    '/run/secrets/clickhouse_password_insight': 'pw\n',
    '/run/secrets/efn_ca_crt': PEM,
    ...over,
  };
  return (p) => (p in m ? m[p] : null);
}

describe('konfigurasi', () => {
  it('INSIGHT_ENABLED hanya aktif bila tepat "true"', () => {
    expect(parseConfig({}).enabled).toBe(false);
    expect(parseConfig({ INSIGHT_ENABLED: '1' }).enabled).toBe(false);
    expect(parseConfig({ INSIGHT_ENABLED: 'TRUE' }).enabled).toBe(false);
    expect(parseConfig({ INSIGHT_ENABLED: 'true' }).enabled).toBe(true);
  });

  it('origin Odoo memuat port (scheme+host+port) dan tautan kembali', () => {
    const c = parseConfig(baseEnv);
    expect(c.odooOrigin).toBe('https://erp.efn.test:9443');
    expect(odooOpenUrl(c)).toBe('https://erp.efn.test:9443/efn/insight/open');
    expect(parseConfig({ ...baseEnv, EFN_INSIGHT_ODOO_URL: 'https://erp.efn.test/' }).odooOrigin).toBe('https://erp.efn.test');
  });

  it('CH_URL http ditolak (production, atau tanpa EFN_ALLOW_PLAINTEXT)', () => {
    expect(parseConfig({ ...baseEnv, CH_URL: 'http://clickhouse:8123' }).problems.join()).toContain('CH_URL');
    expect(parseConfig({ ...baseEnv, APP_ENV: 'development', NODE_ENV: 'development', CH_URL: 'http://ch:8123' }).problems.join()).toContain('CH_URL');
    const dev = parseConfig({ APP_ENV: 'development', EFN_ALLOW_PLAINTEXT: 'true', CH_URL: 'http://ch:8123' });
    expect(dev.problems).toEqual([]);
    expect(parseConfig({ ...baseEnv, EFN_ALLOW_PLAINTEXT: 'true', CH_URL: 'http://ch:8123' }).problems.length).toBeGreaterThan(0);
  });

  it('URL dengan kredensial/query ditolak; NODE_TLS_REJECT_UNAUTHORIZED=0 ditolak di production', () => {
    expect(parseConfig({ ...baseEnv, CH_URL: 'https://u:p@ch:8443' }).problems.join()).toContain('kredensial');
    expect(parseConfig({ ...baseEnv, EFN_INSIGHT_ODOO_URL: 'https://erp/?x=1' }).problems.join()).toContain('query');
    expect(parseConfig({ ...baseEnv, NODE_TLS_REJECT_UNAUTHORIZED: '0' }).problems.join()).toContain('NODE_TLS_REJECT_UNAUTHORIZED');
  });

  it('boot: konfigurasi lengkap valid -> tanpa masalah', () => {
    expect(collectBootProblems(parseConfig(baseEnv), baseEnv, files())).toEqual([]);
  });

  it('boot: kunci kembar, bukan hex, < 64, atau hilang -> masalah', () => {
    const cfg = parseConfig(baseEnv);
    expect(collectBootProblems(cfg, baseEnv, files({ '/run/secrets/insight_session_key': K1 })).join()).toContain('berbeda');
    expect(collectBootProblems(cfg, baseEnv, files({ '/run/secrets/insight_scope_key': K1 })).length).toBeGreaterThan(0);
    expect(collectBootProblems(cfg, baseEnv, files({ '/run/secrets/insight_sso_key': 'zz'.repeat(40) })).join()).toContain('insight_sso_key');
    expect(collectBootProblems(cfg, baseEnv, files({ '/run/secrets/insight_sso_key': 'ab'.repeat(31) })).join()).toContain('insight_sso_key');
    expect(collectBootProblems(cfg, baseEnv, files({ '/run/secrets/insight_scope_key': null })).join()).toContain('insight_scope_key');
  });

  it('boot: password kosong, CA hilang/bukan PEM, origin Odoo kosong -> masalah', () => {
    const cfg = parseConfig(baseEnv);
    expect(collectBootProblems(cfg, baseEnv, files({ '/run/secrets/clickhouse_password_insight': '  ' })).join()).toContain('password');
    expect(collectBootProblems(cfg, baseEnv, files({ '/run/secrets/efn_ca_crt': 'bukan pem' })).join()).toContain('NODE_EXTRA_CA_CERTS');
    const noOdoo = { ...baseEnv, EFN_INSIGHT_ODOO_URL: '' };
    expect(collectBootProblems(parseConfig(noOdoo), noOdoo, files()).join()).toContain('EFN_INSIGHT_ODOO_URL');
  });

  it('kunci saat INSIGHT_ENABLED=false: file hilang dilewati, tapi kembar/lemah tetap dilaporkan', () => {
    const cfg = parseConfig({});
    expect(collectKeyProblems(cfg, () => null, false)).toEqual([]);
    const dup = files({ '/run/secrets/insight_session_key': K1 });
    expect(collectKeyProblems(cfg, dup, false).join()).toContain('berbeda');
    expect(collectKeyProblems(cfg, files({ '/run/secrets/insight_sso_key': 'abc' }), false).join()).toContain('insight_sso_key');
  });
});

describe('gerbang sesi', () => {
  it('kill switch menolak termasuk cookie berlaku; kunci hilang = misconfigured', () => {
    expect(authenticate('apa saja', false, K1, 1).status).toBe('disabled');
    expect(authenticate('apa saja', true, null, 1).status).toBe('misconfigured');
    expect(authenticate(undefined, true, K1, 1).status).toBe('unauthenticated');
    expect(authenticate('eis1.bukan.valid', true, K1, 1).status).toBe('invalid');
  });
});

describe('memori: jti & rate limit', () => {
  it('JtiCache kedaluwarsa setelah TTL', () => {
    const c = new JtiCache(1000);
    c.add('j', 0);
    expect(c.has('j', 999)).toBe(true);
    expect(c.has('j', 1000)).toBe(false);
  });
  it('RateLimiter jendela tetap', () => {
    const r = new RateLimiter(2, 1000);
    expect(r.hit('a', 0).allowed).toBe(true);
    expect(r.hit('a', 1).allowed).toBe(true);
    expect(r.hit('a', 2).allowed).toBe(false);
    expect(r.hit('a', 1001).allowed).toBe(true);
  });
  it('clientIp memakai entri paling kanan X-Forwarded-For', () => {
    expect(clientIp('1.1.1.1, 2.2.2.2')).toBe('2.2.2.2');
    expect(clientIp(null)).toBe('unknown');
  });
});

describe('token warna (globals.css) - kontras WCAG', () => {
  const css = readFileSync(fileURLToPath(new URL('../app/globals.css', import.meta.url)), 'utf8').replace(/\r\n/g, '\n');
  const block = (start: string): Record<string, string> => {
    const i = css.indexOf(start);
    const body = css.slice(css.indexOf('{', i) + 1, css.indexOf('}', i));
    const out: Record<string, string> = {};
    for (const m of body.matchAll(/--([\w-]+):\s*(#[0-9a-fA-F]{6});/g)) out[m[1]] = m[2];
    return out;
  };
  const light = block(":root,\n[data-theme='light']");
  const dark = block("[data-theme='dark']");

  it('token terbaca di kedua tema', () => {
    expect(light.surface).toBe('#ffffff');
    expect(dark.surface).toBe('#0a2f2b');
  });

  it.each([
    ['text', 'surface'], ['text', 'bg'], ['text-muted', 'surface'], ['text-muted', 'bg'],
    ['accent', 'surface'], ['good', 'surface'], ['warn', 'surface'], ['bad', 'surface'], ['info', 'surface'],
    ['text', 'accent-soft'], ['text', 'good-soft'], ['text', 'warn-soft'], ['text', 'bad-soft'], ['text', 'info-soft'], ['text', 'surface-2'],
  ])('teks %s di %s >= 4,5 (light dan dark)', (fg, bg) => {
    expect(contrastRatio(light[fg], light[bg])).toBeGreaterThanOrEqual(4.5);
    // Latar `*-soft` gelap dipakai dengan teks --text; pasangan lain dites langsung.
    expect(contrastRatio(dark[fg], dark[bg])).toBeGreaterThanOrEqual(4.5);
  });

  it('tombol primer, aksen kontras, chrome', () => {
    expect(contrastRatio(light['accent-contrast'], light.accent)).toBeGreaterThanOrEqual(4.5);
    expect(contrastRatio(dark['accent-contrast'], dark.accent)).toBeGreaterThanOrEqual(4.5);
    expect(contrastRatio(light['chrome-muted'], light.chrome)).toBeGreaterThanOrEqual(4.5);
    expect(contrastRatio(dark['chrome-muted'], dark.chrome)).toBeGreaterThanOrEqual(4.5);
    expect(contrastRatio(light['chrome-text'], light.chrome)).toBeGreaterThanOrEqual(4.5);
  });

  it('border kontrol (text-muted) >= 3:1 terhadap permukaan', () => {
    expect(contrastRatio(light['text-muted'], light.surface)).toBeGreaterThanOrEqual(3);
    expect(contrastRatio(dark['text-muted'], dark.surface)).toBeGreaterThanOrEqual(3);
  });

  it.each(['c1', 'c2', 'c3', 'c4', 'c5', 'c6', 'c7'])('seri %s >= 3:1 terhadap permukaan (light dan dark)', (c) => {
    expect(contrastRatio(light[c], light.surface)).toBeGreaterThanOrEqual(3);
    expect(contrastRatio(dark[c], dark.surface)).toBeGreaterThanOrEqual(3);
  });

  it('textOn memilih teks berkontras >= 4,5 di seluruh skala sekuensial', () => {
    for (const theme of [light, dark]) {
      for (let i = 1; i <= 5; i += 1) {
        const bg = theme[`seq-${i}`];
        expect(contrastRatio(bg, textOn(bg))).toBeGreaterThanOrEqual(4.5);
      }
    }
  });
});
