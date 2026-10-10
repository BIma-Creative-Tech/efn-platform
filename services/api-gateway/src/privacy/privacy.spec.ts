import { Logger } from '@nestjs/common';
import { MemberService } from '../common/member/member.service';
import { MetricsService } from '../common/metrics/metrics.service';
import { AmqpService, PermanentMessageError } from '../common/amqp/amqp.service';
import { PII_AAD } from '../common/pii/crypto';
import { createHash } from 'node:crypto';
import { memberHash, redisKeyHash } from '../common/pii/pii';
import { RateLimiter } from '../common/rate-limit/rate-limiter';
import { AccessTokenService } from '../common/security/access-token.service';
import {
  FakeClock,
  FakeDb,
  FakeKvStore,
  FakeMemberTable,
  FakeWhatsAppSender,
  QueryHandler,
  catchApiError,
  testCipher,
  testConfig,
} from '../common/testing/fakes';
import { LoyaltyService } from '../loyalty/loyalty.service';
import { OtpService } from '../otp/otp.service';
import { ConsentService, UNKNOWN_TEXT_SHA256 } from './consent.service';
import { parseErasureComplete, parseReauth, parseSetConsent } from './dto/privacy.dto';
import { DsarReceiptService } from './dsar-receipt.service';
import { DsarService, STEP_UP_USED_PREFIX } from './dsar.service';
import { ERASURE_COMPLETED_CONSUMER, ErasureCompletedConsumer, parseErasureCompleted } from './erasure-completed.consumer';
import { ErasureService } from './erasure.service';
import { erasureSuppressionTag } from '../common/pii/erasure-suppression';

const PHONE = '6281234567890';
const MEMBER_ID = '8d7b5a1e-2c3f-4a5b-9c8d-7e6f5a4b3c2d';
const REQ_ID = '5e0c3a2b-1d4f-4e6a-8b7c-9d0e1f2a3b4c';
const SHA = 'ab'.repeat(32);

function setup(overrides: Record<string, string> = {}, handler: QueryHandler = () => undefined) {
  const clock = new FakeClock();
  const config = testConfig({ CONSENT_LOCATION_VERSION: 'v2', CONSENT_LOCATION_TEXT_SHA256: SHA, ...overrides });
  const kv = new FakeKvStore(clock.now);
  const cipher = testCipher(config);
  const members = new FakeMemberTable();
  const hash = memberHash(config.memberHashPepper, PHONE);
  members.rows.set(MEMBER_ID, {
    id: MEMBER_ID,
    member_hash: hash,
    member_ref_enc: cipher.encrypt(PHONE, PII_AAD.MEMBER_REF),
    created_at: new Date(clock.ms),
    deleted_at: null,
  });
  const db = new FakeDb((t, p) => members.handle(t, p) ?? handler(t, p));
  const sender = new FakeWhatsAppSender();
  const limiter = new RateLimiter(kv);
  limiter.now = clock.now;
  const otp = new OtpService(config, kv, db, sender, new MetricsService(), limiter, cipher);
  otp.now = clock.now;
  const tokens = new AccessTokenService(config);
  tokens.now = clock.now;
  const loyalty = new LoyaltyService(config, db, cipher);
  const memberService = new MemberService(config, db, cipher);
  const consents = new ConsentService(config, db, cipher);
  const receipts = new DsarReceiptService(config, db, limiter);
  receipts.now = clock.now;
  const dsar = new DsarService(config, db, memberService, tokens, otp, limiter, loyalty, consents, receipts, kv);
  dsar.now = clock.now;
  const user = { memberId: MEMBER_ID, memberHash: hash };
  return { clock, config, kv, cipher, db, sender, tokens, consents, dsar, receipts, user, hash, members };
}

