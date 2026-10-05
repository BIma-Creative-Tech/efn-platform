import { OutboxService } from '../common/outbox/outbox.service';
import { RateLimiter } from '../common/rate-limit/rate-limiter';
import { PII_AAD } from '../common/pii/crypto';
import { memberHash } from '../common/pii/pii';
import { FakeClock, FakeDb, FakeKvStore, catchApiError, testCipher, testConfig } from '../common/testing/fakes';
import { Errors } from '../common/errors/api-error';
import { MemberService } from '../common/member/member.service';
import { ConsentService } from '../privacy/consent.service';
import { ImageProcessor } from './image/image-processor';
import { MarketScoutService } from './market-scout.service';
import { readReportForm } from './multipart-form';
import { ObjectStorage } from './storage/object-storage';

const MEMBER = '6281234567890';
const MEMBER_ID = '8d7b5a1e-2c3f-4a5b-9c8d-7e6f5a4b3c2d';
const JPEG = Buffer.concat([Buffer.from([0xff, 0xd8, 0xff, 0xe0]), Buffer.alloc(100)]);
const FIELDS = { product_name: 'Minyak Goreng 2L', price: '34500', competitor_name: 'Toko A', lat: '-6.2', lng: '106.8' };

function setup(dbFails = false) {
  const clock = new FakeClock();
  const config = testConfig();
  const kv = new FakeKvStore(clock.now);
  const limiter = new RateLimiter(kv);
  limiter.now = clock.now;
  const db = new FakeDb(() => {
    if (dbFails) throw new Error('db down');
    return undefined;
  });
  const outbox = { enqueue: jest.fn().mockResolvedValue(undefined), relayOnce: jest.fn().mockResolvedValue(0) };
  const images = {
    normalize: jest.fn().mockResolvedValue({ jpeg: Buffer.from('re-encoded'), width: 10, height: 10, phash: '0123456789abcdef' }),
  };
  const storage: ObjectStorage & { put: jest.Mock; remove: jest.Mock } = {
    put: jest.fn().mockResolvedValue(undefined),
    remove: jest.fn().mockResolvedValue(undefined),
  };
  const cipher = testCipher(config);
  const members = { memberRef: jest.fn().mockResolvedValue(MEMBER) };
  const consents = { isGranted: jest.fn().mockResolvedValue(true) };
  const service = new MarketScoutService(
    config,
    db,
    outbox as unknown as OutboxService,
    limiter,
    images as unknown as ImageProcessor,
    storage,
    cipher,
    members as unknown as MemberService,
    consents as unknown as ConsentService,
  );
  service.now = () => new Date(clock.ms);
  // v3 (§8.2): identitas dari token = member.id; user_hash = member_hash
  const user = { memberId: MEMBER_ID, memberHash: memberHash(config.memberHashPepper, MEMBER) };
  return { clock, config, cipher, db, outbox, images, storage, service, members, consents, user };
}

