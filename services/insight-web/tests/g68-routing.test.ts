/**
 * Gerbang, navigasi, dan konfigurasi halaman G68 (docs/04 §32.8): `/pnl` (persona exec), `/waste`, `/fill-rate`,
 * flag INSIGHT_G68_ENABLED (default mati, hanya "true" persis), item menu, dan gerbang proxy.ts. Data sintetis saja.
 */
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { NextRequest } from 'next/server';
import { afterAll, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import { proxy } from '@/proxy';
import { parseConfig } from '@/lib/config';
import { fnbPersonaOfPath, fnbRouteDenial, type FnbSessionLike } from '@/lib/fnb-personas';
import { G68_PATHS, g68NavItems, g68PathOf, g68RouteDenial } from '@/lib/g68-access';
import { appliesTo, selectableCodes } from '@/lib/page-filters';
import { SESSION_COOKIE, createSessionValue } from '@/lib/session';
import { navGroups } from '@/lib/sm-personas';
import { verifySsoTokenV3 } from '@/lib/token';
import { IAT, KEY, TOKEN_D, TOKEN_E, TOKEN_F } from './helpers-v3';

const s3 = (roles: string[], codes: string[], brs: string[] = [], v = 3): FnbSessionLike => ({ v, roles, codes, brs });
const ON = { g68Enabled: true, fnbEnabled: true };
const TW = 'tidak-berwenang';

describe('flag INSIGHT_G68_ENABLED', () => {
  it('default mati; aktif hanya string persis "true"', () => {
    expect(parseConfig({}).g68Enabled).toBe(false);
    for (const v of ['1', 'TRUE', 'True', 'yes', '', ' true']) expect(parseConfig({ INSIGHT_G68_ENABLED: v }).g68Enabled, v).toBe(false);
    expect(parseConfig({ INSIGHT_G68_ENABLED: 'true' }).g68Enabled).toBe(true);
  });
});

describe('g68RouteDenial', () => {
  const exec = s3(['exec', 'user'], ['PB']);
  const mkt = s3(['marketing', 'user'], ['PB', 'WTZ']);

  it('mengenali rute G68 persis atau awalan segmen', () => {
    expect(G68_PATHS).toEqual(['/pnl', '/waste', '/fill-rate']);
    expect(g68PathOf('/pnl')).toBe('/pnl');
    expect(g68PathOf('/waste/x')).toBe('/waste');
    for (const p of ['/pnlx', '/fill-rates', '/wasted', '/', '/fnb/exec']) expect(g68PathOf(p), p).toBeNull();
    expect(g68RouteDenial('/sales', exec, { g68Enabled: false, fnbEnabled: false })).toBeNull();
  });
  it('flag G68 mati -> g68-belum-aktif untuk ketiga rute (walau persona sah)', () => {
    for (const p of G68_PATHS) expect(g68RouteDenial(p, exec, { g68Enabled: false, fnbEnabled: true }), p).toBe('g68-belum-aktif');
  });
  it('/pnl hanya exec: marketing ditolak, /waste dan /fill-rate terbuka bagi pengguna F&B ber-cakupan PT', () => {
    expect(g68RouteDenial('/pnl', exec, ON)).toBeNull();
    expect(g68RouteDenial('/pnl', mkt, ON)).toBe(TW);
    expect(g68RouteDenial('/waste', mkt, ON)).toBeNull();
    expect(g68RouteDenial('/fill-rate', mkt, ON)).toBeNull();
    expect(g68RouteDenial('/waste', s3(['user'], ['PB']), ON)).toBeNull();
  });
  it('/pnl memakai flag F&B (INSIGHT_FNB_PERSONA_ENABLED); /waste dan /fill-rate tidak', () => {
    expect(g68RouteDenial('/pnl', exec, { g68Enabled: true, fnbEnabled: false })).toBe('fnb-belum-aktif');
    expect(g68RouteDenial('/waste', exec, { g68Enabled: true, fnbEnabled: false })).toBeNull();
    expect(fnbPersonaOfPath('/pnl')).toBe('exec');
    expect(fnbRouteDenial('/pnl', mkt, true)).toBe(TW);
  });
  it('sesi < eis3 -> buka-ulang; tanpa kode ESB (SMD saja) -> tidak-berwenang', () => {
    for (const p of G68_PATHS) {
      expect(g68RouteDenial(p, s3(['exec', 'user'], ['PB'], [], 2), ON), p).toBe('buka-ulang');
      expect(g68RouteDenial(p, s3(['exec', 'user'], ['SMD']), ON), p).toBe(TW);
    }
  });
  it('sesi ber-cabang ditolak di ketiga rute (kelas PT)', () => {
    const b = s3(['store', 'exec', 'user'], ['PB'], ['pb:7']);
    for (const p of G68_PATHS) expect(g68RouteDenial(p, b, ON), p).toBe(TW);
  });
});

describe('navigasi G68 (grup Food & Beverage)', () => {
  const hrefs = (g: { items: readonly { href: string }[] } | undefined): string[] => (g?.items ?? []).map((i) => i.href);
  const fnb = (s: Parameters<typeof navGroups>[0], g68: boolean, fnbOn = true) => navGroups(s, false, fnbOn, false, g68).find((g) => g.id === 'fnb');

  it('flag mati -> item tidak muncul', () => {
    const h = hrefs(fnb({ codes: ['PB'], roles: ['exec', 'user'], v: 3, brs: [] }, false));
    for (const p of G68_PATHS) expect(h).not.toContain(p);
  });
  it('exec + flag aktif -> tiga item, disisipkan sebelum Forecast', () => {
    const h = hrefs(fnb({ codes: ['PB'], roles: ['exec', 'user'], v: 3, brs: [] }, true));
    for (const p of G68_PATHS) expect(h).toContain(p);
    expect(h.indexOf('/fill-rate')).toBeLessThan(h.indexOf('/forecast'));
    expect(h.indexOf('/distribution')).toBeLessThan(h.indexOf('/pnl'));
  });
  it('marketing: tanpa Laba Rugi, dengan Waste dan Fill Rate', () => {
    const h = hrefs(fnb({ codes: ['PB'], roles: ['marketing', 'user'], v: 3, brs: [] }, true));
    expect(h).not.toContain('/pnl');
    expect(h).toContain('/waste');
    expect(h).toContain('/fill-rate');
  });
  it('sesi ber-cabang tidak mendapat item G68', () => {
    const g = navGroups({ codes: ['PB'], roles: ['store', 'exec', 'user'], v: 3, brs: ['pb:7'] }, false, true, false, true);
    expect(g.flatMap((x) => x.items).map((i) => i.href)).toEqual(['/fnb/store']);
  });
  it('g68NavItems konsisten dengan g68RouteDenial', () => {
    expect(g68NavItems(s3(['exec'], ['PB']), { g68Enabled: false, fnbEnabled: true })).toEqual([]);
    expect(g68NavItems(s3(['exec'], ['PB']), ON).map((i) => i.href)).toEqual(['/pnl', '/waste', '/fill-rate']);
  });
});

describe('filter toolbar G68', () => {
  it('SMD tidak ditawarkan di /pnl, /waste, /fill-rate', () => {
    for (const p of G68_PATHS) expect(selectableCodes(p, ['PB', 'SMD']), p).toEqual(['PB']);
  });
  it('/pnl tanpa rentang tanggal global (bulan di halaman); /waste dan /fill-rate memakai tanggal; ketiganya PT + cabang', () => {
    expect(appliesTo('/pnl', 'date')).toBe(false);
    expect(appliesTo('/waste', 'date')).toBe(true);
    expect(appliesTo('/fill-rate', 'date')).toBe(true);
    for (const p of G68_PATHS) {
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
const ENV_KEYS = ['INSIGHT_ENABLED', 'INSIGHT_SM_ENABLED', 'INSIGHT_FNB_PERSONA_ENABLED', 'INSIGHT_G68_ENABLED', 'INSIGHT_SESSION_KEY_FILE', 'APP_ENV', 'EFN_INSIGHT_ODOO_URL'];
const resetRuntime = (): void => {
  delete (globalThis as unknown as { __insightRuntime?: unknown }).__insightRuntime;
};

beforeAll(() => {
  dir = mkdtempSync(join(tmpdir(), 'insight-g68-'));
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
  process.env.INSIGHT_G68_ENABLED = 'true';
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

describe('proxy: gerbang G68', () => {
  it('flag aktif + exec -> ketiga rute lanjut', () => {
    for (const p of G68_PATHS) expect(rewriteOf(proxy(req(p, cookieFor(TOKEN_E)))), p).toBeNull();
  });
  it('flag mati/tidak "true" persis -> rewrite /g68-belum-aktif (tanpa Location), item tidak bocor', () => {
    for (const v of ['false', 'TRUE', '']) {
      process.env.INSIGHT_G68_ENABLED = v;
      resetRuntime();
      for (const p of G68_PATHS) {
        const res = proxy(req(p, cookieFor(TOKEN_E)));
        expect(rewriteOf(res), `${p}:${v}`).toContain('/g68-belum-aktif');
        expect(res.headers.get('location')).toBeNull();
      }
    }
    delete process.env.INSIGHT_G68_ENABLED;
    resetRuntime();
    expect(rewriteOf(proxy(req('/waste', cookieFor(TOKEN_E))))).toContain('/g68-belum-aktif');
  });
  it('marketing: /pnl -> tidak-berwenang; /waste dan /fill-rate lanjut', () => {
    const c = cookieFor(TOKEN_F);
    expect(rewriteOf(proxy(req('/pnl', c)))).toContain('/tidak-berwenang');
    expect(rewriteOf(proxy(req('/waste', c)))).toBeNull();
    expect(rewriteOf(proxy(req('/fill-rate', c)))).toBeNull();
  });
  it('sesi ber-cabang: semua rute G68 -> tidak-berwenang (juga saat flag G68 mati)', () => {
    const c = cookieFor(TOKEN_D);
    for (const p of G68_PATHS) expect(rewriteOf(proxy(req(p, c))), p).toContain('/tidak-berwenang');
    process.env.INSIGHT_G68_ENABLED = 'false';
    resetRuntime();
    for (const p of ['/waste', '/fill-rate']) expect(rewriteOf(proxy(req(p, c))), p).toContain('/tidak-berwenang');
  });
  it('/g68-belum-aktif bersifat publik', () => {
    const res = proxy(new NextRequest('https://insight.test/g68-belum-aktif'));
    expect(rewriteOf(res)).toBeNull();
  });
});