describe('ConsentService (§8.4/§8.7/§8.9)', () => {
  it('grant versi aktif → INSERT append-only dengan text_sha256 & ip_enc terenkripsi', async () => {
    const ctx = setup();
    const res = await ctx.consents.set(MEMBER_ID, 'location', { granted: true, version: 'v2' }, '203.0.113.7');
    expect(res).toMatchObject({ purpose: 'location', granted: true, version: 'v2' });
    const q = ctx.db.find('INSERT INTO platform.consent')[0];
    expect(q.params.slice(0, 5)).toEqual([MEMBER_ID, 'location', true, 'v2', SHA]);
    expect(ctx.cipher.decrypt(q.params[5] as Buffer, PII_AAD.CONSENT_IP)).toBe('203.0.113.7');
    expect(ctx.db.queries.some((x) => /UPDATE platform\.consent|DELETE FROM platform\.consent/.test(x.text))).toBe(false);
  });

  it('grant versi lama → 409 stale_text; teks belum dikonfigurasi → 503; penarikan selalu diterima', async () => {
    const ctx = setup();
    const err = await catchApiError(() => ctx.consents.set(MEMBER_ID, 'location', { granted: true, version: 'v1' }, null));
    expect(err.getStatus()).toBe(409);
    expect(err.body).toEqual({ error: { code: 'stale_text', message: expect.any(String) } });
    const unconf = await catchApiError(() => ctx.consents.set(MEMBER_ID, 'marketing', { granted: true, version: 'v1' }, null));
    expect(unconf.getStatus()).toBe(503);
    await ctx.consents.set(MEMBER_ID, 'location', { granted: false, version: 'v1' }, null);
    await ctx.consents.set(MEMBER_ID, 'marketing', { granted: false, version: 'v1' }, null);
    const ins = ctx.db.find('INSERT INTO platform.consent');
    expect(ins.map((q) => q.params[4])).toEqual([SHA, UNKNOWN_TEXT_SHA256]);
  });

  it('list: purpose tanpa riwayat → granted false; DTO validasi', async () => {
    const ctx = setup({}, (t) =>
      t.includes('DISTINCT ON') ? { rows: [{ purpose: 'location', granted: true, version: 'v2', created_at: new Date(0) }], rowCount: 1 } : undefined,
    );
    expect(await ctx.consents.list(MEMBER_ID)).toEqual([
      { purpose: 'location', granted: true, version: 'v2', updated_at: '1970-01-01T00:00:00.000Z' },
      { purpose: 'marketing', granted: false, version: null, updated_at: null },
    ]);
    expect(parseSetConsent({ granted: true, version: 'v2' })).toEqual({ granted: true, version: 'v2' });
    expect((await catchApiError(() => parseSetConsent({ granted: 'ya', version: 'v2' }))).getStatus()).toBe(400);
  });

  it('G6 QA: format versi frontend `<purpose>@<tanggal>` diterima DTO & AppConfig (kontrak frontend ↔ BFF)', async () => {
    expect(parseSetConsent({ granted: true, version: 'location@2026-10-06' })).toEqual({ granted: true, version: 'location@2026-10-06' });
    expect((await catchApiError(() => parseSetConsent({ granted: true, version: 'a b' }))).getStatus()).toBe(400);
    const ctx = setup({ CONSENT_LOCATION_VERSION: 'location@2026-10-06' });
    const res = await ctx.consents.set(MEMBER_ID, 'location', { granted: true, version: 'location@2026-10-06' }, null);
    expect(res).toMatchObject({ granted: true, version: 'location@2026-10-06' });
  });
});

