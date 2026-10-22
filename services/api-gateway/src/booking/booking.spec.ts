import { createHmac, randomUUID } from 'node:crypto';
import { ApiError } from '../common/errors/api-error';
import { FakeKvStore, httpContext, randomSecret, testConfig } from '../common/testing/fakes';
import { computeWebhookSignature } from '../common/security/webhook-signature';
import { CatalogService, ChannelInfo } from '../catalog/catalog.service';
import { BookingEventHmacGuard } from './booking.controller';
import { BookingEventInput, BookingRow, parseBookingEvent, parseCreateBooking, sanitizeGuestText } from './booking.model';
import { BookingPatch, BookingRepo, NewBooking } from './booking.repository';
import { BookingService } from './booking.service';
import { BookingMetrics, MockOdooBooking, OdooBookingError, bookingBusinessError, parseAvailability, parseBookingResult } from './odoo-booking';

/** G23 W-35 (docs/04 §15.5). Repo in-memory meniru semantik SQL (`ON CONFLICT DO NOTHING`, status aktif, retensi). */
class MemRepo implements BookingRepo {
  rows = new Map<string, BookingRow>();
  claimed = new Set<string>();
  outbox: { queue: string; payload: Record<string, unknown>; id: string }[] = [];
  hashes = new Map<string, string>();

  tx<T>(fn: (r: BookingRepo) => Promise<T>): Promise<T> {
    return fn(this);
  }
  async insert(b: NewBooking): Promise<BookingRow | null> {
    if ([...this.rows.values()].some((r) => r.member_id === b.member_id && r.idempotency_key === b.idempotency_key)) return null;
    const row: BookingRow = { ...b, status: 'pending', odoo_booking_ref: null, table_label: null, last_seq: 0, created_at: new Date(), updated_at: new Date() };
    this.rows.set(b.id, row);
    return { ...row };
  }
  async byKey(memberId: string, key: string): Promise<BookingRow | null> {
    const r = [...this.rows.values()].find((x) => x.member_id === memberId && x.idempotency_key === key);
    return r ? { ...r } : null;
  }
  async byId(id: string): Promise<BookingRow | null> {
    const r = this.rows.get(id);
    return r ? { ...r } : null;
  }
  async ofMember(memberId: string, limit: number): Promise<BookingRow[]> {
    return [...this.rows.values()].filter((r) => r.member_id === memberId && r.status !== 'failed').slice(0, limit);
  }
  async countActive(memberId: string, now: Date): Promise<number> {
    return [...this.rows.values()].filter((r) => r.member_id === memberId && ['pending', 'requested', 'confirmed'].includes(r.status) && r.end_at > now).length;
  }
  async update(id: string, patch: BookingPatch): Promise<void> {
    const r = this.rows.get(id);
    if (r) Object.assign(r, patch, { updated_at: new Date() });
  }
  async claim(key: string): Promise<boolean> {
    if (this.claimed.has(key)) return false;
    this.claimed.add(key);
    return true;
  }
  async memberHashOf(memberId: string): Promise<string | null> {
    return this.hashes.get(memberId) ?? null;
  }
  async enqueue(queue: string, payload: Record<string, unknown>, id: string): Promise<void> {
    this.outbox.push({ queue, payload, id });
  }
  async staleActive(until: Date, limit: number): Promise<BookingRow[]> {
    return [...this.rows.values()].filter((r) => ['pending', 'requested', 'confirmed'].includes(r.status) && r.start_at <= until).slice(0, limit);
  }
  async unlinkMembers(): Promise<number> {
    return 0;
  }
  async purge(): Promise<number> {
    return 0;
  }
}

const CHANNELS: ChannelInfo[] = [
  {
    channel_code: 'PB-WEB',
    company_code: 'PB',
    brand_code: 'PB',
    name: 'Paris Baguette',
    fulfilment: { pickup: true, delivery: false },
    outlets: [{ outlet_code: 'PB-SCBD', public_name: 'PB SCBD', pickup_enabled: true, prep_minutes: 15 }],
  },
  {
    channel_code: 'GL-WEB',
    company_code: 'GL',
    brand_code: 'GL',
    name: 'GrandLucky',
    fulfilment: { pickup: true, delivery: false },
    outlets: [{ outlet_code: 'GL-01', public_name: 'GL Satu', pickup_enabled: true, prep_minutes: 30 }],
  },
  {
    channel_code: 'BCF-WEB',
    company_code: 'BCF',
    brand_code: 'BACHA',
    name: 'Bacha Coffee',
    fulfilment: { pickup: true, delivery: false },
    outlets: [{ outlet_code: 'BCF-PI', public_name: 'Bacha PI', pickup_enabled: true, prep_minutes: 10 }],
  },
];

