/**
 * G18: konfirmasi usia saat login (W-19, Q-P3) dan hapus akun dengan pesanan online terbuka (W-18, Q-P18).
 * BFF di-mock; `server-only` di-stub.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { NextRequest } from 'next/server';

const { bffFetch } = vi.hoisted(() => ({ bffFetch: vi.fn() }));
vi.mock('server-only', () => ({}));
vi.mock('@/lib/bff', () => ({ bffFetch }));

import { POST as verify } from '@/app/api/auth/otp/verify/route';
import { DELETE } from '@/app/api/me/route';
import { AGE_CHECKBOX_LABEL, AGE_REQUIRED_MESSAGE, isAgeConfirmed, MIN_AGE } from '@/lib/auth-age';
import {
  DEFER_DEADLINE_PARAM,
  deletionStatusHref,
  isoDateOrNull,
  openOrdersExplanation,
  parseDeleteAccepted,
  parseOpenOrdersConflict,
} from '@/lib/dsar';

const HOST = 'efn.example.id';
const ID = '3f2b8c1e-9a4d-4c7e-8b21-5d6e7f8a9b0c';
const CHALLENGE = '9b1d2c3e-4f5a-4b6c-8d7e-0f1a2b3c4d5e';
const DEADLINE = '2026-10-24T03:00:00.000Z';

function verifyReq(body: Record<string, unknown>): NextRequest {
  return new NextRequest(`https://${HOST}/api/auth/otp/verify`, {
    method: 'POST',
    headers: { origin: `https://${HOST}`, host: HOST, 'content-type': 'application/json' },
    body: JSON.stringify(body),
  });
}

function deleteReq(body: Record<string, unknown>, cookie = 'efn_session=sess; efn_stepup=step'): NextRequest {
  return new NextRequest(`https://${HOST}/api/me`, {
    method: 'DELETE',
    headers: { origin: `https://${HOST}`, host: HOST, cookie, 'content-type': 'application/json' },
    body: JSON.stringify({ confirm: 'HAPUS', acknowledge_points: true, ...body }),
  });
}

const cookieNamed = (res: Response, name: string) => res.headers.getSetCookie().find((c) => c.startsWith(`${name}=`));

const openOrders409 = {
  ok: false,
  status: 409,
  code: 'open_online_orders',
  message: 'pesan BFF',
  body: { error: { code: 'open_online_orders', message: 'pesan BFF', open_parts: 2, max_defer_days: 14, confirm_param: 'confirm_open_orders' } },
};

beforeEach(() => {
  bffFetch.mockReset();
});

describe('konfirmasi usia (lib)', () => {
  it('hanya boolean true persis', () => {
    expect(isAgeConfirmed(true)).toBe(true);
    for (const v of [false, 'true', 1, null, undefined, {}]) expect(isAgeConfirmed(v)).toBe(false);
    expect(MIN_AGE).toBe(17);
    expect(AGE_CHECKBOX_LABEL).toBe('Saya berusia 17 tahun atau lebih');
    expect(AGE_REQUIRED_MESSAGE).toMatch(/17 tahun/);
  });
});

describe('POST /api/auth/otp/verify — age_confirmed', () => {
  it('tidak dicentang / bukan boolean -> 400 age_confirmation_required TANPA memanggil BFF', async () => {
    for (const age of [undefined, false, 'true', 1]) {
      const res = await verify(verifyReq({ challenge_id: CHALLENGE, code: '123456', age_confirmed: age }));
      expect(res.status).toBe(400);
      const body = await res.json();
      expect(body.error.code).toBe('age_confirmation_required');
      expect(body.error.message).toBe(AGE_REQUIRED_MESSAGE);
    }
    expect(bffFetch).not.toHaveBeenCalled();
  });

  it('dicentang -> diteruskan ke BFF sebagai age_confirmed: true; sukses set cookie sesi', async () => {
    bffFetch.mockResolvedValue({ ok: true, status: 200, data: { access_token: 'jwt', token_type: 'Bearer', expires_in: 3600 } });
    const res = await verify(verifyReq({ challenge_id: CHALLENGE, code: '123456', age_confirmed: true, privacy_consent: true }));
    expect(res.status).toBe(200);
    expect(await res.text()).not.toContain('jwt');
    const [path, opts] = bffFetch.mock.calls[0]!;
    expect(path).toBe('/v1/auth/otp/verify');
    expect(opts.json).toEqual({ challenge_id: CHALLENGE, code: '123456', age_confirmed: true, privacy_consent: { granted: true, version: 'privacy@2026-10-27' } });
    expect(cookieNamed(res, 'efn_session')).toMatch(/HttpOnly/i);
  });

  it('BFF 400 age_confirmation_required -> pesan Indonesia (bukan "kode tidak cocok")', async () => {
    bffFetch.mockResolvedValue({ ok: false, status: 400, code: 'age_confirmation_required', message: 'x', body: null });
    const res = await verify(verifyReq({ challenge_id: CHALLENGE, code: '123456', age_confirmed: true, privacy_consent: true }));
    expect(res.status).toBe(400);
    const body = await res.json();
    expect(body.error.code).toBe('age_confirmation_required');
    expect(body.reason).toBeUndefined();
  });

  it('OTP salah tetap dipetakan seperti sebelumnya', async () => {
    bffFetch.mockResolvedValue({ ok: false, status: 400, code: 'http_400', message: 'x', body: { ok: false, reason: 'invalid', attempts_left: 2 } });
    const res = await verify(verifyReq({ challenge_id: CHALLENGE, code: '123456', age_confirmed: true, privacy_consent: true }));
    expect(await res.json()).toEqual({ ok: false, reason: 'invalid', attempts_left: 2 });
  });
});

describe('POST /api/auth/otp/verify — G27 §18.3a privacy_consent', () => {
  it('tidak dicentang -> 400 privacy_consent_required TANPA memanggil BFF', async () => {
    for (const pc of [undefined, false, 'true']) {
      const res = await verify(verifyReq({ challenge_id: CHALLENGE, code: '123456', age_confirmed: true, privacy_consent: pc }));
      expect(res.status).toBe(400);
      expect((await res.json()).error.code).toBe('privacy_consent_required');
    }
    expect(bffFetch).not.toHaveBeenCalled();
  });
  it('promo opsional ikut diteruskan hanya bila dicentang', async () => {
    bffFetch.mockResolvedValue({ ok: true, status: 200, data: { access_token: 'jwt', token_type: 'Bearer', expires_in: 3600 } });
    await verify(verifyReq({ challenge_id: CHALLENGE, code: '123456', age_confirmed: true, privacy_consent: true, marketing_consent: true }));
    expect(bffFetch.mock.calls[0]![1].json.marketing_consent).toEqual({ granted: true, version: 'marketing@2026-10-27' });
  });
});

describe('hapus akun dengan pesanan online terbuka (lib)', () => {
  it('parseOpenOrdersConflict: allowlist + plafon 14 hari', () => {
    expect(parseOpenOrdersConflict(openOrders409.body)).toEqual({ open_parts: 2, max_defer_days: 14 });
    expect(parseOpenOrdersConflict({ error: { code: 'open_online_orders', open_parts: 1, max_defer_days: 30 } })).toEqual({ open_parts: 1, max_defer_days: 14 });
    expect(parseOpenOrdersConflict({ error: { code: 'open_online_orders', max_defer_days: 7 } })).toEqual({ open_parts: 0, max_defer_days: 7 });
    expect(parseOpenOrdersConflict({ error: { code: 'other' } })).toBeNull();
    expect(parseOpenOrdersConflict(null)).toBeNull();
  });
  it('penjelasan menyebut pesanan terbuka, kode ambil, dan batas hari', () => {
    const t = openOrdersExplanation({ open_parts: 2, max_defer_days: 14 });
    expect(t).toMatch(/2 pesanan online/);
    expect(t).toMatch(/kode ambil tidak lagi tampil/);
    expect(t).toMatch(/paling lambat 14 hari/);
  });
  it('parseDeleteAccepted membawa open_online_orders & erasure_deadline (tervalidasi)', () => {
    const base = { request_id: ID, scheduled_at: '2026-10-17T03:00:00.000Z', points_forfeited: 5 };
    expect(parseDeleteAccepted({ ...base, open_online_orders: 2, erasure_deadline: DEADLINE })).toMatchObject({ open_online_orders: 2, erasure_deadline: DEADLINE });
    expect(parseDeleteAccepted({ ...base, open_online_orders: 0, erasure_deadline: DEADLINE })).toMatchObject({ open_online_orders: 0, erasure_deadline: null });
    expect(parseDeleteAccepted({ ...base, open_online_orders: 1, erasure_deadline: '<script>' })).toMatchObject({ erasure_deadline: null });
    expect(parseDeleteAccepted(base)).toMatchObject({ open_online_orders: 0, erasure_deadline: null });
  });
  it('isoDateOrNull & tautan halaman status', () => {
    expect(isoDateOrNull(DEADLINE)).toBe(DEADLINE);
    expect(isoDateOrNull('2026-10-24')).toBeNull();
    expect(isoDateOrNull('2026-13-45T99:99:99Z')).toBeNull();
    expect(deletionStatusHref(ID, DEADLINE)).toBe(`/akun/penghapusan/${ID}?${DEFER_DEADLINE_PARAM}=${encodeURIComponent(DEADLINE)}`);
    expect(deletionStatusHref(ID, null)).toBe(`/akun/penghapusan/${ID}`);
    expect(deletionStatusHref(ID, 'javascript:alert(1)')).toBe(`/akun/penghapusan/${ID}`);
  });
});

describe('DELETE /api/me — W-18', () => {
  it('409 open_online_orders: penjelasan frontend + angka; step-up TIDAK dihapus, sesi tetap', async () => {
    bffFetch.mockResolvedValue(openOrders409);
    const res = await DELETE(deleteReq({}));
    expect(res.status).toBe(409);
    expect(res.headers.get('cache-control')).toBe('no-store');
    const body = await res.json();
    expect(body.error.code).toBe('open_online_orders');
    expect(body.error.open_parts).toBe(2);
    expect(body.error.max_defer_days).toBe(14);
    expect(body.error.message).toMatch(/paling lambat 14 hari/);
    expect(body.error.message).not.toBe('pesan BFF');
    expect(cookieNamed(res, 'efn_stepup')).toBeUndefined();
    expect(cookieNamed(res, 'efn_session')).toBeUndefined();
    expect(bffFetch.mock.calls[0]![0]).toBe('/v1/me');
  });

  it('konfirmasi: ?confirm_open_orders=true + token step-up yang SAMA; 202 membawa erasure_deadline', async () => {
    bffFetch.mockResolvedValue({
      ok: true,
      status: 202,
      data: { request_id: ID, scheduled_at: '2026-10-17T03:00:00.000Z', points_forfeited: 0, receipt_token: 'x', open_online_orders: 2, erasure_deadline: DEADLINE },
    });
    const res = await DELETE(deleteReq({ confirm_open_orders: true }));
    expect(res.status).toBe(202);
    const body = await res.json();
    expect(body).toMatchObject({ request_id: ID, open_online_orders: 2, erasure_deadline: DEADLINE });
    const [path, opts] = bffFetch.mock.calls[0]!;
    expect(path).toBe('/v1/me?confirm_open_orders=true');
    expect(opts.headers).toEqual({ 'x-step-up-token': 'step' });
    expect(cookieNamed(res, 'efn_stepup')).toMatch(/Max-Age=0/i);
  });

  it('confirm_open_orders bukan boolean true -> tidak dikirim ke BFF', async () => {
    bffFetch.mockResolvedValue(openOrders409);
    for (const v of ['true', 1, false]) {
      await DELETE(deleteReq({ confirm_open_orders: v }));
    }
    expect(bffFetch.mock.calls.map((c) => c[0])).toEqual(['/v1/me', '/v1/me', '/v1/me']);
  });

  it('409 lain tetap diteruskan apa adanya', async () => {
    bffFetch.mockResolvedValue({ ok: false, status: 409, code: 'already_requested', message: 'Sudah ada permintaan.', body: null });
    const res = await DELETE(deleteReq({}));
    expect(res.status).toBe(409);
    expect((await res.json()).error.code).toBe('already_requested');
  });
});
