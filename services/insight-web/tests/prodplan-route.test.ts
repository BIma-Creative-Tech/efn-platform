/**
 * Route handler `/api/prodplan/*` dan klien prodplan (docs/04 §30.12 + §30.17): gerbang (flag, cookie `__Host-epp`, same-origin,
 * batas per sha256(sid)), aksi hanya dari daftar, peran dari SESI prodplan (bukan input/cookie Odoo), asersi `ppa2` membawa sid,
 * pemetaan galat tanpa detail internal, `session_expired` menghapus cookie, timeout, tanpa redirect. runtime di-mock; fetch ke
 * sidecar palsu.
 */
import { createHash, createHmac } from 'node:crypto';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const KEY = 'ab'.repeat(32);
const SID = 'Q'.repeat(43);
const { state } = vi.hoisted(() => ({ state: { enabled: true, key: 'ab'.repeat(32) as string | null } }));

vi.mock('@/lib/runtime', async () => {
  const { RateLimiter } = await import('@/lib/rate-limit');
  let mut = new RateLimiter(60, 60_000);
  const read = new RateLimiter(150, 60_000);
  (globalThis as unknown as { __resetPpLimiter: () => void }).__resetPpLimiter = () => {
    mut = new RateLimiter(60, 60_000);
  };
  return {
    getConfig: () => ({ ppEnabled: state.enabled, ppUrl: 'http://127.0.0.1:7443' }),
    getKey: () => state.key,
    getPpMutationLimiter: () => mut,
    getPpReadLimiter: () => read,
  };
});

import { POST } from '@/app/api/prodplan/[action]/route';
import { GET } from '@/app/api/prodplan/jobs/[id]/route';
import { isSameOriginMutation } from '@/lib/origin-guard';
import { ppCall, ppQuery } from '@/lib/prodplan-client';
import { ACTOR_RE } from '@/lib/prodplan-token';

const ID = '0b0c0a0e-1111-4222-8333-444455556666';
const COOKIE = `__Host-epp=${SID}`;
const PLANNER = ['pp_planner'];
const WAREHOUSE = ['pp_warehouse'];

const sessionView = (roles: string[]) => ({
  name: 'Rina Planner',
  esb_env: 'staging',
  esb_brand: 'pb',
  company_code: 'PB',
  roles,
  doc_access: { insert: [], delete: [] },
  branches: [{ branch_id: 4, name: 'CK Paris' }],
  expires_at: '2026-11-25T10:00:00Z',
});

const post = (action: string, body: unknown, headers: Record<string, string> = {}): Promise<Response> =>
  POST(
    new Request(`https://insight.test/api/prodplan/${action}`, {
      method: 'POST',
      headers: { 'content-type': 'application/json', 'sec-fetch-site': 'same-origin', cookie: COOKIE, ...headers },
      body: typeof body === 'string' ? body : JSON.stringify(body),
    }),
    { params: Promise.resolve({ action }) },
  );

type FetchCall = { url: string; init: RequestInit };
let calls: FetchCall[] = [];
/**
 * fetch palsu: `GET /v1/session` -> tampilan sesi dengan `roles` (bukan bagian `calls` aksi); selain itu `status/body`.
 * `calls` hanya mencatat panggilan selain GET /v1/session.
 */
function stubFetch(status: number, body: unknown, ctype = 'application/json', roles: string[] = PLANNER, sessionStatus = 200): void {
  calls = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init: RequestInit) => {
      if (url.endsWith('/v1/session') && init.method === 'GET') {
        return sessionStatus === 200
          ? new Response(JSON.stringify(sessionView(roles)), { status: 200 })
          : new Response(JSON.stringify({ error: { code: 'session_expired' } }), { status: sessionStatus });
      }
      calls.push({ url, init });
      return new Response(typeof body === 'string' ? body : JSON.stringify(body), { status, headers: { 'content-type': ctype } });
    }),
  );
}