describe('DsarService (§8.9)', () => {
  it('reauth dua langkah: {} → 202 OTP ke nomor sendiri; {challenge_id, code} → 200 step_up_token', async () => {
    const ctx = setup();
    expect(parseReauth({})).toEqual({ kind: 'request' });
    const r1 = await ctx.dsar.reauth(ctx.user, { kind: 'request' }, '203.0.113.7');
    expect(r1.status).toBe(202);
    expect(ctx.sender.otps[0].to).toBe(PHONE);
    const body1 = r1.body as { challenge_id: string };
    const r2 = await ctx.dsar.reauth(ctx.user, { kind: 'verify', challengeId: body1.challenge_id, code: ctx.sender.lastCode() }, null);
    expect(r2.status).toBe(200);
    const body2 = r2.body as { step_up_token: string; expires_in: number };
    expect(body2.expires_in).toBe(600);
    expect(ctx.tokens.verifyStepUp(body2.step_up_token, MEMBER_ID)).toBe(true);
    expect(Object.keys(body2).sort()).toEqual(['expires_in', 'step_up_token']);
  });

  it('challenge milik member lain tidak menghasilkan step-up', async () => {
    const ctx = setup();
    const r1 = await ctx.dsar.reauth(ctx.user, { kind: 'request' }, null);
    const other = { memberId: '00000000-0000-4000-8000-000000000001', memberHash: 'c'.repeat(64) };
    const err = await catchApiError(() =>
      ctx.dsar.reauth(other, { kind: 'verify', challengeId: (r1.body as { challenge_id: string }).challenge_id, code: ctx.sender.lastCode() }, null),
    );
    expect(err.getStatus()).toBe(400);
  });

  it('step-up: hilang → 403 step_up_required; invalid/kedaluwarsa → 403 step_up_invalid', async () => {
    const ctx = setup();
    let err = await catchApiError(() => ctx.dsar.requireStepUp(ctx.user, undefined));
    expect(err.body).toEqual({ error: { code: 'step_up_required', message: expect.any(String) } });
    err = await catchApiError(() => ctx.dsar.requireStepUp(ctx.user, 'a.b.c'));
    expect(err.body).toEqual({ error: { code: 'step_up_invalid', message: expect.any(String) } });
    const t = ctx.tokens.issueStepUp(MEMBER_ID).token;
    expect(() => ctx.dsar.requireStepUp(ctx.user, t)).not.toThrow();
    ctx.clock.advanceSeconds(601);
    err = await catchApiError(() => ctx.dsar.requireStepUp(ctx.user, t));
    expect(err.getStatus()).toBe(403);
  });

  it('DELETE /v1/me → 202 {request_id, scheduled_at (+7 hari), points_forfeited}; idempotent bila sudah aktif', async () => {
    let active: { id: string; scheduled_at: Date } | null = null;
    const ctx = setup({}, (t, p) => {
      if (t.includes('SUM(delta_points)')) return { rows: [{ balance: 350 }], rowCount: 1 };
      if (t.includes("status IN ('scheduled', 'processing')")) return active ? { rows: [active], rowCount: 1 } : { rows: [], rowCount: 0 };
      if (t.includes('INSERT INTO platform.dsar_request')) {
        active = { id: p[0] as string, scheduled_at: new Date(p[3] as string) };
        return { rows: [], rowCount: 1 };
      }
      return undefined;
    });
    const res = await ctx.dsar.requestDelete(ctx.user);
    expect(res.points_forfeited).toBe(350);
    expect(res.scheduled_at).toBe(new Date(ctx.clock.ms + 7 * 86_400_000).toISOString());
    const ins = ctx.db.find('INSERT INTO platform.dsar_request')[0];
    expect(ins.text).toContain("'delete', 'scheduled'");
    expect(ins.params.slice(1, 3)).toEqual([MEMBER_ID, ctx.hash]);
    // R6: receipt_token opak, terikat request_id, tanpa PII
    expect(Object.keys(res).sort()).toEqual(['points_forfeited', 'receipt_token', 'request_id', 'scheduled_at']);
    expect(res.receipt_token).toMatch(/^v1\.\d+\.[A-Za-z0-9_-]{43}$/);
    expect(res.receipt_token).not.toContain(PHONE);
    expect(res.receipt_token).not.toContain(res.request_id);
    expect(ctx.receipts.verify(res.request_id, res.receipt_token)).toBe(true);
    const again = await ctx.dsar.requestDelete(ctx.user);
    expect(again.request_id).toBe(res.request_id);
    expect(ctx.receipts.verify(again.request_id, again.receipt_token)).toBe(true);
    expect(ctx.db.find('INSERT INTO platform.dsar_request')).toHaveLength(1);
  });

  it('SEC-40: step-up DELETE sekali pakai (jti di Redis sampai exp); pakai ulang/milik member lain → 403 step_up_invalid', async () => {
    const ctx = setup();
    const t = ctx.tokens.issueStepUp(MEMBER_ID).token;
    await ctx.dsar.consumeStepUp(ctx.user, t);
    const used = ctx.kv.keys().filter((k) => k.startsWith(STEP_UP_USED_PREFIX));
    expect(used).toHaveLength(1);
    expect(used[0]).toMatch(/^otp:stepup-used:[0-9a-f-]{36}$/);
    expect(await ctx.kv.ttl(used[0])).toBeGreaterThan(590);
    expect(await ctx.kv.ttl(used[0])).toBeLessThanOrEqual(601);
    const reuse = await catchApiError(() => ctx.dsar.consumeStepUp(ctx.user, t));
    expect(reuse.body).toEqual({ error: { code: 'step_up_invalid', message: expect.any(String) } });
    const other = ctx.tokens.issueStepUp('00000000-0000-4000-8000-000000000001').token;
    expect((await catchApiError(() => ctx.dsar.consumeStepUp(ctx.user, other))).getStatus()).toBe(403);
    expect((await catchApiError(() => ctx.dsar.consumeStepUp(ctx.user, undefined))).body).toEqual({
      error: { code: 'step_up_required', message: expect.any(String) },
    });
    // token baru tetap bisa dipakai; requireStepUp (ekspor) tidak menandai jti
    const t2 = ctx.tokens.issueStepUp(MEMBER_ID).token;
    ctx.dsar.requireStepUp(ctx.user, t2);
    ctx.dsar.requireStepUp(ctx.user, t2);
    await expect(ctx.dsar.consumeStepUp(ctx.user, t2)).resolves.toBeUndefined();
  });

  it('cancel dalam masa tenggang → cancelled; tidak ada → 404; requests → { items }', async () => {
    let cancelled = false;
    const ctx = setup({}, (t) => {
      if (t.includes("SET status = 'cancelled'")) {
        if (cancelled) return { rows: [], rowCount: 0 };
        cancelled = true;
        return { rows: [{ id: REQ_ID }], rowCount: 1 };
      }
      if (t.includes('ORDER BY requested_at DESC LIMIT 50')) {
        return {
          rows: [{ id: REQ_ID, type: 'delete', status: 'cancelled', requested_at: new Date(0), scheduled_at: new Date(0), completed_at: null }],
          rowCount: 1,
        };
      }
      return undefined;
    });
    expect(await ctx.dsar.cancelDelete(ctx.user)).toEqual({ request_id: REQ_ID, status: 'cancelled' });
    expect((await catchApiError(() => ctx.dsar.cancelDelete(ctx.user))).getStatus()).toBe(404);
    const list = await ctx.dsar.listRequests(ctx.user);
    expect(list.items[0]).toEqual({
      id: REQ_ID,
      type: 'delete',
      status: 'cancelled',
      requested_at: '1970-01-01T00:00:00.000Z',
      scheduled_at: '1970-01-01T00:00:00.000Z',
      completed_at: null,
    });
  });

  it('export: data milik sendiri + nomor penuh, dicatat sebagai dsar_request type export', async () => {
    const ctx = setup({}, (t) => (t.includes('SUM(delta_points)') ? { rows: [{ balance: 10 }], rowCount: 1 } : undefined));
    const data = await ctx.dsar.exportData(ctx.user);
    expect(data.profile).toMatchObject({ member_id: MEMBER_ID, member_ref: PHONE, member_ref_masked: '62812****890' });
    expect((data.points as { balance: number }).balance).toBe(10);
    const ins = ctx.db.find('INSERT INTO platform.dsar_request')[0];
    expect(ins.text).toContain("'export', 'completed'");
    // query data selalu dibatasi member_hash / member_id pemanggil
    for (const q of ctx.db.find('WHERE member_ref_hash = $1').concat(ctx.db.find('WHERE user_hash = $1'))) expect(q.params[0]).toBe(ctx.hash);
  });
});

