/**
 * Cookie sesi `eis1` (docs/04 §26.5) dan POST /logout. Melengkapi sso-handler.test.ts (yang hanya menguji sisi
 * "sesi dibuat dari /sso"): bentuk byte-exact payload, penolakan cookie palsu/aneh, pemisahan domain SSO vs sesi,
 * nama cookie `__Host-eis` (C2), dan logout. Vektor token = docs/04 §26.3.
 */
import { describe, expect, it } from 'vitest';
import { POST as logout } from '@/app/logout/route';
import { hmacSha256B64Url } from '@/lib/hmac';
import { isSameOriginRequest } from '@/lib/origin-guard';
import {
  SESSION_COOKIE,
  SESSION_TTL_SEC,
  clearCookieHeader,
  createSessionValue,
  readCookie,
  setCookieHeader,
  verifySessionValue,
} from '@/lib/session';
import type { SsoPayload } from '@/lib/token';
import { BASE_PAYLOAD, IAT, KEY, VECTOR_TOKEN } from './helpers';

const SKEY = 'cd'.repeat(32);
const NOW = IAT + 10;
const PAYLOAD = BASE_PAYLOAD as unknown as SsoPayload;

/** Tanda tangani payload sesi arbitrer (untuk membuat cookie yang sengaja salah bentuk). */
function forge(payload: Record<string, unknown>, key = SKEY, prefix = 'eis1'): string {
  const b64 = Buffer.from(JSON.stringify(payload), 'utf8').toString('base64url');
  return `eis1.${b64}.${hmacSha256B64Url(key, `${prefix}.${b64}`)}`;
}
const GOOD = { cids: [3, 5], codes: ['BCF', 'PB'], exp: NOW + 100, iat: NOW, name: 'Budi Uji', roles: ['user'], sub: 7 };

describe('cookie sesi eis1', () => {
  it('nama __Host-eis (C2) dan atribut sesuai kontrak; tanpa Domain', () => {
    expect(SESSION_COOKIE).toBe('__Host-eis');
    expect(SESSION_TTL_SEC).toBe(7200);
    const set = setCookieHeader('v');
    expect(set).toBe('__Host-eis=v; Max-Age=7200; Path=/; HttpOnly; Secure; SameSite=Lax');
    expect(clearCookieHeader()).toBe('__Host-eis=; Max-Age=0; Path=/; HttpOnly; Secure; SameSite=Lax');
    expect(set).not.toMatch(/Domain/i);
  });

  it('payload byte-exact: tujuh kunci urut abjad, UTF-8 (bukan \\u-escape), sig = HMAC("eis1." + payload_b64)', () => {
    const v = createSessionValue({ ...PAYLOAD, name: 'Siti Ünal' }, SKEY, NOW);
    const [prefix, b64, sig] = v.split('.');
    expect(prefix).toBe('eis1');
    expect(Buffer.from(b64, 'base64url').toString('utf8')).toBe(
      `{"cids":[3,5],"codes":["BCF","PB"],"exp":${NOW + 7200},"iat":${NOW},"name":"Siti Ünal","roles":["user"],"sub":7}`,
    );
    expect(sig).toBe(hmacSha256B64Url(SKEY, `eis1.${b64}`));
    expect(sig).toHaveLength(43);
    expect(b64).not.toContain('=');
    // roundtrip
    expect(verifySessionValue(v, SKEY, NOW + 5)).toMatchObject({ sub: 7, name: 'Siti Ünal', roles: ['user'] });
  });

  it('hanya memuat klaim dari token: tidak ada jti/aud/iss/v di cookie', () => {
    const [, b64] = createSessionValue(PAYLOAD, SKEY, NOW).split('.');
    const keys = Object.keys(JSON.parse(Buffer.from(b64, 'base64url').toString('utf8')) as object);
    expect(keys).toEqual(['cids', 'codes', 'exp', 'iat', 'name', 'roles', 'sub']);
  });

  it('cookie sah diterima (dasar pembanding untuk kasus negatif)', () => {
    expect(verifySessionValue(forge(GOOD), SKEY, NOW + 1)).not.toBeNull();
  });

  it('kunci ekstra / kurang ditolak walau tanda tangan sah', () => {
    expect(verifySessionValue(forge({ ...GOOD, jti: '0'.repeat(32) }), SKEY, NOW + 1)).toBeNull();
    const { name: _name, ...noName } = GOOD;
    void _name;
    expect(verifySessionValue(forge(noName), SKEY, NOW + 1)).toBeNull();
  });

  it('waktu: exp - iat > 7200, iat > now + 30, sudah kedaluwarsa, exp = iat ditolak', () => {
    expect(verifySessionValue(forge({ ...GOOD, exp: NOW + 28801 }), SKEY, NOW + 1)).toBeNull();
    expect(verifySessionValue(forge({ ...GOOD, iat: NOW + 40, exp: NOW + 100 }), SKEY, NOW)).toBeNull();
    expect(verifySessionValue(forge(GOOD), SKEY, NOW + 100)).toBeNull(); // now >= exp
    expect(verifySessionValue(forge({ ...GOOD, exp: NOW }), SKEY, NOW - 1)).toBeNull(); // umur 0
    // toleransi skew 30 dtk: iat = now + 30 masih sah
    expect(verifySessionValue(forge({ ...GOOD, iat: NOW + 30, exp: NOW + 100 }), SKEY, NOW)).not.toBeNull();
  });

  it('klaim: codes asing / tak urut, cids nol, roles user tanpa cids, sub 0, name > 64 ditolak', () => {
    for (const bad of [
      { codes: ['GL'] },
      { codes: ['PB', 'BCF'] },
      { codes: [] },
      { cids: [0] },
      { cids: [5, 3] },
      { cids: [], roles: ['user'] },
      { roles: ['admin'] },
      { sub: 0 },
      { name: 'x'.repeat(65) },
    ]) {
      expect(verifySessionValue(forge({ ...GOOD, ...bad }), SKEY, NOW + 1), JSON.stringify(bad)).toBeNull();
    }
    // all_brands tanpa company (IMD/CUP belum jadi company, L-120) sah
    expect(verifySessionValue(forge({ ...GOOD, roles: ['all_brands'], cids: [] }), SKEY, NOW + 1)).not.toBeNull();
  });

  it('pemisahan domain: tanda tangan berawalan "eit1." atau kunci SSO tidak berlaku sebagai cookie', () => {
    expect(verifySessionValue(forge(GOOD, SKEY, 'eit1'), SKEY, NOW + 1)).toBeNull();
    expect(verifySessionValue(forge(GOOD, KEY), SKEY, NOW + 1)).toBeNull();
    // token SSO utuh bukan cookie (awalan eit1 tidak lolos regex sesi)
    expect(verifySessionValue(VECTOR_TOKEN, SKEY, IAT)).toBeNull();
    expect(verifySessionValue(VECTOR_TOKEN, KEY, IAT)).toBeNull();
  });

  it('format: bukan string / kosong / tanpa tiga bagian / sig 42 atau 44 karakter ditolak', () => {
    const good = forge(GOOD);
    const [h, p, s] = good.split('.');
    for (const bad of [undefined, null, 42, '', 'eis1', `${h}.${p}`, `${h}.${p}.${s.slice(1)}`, `${h}.${p}.${s}A`, `${good}.x`]) {
      expect(verifySessionValue(bad, SKEY, NOW + 1)).toBeNull();
    }
  });
});