describe('MarketScoutService.submit', () => {
  it('foto valid → 202 body, MinIO key sesuai kontrak, insert + outbox ocr.requested dalam satu transaksi', async () => {
    const ctx = setup();
    const res = await ctx.service.submit(ctx.user, { fields: FIELDS, photo: JPEG });
    expect(res.status).toBe('queued');
    expect(res.report_id).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);

    const key = `reports/2026/10/${res.report_id}.jpg`;
    expect(ctx.storage.put).toHaveBeenCalledWith('market-scout', key, Buffer.from('re-encoded'), 'image/jpeg');
    // Byte asli tidak pernah disimpan
    expect(ctx.storage.put.mock.calls[0][2]).not.toBe(JPEG);

    const insert = ctx.db.find('INSERT INTO platform.price_hunter_report')[0];
    const userHash = memberHash(ctx.config.memberHashPepper, MEMBER);
    // image_phash milik ai-ocr-worker (pHash) → BFF tidak menulis kolom itu.
    expect(insert.text).not.toContain('image_phash');
    expect(insert.text).not.toMatch(/\buser_id\b/);
    expect(insert.params[0]).toBe(res.report_id);
    expect(insert.params[1]).toBe(userHash);
    // user_enc: nomor HP terenkripsi AES-256-GCM, bukan plaintext
    const userEnc = insert.params[2] as Buffer;
    expect(userEnc.includes(Buffer.from(MEMBER))).toBe(false);
    expect(ctx.cipher.decrypt(userEnc, PII_AAD.PRICE_HUNTER_REPORT_USER)).toBe(MEMBER);
    expect(insert.params.slice(3)).toEqual(['Minyak Goreng 2L', 34500, 'Toko A', -6.2, 106.8, key]);
    expect(insert.params).not.toContain(MEMBER);

    expect(ctx.outbox.enqueue).toHaveBeenCalledWith(
      ctx.db,
      'ocr.requested',
      {
        schema_version: 2,
        report_id: res.report_id,
        user_hash: userHash,
        image_bucket: 'market-scout',
        image_object: key,
        product_name: 'Minyak Goreng 2L',
        reported_price: 34500,
        competitor_name: 'Toko A',
        geo: { lat: -6.2, lng: 106.8 },
        submitted_at: new Date(ctx.clock.ms).toISOString(),
      },
      `ocr.requested:${res.report_id}`,
    );
    expect(ctx.outbox.relayOnce).toHaveBeenCalled();
  });

  it('SEC-17: koordinat presisi tinggi dibulatkan 3 desimal di server (DB & payload)', async () => {
    const ctx = setup();
    const res = await ctx.service.submit(ctx.user, {
      fields: { ...FIELDS, lat: '-6.2087634512', lng: '106.8455990001' },
      photo: JPEG,
    });
    const insert = ctx.db.find('INSERT INTO platform.price_hunter_report')[0];
    expect(insert.params.slice(6, 8)).toEqual([-6.209, 106.846]);
    const payload = ctx.outbox.enqueue.mock.calls[0][2] as { geo: unknown; report_id: string };
    expect(payload.report_id).toBe(res.report_id);
    expect(payload.geo).toEqual({ lat: -6.209, lng: 106.846 });
  });

  it('§8.4: tanpa consent location aktif → koordinat dibuang (DB & payload), laporan tetap diterima', async () => {
    const ctx = setup();
    ctx.consents.isGranted.mockResolvedValue(false);
    await ctx.service.submit(ctx.user, { fields: FIELDS, photo: JPEG });
    expect(ctx.consents.isGranted).toHaveBeenCalledWith(MEMBER_ID, 'location');
    const insert = ctx.db.find('INSERT INTO platform.price_hunter_report')[0];
    expect(insert.params.slice(6, 8)).toEqual([null, null]);
    expect((ctx.outbox.enqueue.mock.calls[0][2] as { geo: unknown }).geo).toBeNull();
  });

  it('tanpa koordinat → consent tidak perlu dicek', async () => {
    const ctx = setup();
    await ctx.service.submit(ctx.user, { fields: { product_name: 'Gula', price: '15000' }, photo: JPEG });
    expect(ctx.consents.isGranted).not.toHaveBeenCalled();
  });

  it('SEC-19: antrean decode penuh (ApiError 429 dari ImageProcessor) diteruskan, bukan 415', async () => {
    const ctx = setup();
    ctx.images.normalize.mockRejectedValueOnce(Errors.rateLimited(5));
    const err = await catchApiError(() => ctx.service.submit(ctx.user, { fields: FIELDS, photo: JPEG }));
    expect(err.getStatus()).toBe(429);
  });

  it('bukan gambar (magic bytes) → 415, tidak ada upload', async () => {
    const ctx = setup();
    const err = await catchApiError(() => ctx.service.submit(ctx.user, { fields: FIELDS, photo: Buffer.from('%PDF-1.7 ...') }));
    expect(err.getStatus()).toBe(415);
    expect(ctx.storage.put).not.toHaveBeenCalled();
  });

  it('lolos magic bytes tetapi gagal decode sharp → 415', async () => {
    const ctx = setup();
    ctx.images.normalize.mockRejectedValueOnce(new Error('corrupt'));
    const err = await catchApiError(() => ctx.service.submit(ctx.user, { fields: FIELDS, photo: JPEG }));
    expect(err.getStatus()).toBe(415);
  });

  it('melebihi 8 MB → 413', async () => {
    const ctx = setup();
    const big = Buffer.concat([JPEG, Buffer.alloc(8 * 1024 * 1024)]);
    const err = await catchApiError(() => ctx.service.submit(ctx.user, { fields: FIELDS, photo: big }));
    expect(err.getStatus()).toBe(413);
  });

  it('cap harian 20 → laporan ke-21 ditolak 429; reset setelah tengah malam WIB', async () => {
    const ctx = setup();
    // 2026-10-05 03:00 UTC = 10:00 WIB
    for (let i = 0; i < 20; i += 1) await ctx.service.submit(ctx.user, { fields: FIELDS, photo: JPEG });
    const err = await catchApiError(() => ctx.service.submit(ctx.user, { fields: FIELDS, photo: JPEG }));
    expect(err.getStatus()).toBe(429);
    ctx.clock.advanceSeconds(14 * 3600 + 1); // lewat 00:00 WIB
    await expect(ctx.service.submit(ctx.user, { fields: FIELDS, photo: JPEG })).resolves.toBeDefined();
  });

  it.each([
    ['tanpa product_name', { ...FIELDS, product_name: '' }],
    ['harga bukan angka', { ...FIELDS, price: '12abc' }],
    ['harga nol', { ...FIELDS, price: '0' }],
    ['lat tanpa lng', { product_name: 'x', price: '1', lat: '-6.2' }],
    ['lat di luar rentang', { ...FIELDS, lat: '95' }],
  ])('field tidak valid (%s) → 400', async (_n, fields) => {
    const ctx = setup();
    const err = await catchApiError(() => ctx.service.submit(ctx.user, { fields: fields as Record<string, string>, photo: JPEG }));
    expect(err.getStatus()).toBe(400);
  });

  it('transaksi DB gagal → 503 dan objek MinIO dihapus', async () => {
    const ctx = setup(true);
    const err = await catchApiError(() => ctx.service.submit(ctx.user, { fields: FIELDS, photo: JPEG }));
    expect(err.getStatus()).toBe(503);
    expect(ctx.storage.remove).toHaveBeenCalledTimes(1);
  });
});

