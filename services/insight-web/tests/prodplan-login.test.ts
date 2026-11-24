/**
 * Login/logout Produksi per pengguna ESB (docs/04 §30.17.2, §30.17.5, §30.17.15): urutan gerbang, atribut cookie persis,
 * pemetaan galat ke `?e=`, batas laju per IP, `n` acak per percobaan, asersi `ppa2` dengan sid kosong, dan password TIDAK
 * pernah bocor (log, redirect, header, badan respons). prodplan palsu lewat fetchFn.
 */
import { createHash, createHmac } from 'node:crypto';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { LOGIN_TIMEOUT_MS, clientBinding, handleExpired, handleLogin, handleLogout, type LoginDeps } from '@/lib/prodplan-login';
import {
  LOGIN_ERROR_MESSAGES,
  loginErrorFor,
  normalizeUsername,
  parseLoginError,
  parseSessionView,
  ppClearCookie,
  ppSetCookie,
  ppSidFromHeader,
  remainingLabel,
  validPassword,
} from '@/lib/prodplan-session';
import { RateLimiter } from '@/lib/rate-limit';

const KEY = 'ab'.repeat(32);
const SID = 'L'.repeat(43);
const NOW = 1_795_000_000;
const PASSWORD = 'Sup3r-Rahasia!#9';
const NONCE = 'N'.repeat(22);
/** Referensi independen SEC-234: 22 karakter pertama base64url(HMAC-SHA256(KEY ASCII, "ppc1|" + ip)). */
const refC = (ip: string, key = KEY): string => createHmac('sha256', Buffer.from(key, 'ascii')).update(`ppc1|${ip}`, 'ascii').digest('base64url').slice(0, 22);

type Call = { url: string; init: RequestInit };
let calls: Call[] = [];
let logs: string[] = [];

function fakePp(status: number, body: unknown): typeof fetch {
  return (async (url: string, init: RequestInit) => {
    calls.push({ url, init });
    return new Response(body === null ? null : JSON.stringify(body), { status });
  }) as unknown as typeof fetch;
}

const okBody = (over: Record<string, unknown> = {}) => ({
  sid: SID,
  expires_at: new Date((NOW + 3000) * 1000).toISOString(),
  session: { name: 'Rina Planner', esb_env: 'staging', esb_brand: 'pb', company_code: 'PB', roles: ['pp_planner'], doc_access: { insert: [], delete: [] }, branches: [], expires_at: NOW + 3000 },
  ...over,
});

function deps(fetchFn: typeof fetch, over: Partial<LoginDeps> = {}): LoginDeps {
  return {
    enabled: true,
    pp: { baseUrl: 'http://127.0.0.1:7443', key: KEY, fetchFn },
    limiter: new RateLimiter(10, 15 * 60_000),
    nowMs: () => NOW * 1000,
    nonce: () => NONCE,
    ...over,
  };
}

function loginReq(fields: Record<string, string>, headers: Record<string, string> = {}): Request {
  return new Request('https://insight.test/api/prodplan/login', {
    method: 'POST',
    headers: { 'content-type': 'application/x-www-form-urlencoded', 'sec-fetch-site': 'same-origin', 'x-forwarded-for': '10.0.0.5', ...headers },
    body: new URLSearchParams(fields).toString(),
  });
}

const creds = (over: Record<string, string> = {}) => ({ username: 'rina.planner', password: PASSWORD, ...over });

beforeEach(() => {
  calls = [];
  logs = [];
  for (const m of ['info', 'warn', 'error'] as const) vi.spyOn(console, m).mockImplementation((...a: unknown[]) => void logs.push(a.map(String).join(' ')));
});
afterEach(() => vi.restoreAllMocks());

const loc = (r: Response): string => r.headers.get('location') ?? '';
const allOf = async (r: Response): Promise<string> => `${JSON.stringify([...r.headers.entries()])}${await r.clone().text()}`;

