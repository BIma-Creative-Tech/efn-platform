import { randomUUID } from 'node:crypto';
import { AmqpService } from '../common/amqp/amqp.service';
import { ApiError } from '../common/errors/api-error';
import { RateLimiter } from '../common/rate-limit/rate-limiter';
import { FakeDb, FakeKvStore, catchApiError, randomSecret, testConfig } from '../common/testing/fakes';
import { BehaviorEventMessage } from './behavior-message';
import { BehaviorService } from './behavior.service';
import { parseEventBatch, redactQuery } from './dto/behavior.dto';
import { anonSubjectKey, deviceKey, memberSubjectKey } from './subject-key';

const NOW = Date.UTC(2026, 10, 20, 3, 0, 0);
const DEVICE = randomUUID();
const SUBJECT_PEPPER = randomSecret();
const DEVICE_PEPPER = randomSecret();
const TRACKING_VERSION = 'tracking@2026-11-20';

const ev = (extra: Record<string, unknown> = {}): Record<string, unknown> => ({
  event_id: randomUUID(),
  type: 'product_view',
  client_ts: new Date(NOW - 5000).toISOString(),
  channel_code: 'SMD',
  outlet_code: 'SMD01',
  product_id: 10,
  duration_s: 12,
  ...extra,
});
const body = (events: unknown[]): Record<string, unknown> => ({ schema_version: 1, device_id: DEVICE, channel: 'web', events });

interface Opts {
  env?: Record<string, string>;
  device?: boolean;
  /** Versi baris device_consent terbaru (`erasure` = baris penarikan saat erasure akun, SEC-G50-07). */
  deviceVersion?: string;
  tracking?: boolean;
  linkedMember?: string;
  brokerDown?: boolean;
}

function setup(opts: Opts = {}) {
  const config = testConfig({
    BEHAVIOR_TRACKING_ENABLED: 'true',
    BEHAVIOR_SUBJECT_PEPPER: SUBJECT_PEPPER,
    BEHAVIOR_DEVICE_PEPPER: DEVICE_PEPPER,
    CONSENT_TRACKING_VERSION: TRACKING_VERSION,
    CONSENT_TRACKING_TEXT_SHA256: 'a'.repeat(64),
    ...opts.env,
  });
  const kv = new FakeKvStore(() => NOW);
  const limiter = new RateLimiter(kv);
  limiter.now = () => NOW;
  const published: { queue: string; payload: BehaviorEventMessage; id: string }[] = [];
  const amqp = {
    publish: jest.fn(async (queue: string, payload: BehaviorEventMessage, id: string) => {
      if (opts.brokerDown) throw new Error('broker down');
      published.push({ queue, payload, id });
    }),
  } as unknown as AmqpService;
  const db = new FakeDb((t) => {
    if (t.includes('FROM behavior.device_consent')) {
      return opts.device === undefined ? { rows: [], rowCount: 0 } : { rows: [{ granted: opts.device, version: opts.deviceVersion ?? TRACKING_VERSION }], rowCount: 1 };
    }
    if (t.includes('FROM platform.consent')) return opts.tracking ? { rows: [{ granted: true }], rowCount: 1 } : { rows: [], rowCount: 0 };
    if (t.includes('FROM behavior.device_link l')) return opts.linkedMember ? { rows: [{ member_id: opts.linkedMember }], rowCount: 1 } : { rows: [], rowCount: 0 };
    if (t.includes('FROM platform.online_catalog_sync')) {
      // Pemetaan kanal → PT: SMD aktif; OLD tidak aktif; ZZ kode PT di luar COMPANY_CODES
      return {
        rows: [
          { channel_code: 'SMD', code: 'SMD', active: 'true', company_code: 'SMD' },
          { channel_code: 'OLD', code: 'OLD', active: 'false', company_code: 'GL' },
          { channel_code: 'ZZ', code: 'ZZ', active: 'true', company_code: 'ZZ' },
        ],
        rowCount: 3,
      };
    }
    if (t.includes('FROM platform.online_catalog_item')) {
      return {
        rows: [{ product_id: 10, data: { prices: [{ outlet_code: null, price_incl: 15000.4 }, { outlet_code: 'SMD01', price_incl: 14000 }] } }],
        rowCount: 1,
      };
    }
    return undefined;
  });
  const svc = new BehaviorService(config, db, kv, limiter, amqp);
  svc.now = () => NOW;
  return { config, db, kv, svc, published, amqp };
}