beforeEach(() => {
  state.enabled = true;
  state.key = KEY;
  (globalThis as unknown as { __resetPpLimiter: () => void }).__resetPpLimiter();
  vi.spyOn(console, 'info').mockImplementation(() => undefined);
  vi.spyOn(console, 'warn').mockImplementation(() => undefined);
  vi.spyOn(console, 'error').mockImplementation(() => undefined);
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

const errOf = async (res: Response): Promise<{ code: string; message: string; details?: unknown }> =>
  ((await res.json()) as { error: { code: string; message: string; details?: unknown } }).error;

describe('POST /api/prodplan/<aksi>: gerbang', () => {
  it('flag mati -> 503 tanpa memanggil prodplan', async () => {
    state.enabled = false;
    stubFetch(200, {});
    const res = await post('plan-submit', { id: ID, row_version: 1 });
    expect(res.status).toBe(503);
    expect((await errOf(res)).code).toBe('disabled');
    expect(calls).toHaveLength(0);
  });

  it('tanpa cookie __Host-epp, bentuk salah, atau hanya cookie Odoo -> 401 session_expired + hapus cookie; prodplan tidak dipanggil', async () => {
    stubFetch(200, {});
    for (const cookie of [undefined, '__Host-epp=pendek', `__Host-epp=${'a'.repeat(44)}`, '__Host-eis=eis3.aaaaaaaaaaaaaaaaaaaa.bbbb', `__Host-eisx=${SID}`]) {
      const headers: Record<string, string> = cookie === undefined ? { cookie: '' } : { cookie };
      const res = await post('plan-submit', { id: ID, row_version: 1 }, headers);
      expect(res.status, String(cookie)).toBe(401);
      expect((await errOf(res)).code).toBe('session_expired');
      expect(res.headers.get('set-cookie')).toContain('__Host-epp=; Max-Age=0');
    }
    expect(calls).toHaveLength(0);
  });

  it('lintas-origin / tanpa metadata Origin dan Content-Type bukan JSON ditolak sebelum menyentuh prodplan', async () => {
    stubFetch(200, {});
    expect((await post('plan-submit', { id: ID, row_version: 1 }, { 'sec-fetch-site': 'cross-site' })).status).toBe(403);
    expect((await post('plan-submit', { id: ID, row_version: 1 }, { 'sec-fetch-site': 'same-site' })).status).toBe(403);
    expect((await post('plan-submit', { id: ID, row_version: 1 }, { 'sec-fetch-site': 'none' })).status).toBe(403);
    // tanpa Sec-Fetch-Site dan tanpa Origin -> ditolak (lebih ketat dari /logout)
    expect((await post('plan-submit', { id: ID, row_version: 1 }, { 'sec-fetch-site': '' })).status).toBe(403);
    expect((await post('plan-submit', { id: ID, row_version: 1 }, { 'content-type': 'text/plain' })).status).toBe(400);
    expect((await post('plan-submit', { id: ID, row_version: 1 }, { 'content-type': 'application/x-www-form-urlencoded' })).status).toBe(400);
    expect(calls).toHaveLength(0);
  });

  it('batas 60 mutasi/menit per sesi -> 429 + Retry-After', async () => {
    stubFetch(200, { ok: 1 });
    for (let i = 0; i < 60; i += 1) expect((await post('plan-submit', { id: ID, row_version: 1 })).status).toBe(200);
    const over = await post('plan-submit', { id: ID, row_version: 1 });
    expect(over.status).toBe(429);
    expect(Number(over.headers.get('retry-after'))).toBeGreaterThanOrEqual(1);
    expect((await errOf(over)).code).toBe('rate_limited');
  });

  it('header respons: no-store, nosniff, CSP API', async () => {
    stubFetch(200, { ok: 1 });
    const res = await post('plan-submit', { id: ID, row_version: 1 });
    expect(res.headers.get('cache-control')).toBe('no-store');
    expect(res.headers.get('x-content-type-options')).toBe('nosniff');
    expect(res.headers.get('content-security-policy')).toContain("default-src 'none'");
    expect(res.headers.get('content-type')).toContain('application/json');
  });

  it('prodplan membalas 401 session_expired (sesi habis) -> 401 + cookie dihapus, pada GET sesi maupun pada aksi', async () => {
    stubFetch(200, {}, 'application/json', PLANNER, 401);
    const a = await post('plan-submit', { id: ID, row_version: 1 });
    expect(a.status).toBe(401);
    expect((await errOf(a)).code).toBe('session_expired');
    expect(a.headers.get('set-cookie')).toContain('Max-Age=0');
    // sesi sah saat dicek peran, habis tepat saat aksi
    stubFetch(401, { error: { code: 'session_expired', message: 'x' } });
    const b = await post('plan-submit', { id: ID, row_version: 1 });
    expect(b.status).toBe(401);
    expect(b.headers.get('set-cookie')).toContain('__Host-epp=; Max-Age=0; Path=/; HttpOnly; Secure; SameSite=Strict');
    expect((await errOf(b)).message).toContain('Sesi ESB berakhir');
  });
});

describe('POST /api/prodplan/<aksi>: aksi, peran, validasi', () => {
  it('aksi tak dikenal -> 404; peran DARI SESI prodplan tidak cocok aksi -> 403 (planner tidak boleh menyetujui, gudang tidak boleh membuat)', async () => {
    stubFetch(200, {});
    expect((await post('plan-delete', {})).status).toBe(404);
    expect((await post('..%2Fadmin', {})).status).toBe(404);
    expect((await post('plan-approve', { id: ID, row_version: 1 })).status).toBe(403);
    expect((await post('transfer-approve', { id: ID, tid: 1, row_version: 1 })).status).toBe(403);
    expect((await post('plan-resume-posting', { id: ID, row_version: 1 })).status).toBe(200); // planner = salah satu (R)
    stubFetch(200, {}, 'application/json', WAREHOUSE);
    expect((await post('plan-create', { brand: 'pb', branch_id: 4, production_location_id: 7, production_date: '2026-11-24' })).status).toBe(403);
    expect((await post('pack-rule-put', { brand: 'pb', product_code: 'A', base_uom: 'ML', pack_qty: 1 })).status).toBe(403);
    stubFetch(200, {}, 'application/json', []);
    expect((await post('plan-submit', { id: ID, row_version: 1 })).status).toBe(403);
    expect(calls).toHaveLength(0);
  });

  it('badan bukan JSON objek / terlalu besar -> 400; input tidak sah -> 422 dengan nama field', async () => {
    stubFetch(200, {});
    expect((await post('plan-submit', 'bukan json')).status).toBe(400);
    expect((await post('plan-submit', '[1,2]')).status).toBe(400);
    expect((await post('plan-submit', JSON.stringify({ id: ID, row_version: 1, pad: 'x'.repeat(300 * 1024) }))).status).toBe(400);
    const res = await post('plan-submit', { id: 'bukan-uuid', row_version: 1 });
    expect(res.status).toBe(422);
    const e = await errOf(res);
    expect(e.code).toBe('bad_input');
    expect(e.details).toEqual({ fields: ['id'] });
    expect(calls).toHaveLength(0);
  });

  it('resume-posting -> POST /v1/plans/{id}/resume-posting dengan {row_version}', async () => {
    stubFetch(202, { outbox_ids: ['a'] });
    const res = await post('plan-resume-posting', { id: ID, row_version: 4, run_as: 'x' });
    expect(res.status).toBe(200);
    expect(calls).toHaveLength(1);
    expect(calls[0].url).toBe(`http://127.0.0.1:7443/v1/plans/${ID}/resume-posting`);
    expect(calls[0].init.body).toBe(JSON.stringify({ row_version: 4 }));
  });

  it('kode galat baru dipetakan ke pesan Indonesia', async () => {
    for (const [status, code, text] of [
      [403, 'resume_requires_original_user', 'pengguna yang sama'],
      [403, 'esb_access_missing', 'hak akses dokumen'],
      [403, 'branch_forbidden', 'cabang'],
      [409, 'nothing_to_resume', 'tidak ada posting'],
    ] as const) {
      stubFetch(status, { error: { code, message: 'x' } });
      const res = await post('plan-resume-posting', { id: ID, row_version: 1 });
      expect(res.status).toBe(status);
      const e = await errOf(res);
      expect(e.code).toBe(code);
      expect(e.message.toLowerCase()).toContain(text);
    }
  });
});

describe('POST /api/prodplan/<aksi>: penerusan ke prodplan', () => {
  it('meneruskan ke sidecar loopback dengan X-EFN-Actor ppa2 membawa sid; tanpa cookie/otorisasi; identitas dari input diabaikan', async () => {
    stubFetch(200, { id: ID }, 'application/json', WAREHOUSE);
    const res = await post('plan-approve', { id: ID, row_version: 5, sub: 1, roles: ['pp_planner'], created_by_sub: 1, sid: 'x' });
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ ok: true, status: 200, data: { id: ID } });

    expect(calls).toHaveLength(1);
    const { url, init } = calls[0];
    expect(url).toBe(`http://127.0.0.1:7443/v1/plans/${ID}/approve`);
    expect(init.method).toBe('POST');
    expect(init.redirect).toBe('error');
    expect(init.body).toBe(JSON.stringify({ row_version: 5 }));
    const headers = init.headers as Record<string, string>;
    expect(headers['Content-Type']).toBe('application/json');
    expect(Object.keys(headers).map((k) => k.toLowerCase())).toEqual(expect.not.arrayContaining(['cookie', 'authorization']));

    const actor = headers['X-EFN-Actor'];
    expect(ACTOR_RE.test(actor)).toBe(true);
    const [prefix, b64, sig] = actor.split('.');
    expect(prefix).toBe('ppa2');
    expect(sig).toBe(createHmac('sha256', Buffer.from(KEY, 'ascii')).update(`${prefix}.${b64}`).digest('base64url'));
    const p = JSON.parse(Buffer.from(b64, 'base64url').toString('utf8')) as Record<string, unknown>;
    expect(Object.keys(p)).toEqual(['aud', 'bh', 'exp', 'iat', 'jti', 'm', 'p', 'sid']);
    expect(p).toMatchObject({ aud: 'prodplan', m: 'POST', p: `/v1/plans/${ID}/approve`, sid: SID });
    expect(p.bh).toBe(createHash('sha256').update(JSON.stringify({ row_version: 5 })).digest('base64url'));
    expect((p.exp as number) - (p.iat as number)).toBe(60);
  });

  it('PUT demand dan pack-rule memakai method PUT; path ber-encode', async () => {
    stubFetch(200, { ok: true });
    await post('plan-demand', { id: ID, row_version: 1, lines: [{ product_detail_id: 1, bom_id: 2, qty: 3, source: 'manual' }] });
    await post('pack-rule-put', { brand: 'pb', product_code: 'RMT000021', base_uom: 'ML', pack_qty: 1000, active: true });
    expect(calls.map((c) => [c.init.method, c.url])).toEqual([
      ['PUT', `http://127.0.0.1:7443/v1/plans/${ID}/demand`],
      ['PUT', 'http://127.0.0.1:7443/v1/pack-rules/pb/RMT000021'],
    ]);
  });

  it('galat prodplan dipetakan ke pesan Indonesia; badan/stack/SQL/URL ESB tidak bocor', async () => {
    stubFetch(
      409,
      { error: { code: 'sod_violation', message: 'SQL: SELECT x FROM prodplan.plan', details: { stack: 'Traceback', esb_url: 'https://stg7.esb.co.id/core-stg/x', docs: ['ST0001'] } } },
      'application/json',
      WAREHOUSE,
    );
    const res = await post('plan-approve', { id: ID, row_version: 1 });
    expect(res.status).toBe(409);
    const text = JSON.stringify(await res.json());
    expect(text).toContain('sod_violation');
    expect(text).toContain('Pemisahan tugas');
    expect(text).not.toMatch(/SELECT|Traceback|esb\.co\.id|stg7/);
  });

  it('status tak dikenal (500) -> 502 bad_response; teks mentah tidak diteruskan', async () => {
    stubFetch(500, '<html>Internal Server Error psycopg2.OperationalError</html>', 'text/html');
    const res = await post('plan-submit', { id: ID, row_version: 1 });
    expect(res.status).toBe(502);
    const e = await errOf(res);
    expect(e.code).toBe('bad_response');
    expect(JSON.stringify(e)).not.toMatch(/psycopg2|html/);
  });

  it('prodplan tidak menjawab (fetch ditolak) -> galat unreachable tanpa detail; AbortError -> timeout', async () => {
    vi.stubGlobal('fetch', vi.fn(async (url: string, init: RequestInit) => {
      if (url.endsWith('/v1/session') && init.method === 'GET') return new Response(JSON.stringify(sessionView(PLANNER)), { status: 200 });
      return Promise.reject(new TypeError('connect ECONNREFUSED 127.0.0.1:7443'));
    }));
    const a = await post('plan-submit', { id: ID, row_version: 1 });
    expect(a.status).toBe(502);
    expect(JSON.stringify(await a.json())).not.toMatch(/ECONNREFUSED|127\.0\.0\.1/);
    const abort = Object.assign(new Error('The operation was aborted'), { name: 'AbortError' });
    vi.stubGlobal('fetch', vi.fn(async (url: string, init: RequestInit) => {
      if (url.endsWith('/v1/session') && init.method === 'GET') return new Response(JSON.stringify(sessionView(PLANNER)), { status: 200 });
      return Promise.reject(abort);
    }));
    const b = await post('plan-submit', { id: ID, row_version: 1 });
    expect((await errOf(b)).code).toBe('timeout');
  });

  it('202 dengan job_id diteruskan apa adanya dalam data', async () => {
    stubFetch(202, { job_id: ID });
    const res = await post('plan-calculate', { id: ID, row_version: 2 });
    expect(res.status).toBe(200);
    expect(await res.json()).toMatchObject({ ok: true, status: 202, data: { job_id: ID } });
  });
});