describe('POST /api/prodplan/login: sukses', () => {
  it('303 /produksi + Set-Cookie atribut persis (__Host-, HttpOnly, Secure, SameSite=Strict, Path=/, Max-Age = sisa waktu)', async () => {
    const res = await handleLogin(loginReq(creds()), deps(fakePp(201, okBody())));
    expect(res.status).toBe(303);
    expect(loc(res)).toBe('/produksi');
    expect(res.headers.get('set-cookie')).toBe(`__Host-epp=${SID}; Max-Age=3000; Path=/; HttpOnly; Secure; SameSite=Strict`);
    expect(res.headers.get('cache-control')).toBe('no-store');
  });

  it('Max-Age dibatasi 3600 dan minimal 1; expires_at tak terbaca -> 3540; sudah lewat -> galat tanpa cookie', async () => {
    const far = await handleLogin(loginReq(creds()), deps(fakePp(201, okBody({ expires_at: NOW + 99_999 }))));
    expect(far.headers.get('set-cookie')).toContain('Max-Age=3600;');
    const unreadable = await handleLogin(loginReq(creds()), deps(fakePp(201, okBody({ expires_at: 'kapan-kapan', session: { ...okBody().session, expires_at: 'x' } }))));
    expect(unreadable.headers.get('set-cookie')).toContain('Max-Age=3540;');
    const past = await handleLogin(loginReq(creds()), deps(fakePp(201, okBody({ expires_at: NOW - 5 }))));
    expect(loc(past)).toBe('/produksi/masuk?e=unavailable');
    expect(past.headers.get('set-cookie')).toBeNull();
  });

  it('POST /v1/session: body tepat 3 kunci (username, password, n) berurutan; username dipangkas; password apa adanya', async () => {
    await handleLogin(loginReq(creds({ username: '  rina.planner  ' })), deps(fakePp(201, okBody())));
    expect(calls).toHaveLength(1);
    expect(calls[0].url).toBe('http://127.0.0.1:7443/v1/session');
    expect(calls[0].init.method).toBe('POST');
    expect(calls[0].init.redirect).toBe('error');
    expect(calls[0].init.body).toBe(JSON.stringify({ username: 'rina.planner', password: PASSWORD, n: NONCE, c: refC('10.0.0.5') }));
    expect(Object.keys(JSON.parse(String(calls[0].init.body)) as object)).toEqual(['username', 'password', 'n', 'c']);
  });

  it('asersi ppa2 login: sid kosong, m=POST, p=/v1/session, bh = hash body (yang memuat n, bukan hash password)', async () => {
    await handleLogin(loginReq(creds()), deps(fakePp(201, okBody())));
    const h = (calls[0].init.headers as Record<string, string>)['X-EFN-Actor'];
    const [prefix, b64, sig] = h.split('.');
    expect(prefix).toBe('ppa2');
    expect(sig).toBe(createHmac('sha256', Buffer.from(KEY, 'ascii')).update(`${prefix}.${b64}`).digest('base64url'));
    const p = JSON.parse(Buffer.from(b64, 'base64url').toString('utf8')) as Record<string, unknown>;
    expect(Object.keys(p)).toEqual(['aud', 'bh', 'exp', 'iat', 'jti', 'm', 'p', 'sid']);
    expect(p).toMatchObject({ aud: 'prodplan', m: 'POST', p: '/v1/session', sid: '', iat: NOW, exp: NOW + 60 });
    expect(p.bh).toBe(createHash('sha256').update(String(calls[0].init.body), 'utf8').digest('base64url'));
    expect(p.bh).not.toBe(createHash('sha256').update(PASSWORD, 'utf8').digest('base64url'));
    expect(h).not.toContain(PASSWORD);
    expect((calls[0].init.headers as Record<string, string>)['Content-Type']).toBe('application/json');
  });

  it('n acak dan berbeda tiap percobaan (bawaan newJti: 22 karakter base64url)', async () => {
    const d = deps(fakePp(201, okBody()), { nonce: undefined });
    await handleLogin(loginReq(creds()), d);
    await handleLogin(loginReq(creds()), d);
    const ns = calls.map((c) => (JSON.parse(String(c.init.body)) as { n: string }).n);
    expect(ns[0]).toMatch(/^[A-Za-z0-9_-]{22}$/);
    expect(ns[1]).toMatch(/^[A-Za-z0-9_-]{22}$/);
    expect(ns[0]).not.toBe(ns[1]);
    expect(calls[0].init.body).not.toBe(calls[1].init.body);
  });
});