const user = { memberId: randomUUID(), memberHash: 'b'.repeat(64) };

describe('behavior DTO (docs/04 §28.6)', () => {
  it('redaksi query: NFKC + lowercase, email & rangkaian ≥ 6 digit diganti [redacted]', () => {
    expect(redactQuery('Susu ＵＨＴ 0812-3456-7890 budi@contoh.id')).toBe('susu uht [redacted] [redacted]');
    expect(redactQuery('Beras 5kg 12345')).toBe('beras 5kg 12345');
    expect(redactQuery('x'.repeat(10) + ' 123456')).toBe('xxxxxxxxxx [redacted]');
  });

  it('bentuk body salah → 400; field tambahan per event / cart_abandon dari klien → invalid; stale; duplikat', async () => {
    expect((await catchApiError(() => parseEventBatch({ ...body([]), extra: 1 }, NOW))).getStatus()).toBe(400);
    expect((await catchApiError(() => parseEventBatch({ ...body([]), device_id: 'bukan-uuid' }, NOW))).getStatus()).toBe(400);
    expect((await catchApiError(() => parseEventBatch(body(Array.from({ length: 51 }, () => ev())), NOW))).getStatus()).toBe(400);
    const dup = ev();
    const b = parseEventBatch(
      body([
        ev(),
        ev({ client_ts: new Date(NOW - 25 * 3600 * 1000).toISOString() }),
        ev({ ip: '1.2.3.4' }),
        ev({ type: 'cart_abandon' }),
        ev({ type: 'product_view', product_id: undefined }),
        ev({ type: 'cart_add', query: 'susu' }),
        dup,
        dup,
        ev({ type: 'search', product_id: undefined, query: 'telur 081234567890', result_count: 0 }),
        ev({ duration_s: 9999 }),
      ]),
      NOW,
    );
    expect(b.rejected).toEqual([
      { index: 1, code: 'stale' },
      { index: 2, code: 'invalid' },
      { index: 3, code: 'invalid' },
      { index: 4, code: 'invalid' },
      { index: 5, code: 'invalid' },
    ]);
    expect(b.duplicates).toBe(1);
    expect(b.events.find((e) => e.event.type === 'search')?.event.query).toBe('telur [redacted]');
    expect(b.events[b.events.length - 1]!.event.duration_s).toBe(600);
  });

  it('V-01 page_view: page wajib ∈ enum; product_id hanya untuk page=product lalu diabaikan; page dilarang di tipe lain', () => {
    const pv = (extra: Record<string, unknown>) => ev({ type: 'page_view', product_id: undefined, duration_s: undefined, ...extra });
    const b = parseEventBatch(
      body([
        pv({ page: 'home' }),
        pv({ page: 'product', product_id: 10 }),
        pv({}),
        pv({ page: '/produk/susu?ref=wa' }),
        pv({ page: 'HOME' }),
        pv({ page: 'cart', product_id: 10 }),
        ev({ page: 'product' }),
        pv({ page: 'search', query: 'susu' }),
      ]),
      NOW,
    );
    expect(b.events.map((e) => [e.index, e.event.type, e.event.page, e.event.product_id])).toEqual([
      [0, 'page_view', 'home', null],
      [1, 'page_view', 'product', null],
    ]);
    expect(b.rejected).toEqual([2, 3, 4, 5, 6, 7].map((index) => ({ index, code: 'invalid' })));
  });
});

