/**
 * G22 — Route Handler `/api/fnb/*` (BFF di-mock) dan perluasan `/api/orders` (points_redeem): header X-Table-Session,
 * Bearer opsional, Idempotency-Key dari klien, allowlist body, peta error, tanpa kebocoran token.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { NextRequest } from 'next/server';

const { bffFetch, bffBaseUrl } = vi.hoisted(() => ({ bffFetch: vi.fn(), bffBaseUrl: vi.fn() }));
vi.mock('server-only', () => ({}));
vi.mock('@/lib/bff', () => ({ bffFetch, bffBaseUrl }));

import { GET as getSession } from '@/app/api/fnb/session/route';
import { GET as getMenu } from '@/app/api/fnb/menu/route';
import { GET as listSubs, POST as postSub } from '@/app/api/fnb/orders/route';
import { GET as getSub } from '@/app/api/fnb/orders/[id]/route';
import { DELETE as unclaim, POST as claim } from '@/app/api/fnb/points-claim/route';
import { POST as postOrder } from '@/app/api/orders/route';
import { checkoutIdempotencyKey } from '@/lib/orders-route';

const HOST = 'app.efn.example';
const TOKEN = `fts1.${'A'.repeat(22)}.1790000000.${'B'.repeat(43)}`;
const SID = '3f2b8c1e-9a4d-4c7e-8b21-5d6e7f8a9b0c';
const QUOTE_ID = '9b1d2c3e-4f5a-4b6c-8d7e-0f1a2b3c4d5e';
const KEY = 'a-valid-idempotency-key-0001'; // gitleaks:allow (fixture idempotency key, bukan secret)

interface Init {
  method?: string;
  body?: unknown;
  cookie?: string | null;
  origin?: string | null;
  site?: string;
  table?: string | null;
  key?: string | null;
}

function req(path: string, init: Init = {}) {
  const method = init.method ?? 'GET';
  const headers: Record<string, string> = { host: HOST };
  if (init.cookie) headers.cookie = init.cookie;
  if (init.table !== null) headers['x-table-session'] = init.table ?? TOKEN;
  if (init.key) headers['idempotency-key'] = init.key;
  if (method !== 'GET' && init.origin !== null) headers.origin = init.origin ?? `https://${HOST}`;
  if (init.site) headers['sec-fetch-site'] = init.site;
  if (init.body !== undefined) headers['content-type'] = 'application/json';
  return new NextRequest(`https://${HOST}${path}`, {
    method,
    headers,
    body: init.body === undefined ? undefined : typeof init.body === 'string' ? init.body : JSON.stringify(init.body),
  });
}
const params = <T extends Record<string, string>>(p: T) => ({ params: Promise.resolve(p) });
const fail = (status: number, code: string, body: unknown = null) => ({ ok: false, status, code, message: 'pesan internal BFF', body });
const SESSION = { member: 'efn_session=tok' };

const logs = () => [vi.spyOn(console, 'log'), vi.spyOn(console, 'info'), vi.spyOn(console, 'warn'), vi.spyOn(console, 'error'), vi.spyOn(console, 'debug')];

beforeEach(() => {
  bffFetch.mockReset();
  bffBaseUrl.mockReset();
});
afterEach(() => vi.restoreAllMocks());

describe('guard bersama', () => {
  it('header token meja hilang / salah bentuk → 401 table_session_invalid tanpa memanggil BFF', async () => {
    for (const table of [null, '', 'bukan-token', TOKEN.replace('fts1', 'fts9')]) {
      const res = await getSession(req('/api/fnb/session', { table }));
      expect(res.status).toBe(401);
      expect(res.headers.get('cache-control')).toBe('no-store');
      const b = await res.json();
      expect(b.error.code).toBe('table_session_invalid');
      expect(b.error.message).toMatch(/Pindai ulang/);
    }
    expect(bffFetch).not.toHaveBeenCalled();
  });
  it('query ?ts= diabaikan: token hanya dari header', async () => {
    const r = new NextRequest(`https://${HOST}/api/fnb/session?ts=${TOKEN}`, { headers: { host: HOST } });
    expect((await getSession(r)).status).toBe(401);
    expect(bffFetch).not.toHaveBeenCalled();
  });
  it('GET lintas situs ditolak; tulis tanpa Origin sama ditolak', async () => {
    expect((await getSession(req('/api/fnb/session', { site: 'cross-site' }))).status).toBe(403);
    expect((await postSub(req('/api/fnb/orders', { method: 'POST', origin: null, key: KEY, body: { lines: [{ product_id: 5, qty: 1 }] } }))).status).toBe(403);
    expect((await postSub(req('/api/fnb/orders', { method: 'POST', origin: 'https://evil.example', key: KEY, body: { lines: [{ product_id: 5, qty: 1 }] } }))).status).toBe(403);
    expect(bffFetch).not.toHaveBeenCalled();
  });
});

describe('GET /api/fnb/session & /menu', () => {
  const sessionOk = {
    ok: true,
    status: 200,
    data: {
      outlet_name: 'Paris Baguette Kemang',
      table_label: 'T12 / Teras',
      company_code: 'PB',
      brand_code: 'PB',
      expires_at: '2026-10-19T12:00:00Z',
      confirm_mode: 'waiter',
      limits: { max_lines: 30, max_line_qty: 20, submissions_left: 19 },
      points: { claimable: true, claimed: false, claimed_by_me: false },
      table_session_id: 'jangan-bocor',
    },
  };

  it('tamu: tanpa Bearer, token meja diteruskan sebagai header; respons di-allowlist, no-store, tanpa token', async () => {
    bffFetch.mockResolvedValue(sessionOk);
    const res = await getSession(req('/api/fnb/session'));
    expect(res.status).toBe(200);
    expect(res.headers.get('cache-control')).toBe('no-store');
    const text = await res.text();
    expect(text).not.toContain('jangan-bocor');
    expect(text).not.toContain('fts1');
    expect(JSON.parse(text).table_label).toBe('T12 / Teras');
    const [path, opts] = bffFetch.mock.calls[0]!;
    expect(path).toBe('/v1/fnb/session');
    expect(opts.token).toBeUndefined();
    expect(opts.headers['x-table-session']).toBe(TOKEN);
    expect(path).not.toContain('fts1'); // tidak pernah di URL/query ke BFF
  });
  it('member: Bearer dari cookie HttpOnly ikut diteruskan', async () => {
    bffFetch.mockResolvedValue(sessionOk);
    await getSession(req('/api/fnb/session', { cookie: SESSION.member }));
    expect(bffFetch.mock.calls[0]![1].token).toBe('tok');
  });
  it('Bearer tidak valid → 401 unauthenticated + cookie sesi dihapus (klien mengulang sebagai tamu)', async () => {
    bffFetch.mockResolvedValue(fail(401, 'unauthorized'));
    const res = await getSession(req('/api/fnb/session', { cookie: SESSION.member }));
    expect(res.status).toBe(401);
    expect((await res.json()).error.code).toBe('unauthenticated');
    expect(res.headers.get('set-cookie')).toMatch(/efn_session=;/);
    expect(res.headers.get('set-cookie')).toMatch(/Max-Age=0/i);
  });
  it('token meja ditolak BFF → table_session_invalid; cookie sesi member TIDAK dihapus', async () => {
    bffFetch.mockResolvedValue(fail(401, 'table_session_invalid'));
    const res = await getSession(req('/api/fnb/session', { cookie: SESSION.member }));
    expect(res.status).toBe(401);
    expect((await res.json()).error.code).toBe('table_session_invalid');
    expect(res.headers.get('set-cookie')).toBeNull();
  });
  it('respons BFF rusak → 502 generik', async () => {
    bffFetch.mockResolvedValue({ ok: true, status: 200, data: { outlet_name: 'x' } });
    const res = await getSession(req('/api/fnb/session'));
    expect(res.status).toBe(502);
  });
  it('menu: allowlist atribut, harga sebelum pajak', async () => {
    bffFetch.mockResolvedValue({
      ok: true,
      status: 200,
      data: {
        categories: [{ category_id: 1, name: 'Minuman', sequence: 1, image_sha: null }],
        products: [{ product_id: 5, name: 'Es Teh', category_id: 1, price_excl: 10000, tax_labels: ['PB1 10%'], available: true, cost_price: 1, attributes: [{ attribute_id: 1, name: 'Gula', display_type: 'radio', required: true, values: [{ id: 11, name: 'Normal', price_extra_excl: 0 }] }] }],
        menu_synced_at: '2026-10-19T09:00:00Z',
      },
    });
    const res = await getMenu(req('/api/fnb/menu'));
    expect(res.status).toBe(200);
    const b = await res.json();
    expect(JSON.stringify(b)).not.toContain('cost_price');
    expect(b.products[0].attributes[0].values[0].id).toBe(11);
    expect(bffFetch.mock.calls[0]![0]).toBe('/v1/fnb/menu');
  });
});

describe('POST /api/fnb/orders (kirim)', () => {
  const body = { lines: [{ product_id: 5, qty: 2, attribute_value_ids: [9, 3], note: '  tanpa   es ', price_excl: 1 }], harga: 1, table_id: 12 };
  const accepted = { ok: true, status: 202, data: { submission_id: SID, status: 'pending_confirmation', lines: [{ product_id: 5, name: 'Es Teh', qty: 2 }], indicative_subtotal_excl: 20000, odoo_ref: 'x' } };

  it('202: Idempotency-Key dari klien diteruskan; body di-allowlist (tanpa harga/meja); respons di-allowlist', async () => {
    bffFetch.mockResolvedValue(accepted);
    const res = await postSub(req('/api/fnb/orders', { method: 'POST', key: KEY, body }));
    expect(res.status).toBe(202);
    expect(res.headers.get('cache-control')).toBe('no-store');
    const out = await res.json();
    expect(out).toEqual({ submission_id: SID, status: 'pending_confirmation', lines: [{ name: 'Es Teh', qty: 2, attributes_text: '', attribute_value_ids: [] }], indicative_subtotal_excl: 20000 });
    const [path, opts] = bffFetch.mock.calls[0]!;
    expect(path).toBe('/v1/fnb/orders');
    expect(opts.method).toBe('POST');
    expect(opts.headers['idempotency-key']).toBe(KEY);
    expect(opts.headers['x-table-session']).toBe(TOKEN);
    expect(opts.json).toEqual({ lines: [{ product_id: 5, qty: 2, attribute_value_ids: [3, 9], note: 'tanpa es' }] });
    expect(JSON.stringify(opts.json)).not.toMatch(/harga|table_id|price/);
  });
  it('replay (BFF 200) tetap dijawab dengan data yang sama', async () => {
    bffFetch.mockResolvedValue({ ...accepted, status: 200 });
    expect((await postSub(req('/api/fnb/orders', { method: 'POST', key: KEY, body }))).status).toBe(200);
  });
  it('Idempotency-Key hilang / salah bentuk → 400 idempotency_key_required tanpa BFF', async () => {
    for (const key of [null, 'pendek', 'ada spasi dan simbol!!!!!!']) {
      const res = await postSub(req('/api/fnb/orders', { method: 'POST', key, body }));
      expect(res.status).toBe(400);
      expect((await res.json()).error.code).toBe('idempotency_key_required');
    }
    expect(bffFetch).not.toHaveBeenCalled();
  });
  it('catatan mirip nomor HP / email → 400 note_invalid; bentuk salah → 400 invalid_request; tanpa BFF', async () => {
    const withNote = (note: string) => ({ lines: [{ product_id: 5, qty: 1, note }] });
    for (const note of ['wa 0812 3456 7890', 'a@b.com', 'x'.repeat(141)]) {
      const res = await postSub(req('/api/fnb/orders', { method: 'POST', key: KEY, body: withNote(note) }));
      expect(res.status).toBe(400);
      expect((await res.json()).error.code).toBe('note_invalid');
    }
    for (const bad of [{}, { lines: [] }, { lines: [{ product_id: 5, qty: 0 }] }, 'bukan json {']) {
      const res = await postSub(req('/api/fnb/orders', { method: 'POST', key: KEY, body: bad }));
      expect(res.status).toBe(400);
      expect((await res.json()).error.code).toBe('invalid_request');
    }
    expect(bffFetch).not.toHaveBeenCalled();
  });
  it('body > 16 KB → 413', async () => {
    const res = await postSub(req('/api/fnb/orders', { method: 'POST', key: KEY, body: { lines: [{ product_id: 5, qty: 1, note: 'x'.repeat(20_000) }] } }));
    expect(res.status).toBe(413);
    expect(bffFetch).not.toHaveBeenCalled();
  });
  it.each([
    [409, 'table_session_closed', 409, /sudah ditutup/],
    [409, 'idempotency_conflict', 409, /isi yang berbeda/],
    [422, 'session_limit_reached', 422, /pesan ke pelayan/],
    [429, 'rate_limited', 429, /Terlalu banyak/],
    [400, 'captcha_required', 400, /bukan robot/],
    [400, 'note_invalid', 400, /Catatan tidak valid/],
    [503, 'ordering_unavailable', 503, /pesan ke pelayan/],
    [503, 'bff_unavailable', 503, /pesan ke pelayan/],
    [504, 'bff_timeout', 503, /pesan ke pelayan/],
    [409, 'kode_baru_tak_dikenal', 409, /tidak valid/],
    [500, 'internal', 502, /Terjadi kendala/],
  ])('BFF %i %s → %i, pesan Indonesia, pesan BFF tidak bocor', async (status, code, expected, msg) => {
    bffFetch.mockResolvedValue(fail(status, code));
    const res = await postSub(req('/api/fnb/orders', { method: 'POST', key: KEY, body }));
    expect(res.status).toBe(expected);
    expect(res.headers.get('cache-control')).toBe('no-store');
    const b = await res.json();
    expect(b.error.message).toMatch(msg);
    expect(JSON.stringify(b)).not.toContain('pesan internal BFF');
  });
  it('409 item_unavailable membawa product_ids yang sudah divalidasi', async () => {
    bffFetch.mockResolvedValue(fail(409, 'item_unavailable', { error: { code: 'item_unavailable', product_ids: [5, 'x', 5, 8] } }));
    const res = await postSub(req('/api/fnb/orders', { method: 'POST', key: KEY, body }));
    expect(res.status).toBe(409);
    expect((await res.json()).error).toMatchObject({ code: 'item_unavailable', product_ids: [5, 8] });
  });
  it('tamu & member sama-sama boleh mengirim; Bearer hanya bila cookie ada', async () => {
    bffFetch.mockResolvedValue(accepted);
    await postSub(req('/api/fnb/orders', { method: 'POST', key: KEY, body }));
    await postSub(req('/api/fnb/orders', { method: 'POST', key: KEY, body, cookie: SESSION.member }));
    expect(bffFetch.mock.calls[0]![1].token).toBeUndefined();
    expect(bffFetch.mock.calls[1]![1].token).toBe('tok');
  });
  it('tidak ada log berisi token meja atau catatan', async () => {
    const spies = logs();
    bffFetch.mockResolvedValue(fail(500, 'internal'));
    await postSub(req('/api/fnb/orders', { method: 'POST', key: KEY, body: { lines: [{ product_id: 5, qty: 1, note: 'rahasia-catatan' }] } }));
    for (const s of spies) expect(JSON.stringify(s.mock.calls)).not.toMatch(/fts1|rahasia-catatan/);
  });
});

describe('daftar & status kiriman', () => {
  it('GET daftar: banyak kiriman per sesi, terbaru di atas; no-store', async () => {
    bffFetch.mockResolvedValue({
      ok: true,
      status: 200,
      data: { items: [{ submission_id: SID, created_at: '2026-10-19T10:00:00Z', status: 'served', lines: [{ name: 'Kopi', qty: 1 }] }, { submission_id: '9b1d2c3e-4f5a-4b6c-8d7e-0f1a2b3c4d5e', created_at: '2026-10-19T10:20:00Z', status: 'preparing', lines: [], note: 'x' }] },
    });
    const res = await listSubs(req('/api/fnb/orders'));
    expect(res.status).toBe(200);
    const b = await res.json();
    expect(b.items.map((i: { status: string }) => i.status)).toEqual(['preparing', 'served']);
    expect(JSON.stringify(b)).not.toContain('"note"');
    expect(bffFetch.mock.calls[0]![0]).toBe('/v1/fnb/orders');
  });
  it('GET :id — bukan UUID → 404 tanpa BFF; 404 BFF → 404; sukses → status saja', async () => {
    expect((await getSub(req('/api/fnb/orders/abc'), params({ id: 'abc' }))).status).toBe(404);
    expect(bffFetch).not.toHaveBeenCalled();
    bffFetch.mockResolvedValue(fail(404, 'not_found'));
    expect((await getSub(req(`/api/fnb/orders/${SID}`), params({ id: SID }))).status).toBe(404);
    bffFetch.mockResolvedValue({ ok: true, status: 200, data: { submission_id: SID, status: 'ready', updated_at: '2026-10-19T10:30:00Z', internal: 1 } });
    const res = await getSub(req(`/api/fnb/orders/${SID.toUpperCase()}`), params({ id: SID.toUpperCase() }));
    expect(await res.json()).toEqual({ submission_id: SID, status: 'ready', updated_at: '2026-10-19T10:30:00Z' });
    expect(bffFetch.mock.calls[1]![0]).toBe(`/v1/fnb/orders/${SID}`);
  });
});

describe('klaim poin meja', () => {
  it('butuh login: tanpa cookie → 401 unauthenticated tanpa BFF (tamu tidak bisa klaim)', async () => {
    const res = await claim(req('/api/fnb/points-claim', { method: 'POST', body: {} }));
    expect(res.status).toBe(401);
    expect((await res.json()).error.code).toBe('unauthenticated');
    expect((await unclaim(req('/api/fnb/points-claim', { method: 'DELETE' }))).status).toBe(401);
    expect(bffFetch).not.toHaveBeenCalled();
  });
  it('POST: Bearer + token meja diteruskan; respons hanya { claimed: true }', async () => {
    bffFetch.mockResolvedValue({ ok: true, status: 200, data: { claimed: true, member_id: 'jangan-bocor' } });
    const res = await claim(req('/api/fnb/points-claim', { method: 'POST', body: {}, cookie: SESSION.member }));
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ claimed: true });
    const [path, opts] = bffFetch.mock.calls[0]!;
    expect(path).toBe('/v1/fnb/session/points-claim');
    expect(opts.method).toBe('POST');
    expect(opts.token).toBe('tok');
    expect(opts.headers['x-table-session']).toBe(TOKEN);
  });
  it('DELETE → 204; salah Origin → 403', async () => {
    bffFetch.mockResolvedValue({ ok: true, status: 204, data: null });
    const res = await unclaim(req('/api/fnb/points-claim', { method: 'DELETE', cookie: SESSION.member }));
    expect(res.status).toBe(204);
    expect(bffFetch.mock.calls[0]![1].method).toBe('DELETE');
    expect((await unclaim(req('/api/fnb/points-claim', { method: 'DELETE', cookie: SESSION.member, origin: 'https://evil.example' }))).status).toBe(403);
  });
  it.each([
    [409, 'points_already_claimed', 409, /diklaim oleh akun lain/],
    [409, 'account_deleting', 409, /penghapusan/],
    [422, 'points_unavailable', 422, /belum tersedia/],
    [409, 'table_session_closed', 409, /sudah ditutup/],
  ])('BFF %i %s → %i', async (status, code, expected, msg) => {
    bffFetch.mockResolvedValue(fail(status, code));
    const res = await claim(req('/api/fnb/points-claim', { method: 'POST', body: {}, cookie: SESSION.member }));
    expect(res.status).toBe(expected);
    expect((await res.json()).error.message).toMatch(msg);
  });
  it('401 BFF saat klaim → sesi login dihapus', async () => {
    bffFetch.mockResolvedValue(fail(401, 'unauthorized'));
    const res = await claim(req('/api/fnb/points-claim', { method: 'POST', body: {}, cookie: SESSION.member }));
    expect(res.status).toBe(401);
    expect(res.headers.get('set-cookie')).toMatch(/Max-Age=0/i);
  });
});

describe('POST /api/orders dengan points_redeem (§14.C.2)', () => {
  const base = { quote_id: QUOTE_ID, accept_total: 100000 };
  const post = (body: unknown) => postOrder(req('/api/orders', { method: 'POST', body, cookie: SESSION.member, table: null }));

  it('meneruskan hanya channel_code + points; Idempotency-Key tetap dari quote_id; accept_total tidak berubah', async () => {
    bffFetch.mockResolvedValue({ ok: true, status: 201, data: { order_id: SID, status: 'awaiting_payment' } });
    const res = await post({ ...base, points_redeem: [{ channel_code: 'PB', points: 300, value_idr: 99999 }] });
    expect(res.status).toBe(201);
    const opts = bffFetch.mock.calls[0]![1];
    expect(opts.json).toEqual({ ...base, points_redeem: [{ channel_code: 'PB', points: 300 }] });
    expect(opts.headers['idempotency-key']).toBe(checkoutIdempotencyKey(QUOTE_ID));
  });
  it('tanpa poin / array kosong → field dihilangkan (kompatibel dengan kontrak lama)', async () => {
    bffFetch.mockResolvedValue({ ok: true, status: 201, data: { order_id: SID, status: 'awaiting_payment' } });
    await post(base);
    await post({ ...base, points_redeem: [] });
    expect(bffFetch.mock.calls[0]![1].json).toEqual(base);
    expect(bffFetch.mock.calls[1]![1].json).toEqual(base);
  });
  it('bentuk salah → 400 tanpa BFF', async () => {
    for (const points_redeem of ['300', [{ channel_code: 'PB', points: 0 }], [{ channel_code: 'PB', points: 1.5 }], [{ channel_code: 'PB', points: 3 }, { channel_code: 'PB', points: 4 }], [{ points: 3 }]]) {
      const res = await post({ ...base, points_redeem });
      expect(res.status).toBe(400);
    }
    expect(bffFetch).not.toHaveBeenCalled();
  });
  it.each([
    [409, 'insufficient_points', 409, /Saldo poin tidak cukup/],
    [409, 'below_minimum', 409, /minimum penukaran/],
    [409, 'exceeds_max_pct', 409, /batas penukaran/],
    [422, 'points_unavailable', 422, /tanpa poin/],
    [422, 'points_cover_full_amount', 422, /Rp1/],
    [409, 'idempotency_conflict', 409, /sedang diproses/],
  ])('BFF %i %s → %i pesan Indonesia', async (status, code, expected, msg) => {
    bffFetch.mockResolvedValue(fail(status, code));
    const res = await post({ ...base, points_redeem: [{ channel_code: 'PB', points: 300 }] });
    expect(res.status).toBe(expected);
    const b = await res.json();
    expect(b.error.code).toBe(code);
    expect(b.error.message).toMatch(msg);
    expect(JSON.stringify(b)).not.toContain('pesan internal BFF');
  });
});
