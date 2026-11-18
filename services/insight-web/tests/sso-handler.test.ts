import { describe, expect, it } from 'vitest';
import { JtiCache } from '@/lib/jti-cache';
import { RateLimiter } from '@/lib/rate-limit';
import { SESSION_COOKIE, verifySessionValue } from '@/lib/session';
import { handleSso, type SsoDeps } from '@/lib/sso-handler';
import { IAT, KEY, makeToken, uniqueJti, VECTOR_TOKEN } from './helpers';

const SESSION_KEY = 'cd'.repeat(32);
const ODOO = 'https://erp.test:9443';
const NOW_MS = (IAT + 10) * 1000;

function deps(over: Partial<SsoDeps> = {}): SsoDeps {
  return {
    enabled: true,
    odooOrigin: ODOO,
    odooOpenUrl: `${ODOO}/efn/insight/open`,
    ssoKey: KEY,
    sessionKey: SESSION_KEY,
    nowMs: () => NOW_MS,
    jti: new JtiCache(),
    limiter: new RateLimiter(10, 60_000),
    ...over,
  };
}

function post(token: string, headers: Record<string, string> = {}, body?: string): Request {
  return new Request('https://insight.test/sso', {
    method: 'POST',
    headers: { 'content-type': 'application/x-www-form-urlencoded', origin: ODOO, ...headers },
    body: body ?? new URLSearchParams({ t: token }).toString(),
  });
}

describe('POST /sso', () => {
  it('sukses: 303 ke /, cookie __Host-eis valid, tanpa token di respons', async () => {
    const res = await handleSso(post(VECTOR_TOKEN), deps());
    expect(res.status).toBe(303);
    expect(res.headers.get('location')).toBe('/');
    expect(res.headers.get('cache-control')).toBe('no-store');
    const cookie = res.headers.get('set-cookie') ?? '';
    expect(cookie.startsWith(`${SESSION_COOKIE}=eis1.`)).toBe(true);
    for (const attr of ['Max-Age=28800', 'Path=/', 'HttpOnly', 'Secure', 'SameSite=Lax']) expect(cookie).toContain(attr);
    expect(cookie).not.toContain('Domain');
    expect(cookie).not.toContain(VECTOR_TOKEN);
    const value = cookie.split(';')[0].slice(`${SESSION_COOKIE}=`.length);
    const s = verifySessionValue(value, SESSION_KEY, IAT + 10);
    expect(s).toMatchObject({ sub: 7, name: 'Budi Uji', codes: ['BCF', 'PB'], cids: [3, 5], roles: ['user'] });
    expect(s?.exp).toBe(IAT + 10 + 28800);
    // cookie TIDAK valid di bawah kunci SSO (kunci terpisah)
    expect(verifySessionValue(value, KEY, IAT + 10)).toBeNull();
  });

  it('jti ulang (token dipakai dua kali) -> 401 generik', async () => {
    const d = deps();
    expect((await handleSso(post(VECTOR_TOKEN), d)).status).toBe(303);
    const again = await handleSso(post(VECTOR_TOKEN), d);
    expect(again.status).toBe(401);
    expect(again.headers.get('set-cookie')).toBeNull();
    const html = await again.text();
    expect(html).toContain('Tautan masuk tidak valid atau sudah dipakai');
    expect(html).toContain(`${ODOO}/efn/insight/open`);
    expect(html).not.toContain('replay');
  });

  it('jti kedua kali setelah TTL 120 dtk boleh lagi hanya bila token belum kedaluwarsa (token kedaluwarsa ditolak)', async () => {
    const d = deps({ nowMs: () => (IAT + 70) * 1000 });
    expect((await handleSso(post(VECTOR_TOKEN), d)).status).toBe(401);
  });

  it('Origin salah -> 401 (C1)', async () => {
    const res = await handleSso(post(VECTOR_TOKEN, { origin: 'https://evil.test' }), deps());
    expect(res.status).toBe(401);
    expect(res.headers.get('set-cookie')).toBeNull();
  });

  it('Origin sama host tetapi PORT beda -> 401 (origin lengkap scheme+host+port)', async () => {
    expect((await handleSso(post(VECTOR_TOKEN, { origin: 'https://erp.test' }), deps())).status).toBe(401);
    expect((await handleSso(post(VECTOR_TOKEN, { origin: 'http://erp.test:9443' }), deps())).status).toBe(401);
  });

  it('Origin "null" (mis. Referrer-Policy no-referrer di halaman Odoo) -> 401', async () => {
    expect((await handleSso(post(VECTOR_TOKEN, { origin: 'null' }), deps())).status).toBe(401);
  });

  it('tanpa Origin: selalu ditolak (juga tanpa Sec-Fetch-Site)', async () => {
    const mk = (extra: Record<string, string>) =>
      new Request('https://insight.test/sso', {
        method: 'POST',
        headers: { 'content-type': 'application/x-www-form-urlencoded', ...extra },
        body: new URLSearchParams({ t: VECTOR_TOKEN }).toString(),
      });
    expect((await handleSso(mk({}), deps())).status).toBe(401);
    expect((await handleSso(mk({ 'sec-fetch-site': 'cross-site' }), deps({ jti: new JtiCache() }))).status).toBe(401);
  });

  it('INSIGHT_ENABLED != true -> 404', async () => {
    expect((await handleSso(post(VECTOR_TOKEN), deps({ enabled: false }))).status).toBe(404);
  });

  it('kunci/origin Odoo belum terkonfigurasi -> 503', async () => {
    expect((await handleSso(post(VECTOR_TOKEN), deps({ ssoKey: null }))).status).toBe(503);
    expect((await handleSso(post(VECTOR_TOKEN), deps({ odooOrigin: null }))).status).toBe(503);
  });

  it('token kunci ekstra / aud salah / codes asing -> 401 tanpa cookie', async () => {
    for (const t of [makeToken({ x: 1 }), makeToken({ aud: 'lain' }), makeToken({ codes: ['XX'] })]) {
      const res = await handleSso(post(t), deps());
      expect(res.status).toBe(401);
      expect(res.headers.get('set-cookie')).toBeNull();
    }
  });

  it('content type salah / field ekstra / badan > 4 KiB -> 400', async () => {
    const wrongType = new Request('https://insight.test/sso', { method: 'POST', headers: { 'content-type': 'application/json', origin: ODOO }, body: '{}' });
    expect((await handleSso(wrongType, deps())).status).toBe(400);
    expect((await handleSso(post('', {}, `t=${VECTOR_TOKEN}&x=1`), deps())).status).toBe(400);
    expect((await handleSso(post('', {}, `t=${'a'.repeat(5000)}`), deps())).status).toBe(400);
  });

  it('rate limit: permintaan ke-11 dalam semenit dari IP yang sama -> 429 + Retry-After', async () => {
    const d = deps();
    let last = 0;
    for (let i = 0; i < 11; i += 1) {
      const res = await handleSso(post(makeToken({ jti: uniqueJti(i + 1) }), { 'x-forwarded-for': '10.0.0.9' }), d);
      last = res.status;
      if (i < 10) expect(res.status).toBe(303);
      else expect(res.headers.get('retry-after')).not.toBeNull();
    }
    expect(last).toBe(429);
    // IP lain tidak terdampak
    expect((await handleSso(post(makeToken({ jti: uniqueJti(99) }), { 'x-forwarded-for': '10.0.0.10' }), d)).status).toBe(303);
  });
});