describe('MarketScoutService.get', () => {
  it('hanya pemilik; id bukan UUID / tidak ditemukan → 404', async () => {
    const ctx = setup();
    let err = await catchApiError(() => ctx.service.get(ctx.user, 'bukan-uuid'));
    expect(err.getStatus()).toBe(404);
    err = await catchApiError(() => ctx.service.get(ctx.user, '9b2e1c4d-5f6a-4b7c-8d9e-0f1a2b3c4d5e'));
    expect(err.getStatus()).toBe(404);
    // kepemilikan dibandingkan lewat user_hash dari token, bukan nomor HP
    expect(ctx.db.queries[0].text).toContain('r.user_hash = $2');
    expect(ctx.db.queries[0].params).toEqual([
      '9b2e1c4d-5f6a-4b7c-8d9e-0f1a2b3c4d5e',
      memberHash(ctx.config.memberHashPepper, MEMBER),
    ]);
  });
});

describe('readReportForm', () => {
  async function* parts(...items: unknown[]): AsyncGenerator<unknown> {
    for (const i of items) yield i;
  }
  const file = (fieldname: string, buf: Buffer, truncated = false) => ({
    fieldname,
    toBuffer: async () => buf,
    file: { truncated },
  });

  it('mengumpulkan field yang diizinkan dan satu foto', async () => {
    const form = await readReportForm(
      parts({ fieldname: 'product_name', value: 'Gula' }, { fieldname: 'evil', value: 'x' }, file('photo', JPEG)),
      { maxFileBytes: 1024 },
    );
    expect(form).toEqual({ fields: { product_name: 'Gula' }, photo: JPEG });
  });

  it('file terpotong (limit busboy) → 413', async () => {
    const err = await catchApiError(() => readReportForm(parts(file('photo', JPEG, true)), { maxFileBytes: 1024 }));
    expect(err.getStatus()).toBe(413);
  });

  it('error plugin FST_REQ_FILE_TOO_LARGE → 413', async () => {
    const tooLarge = {
      fieldname: 'photo',
      toBuffer: async () => {
        throw Object.assign(new Error('too large'), { code: 'FST_REQ_FILE_TOO_LARGE' });
      },
    };
    const err = await catchApiError(() => readReportForm(parts(tooLarge), { maxFileBytes: 1024 }));
    expect(err.getStatus()).toBe(413);
  });

  it('dua file / field file selain photo → 400', async () => {
    let err = await catchApiError(() => readReportForm(parts(file('photo', JPEG), file('photo', JPEG)), { maxFileBytes: 1024 }));
    expect(err.getStatus()).toBe(400);
    err = await catchApiError(() => readReportForm(parts(file('doc', JPEG)), { maxFileBytes: 1024 }));
    expect(err.getStatus()).toBe(400);
  });
});
