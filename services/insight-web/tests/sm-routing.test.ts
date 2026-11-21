/**
 * Routing persona supermarket (docs/04 §28.13.11): pemeriksaan akses, beranda, navigasi, chip peran, dan gerbang proxy.ts
 * untuk sesi eis2. Halaman ESB lama disembunyikan untuk sesi SMD-saja.
 */
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { NextRequest } from 'next/server';
import { afterAll, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import { proxy } from '@/proxy';
import { SESSION_COOKIE, createSessionValue } from '@/lib/session';
import { DENIAL_PATH, hasEsbAccess, homePath, navGroups, personaChip, personaOfPath, sessionPersonas, smFilterLinks, smRouteDenial } from '@/lib/sm-personas';
import { verifySsoTokenV2, type SsoPayloadV2 } from '@/lib/token';
import { IAT, KEY, TOKEN_A, TOKEN_B, TOKEN_C } from './helpers-v2';

const s2 = (roles: string[], codes: string[] = ['SMD']) => ({ v: 2 as const, roles, codes });

describe('smRouteDenial', () => {
  it('halaman non-persona tidak dibatasi', () => {
    expect(smRouteDenial('/sales', s2(['store', 'user']), true)).toBeNull();
    expect(smRouteDenial('/', s2(['user']), false)).toBeNull();
  });
  it('flag INSIGHT_SM_ENABLED mati -> sm-belum-aktif untuk semua rute persona', () => {
    for (const p of ['/store', '/exec', '/category', '/dc', '/marketing']) {
      expect(smRouteDenial(p, s2(['store', 'exec', 'user']), false)).toBe('sm-belum-aktif');
    }
  });
  it('sesi eis1 -> buka-ulang', () => {
    expect(smRouteDenial('/store', { v: 1, roles: ['user'], codes: ['PB'] }, true)).toBe('buka-ulang');
  });
  it('persona tidak ada di roles -> tidak-berwenang (exec tidak otomatis boleh halaman lain, Q-INS-S9)', () => {
    expect(smRouteDenial('/store', s2(['exec', 'user']), true)).toBe('tidak-berwenang');
    expect(smRouteDenial('/dc', s2(['store', 'user']), true)).toBe('tidak-berwenang');
  });
  it('persona ada -> boleh, termasuk sub-path', () => {
    expect(smRouteDenial('/store', s2(['store', 'user']), true)).toBeNull();
    expect(smRouteDenial('/store/semua', s2(['store', 'user']), true)).toBeNull();
    expect(smRouteDenial('/storefront', s2(['user']), true)).toBeNull(); // bukan rute persona
  });
  it('personaOfPath & halaman penolakan tersedia', () => {
    expect(personaOfPath('/marketing')).toBe('marketing');
    expect(personaOfPath('/marketingx')).toBeNull();
    expect(Object.values(DENIAL_PATH).sort()).toEqual(['/buka-ulang', '/sm-belum-aktif', '/tidak-berwenang']);
  });
});

describe('beranda, navigasi, chip', () => {
  it('beranda: sesi ber-ESB tetap "/", SMD-saja ke persona pertama menurut prioritas', () => {
    expect(homePath({ codes: ['BCF', 'SMD'], roles: ['store', 'user'] })).toBe('/');
    expect(homePath({ codes: ['SMD'], roles: ['exec', 'store', 'user'] })).toBe('/store');
    expect(homePath({ codes: ['SMD'], roles: ['exec', 'marketing', 'user'] })).toBe('/marketing');
    expect(homePath({ codes: ['SMD'], roles: ['category', 'dc', 'user'] })).toBe('/dc');
    expect(homePath({ codes: ['SMD'], roles: ['exec', 'user'] })).toBe('/exec');
  });
  it('sessionPersonas urut prioritas', () => {
    expect(sessionPersonas(['exec', 'store', 'user', 'category'])).toEqual(['store', 'category', 'exec']);
  });
  it('SMD-saja: halaman ESB disembunyikan, hanya grup Supermarket', () => {
    const g = navGroups({ codes: ['SMD'], roles: ['store', 'user'] }, true);
    expect(g).toHaveLength(1);
    expect(g[0].items.map((i) => i.href)).toEqual(['/store']);
    expect(g[0].title).toBeUndefined();
    expect(navGroups({ codes: ['SMD'], roles: ['exec', 'store', 'user'] }, true)[0].title).toBe('Supermarket');
  });
  it('sesi campuran: dua grup berjudul; ESB-saja: satu grup tanpa judul; flag SM mati: tanpa grup SM', () => {
    const mixed = navGroups({ codes: ['PB', 'SMD'], roles: ['exec', 'user'] }, true);
    expect(mixed.map((g) => g.title)).toEqual(['Food & Beverage', 'Supermarket']);
    expect(mixed[0].items.some((i) => i.href === '/data-quality')).toBe(true);
    const esb = navGroups({ codes: ['PB'], roles: ['user'] }, true);
    expect(esb).toHaveLength(1);
    expect(esb[0].title).toBeUndefined();
    expect(navGroups({ codes: ['PB', 'SMD'], roles: ['exec', 'user'] }, false).flatMap((g) => g.items).some((i) => i.href === '/exec')).toBe(false);
  });
  it('hasEsbAccess', () => {
    expect(hasEsbAccess({ codes: ['SMD'] })).toBe(false);
    expect(hasEsbAccess({ codes: ['SMD', 'PB'] })).toBe(true);
  });
  it('chip: kepala toko satu outlet bernama; direksi semua outlet; DC tidak dihitung outlet', () => {
    const wh = [
      { id: 20, name: 'DC Pusat', type: 'dc' as const },
      { id: 21, name: 'Outlet Kelapa Gading', type: 'outlet' as const },
      { id: 22, name: 'Outlet Cibubur', type: 'outlet' as const },
    ];
    expect(personaChip('store', [21], wh)).toBe('Kepala toko · Outlet Kelapa Gading');
    expect(personaChip('exec', [0, 20, 21, 22], wh)).toBe('Direksi · semua outlet (2)');
    expect(personaChip('store', [21, 22], wh)).toBe('Kepala toko · 2 outlet');
    expect(personaChip('store', [99], [])).toBe('Kepala toko · 1 outlet');
  });
});

// ---- gerbang proxy.ts dengan sesi eis2 ----
const SESSION_KEY = 'cd'.repeat(32);
let dir = '';
const saved: Record<string, string | undefined> = {};
const ENV_KEYS = ['INSIGHT_ENABLED', 'INSIGHT_SM_ENABLED', 'INSIGHT_ACCEPT_EIT1', 'INSIGHT_SESSION_KEY_FILE', 'APP_ENV', 'EFN_INSIGHT_ODOO_URL'];
const resetRuntime = (): void => {
  delete (globalThis as unknown as { __insightRuntime?: unknown }).__insightRuntime;
};

beforeAll(() => {
  dir = mkdtempSync(join(tmpdir(), 'insight-sm-'));
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
  process.env.INSIGHT_SM_ENABLED = 'true';
  delete process.env.INSIGHT_ACCEPT_EIT1;
  process.env.INSIGHT_SESSION_KEY_FILE = join(dir, 'session_key');
  process.env.APP_ENV = 'development';
  process.env.EFN_INSIGHT_ODOO_URL = 'https://erp.test:9443';
  resetRuntime();
});

function cookieFor(token: string): string {
  const r = verifySsoTokenV2(token, KEY, IAT + 10, () => false);
  if (!r.ok) throw new Error('token uji');
  return createSessionValue(r.payload as SsoPayloadV2, SESSION_KEY, Math.floor(Date.now() / 1000));
}
const req = (path: string, cookie: string): NextRequest =>
  new NextRequest(`https://insight.test${path}`, { headers: { cookie: `${SESSION_COOKIE}=${cookie}` } });
const rewriteOf = (res: Response): string | null => res.headers.get('x-middleware-rewrite');

describe('proxy: gerbang persona (eis2)', () => {
  it('persona di roles -> lanjut', () => {
    expect(rewriteOf(proxy(req('/store', cookieFor(TOKEN_B))))).toBeNull();
    expect(rewriteOf(proxy(req('/exec', cookieFor(TOKEN_A))))).toBeNull();
    expect(rewriteOf(proxy(req('/category', cookieFor(TOKEN_C))))).toBeNull();
  });
  it('persona tidak di roles -> rewrite /tidak-berwenang tanpa Location', () => {
    const res = proxy(req('/dc', cookieFor(TOKEN_B)));
    expect(rewriteOf(res)).toContain('/tidak-berwenang');
    expect(res.headers.get('location')).toBeNull();
  });
  it('INSIGHT_SM_ENABLED mati -> /sm-belum-aktif walau persona sah', () => {
    process.env.INSIGHT_SM_ENABLED = 'false';
    resetRuntime();
    expect(rewriteOf(proxy(req('/store', cookieFor(TOKEN_B))))).toContain('/sm-belum-aktif');
    process.env.INSIGHT_SM_ENABLED = 'TRUE';
    resetRuntime();
    expect(rewriteOf(proxy(req('/store', cookieFor(TOKEN_B))))).toContain('/sm-belum-aktif');
  });
  it('INSIGHT_ACCEPT_EIT1=false: cookie eis1 invalid -> /sesi-berakhir; eis2 tetap diterima', () => {
    process.env.INSIGHT_ACCEPT_EIT1 = 'false';
    resetRuntime();
    const v1 = createSessionValue(
      { aud: 'insight-web', cids: [3], codes: ['PB'], exp: 0, iat: 0, iss: 'odoo', jti: '0'.repeat(32), name: 'A', roles: ['user'], sub: 1, v: 1 },
      SESSION_KEY,
      Math.floor(Date.now() / 1000),
    );
    const res = proxy(req('/sales', v1));
    expect(rewriteOf(res)).toContain('/sesi-berakhir');
    expect(res.headers.get('set-cookie')).toContain('Max-Age=0');
    expect(rewriteOf(proxy(req('/store', cookieFor(TOKEN_B))))).toBeNull();
  });
  it('cookie eis1 (diterima) membuka halaman persona -> /buka-ulang', () => {
    const v1 = createSessionValue(
      { aud: 'insight-web', cids: [3], codes: ['PB'], exp: 0, iat: 0, iss: 'odoo', jti: '0'.repeat(32), name: 'A', roles: ['user'], sub: 1, v: 1 },
      SESSION_KEY,
      Math.floor(Date.now() / 1000),
    );
    expect(rewriteOf(proxy(req('/store', v1)))).toContain('/buka-ulang');
    expect(rewriteOf(proxy(req('/sales', v1)))).toBeNull();
  });
  it('halaman informasi penolakan bersifat publik', () => {
    for (const p of ['/sm-belum-aktif', '/tidak-berwenang', '/buka-ulang']) {
      const res = proxy(new NextRequest(`https://insight.test${p}`));
      expect(rewriteOf(res), p).toBeNull();
      expect(res.headers.get('x-middleware-next'), p).toBe('1');
    }
  });
});

describe('smFilterLinks (chip SMD di filter Brand / PT halaman F&B)', () => {
  it('sesi gabungan ESB + SM -> SMD ke Direksi bila persona exec ada, selain itu persona pertama', () => {
    expect(smFilterLinks({ codes: ['PB', 'SMD'], roles: ['all_brands', 'store', 'exec', 'user'] }, true)).toEqual([{ code: 'SMD', href: '/exec' }]);
    expect(smFilterLinks({ codes: ['PB', 'SMD'], roles: ['marketing', 'category', 'user'] }, true)).toEqual([{ code: 'SMD', href: '/category' }]);
  });
  it('kosong bila SM mati, tanpa persona, atau tanpa kode SM', () => {
    expect(smFilterLinks({ codes: ['PB', 'SMD'], roles: ['exec', 'user'] }, false)).toEqual([]);
    expect(smFilterLinks({ codes: ['PB', 'SMD'], roles: ['user'] }, true)).toEqual([]);
    expect(smFilterLinks({ codes: ['PB'], roles: ['exec', 'user'] }, true)).toEqual([]);
  });
});