describe('ErasureService (§8.4)', () => {
  function erasureSetup(status = 'scheduled', overrides: Record<string, string> = {}) {
    const storage = { put: jest.fn(), remove: jest.fn().mockRejectedValueOnce(new Error('minio down')).mockResolvedValue(undefined) };
    const hash = 'd'.repeat(64);
    let stores = ['platform', 'odoo', 'clickhouse'];
    const db = new FakeDb((t, p) => {
      if (t.includes('SELECT image_object')) return { rows: [{ image_object: 'reports/2026/10/a.jpg' }, { image_object: 'reports/2026/10/b.jpg' }], rowCount: 2 };
      if (t.includes('pg_try_advisory_xact_lock')) return { rows: [{ ok: true }], rowCount: 1 };
      if (t.includes("SET status = 'processing'")) {
        return status === 'scheduled' ? { rows: [{ member_id: MEMBER_ID, member_hash: hash }], rowCount: 1 } : { rows: [], rowCount: 0 };
      }
      if (t.includes('GROUP BY company_code')) return { rows: [{ company_code: 'EFN', balance: '120' }, { company_code: 'FNB', balance: 30 }], rowCount: 2 };
      if (t.includes('array_remove')) {
        stores = stores.filter((s) => s !== p[1]);
        return { rows: [{ status: stores.length === 0 ? 'completed' : 'processing' }], rowCount: 1 };
      }
      return undefined;
    });
    const config = testConfig(overrides);
    const svc = new ErasureService(config, db, storage);
    return { svc, db, storage, hash, config, stores: () => stores };
  }

  it('setelah masa tenggang: processing + erasure platform dalam satu transaksi (tombstone, forfeit per PT, NULL PII, hapus member)', async () => {
    const ctx = erasureSetup();
    expect(await ctx.svc.processDue(REQ_ID)).toBe(true);
    expect(ctx.storage.remove).toHaveBeenCalledTimes(2); // satu gagal → tetap lanjut (lifecycle)

    // SEC-31: tombstone acak 32 byte, bukan turunan request_id/member_hash, sama untuk semua baris subjek
    const forfeits = ctx.db.find('INSERT INTO platform.loyalty_ledger');
    const tomb = forfeits[0].params[0] as string;
    expect(tomb).toMatch(/^[0-9a-f]{64}$/);
    expect(tomb).not.toBe(createHash('sha256').update(`erased:${REQ_ID}`, 'utf8').digest('hex'));
    expect(tomb).not.toBe(ctx.hash);
    expect(forfeits.map((q) => q.params.slice(0, 3))).toEqual([
      [tomb, 'EFN', -120],
      [tomb, 'FNB', -30],
    ]);
    for (const f of forfeits) {
      expect(f.text).toContain('erased_at');
      expect(f.text).toMatch(/'dsar', NULL, \$4/); // source_ref NULL
      expect(f.params[3]).toMatch(/^dsar:forfeit:[0-9a-f-]{36}$/);
      expect(JSON.stringify(f.params)).not.toContain(REQ_ID);
    }
    expect(forfeits[0].params[3]).not.toBe(forfeits[1].params[3]);
    // request_id tidak pernah muncul bersama tombstone dalam satu statement
    for (const q of ctx.db.queries) {
      if (q.params.includes(tomb)) expect(q.params).not.toContain(REQ_ID);
    }
    // dsar_request lain milik subjek tidak lagi memuat member_hash
    expect(ctx.db.find('UPDATE platform.dsar_request SET member_hash = NULL')[0].params).toEqual([MEMBER_ID, REQ_ID]);
    // foto dihapus SETELAH transaksi (SEC-40): daftar diambil sebelum tombstone
    expect(ctx.db.find('SELECT image_object')[0].params).toEqual([ctx.hash]);
    const ledger = ctx.db.find('UPDATE platform.loyalty_ledger')[0];
    expect(ledger.text).toContain('member_ref_enc = NULL');
    expect(ledger.params).toEqual([ctx.hash, tomb]);
    const report = ctx.db.find('UPDATE platform.price_hunter_report')[0];
    expect(report.text).toMatch(/user_enc = NULL.*geo_lat = NULL/s);
    expect(ctx.db.find('UPDATE platform.otp_audit')[0].params).toEqual([ctx.hash, tomb]);
    expect(ctx.db.find('DELETE FROM platform.loyalty_reservation')[0].params).toEqual([ctx.hash]);
    expect(ctx.db.find('DELETE FROM platform.loyalty_pos_earn')[0].params).toEqual([ctx.hash]);
    expect(ctx.db.find('UPDATE platform.consent SET ip_enc = NULL')[0].params).toEqual([MEMBER_ID]);
    expect(ctx.db.find('DELETE FROM platform.member')[0].params).toEqual([MEMBER_ID]);
    expect(ctx.db.find('UPDATE platform.member SET deleted_at')).toHaveLength(1);
    expect(ctx.stores()).toEqual(['odoo', 'clickhouse']);
    // urutan: processing sebelum erasure
    const idx = (frag: string) => ctx.db.queries.findIndex((q) => q.text.includes(frag));
    expect(idx("SET status = 'processing'")).toBeLessThan(idx('UPDATE platform.loyalty_ledger'));
  });

  it('SEC-65: processing menyimpan tag daftar tolak migrasi (HMAC domain terpisah, bukan member_hash) selama ERASURE_SUPPRESSION_DAYS', async () => {
    const ctx = erasureSetup();
    await ctx.svc.processDue(REQ_ID);
    const ins = ctx.db.find('INSERT INTO platform.erasure_suppression');
    expect(ins).toHaveLength(1);
    expect(ins[0].params).toEqual([erasureSuppressionTag(ctx.config.erasureSuppressionKey, ctx.hash), 90]);
    expect(ins[0].params[0]).not.toBe(ctx.hash);
    expect(ins[0].text).toContain('GREATEST'); // erasure ulang memperpanjang, tidak memendekkan
    expect(JSON.stringify(ins[0].params)).not.toContain(REQ_ID);
    // di dalam transaksi erasure yang sama, setelah status processing
    const idx = (frag: string) => ctx.db.queries.findIndex((q) => q.text.includes(frag));
    expect(idx("SET status = 'processing'")).toBeLessThan(idx('INSERT INTO platform.erasure_suppression'));

    const off = erasureSetup('scheduled', { ERASURE_SUPPRESSION_DAYS: '0' });
    await off.svc.processDue(REQ_ID);
    expect(off.db.find('INSERT INTO platform.erasure_suppression')).toHaveLength(0);
  });

  it('SEC-65: siklus job menghapus tag kedaluwarsa; kegagalan retensi tidak menghentikan erasure', async () => {
    const ctx = erasureSetup();
    await ctx.svc.runOnce();
    expect(ctx.db.find('DELETE FROM platform.erasure_suppression WHERE expires_at <= now()')).toHaveLength(1);
    const db = new FakeDb((t) => {
      if (t.includes('DELETE FROM platform.erasure_suppression')) throw new Error('relation does not exist');
      return undefined;
    });
    const svc = new ErasureService(testConfig(), db, { put: jest.fn(), remove: jest.fn() });
    expect(await svc.runOnce()).toEqual({ processed: 0, failed: 0, skipped: false });
  });

  it('request dibatalkan sebelum diproses → tidak ada erasure', async () => {
    const ctx = erasureSetup('cancelled');
    expect(await ctx.svc.processDue(REQ_ID)).toBe(false);
    expect(ctx.db.find('UPDATE platform.loyalty_ledger')).toHaveLength(0);
    expect(ctx.storage.remove).not.toHaveBeenCalled();
  });

  it('dua erasure menghasilkan tombstone berbeda (tidak deterministik)', async () => {
    const a = erasureSetup();
    const b = erasureSetup();
    await a.svc.processDue(REQ_ID);
    await b.svc.processDue(REQ_ID);
    const ta = a.db.find('UPDATE platform.otp_audit')[0].params[1];
    const tb = b.db.find('UPDATE platform.otp_audit')[0].params[1];
    expect(ta).not.toBe(tb);
  });

  it('SEC-31: completed → member_hash di-NULL-kan; pull Odoo hanya request processing ber-hash', async () => {
    const ctx = erasureSetup();
    await ctx.svc.completeStore('odoo', REQ_ID);
    const upd = ctx.db.find('array_remove')[0].text;
    expect(upd).toMatch(/member_hash = CASE WHEN cardinality\(array_remove\(stores_pending, \$2::text\)\) = 0\s+THEN NULL ELSE member_hash END/);
    await ctx.svc.pendingFor('odoo');
    expect(ctx.db.find('ANY(stores_pending) AND member_hash IS NOT NULL')[0].text).toContain("status = 'processing'");
  });

  it('Odoo pull/complete & ClickHouse event → completed setelah semua store; DTO {store:"odoo", records:0}', async () => {
    const ctx = erasureSetup();
    await ctx.svc.processDue(REQ_ID);
    expect(await ctx.svc.completeStore('odoo', REQ_ID)).toEqual({ ok: true, status: 'processing' });
    expect(await ctx.svc.completeStore('clickhouse', REQ_ID)).toEqual({ ok: true, status: 'completed' });
    expect(parseErasureComplete({ store: 'odoo', records: 0 })).toEqual({ store: 'odoo', records: 0 });
    expect((await catchApiError(() => parseErasureComplete({ store: 'clickhouse', records: 1 }))).getStatus()).toBe(400);
  });

  it('completeStore request tidak dikenal → not_found; status lain → not_processing', async () => {
    const db = new FakeDb((t) => (t.includes('SELECT status FROM platform.dsar_request') ? { rows: [{ status: 'cancelled' }], rowCount: 1 } : undefined));
    const svc = new ErasureService(testConfig(), db, { put: jest.fn(), remove: jest.fn() });
    expect(await svc.completeStore('odoo', REQ_ID)).toEqual({ ok: false, reason: 'not_processing' });
    const db2 = new FakeDb();
    const svc2 = new ErasureService(testConfig(), db2, { put: jest.fn(), remove: jest.fn() });
    expect(await svc2.completeStore('odoo', REQ_ID)).toEqual({ ok: false, reason: 'not_found' });
  });
});