describe('BehaviorService.ingest', () => {
  it('flag mati → 202 disabled tanpa menyentuh DB/broker', async () => {
    const ctx = setup({ env: { BEHAVIOR_TRACKING_ENABLED: 'false' } });
    expect(await ctx.svc.ingest(body([ev()]), 100, '10.0.0.1', null)).toEqual({ accepted: 0, disabled: true });
    expect(ctx.db.queries).toHaveLength(0);
    expect(ctx.amqp.publish).not.toHaveBeenCalled();
  });

  it('device tanpa consent → 403 tracking_consent_required', async () => {
    const ctx = setup();
    const err = await catchApiError(() => ctx.svc.ingest(body([ev()]), 100, '10.0.0.1', null));
    expect(err.getStatus()).toBe(403);
    expect((err.body as { error: { code: string } }).error.code).toBe('tracking_consent_required');
    expect(ctx.published).toHaveLength(0);
  });

  it('consent device ditarik (baris terbaru granted=false) → 403', async () => {
    const ctx = setup({ device: false });
    expect((await catchApiError(() => ctx.svc.ingest(body([ev()]), 100, null, null))).getStatus()).toBe(403);
  });

  it('device ber-consent → publish behavior.event dengan subject device_key, pengayaan harga & stok cache, tanpa device ID mentah/IP', async () => {
    const ctx = setup({ device: true });
    await ctx.kv.setEx('ocat:stk:SMD:SMD01:10', 'out', 30);
    const res = await ctx.svc.ingest(body([ev(), ev({ outlet_code: null })]), 500, '10.0.0.9', null);
    expect(res).toEqual({ accepted: 2, duplicates: 0, rejected: [], mode: 'consented' });
    expect(ctx.published).toHaveLength(1);
    const { queue, payload, id } = ctx.published[0]!;
    expect(queue).toBe('behavior.event');
    expect(id).toBe(`behavior:${payload.batch_id}`);
    expect(payload.subject_kind).toBe('device');
    expect(payload.subject_key).toBe(deviceKey(DEVICE_PEPPER, DEVICE));
    expect(payload.events[0]).toMatchObject({ price_idr: 14000, stock_status: 'out', company_code: 'SMD' });
    expect(payload.events[1]).toMatchObject({ price_idr: 15000, stock_status: null, company_code: 'SMD' });
    const raw = JSON.stringify(payload);
    expect(raw).not.toContain(DEVICE);
    expect(raw).not.toContain('10.0.0.9');
  });

  it('produk tidak ada di salinan katalog → rejected unknown_product; semua ditolak → tidak publish', async () => {
    const ctx = setup({ device: true });
    const res = await ctx.svc.ingest(body([ev({ product_id: 99 })]), 100, null, null);
    expect(res).toEqual({ accepted: 0, duplicates: 0, rejected: [{ index: 0, code: 'unknown_product' }], mode: 'consented' });
    expect(ctx.published).toHaveLength(0);
  });

  it('kanal tanpa pemetaan PT (tidak ada / tidak aktif / PT di luar COMPANY_CODES) → rejected unknown_channel, termasuk search', async () => {
    const ctx = setup({ device: true });
    const res = await ctx.svc.ingest(
      body([
        ev({ channel_code: 'NOPE' }),
        ev({ channel_code: 'OLD' }),
        ev({ channel_code: 'ZZ' }),
        ev({ channel_code: 'NOPE', type: 'search', product_id: undefined, query: 'telur', result_count: 0 }),
        ev(),
      ]),
      100,
      null,
      null,
    );
    expect(res).toEqual({
      accepted: 1,
      duplicates: 0,
      rejected: [
        { index: 0, code: 'unknown_channel' },
        { index: 1, code: 'unknown_channel' },
        { index: 2, code: 'unknown_channel' },
        { index: 3, code: 'unknown_channel' },
      ],
      mode: 'consented',
    });
    expect(ctx.published[0]!.payload.events.map((e) => e.company_code)).toEqual(['SMD']);
  });

  it('Bearer tanpa consent tracking AKUN → 403 walau consent device ada', async () => {
    const ctx = setup({ device: true, tracking: false });
    expect((await catchApiError(() => ctx.svc.ingest(body([ev()]), 100, null, user))).getStatus()).toBe(403);
  });

  it('Bearer + consent tracking → subject member, tautan device & pemetaan subject dicatat', async () => {
    const ctx = setup({ tracking: true });
    await ctx.svc.ingest(body([ev()]), 100, null, user);
    expect(ctx.published[0]!.payload.subject_kind).toBe('member');
    expect(ctx.published[0]!.payload.subject_key).toBe(memberSubjectKey(SUBJECT_PEPPER, user.memberId));
    expect(ctx.db.find('INSERT INTO behavior.device_link')[0]!.params).toEqual([user.memberId, deviceKey(DEVICE_PEPPER, DEVICE)]);
    expect(ctx.db.find('INSERT INTO behavior.subject_member')).toHaveLength(1);
  });

  it('tanpa Bearer, device tertaut ke member ber-consent → subject member', async () => {
    const member = randomUUID();
    const ctx = setup({ device: true, tracking: true, linkedMember: member });
    await ctx.svc.ingest(body([ev()]), 100, null, null);
    expect(ctx.published[0]!.payload.subject_key).toBe(memberSubjectKey(SUBJECT_PEPPER, member));
  });

  it('rate limit per device (event/menit) → 429 + Retry-After', async () => {
    const ctx = setup({ device: true, env: { BEHAVIOR_RL_DEVICE_PER_MIN: '2' } });
    const err = await catchApiError(() => ctx.svc.ingest(body([ev(), ev(), ev()]), 100, null, null));
    expect(err.getStatus()).toBe(429);
    expect(err.headers['Retry-After']).toBeDefined();
  });

  it('rate limit per IP gagal → kuota device dikembalikan', async () => {
    const ctx = setup({ device: true, env: { BEHAVIOR_RL_IP_PER_MIN: '1' } });
    expect((await catchApiError(() => ctx.svc.ingest(body([ev(), ev()]), 100, '10.0.0.2', null))).getStatus()).toBe(429);
    const devKeys = ctx.kv.keys().filter((k) => k.startsWith('rl:beh:dev:'));
    expect(devKeys.map((k) => ctx.kv.raw(k))).toEqual(['0']);
  });

  it('body > 32 KB → 413; broker mati → 503 (bukan 500)', async () => {
    const big = setup({ device: true });
    expect((await catchApiError(() => big.svc.ingest(body([ev()]), 40_000, null, null))).getStatus()).toBe(413);
    const down = setup({ device: true, brokerDown: true });
    const err = await catchApiError(() => down.svc.ingest(body([ev()]), 100, null, null));
    expect(err).toBeInstanceOf(ApiError);
    expect(err.getStatus()).toBe(503);
  });
});