const MEMBER = randomUUID();
const OTHER = randomUUID();

/** Tanggal WIB 3 hari ke depan + jam lokal → ISO UTC. */
function slot(hour: number, minute = 0, daysAhead = 3): { date: string; iso: string } {
  const wibNow = new Date(Date.now() + 7 * 3600_000);
  const d = new Date(Date.UTC(wibNow.getUTCFullYear(), wibNow.getUTCMonth(), wibNow.getUTCDate() + daysAhead));
  const date = d.toISOString().slice(0, 10);
  const iso = new Date(Date.parse(`${date}T00:00:00Z`) - 7 * 3600_000 + (hour * 60 + minute) * 60_000).toISOString().replace('.000Z', 'Z');
  return { date, iso };
}

async function code(fn: () => Promise<unknown> | unknown): Promise<string> {
  try {
    await fn();
    return 'ok';
  } catch (e) {
    if (e instanceof ApiError) return `${e.getStatus()} ${(e.body as { error: { code: string } }).error.code}`;
    throw e;
  }
}

function setup(over: Record<string, string> = {}) {
  const config = testConfig({ BOOKING_ENABLED: 'true', ODOO_ONLINE_MOCK: 'true', ODOO_BOOKING_EVENT_HMAC: randomSecret(), ...over });
  const repo = new MemRepo();
  repo.hashes.set(MEMBER, 'h'.repeat(64));
  const odoo = new MockOdooBooking();
  const kv = new FakeKvStore();
  const catalog = { channels: async (brand?: string | null) => CHANNELS.filter((c) => !brand || c.brand_code === brand) } as unknown as CatalogService;
  const metrics = { odoo: jest.fn(), transition: jest.fn() } as unknown as BookingMetrics;
  const svc = new BookingService(config, repo, odoo, kv, catalog, metrics);
  return { config, repo, odoo, kv, svc };
}

const key = (): string => `k-${randomUUID()}`;

describe('booking model', () => {
  it('parseCreateBooking menolak field asing dan format salah', () => {
    const ok = { outlet_code: 'PB-SCBD', start_at: '2026-10-25T05:00:00Z', party_size: 2 };
    expect(parseCreateBooking(ok)).toEqual({ ...ok, guest_label: null, note: null });
    for (const bad of [
      { ...ok, table_id: 3 },
      { ...ok, company_code: 'PB' },
      { ...ok, start_at: '2026-10-25T05:00:30Z' },
      { ...ok, start_at: '2026-10-25 05:00' },
      { ...ok, party_size: 0 },
      { ...ok, party_size: 2.5 },
      { ...ok, outlet_code: 'pb scbd' },
      [ok],
      null,
    ]) {
      expect(() => parseCreateBooking(bad)).toThrow(ApiError);
    }
  });

  it('teks tamu: tanpa email, nomor HP, baris baru (L-86/SEC-168)', () => {
    expect(sanitizeGuestText('  Rina\u200b  ', 40)).toBe('Rina');
    expect(sanitizeGuestText('', 40)).toBeNull();
    expect(sanitizeGuestText(undefined, 40)).toBeNull();
    for (const bad of ['rina@mail.test', 'hubungi 0812 3456 7890', '+62 812-3456-789', 'a\nb', 'x'.repeat(41)]) {
      expect(() => sanitizeGuestText(bad, 40)).toThrow(ApiError);
    }
    expect(sanitizeGuestText('meja 2 dekat jendela', 140)).toBe('meja 2 dekat jendela');
  });

  it('parseBookingEvent ketat', () => {
    const ref = randomUUID();
    const ev = {
      schema_version: 1,
      idempotency_key: `booking:${ref}:confirmed:1`,
      seq: 1,
      platform_booking_ref: ref,
      company_code: 'PB',
      outlet_code: 'PB-SCBD',
      event: 'confirmed',
      state: 'confirmed',
      occurred_at: '2026-10-21T10:00:00Z',
    };
    expect(parseBookingEvent(Buffer.from(JSON.stringify(ev))).seq).toBe(1);
    for (const bad of [{ ...ev, guest_label: 'x' }, { ...ev, company_code: 'GL' }, { ...ev, event: 'requested' }, { ...ev, seq: 0 }]) {
      expect(() => parseBookingEvent(Buffer.from(JSON.stringify(bad)))).toThrow(ApiError);
    }
    expect(() => parseBookingEvent(Buffer.from('x'.repeat(5000)))).toThrow(ApiError);
  });

  it('respons Odoo divalidasi ulang', () => {
    expect(bookingBusinessError({ error: { data: { message: 'efn_booking:slot_unavailable' } } })).toBe('slot_unavailable');
    expect(bookingBusinessError({ error: { message: 'efn_booking:drop_table' } })).toBeNull();
    expect(() => parseAvailability({ outlet_code: 'X', slots: 'all' })).toThrow(OdooBookingError);
    expect(() =>
      parseBookingResult({ booking_ref: 'B', platform_booking_ref: 'not-uuid', state: 'confirmed', start_at: 'x', end_at: 'y', party_size: 2, table_label: null, seq: 0 }),
    ).toThrow(OdooBookingError);
  });
});