describe('POST /api/prodplan/login: galat', () => {
  it.each([
    [401, 'invalid_credentials', 'invalid'],
    [429, 'login_locked', 'locked'],
    [403, 'no_pp_access', 'no_access'],
    [403, 'esb_company_not_allowed', 'no_access'],
    [502, 'esb_unavailable', 'unavailable'],
    [503, 'session_capacity', 'unavailable'],
    [503, 'disabled', 'unavailable'],
    [400, 'invalid_request', 'invalid'],
    [401, 'actor_invalid', 'unavailable'],
    [500, 'internal', 'unavailable'],
  ])('prodplan %i %s -> 303 /produksi/masuk?e=%s tanpa cookie, tanpa username/password di redirect', async (status, code, e) => {
    const res = await handleLogin(loginReq(creds()), deps(fakePp(status, { error: { code, message: PASSWORD } })));
    expect(res.status).toBe(303);
    expect(loc(res)).toBe(`/produksi/masuk?e=${e}`);
    expect(res.headers.get('set-cookie')).toBeNull();
    expect(res.headers.get('cache-control')).toBe('no-store');
    expect(await allOf(res)).not.toContain(PASSWORD);
    expect(loc(res)).not.toContain('rina');
  });

  it('prodplan tidak terjangkau / timeout -> e=unavailable', async () => {
    const down = (async () => Promise.reject(new TypeError('connect ECONNREFUSED'))) as unknown as typeof fetch;
    expect(loc(await handleLogin(loginReq(creds()), deps(down)))).toBe('/produksi/masuk?e=unavailable');
  });

  it('201 tanpa sid sah / tanpa sesi sah -> e=unavailable, tanpa cookie', async () => {
    for (const body of [okBody({ sid: 'pendek' }), okBody({ session: null }), {}, null]) {
      const res = await handleLogin(loginReq(creds()), deps(fakePp(201, body)));
      expect(loc(res)).toBe('/produksi/masuk?e=unavailable');
      expect(res.headers.get('set-cookie')).toBeNull();
    }
  });

  it('bentuk tidak sah -> e=invalid TANPA memanggil prodplan', async () => {
    const f = fakePp(201, okBody());
    for (const c of [
      creds({ username: '' }),
      creds({ username: 'a b' }),
      creds({ username: '<script>' }),
      creds({ username: 'x'.repeat(65) }),
      creds({ password: '' }),
      creds({ password: 'p'.repeat(129) }),
      { username: 'rina' },
      { password: PASSWORD },
    ]) {
      const res = await handleLogin(loginReq(c as Record<string, string>), deps(f));
      expect(loc(res), JSON.stringify(Object.keys(c))).toBe('/produksi/masuk?e=invalid');
    }
    expect(calls).toHaveLength(0);
  });

  it('password 128 karakter dan username 64 karakter diterima', async () => {
    const res = await handleLogin(loginReq(creds({ username: 'u'.repeat(64), password: 'p'.repeat(128) })), deps(fakePp(201, okBody())));
    expect(loc(res)).toBe('/produksi');
  });

  it('flag mati -> 303 /pp-belum-aktif tanpa memanggil prodplan', async () => {
    const res = await handleLogin(loginReq(creds()), deps(fakePp(201, okBody()), { enabled: false }));
    expect(loc(res)).toBe('/pp-belum-aktif');
    expect(calls).toHaveLength(0);
  });
});