describe('rate limit dihitung SETELAH Origin lolos (QA risiko 1)', () => {
  it('permintaan Origin salah / tanpa Origin tidak menghabiskan jatah login sah dari IP yang sama', async () => {
    const limiter = new RateLimiter(2, 60_000);
    const d = deps({ limiter });
    const ip = { 'x-forwarded-for': '10.1.1.1' };
    for (let i = 0; i < 5; i += 1) {
      expect((await handleSso(post(VECTOR_TOKEN, { ...ip, origin: 'https://evil.test' }), d)).status).toBe(401);
    }
    const noOrigin = new Request('https://insight.test/sso', {
      method: 'POST',
      headers: { 'content-type': 'application/x-www-form-urlencoded', ...ip },
      body: new URLSearchParams({ t: VECTOR_TOKEN }).toString(),
    });
    for (let i = 0; i < 5; i += 1) expect((await handleSso(noOrigin.clone(), d)).status).toBe(401);
    // jatah (2) masih utuh: dua login sah lolos, yang ketiga baru 429
    expect((await handleSso(post(makeToken({ jti: uniqueJti(1) }), ip), d)).status).toBe(303);
    expect((await handleSso(post(makeToken({ jti: uniqueJti(2) }), ip), d)).status).toBe(303);
    expect((await handleSso(post(makeToken({ jti: uniqueJti(3) }), ip), d)).status).toBe(429);
  });
});

describe('cookie sesi palsu', () => {
  it('cookie dengan sig kunci lain / payload diubah / kedaluwarsa ditolak', async () => {
    const res = await handleSso(post(VECTOR_TOKEN), deps());
    const value = (res.headers.get('set-cookie') ?? '').split(';')[0].slice(`${SESSION_COOKIE}=`.length);
    expect(verifySessionValue(value, SESSION_KEY, IAT + 10)).not.toBeNull();
    expect(verifySessionValue(value, 'ef'.repeat(32), IAT + 10)).toBeNull();
    const [h, p, s] = value.split('.');
    const altered = Buffer.from(Buffer.from(p, 'base64url').toString('utf8').replace('"sub":7', '"sub":9'), 'utf8').toString('base64url');
    expect(verifySessionValue(`${h}.${altered}.${s}`, SESSION_KEY, IAT + 10)).toBeNull();
    expect(verifySessionValue(value, SESSION_KEY, IAT + 10 + 28800)).toBeNull();
    expect(verifySessionValue('eis1.aaaaaaaaaaaaaaaa.' + 'a'.repeat(43), SESSION_KEY, IAT)).toBeNull();
    expect(verifySessionValue(undefined, SESSION_KEY, IAT)).toBeNull();
  });
});