describe('ErasureCompletedConsumer (privacy.erasure.completed)', () => {
  const payload = { schema_version: 2, idempotency_key: `erasure:${REQ_ID}:clickhouse`, request_id: REQ_ID, store: 'clickhouse' };

  function consumerSetup() {
    const claimed = new Set<string>();
    const db = new FakeDb((t, p) => {
      if (t.includes('INSERT INTO platform.processed_message')) {
        const k = `${p[0]}|${p[1]}`;
        if (claimed.has(k)) return { rows: [], rowCount: 0 };
        claimed.add(k);
        return { rows: [{ consumer: p[0] }], rowCount: 1 };
      }
      return undefined;
    });
    const erasure = { completeStore: jest.fn().mockResolvedValue({ ok: true, status: 'completed' }) };
    const consumer = new ErasureCompletedConsumer(
      {} as AmqpService,
      db,
      erasure as unknown as ErasureService,
      testConfig({ AMQP_CONSUMERS_ENABLED: 'false' }),
    );
    return { consumer, erasure, db };
  }

  it('user_id efn-etl diterima; kiriman ulang tiap siklus → duplicate (processed_message)', async () => {
    const ctx = consumerSetup();
    expect(await ctx.consumer.handle(payload, { userId: 'efn-etl' })).toBe('applied');
    expect(await ctx.consumer.handle(payload, { userId: 'efn-etl' })).toBe('duplicate');
    expect(ctx.erasure.completeStore).toHaveBeenCalledTimes(1);
    expect(ctx.db.find('processed_message')[0].params[0]).toBe(ERASURE_COMPLETED_CONSUMER);
  });

  it('publisher lain / payload salah / request tidak processing → DLQ (PermanentMessageError)', async () => {
    const ctx = consumerSetup();
    await expect(ctx.consumer.handle(payload, { userId: 'efn-ocr' })).rejects.toBeInstanceOf(PermanentMessageError);
    await expect(ctx.consumer.handle(payload, { userId: undefined })).rejects.toBeInstanceOf(PermanentMessageError);
    expect(() => parseErasureCompleted({ ...payload, store: 'odoo' })).toThrow(PermanentMessageError);
    expect(() => parseErasureCompleted({ ...payload, idempotency_key: 'erasure:lain:clickhouse' })).toThrow(PermanentMessageError);
    ctx.erasure.completeStore.mockResolvedValueOnce({ ok: false, reason: 'not_found' });
    await expect(ctx.consumer.handle(payload, { userId: 'efn-etl' })).rejects.toBeInstanceOf(PermanentMessageError);
  });
});