describe('readCookie', () => {
  it('memilih nama persis; "eis" polos dan nama berawalan lain tidak dianggap __Host-eis (cookie tossing)', () => {
    expect(readCookie('a=1; __Host-eis=abc; b=2', SESSION_COOKIE)).toBe('abc');
    expect(readCookie('eis=attacker', SESSION_COOKIE)).toBeUndefined();
    expect(readCookie('x__Host-eis=attacker', SESSION_COOKIE)).toBeUndefined();
    expect(readCookie('__Host-eis2=attacker', SESSION_COOKIE)).toBeUndefined();
    expect(readCookie(null, SESSION_COOKIE)).toBeUndefined();
    expect(readCookie('', SESSION_COOKIE)).toBeUndefined();
    expect(readCookie('tanpa-sama-dengan', SESSION_COOKIE)).toBeUndefined();
  });
});

describe('POST /logout', () => {
  const req = (headers: Record<string, string> = {}) =>
    new Request('https://insight.test/logout', { method: 'POST', headers });

  const withOdooUrl = async (value: string | undefined, fn: () => Promise<void>) => {
    const rt = globalThis as unknown as { __insightRuntime?: unknown };
    const saved = process.env.EFN_INSIGHT_ODOO_URL;
    if (value === undefined) delete process.env.EFN_INSIGHT_ODOO_URL;
    else process.env.EFN_INSIGHT_ODOO_URL = value;
    delete rt.__insightRuntime;
    try {
      await fn();
    } finally {
      if (saved === undefined) delete process.env.EFN_INSIGHT_ODOO_URL;
      else process.env.EFN_INSIGHT_ODOO_URL = saved;
      delete rt.__insightRuntime;
    }
  };

  it('menghapus cookie dan mengalihkan ke beranda Odoo (tanpa Sec-Fetch-Site, same-origin, none)', async () => {
    await withOdooUrl('https://erp.efn.test/web?x=1', async () => {
      const cases: Record<string, string>[] = [{}, { 'sec-fetch-site': 'same-origin' }, { 'sec-fetch-site': 'none' }];
      for (const h of cases) {
        const res = await logout(req(h));
        expect(res.status).toBe(303);
        expect(res.headers.get('location')).toBe('https://erp.efn.test/odoo');
        expect(res.headers.get('set-cookie')).toBe(clearCookieHeader());
        expect(res.headers.get('cache-control')).toBe('no-store');
      }
    });
  });

  it('fallback ke /keluar bila EFN_INSIGHT_ODOO_URL belum diisi', async () => {
    await withOdooUrl(undefined, async () => {
      const res = await logout(req());
      expect(res.status).toBe(303);
      expect(res.headers.get('location')).toBe('/keluar');
      expect(res.headers.get('set-cookie')).toBe(clearCookieHeader());
    });
  });

  it('lintas situs / lintas origin ditolak 403 tanpa menghapus cookie (logout CSRF)', async () => {
    for (const site of ['cross-site', 'same-site']) {
      const res = await logout(req({ 'sec-fetch-site': site }));
      expect(res.status).toBe(403);
      expect(res.headers.get('set-cookie')).toBeNull();
    }
  });

  it('isSameOriginRequest', () => {
    expect(isSameOriginRequest(undefined)).toBe(true);
    expect(isSameOriginRequest('same-origin')).toBe(true);
    expect(isSameOriginRequest('cross-site')).toBe(false);
    expect(isSameOriginRequest('same-site')).toBe(false);
  });
});