describe('BehaviorService.ingest — V-01 log kunjungan anonim (keputusan pemilik proyek 23 Nov 2026)', () => {
  const ANON = { BEHAVIOR_ANON_VISIT_ENABLED: 'true' };
  const pageView = (page = 'home') => ev({ type: 'page_view', page, product_id: undefined, duration_s: undefined, outlet_code: undefined });

  it('tanpa consent + visit_log aktif → diterima anonim: subject anonSubjectKey (≠ device_key), consented=false, tanpa tautan', async () => {
    const ctx = setup({ env: ANON });
    const res = await ctx.svc.ingest(body([pageView(), ev()]), 200, '10.0.0.7', null);
    expect(res).toEqual({ accepted: 2, duplicates: 0, rejected: [], mode: 'anonymous' });
    const { payload } = ctx.published[0]!;
    expect(payload).toMatchObject({ subject_kind: 'device', consented: false, subject_key: anonSubjectKey(DEVICE_PEPPER, DEVICE) });
    expect(payload.subject_key).not.toBe(deviceKey(DEVICE_PEPPER, DEVICE));
    expect(payload.events[0]).toMatchObject({ type: 'page_view', page: 'home', product_id: null, price_idr: null });
    expect(payload.events[1]).toMatchObject({ type: 'product_view', page: null, price_idr: 14000 });
    expect(ctx.db.find('behavior.device_link')).toHaveLength(0);
    expect(ctx.db.find('behavior.subject_member')).toHaveLength(0);
    const raw = JSON.stringify(payload);
    expect(raw).not.toContain(DEVICE);
    expect(raw).not.toContain('10.0.0.7');
  });

  it('device ber-consent tetap mode consented (perilaku lama) walau visit_log aktif', async () => {
    const ctx = setup({ device: true, env: ANON });
    const res = await ctx.svc.ingest(body([ev()]), 100, null, null);
    expect(res.mode).toBe('consented');
    expect(ctx.published[0]!.payload).toMatchObject({ consented: true, subject_kind: 'device', subject_key: deviceKey(DEVICE_PEPPER, DEVICE) });
  });

  it('SEC-V01-01: consent device versi teks lama → anonim tanpa penautan member (visit_log aktif) / 403 (visit_log mati)', async () => {
    const member = randomUUID();
    const ctx = setup({ device: true, deviceVersion: 'tracking@lama', tracking: true, linkedMember: member, env: ANON });
    const res = await ctx.svc.ingest(body([pageView()]), 100, null, null);
    expect(res.mode).toBe('anonymous');
    expect(ctx.published[0]!.payload).toMatchObject({ consented: false, subject_kind: 'device', subject_key: anonSubjectKey(DEVICE_PEPPER, DEVICE) });
    expect(ctx.db.find('behavior.device_link')).toHaveLength(0);
    expect(JSON.stringify(ctx.published[0]!.payload)).not.toContain(member);
    const off = setup({ device: true, deviceVersion: 'tracking@lama' });
    expect((await catchApiError(() => off.svc.ingest(body([ev()]), 100, null, null))).getStatus()).toBe(403);
  });

  it('SEC-V01-01: teks consent aktif tidak dikonfigurasi → consent device granted tidak berlaku (anonim / 403)', async () => {
    const noText = { CONSENT_TRACKING_VERSION: '', CONSENT_TRACKING_TEXT_SHA256: '' };
    const ctx = setup({ device: true, linkedMember: randomUUID(), tracking: true, env: { ...ANON, ...noText } });
    expect((await ctx.svc.ingest(body([pageView()]), 100, null, null)).mode).toBe('anonymous');
    expect(ctx.published[0]!.payload.consented).toBe(false);
    expect(ctx.db.find('behavior.device_link')).toHaveLength(0);
    const off = setup({ device: true, env: noText });
    expect((await catchApiError(() => off.svc.ingest(body([ev()]), 100, null, null))).getStatus()).toBe(403);
  });

  it('Bearer tanpa consent tracking akun → tetap anonim tanpa member id/tautan (consent device tidak dipakai saat Bearer)', async () => {
    for (const device of [undefined, true]) {
      const ctx = setup({ device, tracking: false, env: ANON });
      const res = await ctx.svc.ingest(body([pageView('account')]), 100, null, user);
      expect(res.mode).toBe('anonymous');
      const { payload } = ctx.published[0]!;
      expect(payload).toMatchObject({ subject_kind: 'device', consented: false, subject_key: anonSubjectKey(DEVICE_PEPPER, DEVICE) });
      expect(JSON.stringify(payload)).not.toContain(user.memberId);
      expect(payload.subject_key).not.toBe(memberSubjectKey(SUBJECT_PEPPER, user.memberId));
      expect(ctx.db.find('INSERT INTO behavior.device_link')).toHaveLength(0);
      expect(ctx.db.find('INSERT INTO behavior.subject_member')).toHaveLength(0);
    }
  });

  it('Bearer + consent tracking akun → tetap subject member consented', async () => {
    const ctx = setup({ tracking: true, env: ANON });
    expect((await ctx.svc.ingest(body([ev()]), 100, null, user)).mode).toBe('consented');
    expect(ctx.published[0]!.payload).toMatchObject({ subject_kind: 'member', consented: true });
  });

  it('penarikan consent device biasa → boleh dicatat anonim', async () => {
    const ctx = setup({ device: false, deviceVersion: 'tracking@lama', env: ANON });
    expect((await ctx.svc.ingest(body([pageView()]), 100, null, null)).mode).toBe('anonymous');
  });

  it('baris erasure akun (version=erasure) → 403 tracking_consent_required, juga dengan Bearer tanpa consent', async () => {
    for (const u of [null, user]) {
      const ctx = setup({ device: false, deviceVersion: 'erasure', env: ANON });
      const err = await catchApiError(() => ctx.svc.ingest(body([pageView()]), 100, null, u));
      expect(err.getStatus()).toBe(403);
      expect((err.body as { error: { code: string } }).error.code).toBe('tracking_consent_required');
      expect(ctx.published).toHaveLength(0);
    }
  });

  it('flag anonim mati → 403 lama (tanpa Bearer & Bearer tanpa consent); tracking mati → disabled walau flag anonim aktif', async () => {
    const off = setup();
    expect((await catchApiError(() => off.svc.ingest(body([pageView()]), 100, null, null))).getStatus()).toBe(403);
    const offBearer = setup({ tracking: false });
    expect((await catchApiError(() => offBearer.svc.ingest(body([pageView()]), 100, null, user))).getStatus()).toBe(403);
    expect(offBearer.db.find('FROM behavior.device_consent')).toHaveLength(0);
    const noTracking = setup({ env: { ...ANON, BEHAVIOR_TRACKING_ENABLED: 'false' } });
    expect(await noTracking.svc.ingest(body([pageView()]), 100, null, null)).toEqual({ accepted: 0, disabled: true });
  });

  it('anonim: wishlist_* / notify_me_request / price_alert_set → rejected invalid, event lain tetap diterima', async () => {
    const ctx = setup({ env: ANON });
    const res = await ctx.svc.ingest(
      body([ev({ type: 'wishlist_add' }), ev({ type: 'wishlist_remove' }), ev({ type: 'notify_me_request' }), ev({ type: 'price_alert_set' }), pageView(), ev({ type: 'cart_add', qty: 1 })]),
      100,
      null,
      null,
    );
    expect(res).toEqual({ accepted: 2, duplicates: 0, rejected: [0, 1, 2, 3].map((index) => ({ index, code: 'invalid' })), mode: 'anonymous' });
    expect(ctx.published[0]!.payload.events.map((e) => e.type)).toEqual(['page_view', 'cart_add']);
  });

  it('anonim dengan hanya event fitur member → tidak publish, mode tetap dilaporkan', async () => {
    const ctx = setup({ env: ANON });
    expect(await ctx.svc.ingest(body([ev({ type: 'wishlist_add' })]), 100, null, null)).toEqual({
      accepted: 0,
      duplicates: 0,
      rejected: [{ index: 0, code: 'invalid' }],
      mode: 'anonymous',
    });
    expect(ctx.published).toHaveLength(0);
  });
});