describe('DsarReceiptService (R6, §8.13)', () => {
  const OTHER_ID = '6f1d2c3b-4a5e-4f60-9a7b-8c9d0e1f2a3b';
  function receiptSetup(overrides: Record<string, string> = {}) {
    const ctx = setup(overrides, (t, p) =>
      t.includes("FROM platform.dsar_request WHERE id = $1 AND type = 'delete'") && (p[0] === REQ_ID || p[0] === OTHER_ID)
        ? {
            rows: [{ id: p[0], type: 'delete', status: 'completed', requested_at: new Date(0), scheduled_at: new Date(0), completed_at: new Date(1000) }],
            rowCount: 1,
          }
        : undefined,
    );
    return ctx;
  }

  it('receipt sah → status tanpa PII; format opak tanpa request_id', async () => {
    const ctx = receiptSetup();
    const token = ctx.receipts.issue(REQ_ID);
    expect(token).not.toContain(REQ_ID);
    const res = await ctx.receipts.status(REQ_ID, token, '203.0.113.7');
    expect(res).toEqual({
      id: REQ_ID,
      type: 'delete',
      status: 'completed',
      requested_at: '1970-01-01T00:00:00.000Z',
      scheduled_at: '1970-01-01T00:00:00.000Z',
      completed_at: '1970-01-01T00:00:01.000Z',
    });
    expect(Object.keys(res)).not.toEqual(expect.arrayContaining(['member_hash']));
  });

  it('token salah / milik request lain / kedaluwarsa (30 hari) / hilang / id bukan UUID → 404 generik', async () => {
    const ctx = receiptSetup();
    const token = ctx.receipts.issue(REQ_ID);
    const i = token.length - 10;
    const tampered = `${token.slice(0, i)}${token[i] === 'A' ? 'B' : 'A'}${token.slice(i + 1)}`;
    const extended = token.replace(/^v1\.(\d+)\./, (_m, e: string) => `v1.${Number(e) + 86_400}.`);
    const cases: [string, string | undefined][] = [
      [REQ_ID, tampered],
      [REQ_ID, extended],
      [OTHER_ID, token],
      [REQ_ID, undefined],
      [REQ_ID, ''],
      [REQ_ID, 'x'.repeat(500)],
      ['not-a-uuid', token],
    ];
    for (const [id, tok] of cases) {
      const err = await catchApiError(() => ctx.receipts.status(id, tok, null));
      expect(err.getStatus()).toBe(404);
      expect(err.body).toEqual({ error: { code: 'not_found', message: expect.any(String) } });
    }
    // masih berlaku di hari ke-29, kedaluwarsa setelah 30 hari
    ctx.clock.advanceSeconds(29 * 86_400);
    await expect(ctx.receipts.status(REQ_ID, token, null)).resolves.toMatchObject({ id: REQ_ID });
    ctx.clock.advanceSeconds(86_400);
    expect((await catchApiError(() => ctx.receipts.status(REQ_ID, token, null))).getStatus()).toBe(404);
  });

  it('request tidak ada (receipt sah) → 404; rate limit per IP → 429', async () => {
    const ctx = receiptSetup({ DSAR_RECEIPT_RL_LIMIT: '3' });
    const missing = '0e9d8c7b-6a5f-4e3d-8c2b-1a0f9e8d7c6b';
    expect((await catchApiError(() => ctx.receipts.status(missing, ctx.receipts.issue(missing), '198.51.100.1'))).getStatus()).toBe(404);
    await ctx.receipts.status(REQ_ID, ctx.receipts.issue(REQ_ID), '198.51.100.1');
    await catchApiError(() => ctx.receipts.status(REQ_ID, 'salah', '198.51.100.1'));
    const err = await catchApiError(() => ctx.receipts.status(REQ_ID, ctx.receipts.issue(REQ_ID), '198.51.100.1'));
    expect(err.getStatus()).toBe(429);
    // IP lain tidak terpengaruh; key Redis tidak memuat IP mentah
    await expect(ctx.receipts.status(REQ_ID, ctx.receipts.issue(REQ_ID), '198.51.100.2')).resolves.toMatchObject({ id: REQ_ID });
    expect(ctx.kv.keys().some((k) => k.includes('198.51.100'))).toBe(false);
  });

  it('kunci receipt terpisah dari jwt_secret (HKDF) — receipt tidak berlaku bila JWT_SECRET lain', () => {
    const a = receiptSetup();
    const b = receiptSetup();
    expect(b.receipts.verify(REQ_ID, a.receipts.issue(REQ_ID))).toBe(false);
    expect(a.config.dsarReceiptKey.equals(Buffer.from(a.config.jwtSecret))).toBe(false);
  });
});