describe('GET /api/prodplan/jobs/{id}', () => {
  const get = (id: string, cookie: string | null = COOKIE): Promise<Response> =>
    GET(new Request(`https://insight.test/api/prodplan/jobs/${id}`, { headers: cookie === null ? {} : { cookie } }), { params: Promise.resolve({ id }) });

  it('sesi sah mana pun; hanya status/kind/error_code dikembalikan', async () => {
    stubFetch(200, { status: 'failed', kind: 'calculate', error_code: 'esb_unavailable', result: { rahasia: 1 }, params: { x: 1 } });
    const res = await get(ID);
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({
      ok: true,
      data: { status: 'failed', kind: 'calculate', error_code: 'esb_unavailable', plan_status: null, changed: null, path: [] },
    });
    expect(calls[0].url).toBe(`http://127.0.0.1:7443/v1/jobs/${ID}`);
    expect(calls[0].init.method).toBe('GET');
    expect(calls[0].init.body).toBeUndefined();
  });

  it('id bukan UUID -> 404 tanpa memanggil prodplan; tanpa cookie -> 401; sesi habis -> 401 + hapus cookie; bentuk salah -> 502', async () => {
    stubFetch(200, { status: 'done' });
    expect((await get('../plans')).status).toBe(404);
    expect(calls).toHaveLength(0);
    const none = await get(ID, null);
    expect(none.status).toBe(401);
    expect((await errOf(none)).code).toBe('session_expired');
    stubFetch(401, { error: { code: 'session_expired' } });
    const gone = await get(ID);
    expect(gone.status).toBe(401);
    expect(gone.headers.get('set-cookie')).toContain('Max-Age=0');
    stubFetch(200, { status: 'aneh' });
    expect((await get(ID)).status).toBe(502);
  });
});