describe('POST /api/prodplan/login: gerbang masukan', () => {
  it('origin asing / tanpa metadata -> 403 sebelum apa pun (batas laju tidak terpakai)', async () => {
    const d = deps(fakePp(201, okBody()), { limiter: new RateLimiter(1, 60_000) });
    for (const h of [{ 'sec-fetch-site': 'cross-site' }, { 'sec-fetch-site': 'same-site' }, { 'sec-fetch-site': '' }]) {
      expect((await handleLogin(loginReq(creds(), h), d)).status).toBe(403);
    }
    expect((await handleLogin(loginReq(creds()), d)).status).toBe(303);
    expect(calls).toHaveLength(1);
  });

  it('Content-Type bukan form dan badan > 4096 byte -> 400', async () => {
    const d = deps(fakePp(201, okBody()));
    const json = new Request('https://insight.test/api/prodplan/login', {
      method: 'POST',
      headers: { 'content-type': 'application/json', 'sec-fetch-site': 'same-origin' },
      body: JSON.stringify(creds()),
    });
    expect((await handleLogin(json, d)).status).toBe(400);
    expect((await handleLogin(loginReq(creds({ password: 'x'.repeat(5000) })), d)).status).toBe(400);
    expect(calls).toHaveLength(0);
  });

  it('batas 10 percobaan / 15 menit per IP: ke-11 -> e=rate tanpa memanggil prodplan; IP lain tidak terpengaruh', async () => {
    const d = deps(fakePp(401, { error: { code: 'invalid_credentials' } }));
    for (let i = 0; i < 10; i += 1) expect(loc(await handleLogin(loginReq(creds()), d))).toBe('/produksi/masuk?e=invalid');
    expect(calls).toHaveLength(10);
    const over = await handleLogin(loginReq(creds()), d);
    expect(loc(over)).toBe('/produksi/masuk?e=rate');
    expect(calls).toHaveLength(10);
    const other = await handleLogin(loginReq(creds(), { 'x-forwarded-for': '10.0.0.6' }), d);
    expect(loc(other)).toBe('/produksi/masuk?e=invalid');
    expect(calls).toHaveLength(11);
  });

  it('IP = entri TERKANAN X-Forwarded-For (kiri bisa dipalsukan klien)', async () => {
    const d = deps(fakePp(401, { error: { code: 'invalid_credentials' } }), { limiter: new RateLimiter(1, 60_000) });
    await handleLogin(loginReq(creds(), { 'x-forwarded-for': '1.1.1.1, 9.9.9.9' }), d);
    expect(loc(await handleLogin(loginReq(creds(), { 'x-forwarded-for': '2.2.2.2, 9.9.9.9' }), d))).toBe('/produksi/masuk?e=rate');
  });
});

describe('password tidak pernah bocor', () => {
  it('tidak ada di log, redirect, header, badan respons, maupun cookie pada sukses, galat, dan exception', async () => {
    const boom = (async () => Promise.reject(new Error(`gagal membaca ${PASSWORD}`))) as unknown as typeof fetch;
    const results: Response[] = [];
    results.push(await handleLogin(loginReq(creds()), deps(fakePp(201, okBody()))));
    results.push(await handleLogin(loginReq(creds()), deps(fakePp(401, { error: { code: 'invalid_credentials', message: PASSWORD } }))));
    results.push(await handleLogin(loginReq(creds()), deps(fakePp(500, `<html>${PASSWORD}</html>`))));
    results.push(await handleLogin(loginReq(creds()), deps(boom)));
    results.push(await handleLogin(loginReq(creds({ username: '<x>' })), deps(fakePp(201, okBody()))));
    results.push(await handleLogin(loginReq(creds(), { 'sec-fetch-site': 'cross-site' }), deps(fakePp(201, okBody()))));
    for (const r of results) expect(await allOf(r)).not.toContain(PASSWORD);
    expect(logs.join('\n')).not.toContain(PASSWORD);
    expect(logs.join('\n')).not.toContain('rina.planner');
    expect(logs.join('\n')).not.toContain(SID);
    expect(logs.join('\n')).not.toContain(NONCE);
    expect(logs.length).toBeGreaterThan(0);
  });
});

