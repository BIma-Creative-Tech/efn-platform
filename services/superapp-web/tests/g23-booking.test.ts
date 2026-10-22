/**
 * G23 web (docs/04 §15.4/§15.5/§15.7): mode pesan, slot pre-order (keranjang), landing Super-App, Route Handler
 * `/api/booking/**` dan `/api/catalog/slots` (BFF di-mock).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { NextRequest } from 'next/server';

const { bffFetch, bffBaseUrl } = vi.hoisted(() => ({ bffFetch: vi.fn(), bffBaseUrl: vi.fn() }));
vi.mock('server-only', () => ({}));
vi.mock('@/lib/bff', () => ({ bffFetch, bffBaseUrl }));

import { GET as getSlots } from '@/app/api/catalog/slots/route';
import { GET as getOutlets } from '@/app/api/booking/outlets/route';
import { GET as getAvailability } from '@/app/api/booking/availability/route';
import { GET as listReservations, POST as createReservation } from '@/app/api/booking/reservations/route';
import { GET as getReservation } from '@/app/api/booking/reservations/[id]/route';
import { POST as cancelReservation } from '@/app/api/booking/reservations/[id]/cancel/route';
import { BRANDS } from '@/lib/brands';
import { parseQuote, sanitizeCart, setOutlet, setPickupSlot, type CartGroup } from '@/lib/cart';
import type { CatalogChannel } from '@/lib/catalog';
import { hasScheduledOutlet, orderModes, parseModeParam } from '@/lib/order-modes';
import { parseOrderDetail } from '@/lib/orders';
import { parseSlotDay, remainingLabel, slotRangeLabel, slotsQuery } from '@/lib/slots';
import { brandShopHref, brandSiteUrl, groupChannelsByBrand } from '@/lib/superapp';

const HOST = 'app.efn.example';
const ID = '3f2b8c1e-9a4d-4c7e-8b21-5d6e7f8a9b0c';
const SLOT = '2026-10-25T03:00:00Z';

function req(path: string, init: { method?: string; body?: unknown; cookie?: string | null; origin?: string | null; headers?: Record<string, string> } = {}) {
  const headers: Record<string, string> = { host: HOST, ...(init.headers ?? {}) };
  if (init.cookie !== null) headers.cookie = init.cookie ?? 'efn_session=tok';
  if (init.origin !== null && init.method && init.method !== 'GET') headers.origin = init.origin ?? `https://${HOST}`;
  if (init.body !== undefined) headers['content-type'] = 'application/json';
  return new NextRequest(`https://${HOST}${path}`, {
    method: init.method ?? 'GET',
    headers,
    body: init.body === undefined ? undefined : typeof init.body === 'string' ? init.body : JSON.stringify(init.body),
  });
}
const params = <T extends Record<string, string>>(p: T) => ({ params: Promise.resolve(p) });
const fail = (status: number, code: string) => ({ ok: false, status, code, message: 'pesan internal BFF', body: null });

function channel(code: string, company: string, over: Partial<CatalogChannel> = {}): CatalogChannel {
  return {
    channel_code: code,
    company_code: company,
    name: code,
    brand_code: null,
    payment_modes: ['qris'],
    business_type: company === 'PB' || company === 'BCF' ? 'fnb' : 'retail',
    loyalty: null,
    fulfilment: { pickup: true, delivery: false },
    outlets: [{ outlet_code: 'TOKO-01', public_name: 'Toko', pickup_enabled: true, prep_minutes: 15 }],
    ...over,
  };
}

beforeEach(() => bffFetch.mockReset());
afterEach(() => vi.restoreAllMocks());

describe('mode pesan', () => {
  it('brand F&B: ambil, makan di tempat, reservasi, pesan-antar nonaktif', () => {
    const modes = orderModes({ brand: BRANDS.parisbaguette, channels: [channel('PB', 'PB')] });
    expect(modes.map((m) => m.id)).toEqual(['pickup', 'dinein', 'reservation', 'delivery']);
    const delivery = modes.find((m) => m.id === 'delivery')!;
    expect(delivery.href).toBeNull();
    expect(delivery.badge).toBe('Segera hadir');
    expect(modes.find((m) => m.id === 'dinein')!.href).toBe('/meja/cara');
  });
  it('ritel (GrandLucky): tanpa makan di tempat & reservasi', () => {
    expect(orderModes({ brand: BRANDS.grandlucky, channels: [channel('GL', 'GL')] }).map((m) => m.id)).toEqual(['pickup', 'delivery']);
  });
  it('pre-order hanya bila ada outlet slot_enabled; tautan ke kanal ber-slot', () => {
    const plain = [channel('GL', 'GL'), channel('PB', 'PB')];
    expect(hasScheduledOutlet(plain)).toBe(false);
    const sched = [channel('GL', 'GL'), channel('PB', 'PB', { outlets: [{ outlet_code: 'PB-01', public_name: 'PB', pickup_enabled: true, prep_minutes: 10, slot_enabled: true, slot_minutes: 60 }] })];
    const modes = orderModes({ brand: BRANDS.efn, channels: sched });
    expect(modes.find((m) => m.id === 'preorder')!.href).toBe('/belanja?channel=PB&mode=preorder');
    expect(parseModeParam('preorder')).toBe('preorder');
    expect(parseModeParam('delivery')).toBeNull();
  });
  it('katalog gagal (null): mode ambil tetap ditawarkan', () => {
    expect(orderModes({ brand: BRANDS.bacha, channels: null })[0]!.id).toBe('pickup');
  });
});

describe('slot pre-order (keranjang)', () => {
  const g: CartGroup = { channel_code: 'PB', outlet_code: 'TOKO-01', fulfilment: 'pickup', lines: [{ product_id: 1, qty: 1 }] };
  it('sanitizeCart: pickup_slot dipertahankan bila valid, dibuang bila salah bentuk/delivery', () => {
    expect(sanitizeCart({ version: 1, groups: [{ ...g, pickup_slot: SLOT }] })!.groups[0]!.pickup_slot).toBe(SLOT);
    expect(sanitizeCart({ version: 1, groups: [{ ...g, pickup_slot: '2026-10-25 10:00' }] })!.groups[0]).not.toHaveProperty('pickup_slot');
    expect(sanitizeCart({ version: 1, groups: [{ ...g, fulfilment: 'delivery', pickup_slot: SLOT }] })!.groups[0]).not.toHaveProperty('pickup_slot');
    expect(sanitizeCart({ version: 1, groups: [g] })!.groups[0]).not.toHaveProperty('pickup_slot');
  });
  it('setPickupSlot pasang/hapus; ganti gerai menghapus slot', () => {
    const withSlot = setPickupSlot([g], 'PB', SLOT);
    expect(withSlot[0]!.pickup_slot).toBe(SLOT);
    expect(setPickupSlot(withSlot, 'PB', null)[0]).not.toHaveProperty('pickup_slot');
    expect(setPickupSlot([g], 'PB', 'besok')[0]).not.toHaveProperty('pickup_slot');
    expect(setOutlet(withSlot, 'PB', 'TOKO-02')[0]).toEqual({ ...g, outlet_code: 'TOKO-02' });
  });
  it('parseSlotDay: allowlist, urut, slot rusak dibuang', () => {
    const d = parseSlotDay({
      outlet_code: 'TOKO-01',
      date: '2026-10-25',
      timezone: 'Asia/Jakarta',
      slot_minutes: 60,
      slots: [
        { start_at: '2026-10-25T04:00:00Z', end_at: '2026-10-25T05:00:00Z', remaining: 0 },
        { start_at: SLOT, end_at: '2026-10-25T04:00:00Z', remaining: 2, secret: 1 },
        { start_at: 'x', end_at: 'y', remaining: 1 },
      ],
    })!;
    expect(d.slots.map((s) => s.start_at)).toEqual([SLOT, '2026-10-25T04:00:00Z']);
    expect(JSON.stringify(d)).not.toContain('secret');
    expect(slotRangeLabel(d.slots[0]!, d.timezone)).toBe('10.00–11.00');
    expect(remainingLabel(0)).toBe('Penuh');
    expect(remainingLabel(2)).toBe('Sisa 2');
    expect(remainingLabel(10)).toBe('Tersedia');
    expect(parseSlotDay({ outlet_code: 'TOKO-01', date: '2026-10-25', slot_minutes: 45, slots: [] })).toBeNull();
    expect(slotsQuery('PB', 'TOKO-01', '2026-02-30')).toBeNull();
  });
  it('quote & detail pesanan membawa pickup_slot', () => {
    const q = parseQuote({
      quote_id: ID,
      amount_payable_total: 10000,
      groups: [{ channel_code: 'PB', amount_payable: 10000, lines: [], issues: [{ product_id: null, code: 'slot_unavailable' }], pickup_slot: SLOT }],
    })!;
    expect(q.groups[0]!.pickup_slot).toBe(SLOT);
    expect(q.groups[0]!.issues[0]!.code).toBe('slot_unavailable');
    const o = parseOrderDetail({
      order_id: ID,
      status: 'paid',
      created_at: SLOT,
      expires_at: SLOT,
      amount_payable: 1,
      parts: [{ channel_code: 'PB', status: 'confirmed', pickup_slot: SLOT, lines: [] }],
    });
    expect(o?.parts[0]?.pickup_slot).toBe(SLOT);
  });
});

describe('landing Super-App', () => {
  it('kanal dikelompokkan per brand (Bacha = BCR + BCF)', () => {
    const groups = groupChannelsByBrand([channel('GL', 'GL'), channel('BCR', 'BCR'), channel('BCF', 'BCF'), channel('PB', 'PB')]);
    expect(groups.map((g) => [g.brand.id, g.channels.map((c) => c.channel_code)])).toEqual([
      ['parisbaguette', ['PB']],
      ['bacha', ['BCR', 'BCF']],
      ['grandlucky', ['GL']],
    ]);
    expect(brandShopHref(groups[1]!)).toBe('/belanja?channel=BCR');
  });
  it('situs brand dari host app.; host lain -> null', () => {
    expect(brandSiteUrl('app.efn.local', BRANDS.parisbaguette)).toBe('https://parisbaguette.efn.local/');
    expect(brandSiteUrl('APP.erarasa.id:443', BRANDS.bacha)).toBe('https://bacha.erarasa.id/');
    expect(brandSiteUrl('localhost', BRANDS.bacha)).toBeNull();
    expect(brandSiteUrl('evil.com/app.x', BRANDS.bacha)).toBeNull();
  });
  it('kontras palet landing holding gelap ≥ 4,5:1', () => {
    const lum = (hex: string) => {
      const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255).map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
      return 0.2126 * r! + 0.7152 * g! + 0.0722 * b!;
    };
    const ratio = (a: string, b: string) => {
      const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p);
      return (x! + 0.05) / (y! + 0.05);
    };
    for (const fg of ['#ffffff', '#c9dcd6', '#f2a900']) {
      expect(ratio(fg, '#0b3b31')).toBeGreaterThanOrEqual(4.5);
      expect(ratio(fg, '#0f4a3e')).toBeGreaterThanOrEqual(4.5);
    }
    expect(ratio('#0b3b31', '#f2a900')).toBeGreaterThanOrEqual(4.5); // teks tombol utama
  });
});

describe('Route Handler slot & reservasi', () => {
  it('slots: publik, query dibangun ulang, allowlist, cache 30 dtk', async () => {
    bffFetch.mockResolvedValue({ ok: true, status: 200, data: { outlet_code: 'TOKO-01', date: '2026-10-25', timezone: 'Asia/Jakarta', slot_minutes: 60, slots: [] } });
    const res = await getSlots(req('/api/catalog/slots?channel=PB&outlet=TOKO-01&date=2026-10-25&member=1', { cookie: null }));
    expect(res.status).toBe(200);
    expect(res.headers.get('cache-control')).toBe('public, max-age=30');
    expect(bffFetch.mock.calls[0]![0]).toBe('/v1/catalog/slots?channel=PB&outlet=TOKO-01&date=2026-10-25');
    expect((await getSlots(req('/api/catalog/slots?channel=pb&outlet=TOKO-01&date=2026-10-25'))).status).toBe(400);
  });
  it('outlets: filter brand, kill switch -> 503 booking_unavailable', async () => {
    bffFetch.mockResolvedValue({
      ok: true,
      status: 200,
      data: { items: [{ outlet_code: 'PB-01', company_code: 'PB', channel_code: 'PB', public_name: 'PB SCBD' }, { outlet_code: 'BC-01', company_code: 'BCF', channel_code: 'BCF', public_name: 'Bacha' }] },
    });
    const res = await getOutlets(req('/api/booking/outlets', { headers: { 'x-efn-brand': 'parisbaguette' } }));
    expect((await res.json()).items.map((o: { outlet_code: string }) => o.outlet_code)).toEqual(['PB-01']);
    expect(bffFetch.mock.calls[0]![0]).toBe('/v1/booking/outlets?brand=PB');
    expect(bffFetch.mock.calls[0]![1].token).toBeUndefined();
    bffFetch.mockResolvedValue(fail(503, 'booking_unavailable'));
    const off = await getOutlets(req('/api/booking/outlets'));
    expect(off.status).toBe(503);
    expect((await off.json()).error.code).toBe('booking_unavailable');
  });
  it('availability: validasi input sebelum BFF, galat bisnis diteruskan dengan pesan Indonesia', async () => {
    expect((await getAvailability(req('/api/booking/availability?outlet=PB-01&date=2026-13-01&party=2'))).status).toBe(400);
    expect((await getAvailability(req('/api/booking/availability?outlet=PB-01&date=2026-10-25&party=0'))).status).toBe(400);
    expect(bffFetch).not.toHaveBeenCalled();
    bffFetch.mockResolvedValue(fail(409, 'party_size'));
    const res = await getAvailability(req('/api/booking/availability?outlet=PB-01&date=2026-10-25&party=20'));
    expect(res.status).toBe(409);
    expect((await res.json()).error.message).toMatch(/Jumlah tamu/);
  });
  it('buat: wajib login + same-origin + Idempotency-Key; body di-allowlist; respons no-store', async () => {
    const body = { outlet_code: 'PB-01', start_at: SLOT, party_size: 2, guest_label: ' Rina ', table_id: 9, member_id: 'x' };
    const key = 'k'.repeat(20);
    expect((await createReservation(req('/api/booking/reservations', { method: 'POST', body, cookie: null, headers: { 'idempotency-key': key } }))).status).toBe(401);
    expect((await createReservation(req('/api/booking/reservations', { method: 'POST', body, origin: 'https://evil.example', headers: { 'idempotency-key': key } }))).status).toBe(403);
    expect((await createReservation(req('/api/booking/reservations', { method: 'POST', body }))).status).toBe(400);
    expect(bffFetch).not.toHaveBeenCalled();
    bffFetch.mockResolvedValue({
      ok: true,
      status: 201,
      data: { booking_id: ID, status: 'requested', outlet_code: 'PB-01', outlet_name: 'PB SCBD', start_at: SLOT, end_at: SLOT, party_size: 2, table_label: 'Meja 1', cancellable: true },
    });
    const res = await createReservation(req('/api/booking/reservations', { method: 'POST', body, headers: { 'idempotency-key': key } }));
    expect(res.status).toBe(201);
    expect(res.headers.get('cache-control')).toBe('no-store');
    const [path, opts] = bffFetch.mock.calls[0]!;
    expect(path).toBe('/v1/booking/reservations');
    expect(opts.json).toEqual({ outlet_code: 'PB-01', start_at: SLOT, party_size: 2, guest_label: 'Rina' });
    expect(opts.headers['idempotency-key']).toBe(key);
    expect(opts.token).toBe('tok');
  });
  it('buat: batas aktif BFF (booking_limit) & slot penuh -> 409 pesan Indonesia', async () => {
    const key = 'k'.repeat(20);
    for (const code of ['booking_limit', 'slot_unavailable']) {
      bffFetch.mockResolvedValueOnce(fail(409, code));
      const res = await createReservation(req('/api/booking/reservations', { method: 'POST', body: { outlet_code: 'PB-01', start_at: SLOT, party_size: 2 }, headers: { 'idempotency-key': key } }));
      expect(res.status).toBe(409);
      const b = await res.json();
      expect(b.error.code).toBe(code);
      expect(b.error.message).not.toContain('internal');
    }
  });
  it('daftar/detail/batal: no-store, id bukan UUID -> 404 tanpa BFF, 401 menghapus sesi', async () => {
    bffFetch.mockResolvedValue({ ok: true, status: 200, data: { items: [{ booking_id: ID, status: 'confirmed', start_at: SLOT, party_size: 2 }] } });
    const list = await listReservations(req('/api/booking/reservations'));
    expect(list.headers.get('cache-control')).toBe('no-store');
    expect((await list.json()).items).toHaveLength(1);
    bffFetch.mockReset();
    expect((await getReservation(req('/api/booking/reservations/abc'), params({ id: 'abc' }))).status).toBe(404);
    expect((await cancelReservation(req('/api/booking/reservations/abc/cancel', { method: 'POST', body: {} }), params({ id: 'abc' }))).status).toBe(404);
    expect(bffFetch).not.toHaveBeenCalled();
    bffFetch.mockResolvedValue(fail(409, 'booking_not_cancellable'));
    const c = await cancelReservation(req(`/api/booking/reservations/${ID}/cancel`, { method: 'POST', body: {} }), params({ id: ID }));
    expect(c.status).toBe(409);
    expect(bffFetch.mock.calls[0]![0]).toBe(`/v1/booking/reservations/${ID}/cancel`);
    bffFetch.mockResolvedValue(fail(401, 'unauthenticated'));
    const u = await getReservation(req(`/api/booking/reservations/${ID}`), params({ id: ID }));
    expect(u.status).toBe(401);
    expect(u.headers.get('set-cookie') ?? '').toMatch(/efn_session=/);
  });
});