describe('isSameOriginMutation', () => {
  it('Sec-Fetch-Site ada -> hanya same-origin; tanpa itu Origin wajib ada dan hostnya = Host', () => {
    expect(isSameOriginMutation('same-origin', null, null)).toBe(true);
    for (const s of ['same-site', 'cross-site', 'none', 'aneh']) expect(isSameOriginMutation(s, 'https://insight.test', 'insight.test'), s).toBe(false);
    expect(isSameOriginMutation(null, 'https://insight.test', 'insight.test')).toBe(true);
    expect(isSameOriginMutation(undefined, 'https://insight.test:9443', 'insight.test:9443')).toBe(true);
    expect(isSameOriginMutation(null, 'https://evil.test', 'insight.test')).toBe(false);
    expect(isSameOriginMutation(null, 'null', 'insight.test')).toBe(false);
    expect(isSameOriginMutation(null, null, 'insight.test')).toBe(false);
    expect(isSameOriginMutation(null, 'https://insight.test', null)).toBe(false);
    expect(isSameOriginMutation(null, '', 'insight.test')).toBe(false);
  });
});

describe('ppCall / ppQuery (klien)', () => {
  const sess = { sid: SID };
  const base = 'http://127.0.0.1:7443';

  it('baseUrl atau kunci kosong -> 503 disabled tanpa fetch; sid tidak sah -> 401 session_expired; path tidak sah -> 400', async () => {
    const f = vi.fn();
    const fetchFn = f as unknown as typeof fetch;
    expect(await ppCall(sess, 'GET', '/v1/meta', undefined, { baseUrl: null, key: KEY, fetchFn })).toMatchObject({ ok: false, status: 503 });
    expect(await ppCall(sess, 'GET', '/v1/meta', undefined, { baseUrl: base, key: null, fetchFn })).toMatchObject({ ok: false, status: 503 });
    expect(await ppCall({ sid: 'pendek' }, 'GET', '/v1/meta', undefined, { baseUrl: base, key: KEY, fetchFn })).toMatchObject({ ok: false, status: 401, error: { code: 'session_expired' } });
    // sid kosong hanya login/meta
    expect(await ppCall({ sid: '' }, 'GET', '/v1/plans', undefined, { baseUrl: base, key: KEY, fetchFn })).toMatchObject({ ok: false, status: 401 });
    expect(await ppCall(sess, 'GET', '/healthz', undefined, { baseUrl: base, key: KEY, fetchFn })).toMatchObject({ ok: false, status: 400 });
    expect(f).not.toHaveBeenCalled();
  });

  it('GET tanpa body: tanpa Content-Type, bh = hash string kosong; query ikut terikat di p', async () => {
    const f = vi.fn(async () => new Response('{"items":[],"count":0}', { status: 200 }));
    const path = `/v1/plans${ppQuery({ brand: 'pb', from: '', status: 'draft', limit: 50 })}`;
    expect(path).toBe('/v1/plans?brand=pb&status=draft&limit=50');
    const r = await ppCall(sess, 'GET', path, undefined, { baseUrl: base, key: KEY, fetchFn: f as unknown as typeof fetch, now: () => 1_795_000_000_000 });
    expect(r).toEqual({ ok: true, status: 200, data: { items: [], count: 0 } });
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(`${base}${path}`);
    const h = init.headers as Record<string, string>;
    expect(h['Content-Type']).toBeUndefined();
    const p = JSON.parse(Buffer.from(h['X-EFN-Actor'].split('.')[1], 'base64url').toString('utf8')) as Record<string, unknown>;
    expect(p).toMatchObject({ m: 'GET', p: path, iat: 1_795_000_000, exp: 1_795_000_060, sid: SID });
    expect(p.bh).toBe(createHash('sha256').update('').digest('base64url'));
  });

  it('DELETE /v1/session didukung (logout)', async () => {
    const f = vi.fn(async () => new Response(null, { status: 204 }));
    const r = await ppCall(sess, 'DELETE', '/v1/session', undefined, { baseUrl: base, key: KEY, fetchFn: f as unknown as typeof fetch });
    // 204 tanpa badan: bukan JSON, tetapi res.ok dan teks kosong -> data null
    expect(r).toEqual({ ok: true, status: 204, data: null });
    expect((f.mock.calls[0] as unknown as [string, RequestInit])[1].method).toBe('DELETE');
  });

  it('respons terlalu besar atau bukan JSON pada 2xx -> bad_response', async () => {
    const big = vi.fn(async () => new Response('x', { status: 200, headers: { 'content-length': String(9 * 1024 * 1024) } }));
    expect(await ppCall(sess, 'GET', '/v1/meta', undefined, { baseUrl: base, key: KEY, fetchFn: big as unknown as typeof fetch })).toMatchObject({
      ok: false,
      error: { code: 'bad_response' },
    });
    const html = vi.fn(async () => new Response('<html>', { status: 200 }));
    expect(await ppCall(sess, 'GET', '/v1/meta', undefined, { baseUrl: base, key: KEY, fetchFn: html as unknown as typeof fetch })).toMatchObject({
      ok: false,
      error: { code: 'bad_response' },
    });
  });

  it('timeout: fetch yang menggantung dibatalkan oleh AbortController', async () => {
    const hang = vi.fn((_url: string, init: RequestInit) => new Promise<Response>((_res, rej) => {
      init.signal?.addEventListener('abort', () => rej(Object.assign(new Error('aborted'), { name: 'AbortError' })));
    }));
    const r = await ppCall(sess, 'GET', '/v1/meta', undefined, { baseUrl: base, key: KEY, fetchFn: hang as unknown as typeof fetch, timeoutMs: 20 });
    expect(r).toMatchObject({ ok: false, status: 504, error: { code: 'timeout' } });
  });

  it('ppQuery membuang nilai kosong dan meng-encode', () => {
    expect(ppQuery({})).toBe('');
    expect(ppQuery({ a: '', b: undefined })).toBe('');
    expect(ppQuery({ q: 'a b&c' })).toBe('?q=a+b%26c');
  });
});
