/**
 * Gerbang, navigasi, dan konfigurasi halaman G67 (docs/04 §31.8): `/pnl` (persona exec), `/waste`, `/fill-rate`,
 * flag INSIGHT_G67_ENABLED (default mati, hanya "true" persis), item menu, dan gerbang proxy.ts. Data sintetis saja.
 */
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { NextRequest } from 'next/server';
import { afterAll, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import { proxy } from '@/proxy';
import { parseConfig } from '@/lib/config';
import { fnbPersonaOfPath, fnbRouteDenial, type FnbSessionLike } from '@/lib/fnb-personas';
import { G67_PATHS, g67NavItems, g67PathOf, g67RouteDenial } from '@/lib/g67-access';
import { appliesTo, selectableCodes } from '@/lib/page-filters';
import { SESSION_COOKIE, createSessionValue } from '@/lib/session';
import { navGroups } from '@/lib/sm-personas';
import { verifySsoTokenV3 } from '@/lib/token';
import { IAT, KEY, TOKEN_D, TOKEN_E, TOKEN_F } from './helpers-v3';

const s3 = (roles: string[], codes: string[], brs: string[] = [], v = 3): FnbSessionLike => ({ v, roles, codes, brs });
const ON = { g67Enabled: true, fnbEnabled: true };
const TW = 'tidak-berwenang';

describe('flag INSIGHT_G67_ENABLED', () => {
  it('default mati; aktif hanya string persis "true"', () => {
    expect(parseConfig({}).g67Enabled).toBe(false);
    for (const v of ['1', 'TRUE', 'True', 'yes', '', ' true']) expect(parseConfig({ INSIGHT_G67_ENABLED: v }).g67Enabled, v).toBe(false);
    expect(parseConfig({ INSIGHT_G67_ENABLED: 'true' }).g67Enabled).toBe(true);
  });
});

describe('g67RouteDenial', () => {
  const exec = s3(['exec', 'user'], ['PB']);
  const mkt = s3(['marketing', 'user'], ['PB', 'WTZ']);

  it('mengenali rute G67 persis atau awalan segmen', () => {
    expect(G67_PATHS).toEqual(['/pnl', '/waste', '/fill-rate']);
    expect(g67PathOf('/pnl')).toBe('/pnl');
    expect(g67PathOf('/waste/x')).toBe('/waste');
    for (const p of ['/pnlx', '/fill-rates', '/wasted', '/', '/fnb/exec']) expect(g67PathOf(p), p).toBeNull();
    expect(g67RouteDenial('/sales', exec, { g67Enabled: false, fnbEnabled: false })).toBeNull();
  });
  it('flag G67 mati -> g67-belum-aktif untuk ketiga rute (walau persona sah)', () => {
    for (const p of G67_PATHS) expect(g67RouteDenial(p, exec, { g67Enabled: false, fnbEnabled: true }), p).toBe('g67-belum-aktif');
  });
  it('/pnl hanya exec: marketing ditolak, /waste dan /fill-rate terbuka bagi pengguna F&B ber-cakupan PT', () => {
    expect(g67RouteDenial('/pnl', exec, ON)).toBeNull();
    expect(g67RouteDenial('/pnl', mkt, ON)).toBe(TW);
    expect(g67RouteDenial('/waste', mkt, ON)).toBeNull();
    expect(g67RouteDenial('/fill-rate', mkt, ON)).toBeNull();
    expect(g67RouteDenial('/waste', s3(['user'], ['PB']), ON)).toBeNull();
  });
  it('/pnl memakai flag F&B (INSIGHT_FNB_PERSONA_ENABLED); /waste dan /fill-rate tidak', () => {
    expect(g67RouteDenial('/pnl', exec, { g67Enabled: true, fnbEnabled: false })).toBe('fnb-belum-aktif');
    expect(g67RouteDenial('/waste', exec, { g67Enabled: true, fnbEnabled: false })).toBeNull();
    expect(fnbPersonaOfPath('/pnl')).toBe('exec');
    expect(fnbRouteDenial('/pnl', mkt, true)).toBe(TW);
  });
  it('sesi < eis3 -> buka-ulang; tanpa kode ESB (SMD saja) -> tidak-berwenang', () => {
    for (const p of G67_PATHS) {
      expect(g67RouteDenial(p, s3(['exec', 'user'], ['PB'], [], 2), ON), p).toBe('buka-ulang');
      expect(g67RouteDenial(p, s3(['exec', 'user'], ['SMD']), ON), p).toBe(TW);
    }
  });
  it('sesi ber-cabang ditolak di ketiga rute (kelas PT)', () => {
    const b = s3(['store', 'exec', 'user'], ['PB'], ['pb:7']);
    for (const p of G67_PATHS) expect(g67RouteDenial(p, b, ON), p).toBe(TW);
  });
});

describe('navigasi G67 (grup Food & Beverage)', () => {
  const hrefs = (g: { items: readonly { href: string }[] } | undefined): string[] => (g?.items ?? []).map((i) => i.href);
  const fnb = (s: Parameters<typeof navGroups>[0], g67: boolean, fnbOn = true) => navGroups(s, false, fnbOn, false, g67).find((g) => g.id === 'fnb');

  it('flag mati -> item tidak muncul', () => {
    const h = hrefs(fnb({ codes: ['PB'], roles: ['exec', 'user'], v: 3, brs: [] }, false));
    for (const p of G67_PATHS) expect(h).not.toContain(p);
  });
  it('exec + flag aktif -> tiga item, disisipkan sebelum Forecast', () => {
    const h = hrefs(fnb({ codes: ['PB'], roles: ['exec', 'user'], v: 3, brs: [] }, true));
    for (const p of G67_PATHS) expect(h).toContain(p);
    expect(h.indexOf('/fill-rate')).toBeLessThan(h.indexOf('/forecast'));
    expect(h.indexOf('/distribution')).toBeLessThan(h.indexOf('/pnl'));
  });
  it('marketing: tanpa Laba Rugi, dengan Waste dan Fill Rate', () => {
    const h = hrefs(fnb({ codes: ['PB'], roles: ['marketing', 'user'], v: 3, brs: [] }, true));
    expect(h).not.toContain('/pnl');
    expect(h).toContain('/waste');
    expect(h).toContain('/fill-rate');
  });
  it('sesi ber-cabang tidak mendapat item G67', () => {
    const g = navGroups({ codes: ['PB'], roles: ['store', 'exec', 'user'], v: 3, brs: ['pb:7'] }, false, true, false, true);
    expect(g.flatMap((x) => x.items).map((i) => i.href)).toEqual(['/fnb/store']);
  });
  it('g67NavItems konsisten dengan g67RouteDenial', () => {
    expect(g67NavItems(s3(['exec'], ['PB']), { g67Enabled: false, fnbEnabled: true })).toEqual([]);
    expect(g67NavItems(s3(['exec'], ['PB']), ON).map((i) => i.href)).toEqual(['/pnl', '/waste', '/fill-rate']);
  });
});

describe('filter toolbar G67', () => {
  it('SMD tidak ditawarkan di /pnl, /waste, /fill-rate', () => {
    for (const p of G67_PATHS) expect(selectableCodes(p, ['PB', 'SMD']), p).toEqual(['PB']);
  });
  it('/pnl tanpa rentang tanggal global (bulan di halaman); /waste dan /fill-rate memakai tanggal; ketiganya PT + cabang', () => {
    expect(appliesTo('/pnl', 'date')).toBe(false);
    expect(appliesTo('/waste', 'date')).toBe(true);
    expect(appliesTo('/fill-rate', 'date')).toBe(true);
    for (const p of G67_PATHS) {
      expect(appliesTo(p, 'pt'), p).toBe(true);
      expect(appliesTo(p, 'branch'), p).toBe(true);
      expect(appliesTo(p, 'cat'), p).toBe(false);
    }
  });
});

// ---------- gerbang proxy.ts ----------
const SESSION_KEY = 'ef'.repeat(32);
let dir = '';
const saved: Record<string, string | undefined> = {};
const ENV_KEYS = ['INSIGHT_ENABLED', 'INSIGHT_SM_ENABLED', 'INSIGHT_FNB_PERSONA_ENABLED', 'INSIGHT_G67_ENABLED', 'INSIGHT_SESSION_KEY_FILE', 'APP_ENV', 'EFN_INSIGHT_ODOO_URL'];
const resetRuntime = (): void => {
  delete (globalThis as unknown as { __insightRuntime?: unknown }).__insightRuntime;
};

beforeAll(() => {
  dir = mkdtempSync(join(tmpdir(), 'insight-g67-'));
  writeFileSync(join(dir, 'session_key'), `${SESSION_KEY}\n`, 'ascii');
  for (const k of ENV_KEYS) saved[k] = process.env[k];
});
afterAll(() => {
  rmSync(dir, { recursive: true, force: true });
  for (const k of ENV_KEYS) {
    if (saved[k] === undefined) delete process.env[k];
    else process.env[k] = saved[k];
  }
  resetRuntime();
});
beforeEach(() => {
  process.env.INSIGHT_ENABLED = 'true';
  process.env.INSIGHT_SM_ENABLED = 'false';
  process.env.INSIGHT_FNB_PERSONA_ENABLED = 'true';
  process.env.INSIGHT_G67_ENABLED = 'true';
  process.env.INSIGHT_SESSION_KEY_FILE = join(dir, 'session_key');
  process.env.APP_ENV = 'development';
  process.env.EFN_INSIGHT_ODOO_URL = 'https://erp.test:9443';
  resetRuntime();
});

function cookieFor(token: string): string {
  const r = verifySsoTokenV3(token, KEY, IAT + 10, () => false);
  if (!r.ok) throw new Error('token uji');
  return createSessionValue(r.payload, SESSION_KEY, Math.floor(Date.now() / 1000));
}
const req = (path: string, cookie: string): NextRequest =>
  new NextRequest(`https://insight.test${path}`, { headers: { cookie: `${SESSION_COOKIE}=${cookie}` } });
const rewriteOf = (res: Response): string | null => res.headers.get('x-middleware-rewrite');

describe('proxy: gerbang G67', () => {
  it('flag aktif + exec -> ketiga rute lanjut', () => {
    for (const p of G67_PATHS) expect(rewriteOf(proxy(req(p, cookieFor(TOKEN_E)))), p).toBeNull();
  });
  it('flag mati/tidak "true" persis -> rewrite /g67-belum-aktif (tanpa Location), item tidak bocor', () => {
    for (const v of ['false', 'TRUE', '']) {
      process.env.INSIGHT_G67_ENABLED = v;
      resetRuntime();
      for (const p of G67_PATHS) {
        const res = proxy(req(p, cookieFor(TOKEN_E)));
        expect(rewriteOf(res), `${p}:${v}`).toContain('/g67-belum-aktif');
        expect(res.headers.get('location')).toBeNull();
      }
    }
    delete process.env.INSIGHT_G67_ENABLED;
    resetRuntime();
    expect(rewriteOf(proxy(req('/waste', cookieFor(TOKEN_E))))).toContain('/g67-belum-aktif');
  });
  it('marketing: /pnl -> tidak-berwenang; /waste dan /fill-rate lanjut', () => {
    const c = cookieFor(TOKEN_F);
    expect(rewriteOf(proxy(req('/pnl', c)))).toContain('/tidak-berwenang');
    expect(rewriteOf(proxy(req('/waste', c)))).toBeNull();
    expect(rewriteOf(proxy(req('/fill-rate', c)))).toBeNull();
  });
  it('sesi ber-cabang: semua rute G67 -> tidak-berwenang (juga saat flag G67 mati)', () => {
    const c = cookieFor(TOKEN_D);
    for (const p of G67_PATHS) expect(rewriteOf(proxy(req(p, c))), p).toContain('/tidak-berwenang');
    process.env.INSIGHT_G67_ENABLED = 'false';
    resetRuntime();
    for (const p of ['/waste', '/fill-rate']) expect(rewriteOf(proxy(req(p, c))), p).toContain('/tidak-berwenang');
  });
  it('/g67-belum-aktif bersifat publik', () => {
    const res = proxy(new NextRequest('https://insight.test/g67-belum-aktif'));
    expect(rewriteOf(res)).toBeNull();
  });
});