describe('POST /api/prodplan/logout', () => {
  const logoutReq = (headers: Record<string, string> = {}): Request =>
    new Request('https://insight.test/api/prodplan/logout', { method: 'POST', headers: { 'sec-fetch-site': 'same-origin', cookie: `__Host-epp=${SID}`, ...headers } });
  const base = { enabled: true, pp: { baseUrl: 'http://127.0.0.1:7443', key: KEY } };

  it('memanggil DELETE /v1/session dengan ppa2 sid, menghapus cookie, 303 /produksi/masuk', async () => {
    const res = await handleLogout(logoutReq(), { ...base, pp: { ...base.pp, fetchFn: fakePp(204, null) } });
    expect(res.status).toBe(303);
    expect(loc(res)).toBe('/produksi/masuk');
    expect(res.headers.get('set-cookie')).toBe('__Host-epp=; Max-Age=0; Path=/; HttpOnly; Secure; SameSite=Strict');
    expect(calls).toHaveLength(1);
    expect(calls[0].url).toBe('http://127.0.0.1:7443/v1/session');
    expect(calls[0].init.method).toBe('DELETE');
    const p = JSON.parse(Buffer.from((calls[0].init.headers as Record<string, string>)['X-EFN-Actor'].split('.')[1], 'base64url').toString('utf8')) as Record<string, unknown>;
    expect(p).toMatchObject({ m: 'DELETE', p: '/v1/session', sid: SID });
  });

  it('prodplan gagal (5xx / tidak terjangkau) -> cookie tetap dihapus', async () => {
    const down = (async () => Promise.reject(new TypeError('ECONNREFUSED'))) as unknown as typeof fetch;
    for (const f of [down, fakePp(500, { error: { code: 'internal' } })]) {
      const res = await handleLogout(logoutReq(), { ...base, pp: { ...base.pp, fetchFn: f } });
      expect(loc(res)).toBe('/produksi/masuk');
      expect(res.headers.get('set-cookie')).toContain('Max-Age=0');
    }
  });

  it('tanpa cookie / cookie salah bentuk: tanpa panggilan prodplan, cookie tetap dihapus; origin asing -> 403', async () => {
    const f = fakePp(204, null);
    const d = { ...base, pp: { ...base.pp, fetchFn: f } };
    const none = await handleLogout(new Request('https://insight.test/api/prodplan/logout', { method: 'POST', headers: { 'sec-fetch-site': 'same-origin' } }), d);
    expect(none.headers.get('set-cookie')).toContain('Max-Age=0');
    await handleLogout(logoutReq({ cookie: '__Host-epp=pendek' }), d);
    expect(calls).toHaveLength(0);
    const foreign = await handleLogout(logoutReq({ 'sec-fetch-site': 'cross-site' }), d);
    expect(foreign.status).toBe(403);
    expect(foreign.headers.get('set-cookie')).toBeNull();
    expect(calls).toHaveLength(0);
  });
});