describe('BookingService', () => {
  it('gerai hanya PT BOOKING_COMPANIES + filter brand', async () => {
    const { svc } = setup();
    expect((await svc.outlets()).map((o) => o.outlet_code)).toEqual(['PB-SCBD', 'BCF-PI']);
    expect((await svc.outlets('BACHA')).map((o) => o.outlet_code)).toEqual(['BCF-PI']);
    const pbOnly = setup({ BOOKING_COMPANIES: 'PB' });
    expect((await pbOnly.svc.outlets()).map((o) => o.outlet_code)).toEqual(['PB-SCBD']);
  });

  it('kill switch → 503 booking_unavailable', async () => {
    const { svc } = setup({ BOOKING_ENABLED: 'false' });
    expect(await code(() => svc.requireEnabled())).toBe('503 booking_unavailable');
  });

  it('ketersediaan: outlet non-F&B ditolak, hasil di-cache 30 s', async () => {
    const { svc, kv, odoo } = setup();
    expect(await code(() => svc.availability('GL-01', slot(12).date, 2))).toBe('409 booking_unavailable');
    const spy = jest.spyOn(odoo, 'availability');
    const a = await svc.availability('PB-SCBD', slot(12).date, 2);
    await svc.availability('PB-SCBD', slot(12).date, 2);
    expect(spy).toHaveBeenCalledTimes(1);
    expect(a.outlet_name).toBe('PB SCBD');
    expect(a.slots.length).toBeGreaterThan(0);
    expect(kv.keys().some((k) => k.startsWith('booking:avail:PB-SCBD'))).toBe(true);
  });

  it('buat: PB → requested, BCF → confirmed; teks tamu diteruskan ke Odoo tetapi tidak disimpan', async () => {
    const { svc, repo, odoo } = setup();
    const spy = jest.spyOn(odoo, 'create');
    const pb = await svc.create(MEMBER, key(), parseCreateBooking({ outlet_code: 'PB-SCBD', start_at: slot(12).iso, party_size: 2, guest_label: 'Rina', note: 'kursi bayi' }));
    expect(pb.status).toBe('requested');
    expect(pb.table_label).toBe('Meja 1');
    expect(pb.cancellable).toBe(true);
    expect(spy.mock.calls[0][2]).toMatchObject({ guest_label: 'Rina', note: 'kursi bayi', platform_booking_ref: pb.booking_id });
    expect(JSON.stringify([...repo.rows.values()])).not.toMatch(/Rina|kursi/);
    const bcf = await svc.create(MEMBER, key(), parseCreateBooking({ outlet_code: 'BCF-PI', start_at: slot(13).iso, party_size: 4 }));
    expect(bcf.status).toBe('confirmed');
  });

  it('idempoten: key sama → respons sama; isi beda → 409; tanpa key → 400', async () => {
    const { svc, repo } = setup();
    const k = key();
    const body = parseCreateBooking({ outlet_code: 'PB-SCBD', start_at: slot(14).iso, party_size: 2 });
    const a = await svc.create(MEMBER, k, body);
    const b = await svc.create(MEMBER, k, body);
    expect(b).toEqual(a);
    expect(repo.rows.size).toBe(1);
    expect(await code(() => svc.create(MEMBER, k, { ...body, party_size: 3 }))).toBe('409 idempotency_conflict');
    expect(await code(() => svc.create(MEMBER, undefined, body))).toBe('400 idempotency_key_required');
    expect(await code(() => svc.create(MEMBER, 'short', body))).toBe('400 invalid_request');
  });

  it('batas reservasi aktif per member (default 2)', async () => {
    const { svc } = setup();
    await svc.create(MEMBER, key(), parseCreateBooking({ outlet_code: 'PB-SCBD', start_at: slot(12).iso, party_size: 2 }));
    await svc.create(MEMBER, key(), parseCreateBooking({ outlet_code: 'PB-SCBD', start_at: slot(15).iso, party_size: 2 }));
    expect(await code(() => svc.create(MEMBER, key(), parseCreateBooking({ outlet_code: 'PB-SCBD', start_at: slot(18).iso, party_size: 2 })))).toBe(
      '409 booking_limit',
    );
  });

  it('galat bisnis Odoo → 409 kode sama, baris failed (tidak tampil)', async () => {
    const { svc } = setup();
    expect(await code(() => svc.create(MEMBER, key(), parseCreateBooking({ outlet_code: 'PB-SCBD', start_at: slot(23).iso, party_size: 2 })))).toBe(
      '409 outside_hours',
    );
    expect(await code(() => svc.create(MEMBER, key(), parseCreateBooking({ outlet_code: 'PB-SCBD', start_at: slot(12).iso, party_size: 9 })))).toBe(
      '409 party_size',
    );
    expect((await svc.list(MEMBER)).items).toEqual([]);
    // Meja 1/2/3 penuh di jam yang sama → slot_unavailable
    for (const p of [2, 4, 6]) await svc.create(randomUUID(), key(), parseCreateBooking({ outlet_code: 'PB-SCBD', start_at: slot(16).iso, party_size: p }));
    expect(await code(() => svc.create(MEMBER, key(), parseCreateBooking({ outlet_code: 'PB-SCBD', start_at: slot(16).iso, party_size: 2 })))).toBe(
      '409 slot_unavailable',
    );
  });

  it('Odoo transport mati → 503, baris tetap pending; ulang dengan key sama berhasil', async () => {
    const { svc, odoo, repo } = setup();
    const spy = jest.spyOn(odoo, 'create').mockRejectedValueOnce(new OdooBookingError('transport'));
    const k = key();
    const body = parseCreateBooking({ outlet_code: 'PB-SCBD', start_at: slot(12).iso, party_size: 2 });
    expect(await code(() => svc.create(MEMBER, k, body))).toBe('503 booking_unavailable');
    expect([...repo.rows.values()][0].status).toBe('pending');
    const ok = await svc.create(MEMBER, k, body);
    expect(ok.status).toBe('requested');
    expect(spy).toHaveBeenCalledTimes(2);
    expect(spy.mock.calls[0][2].idempotency_key).toBe(spy.mock.calls[1][2].idempotency_key);
  });

  it('member lain tidak bisa melihat/membatalkan; batal idempoten', async () => {
    const { svc } = setup();
    const b = await svc.create(MEMBER, key(), parseCreateBooking({ outlet_code: 'PB-SCBD', start_at: slot(12).iso, party_size: 2 }));
    expect(await code(() => svc.get(OTHER, b.booking_id))).toBe('404 not_found');
    expect(await code(() => svc.cancel(OTHER, b.booking_id))).toBe('404 not_found');
    expect(await svc.cancel(MEMBER, b.booking_id)).toEqual({ status: 'cancelled' });
    expect(await svc.cancel(MEMBER, b.booking_id)).toEqual({ status: 'cancelled' });
    expect((await svc.get(MEMBER, b.booking_id)).status).toBe('cancelled');
  });

  it('tidak bisa batal setelah tamu datang', async () => {
    const { svc, repo } = setup();
    const b = await svc.create(MEMBER, key(), parseCreateBooking({ outlet_code: 'PB-SCBD', start_at: slot(12).iso, party_size: 2 }));
    await repo.update(b.booking_id, { status: 'seated' });
    expect(await code(() => svc.cancel(MEMBER, b.booking_id))).toBe('409 booking_not_cancellable');
  });

  describe('event Odoo', () => {
    async function made() {
      const s = setup();
      const b = await s.svc.create(MEMBER, key(), parseCreateBooking({ outlet_code: 'PB-SCBD', start_at: slot(12).iso, party_size: 2 }));
      const ev = (event: BookingEventInput['event'], seq: number, over: Partial<BookingEventInput> = {}): BookingEventInput => ({
        schema_version: 1,
        idempotency_key: `booking:${b.booking_id}:${event}:${seq}`,
        seq,
        platform_booking_ref: b.booking_id,
        company_code: 'PB',
        outlet_code: 'PB-SCBD',
        event,
        state: event,
        occurred_at: new Date().toISOString(),
        ...over,
      });
      return { ...s, b, ev };
    }

    it('confirmed → status + notify.push tanpa PII; duplikat & event lama diabaikan', async () => {
      const { svc, repo, b, ev } = await made();
      expect((await svc.handleEvent(ev('confirmed', 1))).status).toBe('applied');
      expect(repo.rows.get(b.booking_id)?.status).toBe('confirmed');
      expect(repo.outbox).toHaveLength(1);
      expect(repo.outbox[0].payload).toEqual({
        schema_version: 2,
        idempotency_key: `notify:booking_confirmed:${b.booking_id}`,
        member_hash: 'h'.repeat(64),
        source: 'table_booking',
        source_ref: b.booking_id,
        template: 'booking_confirmed',
        params: {},
      });
      expect((await svc.handleEvent(ev('confirmed', 1))).status).toBe('duplicate');
      expect((await svc.handleEvent(ev('seated', 2))).status).toBe('applied');
      // event lama (seq lebih kecil / status mundur) tidak memundurkan status
      expect((await svc.handleEvent(ev('confirmed', 2, { idempotency_key: `booking:${b.booking_id}:confirmed:2` }))).status).toBe('stale');
      expect(repo.rows.get(b.booking_id)?.status).toBe('seated');
    });

    it('PT/outlet tidak cocok → unknown (tidak mengubah apa pun)', async () => {
      const { svc, repo, b, ev } = await made();
      expect((await svc.handleEvent(ev('cancelled', 1, { company_code: 'BCF' }))).status).toBe('unknown');
      expect((await svc.handleEvent(ev('cancelled', 1, { platform_booking_ref: randomUUID() }))).status).toBe('unknown');
      expect(repo.rows.get(b.booking_id)?.status).toBe('requested');
    });
  });

  it('rekonsiliasi: pending tanpa jejak di Odoo > 10 menit → failed; status Odoo yang lebih maju diambil', async () => {
    const { svc, repo, odoo } = setup();
    const ok = await svc.create(OTHER, key(), parseCreateBooking({ outlet_code: 'PB-SCBD', start_at: slot(12).iso, party_size: 2 }));
    const rec = odoo.bookings.get(ok.booking_id);
    if (rec) Object.assign(rec, { state: 'confirmed', seq: 1 });
    const lost = randomUUID();
    repo.rows.set(lost, { ...repo.rows.get(ok.booking_id)!, id: lost, status: 'pending', idempotency_key: key(), created_at: new Date(Date.now() - 20 * 60_000) });
    svc.now = () => Date.now() + 4 * 86_400_000; // semua reservasi masuk jendela 24 jam
    await svc.jobOnce();
    expect(repo.rows.get(ok.booking_id)?.status).toBe('confirmed');
    expect(repo.rows.get(lost)?.status).toBe('failed');
  });
});