describe('SEC-100: erasure & order online terbuka', () => {
  it('kunci member yang sama dengan checkout + cek ulang; masih ada order terbuka → ditunda (rollback), anomali, member tidak dihapus', async () => {
    const db = new FakeDb((t) => {
      if (t.includes('pg_try_advisory_xact_lock')) return { rows: [{ ok: true }], rowCount: 1 };
      if (t.includes("SET status = 'processing'")) return { rows: [{ member_id: MEMBER_ID, member_hash: 'e'.repeat(64) }], rowCount: 1 };
      if (t.includes('FROM platform.online_order o')) return { rows: [{ n: '1' }], rowCount: 1 };
      return undefined;
    });
    const svc = new ErasureService(testConfig(), db, { put: jest.fn(), remove: jest.fn() });
    const warn = jest.spyOn(Logger.prototype, 'warn').mockImplementation(() => undefined);
    try {
      expect(await svc.processDue(REQ_ID)).toBe(false);
      expect(db.find("pg_advisory_xact_lock(hashtext('efn:online:member:'")[0].params).toEqual([MEMBER_ID]);
      expect(db.find('DELETE FROM platform.member')).toHaveLength(0);
      expect(db.find('UPDATE platform.member SET deleted_at')).toHaveLength(0);
      expect(warn.mock.calls.some((c) => String(c[0]).includes('erasure_deferred_open_orders'))).toBe(true);
    } finally {
      warn.mockRestore();
    }
  });
});

