/**
 * Peran PP + aturan T9 (docs/04 §30.7), konfigurasi flag, gerbang rute /produksi (ppRouteDenial + proxy.ts), dan navigasi
 * "Produksi". Token/sesi memakai kunci uji sintetis; tidak ada secret nyata.
 */
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { NextRequest } from 'next/server';
import { afterAll, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import { proxy } from '@/proxy';
import { parseRolesV2, parseRolesV3, ppRolesOf, scopeConsistentV3 } from '@/lib/claims';
import { collectKeyProblems, parseConfig, parsePpUrl } from '@/lib/config';
import { PP_LOGIN_PATH, ppAccessDenial, ppCaps, ppNavActive, ppNavItems, ppRoleLabel, isProduksiPath } from '@/lib/prodplan-access';
import { SESSION_COOKIE, createSessionValue, verifySessionValue } from '@/lib/session';
import { navGroups } from '@/lib/sm-personas';
import { verifySsoTokenV3 } from '@/lib/token';
import { IAT, KEY, makeTokenV3 } from './helpers-v3';

const NOW = IAT + 10;
const never = () => false;
const SESSION_KEY = 'cd'.repeat(32);
const PP = ['pp_planner', 'user'];
interface Over {
  v?: number;
  codes?: string[];
  brs?: string[];
}

const ok = (over: Record<string, unknown>, n: number) => verifySsoTokenV3(makeTokenV3({ jti: n.toString(16).padStart(32, '0'), ...over }), KEY, NOW, never);

describe('roles v3: peran PP', () => {
  it('urutan ASCII dan panjang maks 8; PP hanya di v3 (v2 menolak)', () => {
    expect(parseRolesV3(['pp_planner', 'user'])).toEqual(['pp_planner', 'user']);
    expect(parseRolesV3(['pp_planner', 'pp_warehouse', 'user'])).toEqual(['pp_planner', 'pp_warehouse', 'user']);
    expect(parseRolesV3(['all_brands', 'category', 'dc', 'exec', 'marketing', 'pp_planner', 'pp_warehouse', 'store'])).not.toBeNull();
    // urutan salah / duplikat / tanpa dasar / dua dasar / peran asing
    expect(parseRolesV3(['user', 'pp_planner'])).toBeNull();
    expect(parseRolesV3(['pp_planner', 'pp_planner', 'user'])).toBeNull();
    expect(parseRolesV3(['pp_planner'])).toBeNull();
    expect(parseRolesV3(['all_brands', 'pp_planner', 'user'])).toBeNull();
    expect(parseRolesV3(['pp_admin', 'user'])).toBeNull();
    expect(parseRolesV3(['all_brands', 'category', 'dc', 'exec', 'marketing', 'pp_planner', 'pp_warehouse', 'store', 'user'])).toBeNull();
    // v2 (eit2/eis2) tidak pernah membawa PP
    expect(parseRolesV2(['pp_planner', 'user'])).toBeNull();
    expect(ppRolesOf(['exec', 'pp_warehouse', 'user'])).toEqual(['pp_warehouse']);
  });
});

describe('T9: peran PP => kode ESB dan tanpa cabang', () => {
  const base = { codes: ['PB'], wids: [] as number[], cats: [] as number[], brs: [] as string[] };

  it('PP + kode ESB + brs kosong -> sah', () => {
    expect(scopeConsistentV3({ ...base, roles: PP })).toBe(true);
    expect(scopeConsistentV3({ ...base, roles: ['exec', 'pp_planner', 'user'] })).toBe(true);
    expect(ok({ roles: PP, brs: [], codes: ['PB'] }, 1).ok).toBe(true);
  });

  it('PP dengan brs tidak kosong -> bad_claims (pembuat dan penerima)', () => {
    expect(scopeConsistentV3({ ...base, roles: ['pp_planner', 'store', 'user'], brs: ['pb:7'] })).toBe(false);
    expect(scopeConsistentV3({ ...base, roles: PP, brs: ['pb:7'] })).toBe(false);
    expect(ok({ roles: PP, brs: ['pb:7'] }, 2)).toEqual({ ok: false, code: 'bad_claims' });
  });

  it('PP tanpa kode ESB (hanya SMD) -> ditolak oleh T9 walau T1..T7 lolos', () => {
    const sm = { codes: ['SMD'], wids: [0, 20], cats: [] as number[], brs: [] as string[] };
    expect(scopeConsistentV3({ ...sm, roles: ['category', 'user'] })).toBe(true);
    expect(scopeConsistentV3({ ...sm, roles: ['category', 'pp_planner', 'user'] })).toBe(false);
  });

  it('tanpa peran PP aturan lama tidak berubah', () => {
    expect(scopeConsistentV3({ ...base, roles: ['exec', 'user'] })).toBe(true);
    expect(scopeConsistentV3({ ...base, roles: ['store', 'user'], brs: ['pb:7'] })).toBe(true);
  });

  it('round-trip: eit3 ber-PP -> cookie eis3 -> sesi memuat peran PP; eis3 dengan roles PP namun brs kosong sah', () => {
    const t = ok({ roles: ['pp_planner', 'pp_warehouse', 'user'], brs: [], codes: ['PB'] }, 3);
    expect(t.ok).toBe(true);
    if (!t.ok) return;
    const cookie = createSessionValue(t.payload, SESSION_KEY, NOW);
    const s = verifySessionValue(cookie, SESSION_KEY, NOW + 1);
    expect(s?.roles).toEqual(['pp_planner', 'pp_warehouse', 'user']);
    expect(s?.v).toBe(3);
    expect(ppCaps(s?.roles ?? [])).toEqual({ planner: true, warehouse: true });
  });
});

describe('ppAccessDenial (hanya kelayakan menu "Produksi" untuk sesi Odoo; bukan gerbang akses)', () => {
  const s = (roles: string[], over: Over = {}) => ({ v: 3, roles, codes: ['PB'], brs: [] as string[], ...over });

  it('flag mati -> pp-belum-aktif (didahulukan atas semua)', () => {
    expect(ppAccessDenial(s(PP), false)).toBe('pp-belum-aktif');
    expect(ppAccessDenial(s(['user']), false)).toBe('pp-belum-aktif');
  });
  it('sesi < eis3 -> buka-ulang; tanpa peran PP -> tidak-berwenang', () => {
    expect(ppAccessDenial(s(PP, { v: 2 }), true)).toBe('buka-ulang');
    expect(ppAccessDenial(s(['exec', 'user']), true)).toBe('tidak-berwenang');
    expect(ppAccessDenial(s(['user']), true)).toBe('tidak-berwenang');
  });
  it('PP tanpa kode ESB atau ber-cabang -> tidak-berwenang (pertahanan berlapis di luar T9)', () => {
    expect(ppAccessDenial(s(PP, { codes: ['SMD'] }), true)).toBe('tidak-berwenang');
    expect(ppAccessDenial(s(PP, { brs: ['pb:7'] }), true)).toBe('tidak-berwenang');
  });
  it('peran PP + flag aktif -> boleh', () => {
    expect(ppAccessDenial(s(PP), true)).toBeNull();
    expect(ppAccessDenial(s(['pp_warehouse', 'user']), true)).toBeNull();
  });
  it('pemisah jalur /produksi: /produksix dan /ppx bukan; halaman masuk bagian dari /produksi', () => {
    expect(isProduksiPath('/produksi')).toBe(true);
    expect(isProduksiPath('/produksi/kemasan')).toBe(true);
    expect(isProduksiPath('/produksi/abc?x=1'.split('?')[0])).toBe(true);
    expect(isProduksiPath(PP_LOGIN_PATH)).toBe(true);
    expect(PP_LOGIN_PATH).toBe('/produksi/masuk');
    expect(isProduksiPath('/produksix')).toBe(false);
    expect(isProduksiPath('/sales')).toBe(false);
    expect(isProduksiPath('/api/prodplan/login')).toBe(false);
  });
  it('chip peran', () => {
    expect(ppRoleLabel(['pp_planner', 'user'])).toBe('Planner CK');
    expect(ppRoleLabel(['pp_planner', 'pp_warehouse', 'user'])).toBe('Planner CK + Kepala gudang');
    expect(ppRoleLabel(['user'])).toBe('Produksi');
  });
});

describe('konfigurasi INSIGHT_PRODPLAN_ENABLED / PRODPLAN_URL / kunci', () => {
  it('aktif hanya string persis "true"; default mati', () => {
    expect(parseConfig({}).ppEnabled).toBe(false);
    expect(parseConfig({ INSIGHT_PRODPLAN_ENABLED: '1' }).ppEnabled).toBe(false);
    expect(parseConfig({ INSIGHT_PRODPLAN_ENABLED: 'TRUE' }).ppEnabled).toBe(false);
    expect(parseConfig({ INSIGHT_PRODPLAN_ENABLED: 'true' }).ppEnabled).toBe(true);
    expect(parseConfig({}).ppAssertKeyFile).toBe('/run/secrets/prodplan_assert_key');
  });
  it('PRODPLAN_URL selain http://127.0.0.1:7443 -> fitur nonaktif (fail closed)', () => {
    expect(parsePpUrl(undefined)).toBe('http://127.0.0.1:7443');
    expect(parsePpUrl('http://127.0.0.1:7443/')).toBe('http://127.0.0.1:7443');
    for (const bad of ['https://127.0.0.1:7443', 'http://127.0.0.1:7444', 'http://localhost:7443', 'http://prodplan:8443', 'http://127.0.0.1:7443/v1', 'http://u:p@127.0.0.1:7443', 'http://127.0.0.1:7443/?a=1', 'bukan url']) {
      expect(parsePpUrl(bad), bad).toBeNull();
      expect(parseConfig({ INSIGHT_PRODPLAN_ENABLED: 'true', PRODPLAN_URL: bad }).ppEnabled, bad).toBe(false);
    }
  });
  it('prodplan_assert_key wajib valid dan BERBEDA dari tiga kunci lain hanya bila modul aktif', () => {
    const K1 = 'a1'.repeat(32);
    const K2 = 'b2'.repeat(32);
    const K3 = 'c3'.repeat(32);
    const K4 = 'd4'.repeat(32);
    const files = (over: Record<string, string | null> = {}) => (p: string): string | null => {
      const m: Record<string, string | null> = {
        '/run/secrets/insight_sso_key': K1,
        '/run/secrets/insight_session_key': K2,
        '/run/secrets/insight_scope_key': K3,
        '/run/secrets/prodplan_assert_key': K4,
        ...over,
      };
      return p in m ? m[p] : null;
    };
    const on = parseConfig({ INSIGHT_PRODPLAN_ENABLED: 'true' });
    const off = parseConfig({});
    expect(collectKeyProblems(on, files(), true)).toEqual([]);
    expect(collectKeyProblems(on, files({ '/run/secrets/prodplan_assert_key': K1 }), true).join()).toContain('berbeda');
    expect(collectKeyProblems(on, files({ '/run/secrets/prodplan_assert_key': null }), true).join()).toContain('prodplan_assert_key');
    expect(collectKeyProblems(on, files({ '/run/secrets/prodplan_assert_key': 'abc' }), true).join()).toContain('prodplan_assert_key');
    // modul mati: kunci prodplan tidak diperiksa
    expect(collectKeyProblems(off, files({ '/run/secrets/prodplan_assert_key': null }), true)).toEqual([]);
  });
});

describe('navigasi "Produksi"', () => {
  const sess = (roles: string[], over: Over = {}) => ({ v: 3, codes: ['PB'], roles, brs: [] as string[], ...over });
  it('grup Produksi hanya untuk peran PP bila flag aktif', () => {
    const g = navGroups(sess(PP), false, false, true).find((x) => x.id === 'prod');
    expect(g?.title).toBe('Produksi');
    expect(g?.items.map((i) => i.href)).toEqual(['/produksi', '/produksi/kemasan']);
    expect(navGroups(sess(PP), false, false, false).some((x) => x.id === 'prod')).toBe(false);
    expect(navGroups(sess(['user']), false, false, true).some((x) => x.id === 'prod')).toBe(false);
    expect(navGroups(sess(['exec', 'user']), false, true, true).some((x) => x.id === 'prod')).toBe(false);
    expect(navGroups(sess(PP, { v: 2 }), false, false, true).some((x) => x.id === 'prod')).toBe(false);
    expect(navGroups(sess(PP, { brs: ['pb:7'] }), false, true, true).some((x) => x.id === 'prod')).toBe(false);
  });
  it('item aktif: Rencana untuk /produksi, /produksi/baru, /produksi/<id>; Kemasan hanya /produksi/kemasan', () => {
    expect(ppNavActive('/produksi', '/produksi')).toBe(true);
    expect(ppNavActive('/produksi', '/produksi/baru')).toBe(true);
    expect(ppNavActive('/produksi', '/produksi/0b0c')).toBe(true);
    expect(ppNavActive('/produksi', '/produksi/kemasan')).toBe(false);
    expect(ppNavActive('/produksi/kemasan', '/produksi/kemasan')).toBe(true);
    expect(ppNavActive('/produksi/kemasan', '/produksi')).toBe(false);
    expect(ppNavItems({ v: 3, codes: ['PB'], roles: PP }, true)).toHaveLength(2);
    expect(ppNavItems({ v: 3, codes: ['PB'], roles: PP }, false)).toHaveLength(0);
  });
});

// ---------------------------------------------------------------- proxy.ts

let dir = '';
const saved: Record<string, string | undefined> = {};
const ENV_KEYS = ['INSIGHT_ENABLED', 'INSIGHT_SESSION_KEY_FILE', 'APP_ENV', 'EFN_INSIGHT_ODOO_URL', 'INSIGHT_PRODPLAN_ENABLED'];

function resetRuntime(): void {
  delete (globalThis as unknown as { __insightRuntime?: unknown }).__insightRuntime;
}

beforeAll(() => {
  dir = mkdtempSync(join(tmpdir(), 'insight-pp-proxy-'));
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
  process.env.INSIGHT_SESSION_KEY_FILE = join(dir, 'session_key');
  process.env.APP_ENV = 'development';
  process.env.EFN_INSIGHT_ODOO_URL = 'https://erp.test:9443';
  delete process.env.INSIGHT_PRODPLAN_ENABLED;
  resetRuntime();
});

function cookieFor(roles: string[], over: Record<string, unknown> = {}, n = 50): string {
  const t = ok({ roles, brs: [], codes: ['PB'], ...over }, n);
  if (!t.ok) throw new Error(`token tidak sah: ${t.code}`);
  return createSessionValue(t.payload, SESSION_KEY, Math.floor(Date.now() / 1000));
}

const SID = 'P'.repeat(43);
const eis = (cookie: string): string => `${SESSION_COOKIE}=${cookie}`;
const epp = (sid: string): string => `__Host-epp=${sid}`;
const req = (path: string, ...cookies: string[]): NextRequest =>
  new NextRequest(`https://insight.test${path}`, { headers: cookies.length > 0 ? { cookie: cookies.join('; ') } : {} });
const rewriteOf = (res: Response): string | null => res.headers.get('x-middleware-rewrite');
const PP_PATHS = ['/produksi', '/produksi/baru', '/produksi/kemasan', '/produksi/0b0c0a0e-0000-4000-8000-000000000000'];

describe('proxy.ts: gerbang /produksi (sesi ESB __Host-epp, bukan sesi Odoo)', () => {
  it('flag mati -> rewrite /pp-belum-aktif, termasuk halaman masuk dan walau cookie sah', () => {
    expect(rewriteOf(proxy(req('/produksi', epp(SID))))).toContain('/pp-belum-aktif');
    expect(rewriteOf(proxy(req('/produksi/masuk')))).toContain('/pp-belum-aktif');
    expect(rewriteOf(proxy(req('/produksi', eis(cookieFor(PP)))))).toContain('/pp-belum-aktif');
  });

  it('flag aktif: /produksi/masuk terbuka tanpa sesi apa pun (Odoo maupun ESB)', () => {
    process.env.INSIGHT_PRODPLAN_ENABLED = 'true';
    resetRuntime();
    const res = proxy(req('/produksi/masuk'));
    expect(rewriteOf(res)).toBeNull();
    expect(res.status).toBe(200);
  });

  it('flag aktif + cookie __Host-epp berbentuk sah -> lolos semua sub-rute (tanpa sesi Odoo)', () => {
    process.env.INSIGHT_PRODPLAN_ENABLED = 'true';
    resetRuntime();
    for (const p of PP_PATHS) expect(rewriteOf(proxy(req(p, epp(SID)))), p).toBeNull();
  });

  it('flag aktif tanpa cookie __Host-epp atau bentuknya salah -> rewrite ke /produksi/masuk', () => {
    process.env.INSIGHT_PRODPLAN_ENABLED = 'true';
    resetRuntime();
    for (const p of PP_PATHS) {
      expect(rewriteOf(proxy(req(p))), p).toContain('/produksi/masuk');
      expect(rewriteOf(proxy(req(p, epp('pendek')))), p).toContain('/produksi/masuk');
      expect(rewriteOf(proxy(req(p, epp('a'.repeat(44))))), p).toContain('/produksi/masuk');
    }
  });

  it('sesi Odoo eis3 ber-peran PP TANPA __Host-epp tidak membuka /produksi', () => {
    process.env.INSIGHT_PRODPLAN_ENABLED = 'true';
    resetRuntime();
    for (const p of PP_PATHS) expect(rewriteOf(proxy(req(p, eis(cookieFor(PP))))), p).toContain('/produksi/masuk');
  });

  it('__Host-epp saja TIDAK membuka halaman Insight lain (/, /fnb/*, /sm/*, dashboard): tetap /sesi-berakhir', () => {
    process.env.INSIGHT_PRODPLAN_ENABLED = 'true';
    resetRuntime();
    for (const p of ['/', '/sales', '/fnb/exec', '/fnb/store', '/exec', '/store', '/forecast', '/yoy', '/glosarium']) {
      expect(rewriteOf(proxy(req(p, epp(SID)))), p).toContain('/sesi-berakhir');
    }
  });

  it('sesi Odoo sah + __Host-epp pada halaman lain: gerbang Odoo tidak berubah dan tidak bergantung pada cookie ESB', () => {
    process.env.INSIGHT_PRODPLAN_ENABLED = 'true';
    resetRuntime();
    const c = cookieFor(['user'], {}, 54);
    expect(rewriteOf(proxy(req('/sales', eis(c))))).toBeNull();
    expect(rewriteOf(proxy(req('/sales', eis(c), epp(SID))))).toBeNull();
    // sesi Odoo tanpa PP juga tidak membuka /produksi
    expect(rewriteOf(proxy(req('/produksi', eis(c))))).toContain('/produksi/masuk');
  });

  it('halaman lain tidak terpengaruh flag produksi', () => {
    expect(rewriteOf(proxy(req('/sales', eis(cookieFor(['user'], {}, 53)))))).toBeNull();
  });

  it('halaman informasi /pp-belum-aktif publik (tidak memerlukan sesi)', () => {
    const res = proxy(new NextRequest('https://insight.test/pp-belum-aktif'));
    expect(rewriteOf(res)).toBeNull();
    expect(res.status).toBe(200);
  });
});