describe('BookingEventHmacGuard', () => {
  it('tanda tangan valid lolos; salah/tanpa secret ditolak', async () => {
    const secret = randomSecret();
    const config = testConfig({ BOOKING_ENABLED: 'true', ODOO_BOOKING_EVENT_HMAC: secret });
    const guard = new BookingEventHmacGuard(config);
    const body = Buffer.from('{"a":1}');
    const t = Math.floor(Date.now() / 1000);
    const run = (header: string) => guard.canActivate(httpContext({ headers: { 'x-efn-signature': header }, rawBody: body }));
    expect(run(`t=${t},v1=${computeWebhookSignature(secret, t, body)}`)).toBe(true);
    expect(await code(() => run(`t=${t},v1=${createHmac('sha256', 'salah').update(`${t}.`).update(body).digest('hex')}`))).toBe('401 invalid_signature');
    expect(await code(() => run(`t=${t - 1000},v1=${computeWebhookSignature(secret, t - 1000, body)}`))).toBe('401 invalid_signature');
    const off = new BookingEventHmacGuard(testConfig({ BOOKING_ENABLED: 'false', ODOO_BOOKING_EVENT_HMAC: secret }));
    expect(await code(() => off.canActivate(httpContext({ headers: {}, rawBody: body })))).toBe('503 booking_unavailable');
    const noSecret = new BookingEventHmacGuard(testConfig({ BOOKING_ENABLED: 'true', ODOO_BOOKING_EVENT_HMAC: '' }));
    expect(await code(() => noSecret.canActivate(httpContext({ headers: {}, rawBody: body })))).toBe('503 booking_unavailable');
  });
});