describe('Erasure: cart & quote online di Redis (§10.8.3)', () => {
  it('setelah erasure platform sukses: cart:<member_id>, oq:<id> terindeks, dan indeks ber-HMAC dihapus', async () => {
    const config = testConfig();
    const kv = new FakeKvStore();
    const idx = `oq:m:${redisKeyHash(config.redisKeyPepper, MEMBER_ID)}`;
    const qid = '7e0c3a2b-1d4f-4e6a-8b7c-9d0e1f2a3b4c';
    await kv.setEx(`cart:${MEMBER_ID}`, '{}', 600);
    await kv.setEx(`oq:${qid}`, '{}', 600);
    await kv.setEx(idx, JSON.stringify([qid]), 600);
    await kv.setEx('cart:00000000-0000-4000-8000-000000000001', '{}', 600); // member lain tidak tersentuh
    const db = new FakeDb((t) => {
      if (t.includes('pg_try_advisory_xact_lock')) return { rows: [{ ok: true }], rowCount: 1 };
      if (t.includes("SET status = 'processing'")) return { rows: [{ member_id: MEMBER_ID, member_hash: 'f'.repeat(64) }], rowCount: 1 };
      return undefined;
    });
    const svc = new ErasureService(config, db, { put: jest.fn(), remove: jest.fn() }, kv);
    expect(await svc.processDue(REQ_ID)).toBe(true);
    for (const k of [`cart:${MEMBER_ID}`, `oq:${qid}`, idx]) expect(await kv.get(k)).toBeNull();
    expect(await kv.get('cart:00000000-0000-4000-8000-000000000001')).toBe('{}');
  });
});
