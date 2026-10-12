/**
 * Route Handler R6/SEC-40: DELETE /api/me (cookie receipt + step-up sekali pakai) dan GET /api/dsar/requests/:id.
 * BFF di-mock; `server-only` di-stub (modul server dimuat di lingkungan test node).
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { NextRequest } from 'next/server';

const { bffFetch } = vi.hoisted(() => ({ bffFetch: vi.fn() }));
vi.mock('server-only', () => ({}));
vi.mock('@/lib/bff', () => ({ bffFetch }));

import { DELETE } from '@/app/api/me/route';
import { GET } from '@/app/api/dsar/requests/[id]/route';

const HOST = 'efn.example.id';
const ID = '3f2b8c1e-9a4d-4c7e-8b21-5d6e7f8a9b0c';
const RECEIPT = `v1.${Math.floor(Date.now() / 1000) + 30 * 86_400}.${'Q'.repeat(43)}`;

function deleteReq(cookie = 'efn_session=sess; efn_stepup=step'): NextRequest {
  return new NextRequest(`https://${HOST}/api/me`, {
    method: 'DELETE',
    headers: { origin: `https://${HOST}`, host: HOST, cookie, 'content-type': 'application/json' },
    body: JSON.stringify({ confirm: 'HAPUS', acknowledge_points: true }),
  });
}

function statusReq(id: string, cookie?: string, site = 'same-origin'): [NextRequest, { params: Promise<{ id: string }> }] {
  const headers: Record<string, string> = { host: HOST, 'sec-fetch-site': site };
  if (cookie) headers.cookie = cookie;
  return [new NextRequest(`https://${HOST}/api/dsar/requests/${id}`, { headers }), { params: Promise.resolve({ id }) }];
}

function setCookies(res: Response): string[] {
  return res.headers.getSetCookie();
}

function cookieNamed(res: Response, name: string): string | undefined {
  return setCookies(res).find((c) => c.startsWith(`${name}=`));
}

beforeEach(() => {
  bffFetch.mockReset();
});

describe('DELETE /api/me', () => {
  it('202: receipt ke cookie HttpOnly Path=/api/dsar, tidak ke body; step-up & sesi dihapus', async () => {
    bffFetch.mockResolvedValue({
      ok: true,
      status: 202,
      data: { request_id: ID, scheduled_at: '2026-10-13T03:00:00.000Z', points_forfeited: 120, receipt_token: RECEIPT },
    });
    const res = await DELETE(deleteReq());
    expect(res.status).toBe(202);
    const text = await res.text();
    expect(text).not.toContain(RECEIPT);
    expect(JSON.parse(text)).toEqual({
      request_id: ID,
      scheduled_at: '2026-10-13T03:00:00.000Z',
      points_forfeited: 120,
      open_online_orders: 0,
      erasure_deadline: null,
      status_available: true,
    });

    const receipt = cookieNamed(res, 'efn_dsar_receipt')!;
    expect(receipt).toContain(`efn_dsar_receipt=${RECEIPT}`);
    expect(receipt).toMatch(/HttpOnly/i);
    expect(receipt).toMatch(/SameSite=strict/i);
    expect(receipt).toMatch(/Path=\/api\/dsar/);
    const maxAge = Number(/Max-Age=(\d+)/i.exec(receipt)![1]);
    expect(maxAge).toBeGreaterThan(29 * 86_400);
    expect(maxAge).toBeLessThanOrEqual(30 * 86_400);

    expect(cookieNamed(res, 'efn_stepup')).toMatch(/Max-Age=0/i);
    expect(cookieNamed(res, 'efn_session')).toMatch(/Max-Age=0/i);

    const [path, opts] = bffFetch.mock.calls[0]!;
    expect(path).toBe('/v1/me');
    expect(opts).toMatchObject({ method: 'DELETE', token: 'sess', headers: { 'x-step-up-token': 'step' } });
  });

  it('receipt tidak ada/rusak -> 202 tanpa cookie receipt, status_available=false', async () => {
    bffFetch.mockResolvedValue({
      ok: true,
      status: 202,
      data: { request_id: ID, scheduled_at: null, points_forfeited: 0, receipt_token: 'bukan token' },
    });
    const res = await DELETE(deleteReq());
    expect(res.status).toBe(202);
    expect((await res.json()).status_available).toBe(false);
    expect(cookieNamed(res, 'efn_dsar_receipt')).toBeUndefined();
    expect(cookieNamed(res, 'efn_stepup')).toMatch(/Max-Age=0/i);
  });

  it('SEC-40: 403 step_up_invalid (step-up dipakai ulang) -> 403 step_up_required + cookie step-up dihapus', async () => {
    bffFetch.mockResolvedValue({ ok: false, status: 403, code: 'step_up_invalid', message: 'x', body: null });
    const res = await DELETE(deleteReq());
    expect(res.status).toBe(403);
    const body = await res.json();
    expect(body.error.code).toBe('step_up_required');
    expect(body.error.message).toMatch(/satu kali/);
    expect(cookieNamed(res, 'efn_stepup')).toMatch(/Max-Age=0/i);
    expect(cookieNamed(res, 'efn_dsar_receipt')).toBeUndefined();
  });

  it('tanpa cookie step-up -> 403 tanpa memanggil BFF', async () => {
    const res = await DELETE(deleteReq('efn_session=sess'));
    expect(res.status).toBe(403);
    expect(bffFetch).not.toHaveBeenCalled();
  });
});

describe('GET /api/dsar/requests/:id', () => {
  it('meneruskan cookie sebagai X-DSAR-Receipt tanpa Bearer; respons allowlist', async () => {
    bffFetch.mockResolvedValue({
      ok: true,
      status: 200,
      data: {
        id: ID,
        type: 'delete',
        status: 'processing',
        requested_at: '2026-10-06T03:00:00.000Z',
        scheduled_at: '2026-10-13T03:00:00.000Z',
        completed_at: null,
        extra: 'dibuang',
      },
    });
    const res = await GET(...statusReq(ID, `efn_dsar_receipt=${RECEIPT}; efn_session=sess`));
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body).toEqual({
      id: ID,
      status: 'processing',
      requested_at: '2026-10-06T03:00:00.000Z',
      scheduled_at: '2026-10-13T03:00:00.000Z',
      completed_at: null,
    });
    expect(res.headers.get('cache-control')).toBe('no-store');
    const [path, opts] = bffFetch.mock.calls[0]!;
    expect(path).toBe(`/v1/dsar/requests/${ID}`);
    expect(opts.headers).toEqual({ 'x-dsar-receipt': RECEIPT });
    expect(opts.token).toBeUndefined();
  });

  it('BFF 404 -> 404 generik', async () => {
    bffFetch.mockResolvedValue({ ok: false, status: 404, code: 'not_found', message: 'Not found', body: null });
    const res = await GET(...statusReq(ID, `efn_dsar_receipt=${RECEIPT}`));
    expect(res.status).toBe(404);
    const body = await res.json();
    expect(body.error.code).toBe('not_found');
    expect(body.error.message).toMatch(/tidak ditemukan/);
  });

  it('tanpa cookie / cookie rusak / id bukan UUID -> 404 tanpa memanggil BFF', async () => {
    for (const [id, cookie] of [
      [ID, undefined],
      [ID, 'efn_dsar_receipt=sampah'],
      ['bukan-uuid', `efn_dsar_receipt=${RECEIPT}`],
    ] as const) {
      const res = await GET(...statusReq(id, cookie));
      expect(res.status).toBe(404);
    }
    expect(bffFetch).not.toHaveBeenCalled();
  });

  it('lintas situs -> 403', async () => {
    const res = await GET(...statusReq(ID, `efn_dsar_receipt=${RECEIPT}`, 'cross-site'));
    expect(res.status).toBe(403);
    expect(bffFetch).not.toHaveBeenCalled();
  });

  it('429 diteruskan, 5xx -> 502 generik, respons rusak -> 502', async () => {
    bffFetch.mockResolvedValueOnce({ ok: false, status: 429, code: 'rate_limited', message: 'x', body: null });
    expect((await GET(...statusReq(ID, `efn_dsar_receipt=${RECEIPT}`))).status).toBe(429);
    bffFetch.mockResolvedValueOnce({ ok: false, status: 503, code: 'bff_unavailable', message: 'x', body: null });
    expect((await GET(...statusReq(ID, `efn_dsar_receipt=${RECEIPT}`))).status).toBe(502);
    bffFetch.mockResolvedValueOnce({ ok: true, status: 200, data: { id: ID, type: 'access', status: 'completed' } });
    expect((await GET(...statusReq(ID, `efn_dsar_receipt=${RECEIPT}`))).status).toBe(502);
  });
});
