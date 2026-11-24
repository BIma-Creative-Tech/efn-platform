/**
 * Gerbang dan navigasi persona F&B (docs/04 §28.15.9, §28.15.11, G52): fnbRouteDenial (matriks 7 baris kombinasi persona),
 * sesi ber-cabang, navGroups dengan item persona F&B, smRouteDenial untuk sesi F&B berpersona, dan gerbang proxy.ts.
 */
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { NextRequest } from 'next/server';
import { afterAll, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import { proxy } from '@/proxy';
import {
  BRANCH_BLOCKED_PATHS,
  FNB_DENIAL_PATH,
  fnbPersonaOfPath,
  fnbRouteDenial,
  fnbStoreChip,
  type FnbSessionLike,
} from '@/lib/fnb-personas';
import { SESSION_COOKIE, createSessionValue } from '@/lib/session';
import { navGroups, smRouteDenial } from '@/lib/sm-personas';
import { verifySsoTokenV3 } from '@/lib/token';
import { IAT, KEY, TOKEN_D, TOKEN_E, TOKEN_F, TOKEN_G } from './helpers-v3';

const s3 = (roles: string[], codes: string[], brs: string[] = []): FnbSessionLike => ({ v: 3, roles, codes, brs });
const PERSONA_PATHS = ['/fnb/exec', '/fnb/store', '/fnb/marketing'] as const;
const denials = (s: FnbSessionLike, enabled = true) => PERSONA_PATHS.map((p) => fnbRouteDenial(p, s, enabled));
const TW = 'tidak-berwenang';

describe('fnbRouteDenial: matriks kombinasi persona (§28.15.11)', () => {
  it('1. exec -> hanya Eksekutif', () => {
    expect(denials(s3(['exec', 'user'], ['PB']))).toEqual([null, TW, TW]);
  });
  it('2. marketing -> hanya Marketing', () => {
    expect(denials(s3(['marketing', 'user'], ['PB', 'WTZ']))).toEqual([TW, TW, null]);
  });
  it('3. store saja (ber-cabang) -> hanya Toko', () => {
    expect(denials(s3(['store', 'user'], ['PB'], ['pb:12', 'pb:7']))).toEqual([TW, null, TW]);
  });
  it('4. store + exec (PT penuh, brs kosong) -> Eksekutif dan Toko', () => {
    expect(denials(s3(['exec', 'store', 'user'], ['PB']))).toEqual([null, null, TW]);
  });
  it('5. store + marketing -> Odoo hanya menerbitkan marketing (store tidak): Marketing saja', () => {
    expect(denials(s3(['marketing', 'user'], ['PB']))).toEqual([TW, TW, null]);
  });
  it('6. store + category/dc (SMD): tanpa cakupan ESB -> semua rute F&B ditolak (fail closed)', () => {
    expect(denials(s3(['category', 'dc', 'user'], ['SMD']))).toEqual([TW, TW, TW]);
    // bahkan bila (tidak sah) roles memuat persona F&B tetapi kode ESB tidak ada
    expect(denials(s3(['exec', 'store', 'user'], ['SMD']))).toEqual([TW, TW, TW]);
  });
  it('7. store + all_brands (brs kosong) -> Toko dengan semua cabang', () => {
    expect(denials(s3(['all_brands', 'store'], ['BCF', 'BCR', 'CUP', 'IMD', 'PB', 'WTZ']))).toEqual([TW, null, TW]);
  });
});

describe('fnbRouteDenial: flag, versi sesi, rute', () => {
  const exec = s3(['exec', 'user'], ['PB']);
  it('flag mati -> fnb-belum-aktif untuk semua rute persona F&B (walau persona sah)', () => {
    expect(denials(exec, false)).toEqual(['fnb-belum-aktif', 'fnb-belum-aktif', 'fnb-belum-aktif']);
  });
  it('sesi < eis3 -> buka-ulang (urutan: flag lebih dulu)', () => {
    expect(fnbRouteDenial('/fnb/exec', { ...exec, v: 2 }, true)).toBe('buka-ulang');
    expect(fnbRouteDenial('/fnb/exec', { ...exec, v: 1 }, true)).toBe('buka-ulang');
    expect(fnbRouteDenial('/fnb/exec', { ...exec, v: 2 }, false)).toBe('fnb-belum-aktif');
  });
  it('sub-path persona mengikuti aturan yang sama; /fnb, /fnb/storefront, /storefront bukan rute persona', () => {
    expect(fnbRouteDenial('/fnb/store/semua', s3(['store', 'user'], ['PB'], ['pb:7']), true)).toBeNull();
    expect(fnbRouteDenial('/fnb/exec/x', s3(['store', 'user'], ['PB'], ['pb:7']), true)).toBe(TW);
    for (const p of ['/fnb', '/fnb/storefront', '/storefront', '/']) expect(fnbRouteDenial(p, exec, false), p).toBeNull();
    expect(fnbPersonaOfPath('/fnb/marketing')).toBe('marketing');
    expect(fnbPersonaOfPath('/fnb/marketingx')).toBeNull();
  });
  it('tidak membaca INSIGHT_SM_ENABLED: hasil sama untuk sesi tanpa kode SM (tak ada parameter SM)', () => {
    expect(fnbRouteDenial.length).toBe(3);
    expect(fnbRouteDenial('/fnb/exec', exec, true)).toBeNull();
  });
  it('halaman penolakan: tiga tujuan publik', () => {
    expect(Object.values(FNB_DENIAL_PATH).sort()).toEqual(['/buka-ulang', '/fnb-belum-aktif', '/tidak-berwenang']);
  });
});

describe('sesi ber-cabang (store-only F&B)', () => {
  const branched = s3(['store', 'user'], ['PB'], ['pb:7']);
  it('halaman F&B tingkat PT ditolak, termasuk sub-path; beranda dan glosarium tidak ditolak di gerbang', () => {
    for (const p of BRANCH_BLOCKED_PATHS) {
      expect(fnbRouteDenial(p, branched, true), p).toBe(TW);
      expect(fnbRouteDenial(`${p}/x`, branched, true), p).toBe(TW);
    }
    expect(BRANCH_BLOCKED_PATHS).toEqual(['/sales', '/payments', '/yoy', '/flow', '/distribution', '/forecast', '/data-quality', '/waste', '/fill-rate']);
    expect(fnbRouteDenial('/', branched, true)).toBeNull(); // dialihkan ke /fnb/store oleh pageCtx
    expect(fnbRouteDenial('/glosarium', branched, true)).toBeNull();
  });
  it('penutupan tidak bergantung flag (fail closed bila flag mati)', () => {
    expect(fnbRouteDenial('/sales', branched, false)).toBe(TW);
  });
  it('sesi tanpa cabang tidak terkena penutupan halaman F&B', () => {
    for (const p of BRANCH_BLOCKED_PATHS) expect(fnbRouteDenial(p, s3(['exec', 'user'], ['PB']), true), p).toBeNull();
  });
  it('chip: satu cabang dengan nama, satu cabang tanpa nama, banyak cabang', () => {
    expect(fnbStoreChip(['pb:7'], new Map([['pb:7', 'PB Kelapa Gading']]))).toBe('Kepala toko · PB Kelapa Gading');
    expect(fnbStoreChip(['pb:7'], new Map())).toBe('Kepala toko · 1 cabang');
    expect(fnbStoreChip(['pb:12', 'pb:7'], new Map([['pb:7', 'X']]))).toBe('Kepala toko · 2 cabang');
  });
});

describe('smRouteDenial untuk sesi F&B berpersona (G52)', () => {
  it('kepala toko F&B (tanpa kode SM) tidak boleh membuka halaman persona supermarket walau persona store ada di roles', () => {
    expect(smRouteDenial('/store', { v: 3, roles: ['store', 'user'], codes: ['PB'] }, true)).toBe(TW);
    expect(smRouteDenial('/exec', { v: 3, roles: ['exec', 'user'], codes: ['PB'] }, true)).toBe(TW);
  });
  it('sesi eis3 dengan kode SM dan persona tetap diterima', () => {
    expect(smRouteDenial('/exec', { v: 3, roles: ['exec', 'user'], codes: ['BCF', 'PB', 'SMD'] }, true)).toBeNull();
    expect(smRouteDenial('/store', { v: 3, roles: ['exec', 'user'], codes: ['SMD'] }, true)).toBe(TW);
  });
});

const hrefs = (g: { items: readonly { href: string }[] }): string[] => g.items.map((i) => i.href);
const FNB_BASE = ['/sales', '/payments', '/yoy', '/flow', '/distribution', '/forecast', '/data-quality'];

describe('navGroups dengan item persona F&B (§28.15.9)', () => {
  it('flag aktif: item persona di urutan teratas grup F&B (Eksekutif, Toko, Marketing) sebelum halaman bersama', () => {
    const g = navGroups({ codes: ['PB'], roles: ['exec', 'store', 'user'], v: 3, brs: [] }, false, true);
    expect(g.map((x) => x.id)).toEqual(['summary', 'fnb']);
    expect(hrefs(g[1])).toEqual(['/fnb/exec', '/fnb/store', ...FNB_BASE]);
    const m = navGroups({ codes: ['PB'], roles: ['marketing', 'user'], v: 3, brs: [] }, false, true);
    expect(hrefs(m[1])).toEqual(['/fnb/marketing', ...FNB_BASE]);
    expect(g[1].items.slice(0, 2).map((i) => i.label)).toEqual(['Eksekutif', 'Toko']);
    expect(navGroups({ codes: ['PB'], roles: ['marketing', 'exec', 'store', 'user'], v: 3, brs: [] }, false, true)[1].items.slice(0, 3).map((i) => i.label)).toEqual([
      'Eksekutif',
      'Toko',
      'Marketing',
    ]);
  });
  it('snapshot: flag mati atau sesi < eis3 -> grup F&B persis seperti sebelum G52', () => {
    expect(hrefs(navGroups({ codes: ['PB'], roles: ['exec', 'user'], v: 3, brs: [] }, false, false)[1])).toEqual(FNB_BASE);
    expect(hrefs(navGroups({ codes: ['PB'], roles: ['exec', 'user'] }, false)[1])).toEqual(FNB_BASE);
    expect(hrefs(navGroups({ codes: ['PB'], roles: ['exec', 'user'], v: 2, brs: [] }, false, true)[1])).toEqual(FNB_BASE);
    expect(hrefs(navGroups({ codes: ['PB'], roles: ['user'], v: 3, brs: [] }, false, true)[1])).toEqual(FNB_BASE);
  });
  it('sesi ber-cabang: hanya grup F&B berisi "Toko"; Ringkasan dan grup lain disembunyikan (juga bila flag SM aktif)', () => {
    for (const sm of [false, true]) {
      const g = navGroups({ codes: ['PB'], roles: ['store', 'user'], v: 3, brs: ['pb:7'] }, sm, true);
      expect(g).toHaveLength(1);
      expect(g[0]).toMatchObject({ id: 'fnb', title: 'Food & Beverage' });
      expect(g[0].items).toEqual([{ href: '/fnb/store', label: 'Toko', icon: 'store' }]);
    }
  });
  it('sesi ber-cabang dengan flag F&B mati -> tanpa menu (fail closed)', () => {
    expect(navGroups({ codes: ['PB'], roles: ['store', 'user'], v: 3, brs: ['pb:7'] }, true, false)).toEqual([]);
  });
  it('kepala toko F&B (tanpa kode SM) tidak mendapat grup Supermarket walau INSIGHT_SM_ENABLED aktif', () => {
    const g = navGroups({ codes: ['PB'], roles: ['exec', 'store', 'user'], v: 3, brs: [] }, true, true);
    expect(g.map((x) => x.id)).toEqual(['summary', 'fnb']);
    expect(g.flatMap((x) => hrefs(x))).not.toContain('/exec');
    expect(g.flatMap((x) => hrefs(x))).not.toContain('/store');
  });
  it('exec F&B + SMD: item persona F&B di depan, halaman bersama ber-scope esb, grup Supermarket tetap', () => {
    const g = navGroups({ codes: ['PB', 'SMD'], roles: ['exec', 'user'], v: 3, brs: [] }, true, true);
    expect(g.map((x) => x.id)).toEqual(['summary', 'fnb', 'sm']);
    expect(hrefs(g[1])).toEqual(['/fnb/exec', ...FNB_BASE]);
    expect(g[1].items[0].scope).toBeUndefined();
    expect(g[1].items.filter((i) => i.scope === 'esb').map((i) => i.href)).toEqual(['/sales', '/payments', '/forecast']);
    expect(g[1].esbCodes).toEqual(['PB']);
    expect(hrefs(g[2])).toEqual(['/exec', '/sales', '/payments', '/forecast']);
  });
  it('SMD-saja (tanpa kode ESB): tidak ada item persona F&B', () => {
    const g = navGroups({ codes: ['SMD'], roles: ['exec', 'user'], v: 3, brs: [] }, true, true);
    expect(g.flatMap((x) => hrefs(x)).some((h) => h.startsWith('/fnb/'))).toBe(false);
  });
  it('(href, scope) unik per sesi', () => {
    const all = navGroups({ codes: ['PB', 'SMD'], roles: ['exec', 'marketing', 'user'], v: 3, brs: [] }, true, true).flatMap((x) => x.items);
    expect(new Set(all.map((i) => `${i.href}:${i.scope ?? ''}`)).size).toBe(all.length);
  });
});

// ---- gerbang proxy.ts dengan sesi eis3 ----
const SESSION_KEY = 'cd'.repeat(32);
let dir = '';
const saved: Record<string, string | undefined> = {};
const ENV_KEYS = ['INSIGHT_ENABLED', 'INSIGHT_SM_ENABLED', 'INSIGHT_FNB_PERSONA_ENABLED', 'INSIGHT_ACCEPT_EIT2', 'INSIGHT_SESSION_KEY_FILE', 'APP_ENV', 'EFN_INSIGHT_ODOO_URL'];
const resetRuntime = (): void => {
  delete (globalThis as unknown as { __insightRuntime?: unknown }).__insightRuntime;
};

beforeAll(() => {
  dir = mkdtempSync(join(tmpdir(), 'insight-fnb-'));
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
  process.env.INSIGHT_SM_ENABLED = 'false'; // gerbang F&B tidak boleh bergantung pada flag SM
  process.env.INSIGHT_FNB_PERSONA_ENABLED = 'true';
  delete process.env.INSIGHT_ACCEPT_EIT2;
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

describe('proxy: gerbang F&B (eis3)', () => {
  it('persona sah -> lanjut walau INSIGHT_SM_ENABLED=false', () => {
    expect(rewriteOf(proxy(req('/fnb/exec', cookieFor(TOKEN_E))))).toBeNull();
    expect(rewriteOf(proxy(req('/fnb/marketing', cookieFor(TOKEN_F))))).toBeNull();
    expect(rewriteOf(proxy(req('/fnb/store', cookieFor(TOKEN_D))))).toBeNull();
    expect(rewriteOf(proxy(req('/fnb/store', cookieFor(TOKEN_G))))).toBeNull();
  });
  it('persona tidak di roles -> rewrite /tidak-berwenang tanpa Location', () => {
    const res = proxy(req('/fnb/exec', cookieFor(TOKEN_D)));
    expect(rewriteOf(res)).toContain('/tidak-berwenang');
    expect(res.headers.get('location')).toBeNull();
    expect(rewriteOf(proxy(req('/fnb/store', cookieFor(TOKEN_F))))).toContain('/tidak-berwenang');
  });
  it('flag F&B mati atau tidak "true" persis -> /fnb-belum-aktif', () => {
    for (const v of ['false', 'TRUE', '']) {
      process.env.INSIGHT_FNB_PERSONA_ENABLED = v;
      resetRuntime();
      expect(rewriteOf(proxy(req('/fnb/exec', cookieFor(TOKEN_E)))), v).toContain('/fnb-belum-aktif');
    }
    delete process.env.INSIGHT_FNB_PERSONA_ENABLED;
    resetRuntime();
    expect(rewriteOf(proxy(req('/fnb/store', cookieFor(TOKEN_D))))).toContain('/fnb-belum-aktif');
  });
  it('sesi ber-cabang: /sales dan halaman F&B lain -> tidak-berwenang; beranda lanjut (pageCtx mengalihkan ke /fnb/store)', () => {
    const c = cookieFor(TOKEN_D);
    for (const p of BRANCH_BLOCKED_PATHS) expect(rewriteOf(proxy(req(p, c))), p).toContain('/tidak-berwenang');
    expect(rewriteOf(proxy(req('/', c)))).toBeNull();
    expect(rewriteOf(proxy(req('/glosarium', c)))).toBeNull();
    // juga tertutup bila flag F&B mati
    process.env.INSIGHT_FNB_PERSONA_ENABLED = 'false';
    resetRuntime();
    expect(rewriteOf(proxy(req('/sales', c)))).toContain('/tidak-berwenang');
  });
  it('sesi ber-cabang tidak bisa membuka halaman persona supermarket (/store) walau INSIGHT_SM_ENABLED=true', () => {
    process.env.INSIGHT_SM_ENABLED = 'true';
    resetRuntime();
    expect(rewriteOf(proxy(req('/store', cookieFor(TOKEN_D))))).toContain('/tidak-berwenang');
  });
  it('sesi tanpa cabang (exec) tetap memakai halaman F&B tingkat PT', () => {
    expect(rewriteOf(proxy(req('/sales', cookieFor(TOKEN_E))))).toBeNull();
    expect(rewriteOf(proxy(req('/', cookieFor(TOKEN_F))))).toBeNull();
  });
  it('INSIGHT_ACCEPT_EIT2=false tidak memengaruhi sesi eis3', () => {
    process.env.INSIGHT_ACCEPT_EIT2 = 'false';
    resetRuntime();
    expect(rewriteOf(proxy(req('/fnb/exec', cookieFor(TOKEN_E))))).toBeNull();
  });
  it('/fnb-belum-aktif bersifat publik', () => {
    const res = proxy(new NextRequest('https://insight.test/fnb-belum-aktif'));
    expect(rewriteOf(res)).toBeNull();
    expect(res.headers.get('x-middleware-next')).toBe('1');
  });
});
