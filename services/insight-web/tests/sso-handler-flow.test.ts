/**
 * Alur POST /sso (docs/04 §26.4) -- kasus yang belum dicakup sso-handler.test.ts: token yang ditolak tidak boleh
 * "membakar" jti, Origin salah ditolak SEBELUM token dikonsumsi, nama non-ASCII bertahan sampai cookie, header respons
 * galat, bentuk body, dan urutan pemeriksaan.
 */
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

function post(body: string, headers: Record<string, string> = {}): Request {
  return new Request('https://insight.test/sso', {
    method: 'POST',
    headers: { 'content-type': 'application/x-www-form-urlencoded', origin: ODOO, ...headers },
    body,
  });
}
const form = (t: string): string => new URLSearchParams({ t }).toString();
const cookieValue = (res: Response): string => (res.headers.get('set-cookie') ?? '').split(';')[0].slice(`${SESSION_COOKIE}=`.length);

describe('POST /sso: jti hanya dikonsumsi setelah SEMUA validasi lolos', () => {
  it('Origin salah tidak membakar token: kirim ulang dengan Origin benar berhasil', async () => {
    const d = deps();
    expect((await handleSso(post(form(VECTOR_TOKEN), { origin: 'https://evil.test' }), d)).status).toBe(401);
    expect(d.jti.size()).toBe(0);
    expect((await handleSso(post(form(VECTOR_TOKEN)), d)).status).toBe(303);
    expect(d.jti.size()).toBe(1);
  });

  it('tanpa Origin tidak membakar token', async () => {
    const d = deps();
    const noOrigin = new Request('https://insight.test/sso', {
      method: 'POST',
      headers: { 'content-type': 'application/x-www-form-urlencoded' },
      body: form(VECTOR_TOKEN),
    });
    expect((await handleSso(noOrigin, d)).status).toBe(401);
    expect(d.jti.size()).toBe(0);
  });

  it('token bertanda tangan sah tetapi klaim buruk (roles user tanpa cids) ditolak dan jti tidak tersimpan', async () => {
    const d = deps();
    const jti = uniqueJti(7);
    const bad = makeToken({ jti, cids: [] }); // roles=["user"] wajib >= 1 company
    expect((await handleSso(post(form(bad)), d)).status).toBe(401);
    expect(d.jti.has(jti, NOW_MS)).toBe(false);
    // token sah dengan jti yang sama masih bisa dipakai (belum pernah dikonsumsi)
    expect((await handleSso(post(form(makeToken({ jti }))), d)).status).toBe(303);
    expect(d.jti.has(jti, NOW_MS)).toBe(true);
  });

  it('token kedaluwarsa / sig salah tidak menyimpan jti', async () => {
    const d = deps();
    expect((await handleSso(post(form(makeToken({ jti: uniqueJti(8), iat: IAT - 500, exp: IAT - 440 }))), d)).status).toBe(401);
    expect((await handleSso(post(form(makeToken({ jti: uniqueJti(9) }, 'cd'.repeat(32)))), d)).status).toBe(401);
    expect(d.jti.size()).toBe(0);
  });
});