describe('BehaviorService.recordDeviceConsent', () => {
  const consent = (granted: boolean, version = TRACKING_VERSION) => ({ device_id: DEVICE, granted, version });

  it('grant versi aktif → baris append-only dengan device_key (bukan device ID)', async () => {
    const ctx = setup();
    expect(await ctx.svc.recordDeviceConsent(consent(true), '10.0.0.1')).toEqual({ recorded: true, granted: true });
    const ins = ctx.db.find('INSERT INTO behavior.device_consent')[0]!;
    expect(ins.params).toEqual([deviceKey(DEVICE_PEPPER, DEVICE), true, TRACKING_VERSION, 'a'.repeat(64)]);
    expect(JSON.stringify(ctx.db.queries)).not.toContain(DEVICE);
  });

  it('versi lama → 409 stale_text; teks belum dikonfigurasi → 503; field asing → 400', async () => {
    const ctx = setup();
    expect((await catchApiError(() => ctx.svc.recordDeviceConsent(consent(true, 'tracking@lama'), null))).getStatus()).toBe(409);
    const noText = setup({ env: { CONSENT_TRACKING_VERSION: '', CONSENT_TRACKING_TEXT_SHA256: '' } });
    expect((await catchApiError(() => noText.svc.recordDeviceConsent(consent(true), null))).getStatus()).toBe(503);
    expect((await catchApiError(() => ctx.svc.recordDeviceConsent({ ...consent(true), ua: 'x' }, null))).getStatus()).toBe(400);
  });

  it('penarikan selalu diterima dan menghapus tautan device ↔ member', async () => {
    const ctx = setup();
    await ctx.svc.recordDeviceConsent(consent(false, 'apa-saja'), null);
    expect(ctx.db.find('DELETE FROM behavior.device_link WHERE device_key')).toHaveLength(1);
  });

  it('flag mati → tidak menyimpan apa pun', async () => {
    const ctx = setup({ env: { BEHAVIOR_TRACKING_ENABLED: 'false' } });
    expect(await ctx.svc.recordDeviceConsent(consent(true), null)).toEqual({ recorded: false, disabled: true });
    expect(ctx.db.queries).toHaveLength(0);
  });
});
