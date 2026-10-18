/**
 * L-22 (G18): `open_online_orders` + `erasure_deadline` di status DSAR (jalur receipt & `GET /v1/me/requests`).
 * Allowlist route handler, cadangan `?batas=` hanya untuk BFF lama. BFF di-mock.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { NextRequest } from 'next/server';

const { bffFetch } = vi.hoisted(() => ({ bffFetch: vi.fn() }));
vi.mock('server-only', () => ({}));
vi.mock('@/lib/bff', () => ({ bffFetch }));

import { GET as getStatus } from '@/app/api/dsar/requests/[id]/route';
import { GET as getMyRequests } from '@/app/api/me/requests/route';
import { parseDsarRequests } from '@/lib/dsar';
import { openOrdersCount, parseDeletionStatus, resolveErasureDeadline } from '@/lib/dsar-receipt';

const HOST = 'efn.example.id';
const ID = '3f2b8c1e-9a4d-4c7e-8b21-5d6e7f8a9b0c';
const RECEIPT = `v1.${Math.floor(Date.now() / 1000) + 30 * 86_400}.${'Q'.repeat(43)}`;
const DEADLINE = '2026-10-24T03:00:00.000Z';
const QUERY = '2026-10-30T03:00:00.000Z';

const base = {
  id: ID,
  type: 'delete',
  status: 'scheduled',
  requested_at: '2026-10-10T03:00:00.000Z',
  scheduled_at: '2026-10-17T03:00:00.000Z',
  completed_at: null,
};

beforeEach(() => {
  bffFetch.mockReset();
});

describe('parser', () => {
  it('openOrdersCount: hanya bilangan bulat ≥ 0', () => {
    expect(openOrdersCount(2)).toBe(2);
    for (const v of [-1, 1.5, '2', null, undefined, Number.NaN]) expect(openOrdersCount(v)).toBe(0);
    expect(openOrdersCount(1e9)).toBe(999);
  });
  it('parseDeletionStatus: field L-22 ada / null / rusak / tidak ada', () => {
    expect(parseDeletionStatus({ ...base, open_online_orders: 2, erasure_deadline: DEADLINE }, ID)).toMatchObject({
      open_online_orders: 2,
      erasure_deadline: DEADLINE,
    });
    const none = parseDeletionStatus({ ...base, open_online_orders: 0, erasure_deadline: null }, ID)!;
    expect(none.erasure_deadline).toBeNull();
    expect(parseDeletionStatus({ ...base, open_online_orders: 1, erasure_deadline: '<b>x</b>' }, ID)!.erasure_deadline).toBeNull();
    expect('erasure_deadline' in parseDeletionStatus(base, ID)!).toBe(false);
  });
  it('parseDsarRequests membawa field L-22 (allowlist /api/me/requests)', () => {
    const [r] = parseDsarRequests([{ ...base, open_online_orders: 3, erasure_deadline: DEADLINE, member_hash: 'x' }]);
    expect(r).toMatchObject({ open_online_orders: 3, erasure_deadline: DEADLINE });
    expect(r).not.toHaveProperty('member_hash');
    const [old] = parseDsarRequests([base]);
    expect(old).toMatchObject({ open_online_orders: 0, erasure_deadline: null });
  });
});

describe('resolveErasureDeadline (sumber server utama, ?batas= cadangan)', () => {
  it('field BFF ada -> dipakai, cadangan diabaikan', () => {
    expect(resolveErasureDeadline({ status: 'scheduled', erasure_deadline: DEADLINE }, null, QUERY)).toBe(DEADLINE);
  });
  it('BFF menyatakan null (pesanan sudah selesai) -> tidak menampilkan tanggal basi dari query', () => {
    expect(resolveErasureDeadline({ status: 'scheduled', erasure_deadline: null }, null, QUERY)).toBeNull();
  });
  it('BFF lama (field tidak ada) -> item sesi, lalu ?batas=', () => {
    expect(resolveErasureDeadline({ status: 'scheduled' }, DEADLINE, QUERY)).toBe(DEADLINE);
    expect(resolveErasureDeadline({ status: 'processing' }, null, QUERY)).toBe(QUERY);
    expect(resolveErasureDeadline({ status: 'scheduled' }, null, null)).toBeNull();
  });
  it('status final -> tidak ada batas', () => {
    expect(resolveErasureDeadline({ status: 'completed', erasure_deadline: DEADLINE }, DEADLINE, QUERY)).toBeNull();
    expect(resolveErasureDeadline({ status: 'cancelled' }, DEADLINE, QUERY)).toBeNull();
  });
});

describe('route handler allowlist', () => {
  it('GET /api/dsar/requests/:id meneruskan field L-22, membuang field lain', async () => {
    bffFetch.mockResolvedValue({
      ok: true,
      status: 200,
      data: { ...base, open_online_orders: 2, erasure_deadline: DEADLINE, part_ids: ['p1'], member_id: 'm' },
    });
    const req = new NextRequest(`https://${HOST}/api/dsar/requests/${ID}`, {
      headers: { host: HOST, 'sec-fetch-site': 'same-origin', cookie: `efn_dsar_receipt=${RECEIPT}` },
    });
    const res = await getStatus(req, { params: Promise.resolve({ id: ID }) });
    expect(res.status).toBe(200);
    expect(res.headers.get('cache-control')).toBe('no-store');
    expect(await res.json()).toEqual({
      id: ID,
      status: 'scheduled',
      requested_at: base.requested_at,
      scheduled_at: base.scheduled_at,
      completed_at: null,
      open_online_orders: 2,
      erasure_deadline: DEADLINE,
    });
  });

  it('GET /api/me/requests meneruskan field L-22, membuang field lain', async () => {
    bffFetch.mockResolvedValue({
      ok: true,
      status: 200,
      data: [{ ...base, open_online_orders: 1, erasure_deadline: DEADLINE, part_ids: ['p1'] }],
    });
    const req = new NextRequest(`https://${HOST}/api/me/requests`, {
      headers: { host: HOST, 'sec-fetch-site': 'same-origin', cookie: 'efn_session=sess' },
    });
    const res = await getMyRequests(req);
    expect(res.status).toBe(200);
    const [item] = await res.json();
    expect(item).toMatchObject({ id: ID, open_online_orders: 1, erasure_deadline: DEADLINE });
    expect(item).not.toHaveProperty('part_ids');
  });
});