describe('SEC-234: pengikat IP `c`', () => {
  it('vektor tetap: KEY ab*32, ip 203.0.113.7 -> 22 karakter base64url, sama dengan referensi independen', () => {
    const c = clientBinding(KEY, '203.0.113.7');
    expect(c).toMatch(/^[A-Za-z0-9_-]{22}$/);
    expect(c).toBe(refC('203.0.113.7'));
    // BUKAN kunci hasil hex-decode, dan IP lain -> nilai lain
    expect(c).not.toBe(createHmac('sha256', Buffer.from(KEY, 'hex')).update('ppc1|203.0.113.7').digest('base64url').slice(0, 22));
    expect(clientBinding(KEY, '203.0.113.8')).not.toBe(c);
  });

  it('login memakai IP dari helper batas laju (entri TERKANAN XFF), tidak di-log, tidak ada di respons', async () => {
    const res = await handleLogin(loginReq(creds(), { 'x-forwarded-for': '1.2.3.4, 203.0.113.7' }), deps(fakePp(201, okBody())));
    const body = JSON.parse(String(calls[0].init.body)) as { c: string };
    expect(body.c).toBe(refC('203.0.113.7'));
    expect(logs.join('\n')).not.toContain(body.c);
    expect(await allOf(res)).not.toContain(body.c);
  });
});

describe('SEC-236: timeout login dan /expired', () => {
  it('login memakai 65 dtk bawaan; timeout -> e=unavailable', async () => {
    expect(LOGIN_TIMEOUT_MS).toBe(65_000);
    const hang = ((_u: string, init: RequestInit) =>
      new Promise<Response>((_r, rej) => init.signal?.addEventListener('abort', () => rej(Object.assign(new Error('a'), { name: 'AbortError' }))))) as unknown as typeof fetch;
    const d = deps(hang);
    d.pp.timeoutMs = 20;
    expect(loc(await handleLogin(loginReq(creds()), d))).toBe('/produksi/masuk?e=unavailable');
  });

  const expReq = (cookie?: string): Request => new Request('https://insight.test/api/prodplan/expired', { headers: cookie ? { cookie } : {} });
  const base = { enabled: true, pp: { baseUrl: 'http://127.0.0.1:7443', key: KEY } };

  it('cookie sah -> DELETE /v1/session best-effort lalu hapus cookie + 303 ?e=expired', async () => {
    const res = await handleExpired(expReq(`__Host-epp=${SID}`), { ...base, pp: { ...base.pp, fetchFn: fakePp(204, null) } });
    expect(loc(res)).toBe('/produksi/masuk?e=expired');
    expect(res.headers.get('set-cookie')).toContain('Max-Age=0');
    expect(calls).toHaveLength(1);
    expect(calls[0].init.method).toBe('DELETE');
  });

  it('prodplan gagal -> tetap hapus cookie; cookie tak sah/tanpa cookie -> tanpa panggilan', async () => {
    const down = (async () => Promise.reject(new TypeError('x'))) as unknown as typeof fetch;
    const r = await handleExpired(expReq(`__Host-epp=${SID}`), { ...base, pp: { ...base.pp, fetchFn: down } });
    expect(r.headers.get('set-cookie')).toContain('Max-Age=0');
    calls = [];
    const f = fakePp(204, null);
    await handleExpired(expReq('__Host-epp=pendek'), { ...base, pp: { ...base.pp, fetchFn: f } });
    await handleExpired(expReq(), { ...base, pp: { ...base.pp, fetchFn: f } });
    expect(calls).toHaveLength(0);
  });
});