describe('POST /sso: isi sesi dan respons', () => {
  it('nama non-ASCII (escape \\uXXXX di token) tampil utuh di cookie sesi', async () => {
    const res = await handleSso(post(form(makeToken({ name: 'Siti Ünal ñ' }))), deps());
    expect(res.status).toBe(303);
    expect(verifySessionValue(cookieValue(res), SESSION_KEY, IAT + 10)?.name).toBe('Siti Ünal ñ');
  });

  it('all_brands: enam kode, cids kosong diterima dan dibawa ke sesi', async () => {
    const t = makeToken({ roles: ['all_brands'], cids: [], codes: ['BCF', 'BCR', 'CUP', 'IMD', 'PB', 'WTZ'] });
    const res = await handleSso(post(form(t)), deps());
    expect(res.status).toBe(303);
    expect(verifySessionValue(cookieValue(res), SESSION_KEY, IAT + 10)).toMatchObject({
      roles: ['all_brands'],
      cids: [],
      codes: ['BCF', 'BCR', 'CUP', 'IMD', 'PB', 'WTZ'],
    });
  });

  it('respons 401 generik: header aman, tanpa alasan rinci, tautan kembali ke Odoo', async () => {
    const res = await handleSso(post(form(makeToken({ aud: 'lain' }))), deps());
    expect(res.status).toBe(401);
    expect(res.headers.get('cache-control')).toBe('no-store');
    expect(res.headers.get('referrer-policy')).toBe('no-referrer');
    expect(res.headers.get('x-content-type-options')).toBe('nosniff');
    expect(res.headers.get('content-security-policy')).toContain("default-src 'none'");
    expect(res.headers.get('set-cookie')).toBeNull();
    const html = await res.text();
    expect(html).toContain(`${ODOO}/efn/insight/open`);
    for (const leak of ['bad_claims', 'bad_sig', 'bad_format', 'expired', 'replay', 'eit1']) expect(html).not.toContain(leak);
  });

  it('respons sukses tanpa badan dan tanpa token/cookie di Location', async () => {
    const res = await handleSso(post(form(VECTOR_TOKEN)), deps());
    expect(res.status).toBe(303);
    expect(await res.text()).toBe('');
    expect(res.headers.get('location')).toBe('/');
    expect(res.headers.get('location')).not.toContain('eit1');
  });
});

describe('POST /sso: bentuk permintaan dan urutan', () => {
  it('content-type dengan charset diterima; text/plain dan multipart ditolak 400', async () => {
    const ok = post(form(VECTOR_TOKEN), { 'content-type': 'application/x-www-form-urlencoded; charset=UTF-8' });
    expect((await handleSso(ok, deps())).status).toBe(303);
    for (const ct of ['text/plain', 'multipart/form-data; boundary=x', '']) {
      const res = await handleSso(post(form(VECTOR_TOKEN), { 'content-type': ct }), deps());
      expect(res.status, ct).toBe(400);
    }
  });

  it('badan kosong, field bukan "t", dan "t" ganda -> 400 (tidak menyentuh jti)', async () => {
    const d = deps();
    for (const body of ['', `x=${VECTOR_TOKEN}`, `t=${VECTOR_TOKEN}&t=${VECTOR_TOKEN}`]) {
      expect((await handleSso(post(body), d)).status, body).toBe(400);
    }
    expect(d.jti.size()).toBe(0);
  });

  it('batas badan tepat 4096 byte diproses (ditolak sebagai format, bukan 400); 4097 -> 400', async () => {
    const at = `t=${'a'.repeat(4094)}`; // 4096 byte
    expect(at.length).toBe(4096);
    expect((await handleSso(post(at), deps())).status).toBe(401);
    expect((await handleSso(post(`${at}a`), deps())).status).toBe(400);
  });

  it('urutan: kill switch (404) menang atas konfigurasi hilang (503), rate limit tidak terpicu', async () => {
    const limiter = new RateLimiter(1, 60_000);
    for (let i = 0; i < 3; i += 1) {
      expect((await handleSso(post(form(VECTOR_TOKEN)), deps({ enabled: false, ssoKey: null, limiter }))).status).toBe(404);
    }
    // limiter belum terpakai: permintaan pertama sungguhan masih lolos
    expect((await handleSso(post(form(VECTOR_TOKEN)), deps({ limiter }))).status).toBe(303);
  });

  it('kunci sesi tidak terbaca -> 503 (tidak mencetak cookie dengan kunci kosong)', async () => {
    const res = await handleSso(post(form(VECTOR_TOKEN)), deps({ sessionKey: null }));
    expect(res.status).toBe(503);
    expect(res.headers.get('set-cookie')).toBeNull();
  });

  it('token SSO bukan cookie: Set-Cookie tidak memuat kunci SSO maupun payload token asli', async () => {
    const res = await handleSso(post(form(VECTOR_TOKEN)), deps());
    const set = res.headers.get('set-cookie') ?? '';
    expect(set).not.toContain(VECTOR_TOKEN.split('.')[2]); // sig token
    expect(set).not.toContain(KEY);
    expect(set.startsWith(`${SESSION_COOKIE}=eis1.`)).toBe(true);
  });
});