describe('lib/prodplan-session: cookie, galat, parser', () => {
  it('cookie set/clear persis; Max-Age dijepit 1..3600', () => {
    expect(ppSetCookie(SID, 120)).toBe(`__Host-epp=${SID}; Max-Age=120; Path=/; HttpOnly; Secure; SameSite=Strict`);
    expect(ppSetCookie(SID, 99_999)).toContain('Max-Age=3600');
    expect(ppSetCookie(SID, 0)).toContain('Max-Age=1');
    expect(ppClearCookie()).toBe('__Host-epp=; Max-Age=0; Path=/; HttpOnly; Secure; SameSite=Strict');
  });

  it('ppSidFromHeader: hanya __Host-epp berbentuk 43 karakter base64url; cookie Odoo tidak dibaca', () => {
    expect(ppSidFromHeader(`a=b; __Host-epp=${SID}; c=d`)).toBe(SID);
    expect(ppSidFromHeader('__Host-epp=pendek')).toBeUndefined();
    expect(ppSidFromHeader(`__Host-eis=${SID}`)).toBeUndefined();
    expect(ppSidFromHeader(`x__Host-epp=${SID}`)).toBeUndefined();
    expect(ppSidFromHeader(null)).toBeUndefined();
  });

  it('pesan galat login = tabel kontrak §30.17.5 (tidak membedakan user tidak ada vs password salah)', () => {
    expect(LOGIN_ERROR_MESSAGES.invalid).toBe('Username atau password ESB salah.');
    expect(LOGIN_ERROR_MESSAGES.locked).toBe('Terlalu banyak percobaan masuk. Coba lagi dalam 15 menit.');
    expect(LOGIN_ERROR_MESSAGES.rate).toBe(LOGIN_ERROR_MESSAGES.locked);
    expect(LOGIN_ERROR_MESSAGES.no_access).toBe('Akun ESB ini tidak memiliki izin modul Produksi.');
    expect(LOGIN_ERROR_MESSAGES.unavailable).toBe('ESB sedang tidak dapat dihubungi. Coba beberapa saat lagi.');
    expect(LOGIN_ERROR_MESSAGES.expired).toBe('Sesi ESB berakhir (maksimal 1 jam). Silakan masuk lagi.');
  });

  it('?e= : hanya nilai kontrak; lainnya diabaikan; loginErrorFor memetakan kode prodplan', () => {
    for (const e of ['invalid', 'locked', 'no_access', 'unavailable', 'rate', 'expired']) expect(parseLoginError(e)).toBe(e);
    for (const e of ['x', '', 'INVALID', '__proto__', undefined]) expect(parseLoginError(e)).toBeNull();
    expect(loginErrorFor('esb_company_not_allowed', 403)).toBe('no_access');
    expect(loginErrorFor(undefined, 429)).toBe('locked');
    expect(loginErrorFor('zzz', 500)).toBe('unavailable');
  });

  it('validator bentuk', () => {
    expect(normalizeUsername(' a.b_c@d-1 ')).toBe('a.b_c@d-1');
    expect(normalizeUsername('a b')).toBeNull();
    expect(normalizeUsername(5)).toBeNull();
    expect(validPassword(' x ')).toBe(true);
    expect(validPassword('')).toBe(false);
    expect(validPassword(null)).toBe(false);
  });

  it('parseSessionView: nama, peran PP saja, cabang, sisa waktu; tanpa username/token; actor_id opsional', () => {
    const v = parseSessionView({ name: 'Rina', esb_env: 'staging', esb_brand: 'pb', company_code: 'PB', roles: ['pp_planner', 'admin', 'user'], doc_access: { insert: ['simple_transfer'], delete: [] }, branches: [{ branch_id: 4, name: 'CK' }, { branch_id: 'x' }], expires_at: NOW + 60, actor_id: 1000001, username: 'rina', access_token: 'jwt' });
    expect(v).toEqual({
      name: 'Rina',
      esb_env: 'staging',
      esb_brand: 'pb',
      company_code: 'PB',
      roles: ['pp_planner'],
      doc_access: { insert: ['simple_transfer'], delete: [] },
      branches: [{ branch_id: 4, name: 'CK' }],
      expires_at_sec: NOW + 60,
      actor_id: 1000001,
    });
    expect(JSON.stringify(v)).not.toMatch(/rina"|jwt|username|access_token/);
    expect(parseSessionView({})).toBeNull();
    expect(parseSessionView(null)).toBeNull();
    expect(parseSessionView({ name: 'A' })?.actor_id).toBeNull();
  });

  it('remainingLabel', () => {
    expect(remainingLabel(NOW + 2520, NOW)).toBe('42 menit');
    expect(remainingLabel(NOW + 30, NOW)).toBe('kurang dari 1 menit');
    expect(remainingLabel(NOW, NOW)).toBe('berakhir');
    expect(remainingLabel(null, NOW)).toBe('-');
  });
});
