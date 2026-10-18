import { AppConfig } from '../config/app-config';
import { MetricsService } from '../common/metrics/metrics.service';
import { Db } from '../common/db/db';
import { PII_AAD } from '../common/pii/crypto';
import { RateLimiter } from '../common/rate-limit/rate-limiter';
import {
  FakeClock,
  FakeDb,
  FakeKvStore,
  FakeWhatsAppSender,
  catchApiError,
  testCipher,
  testConfig,
} from '../common/testing/fakes';
import { memberHash } from '../common/pii/pii';
import { OtpService, OTP_TOMBSTONE_GRACE_SECONDS } from './otp.service';

const MEMBER = '6281234567890';
const ORDER = '3f2b8c1e-7d4a-4e0b-9c55-2a1f0e6d9b10'; // pos.order.uuid (opaque)

/** Rakit OtpService dengan dependensi fake (dipakai juga oleh spec lain lewat pola yang sama). */
function makeOtp(config: AppConfig, clock: FakeClock, kv: FakeKvStore, db: Db, sender: FakeWhatsAppSender, metrics = new MetricsService()) {
  const limiter = new RateLimiter(kv);
  limiter.now = clock.now;
  const otp = new OtpService(config, kv, db, sender, metrics, limiter, testCipher(config));
  otp.now = clock.now;
  return otp;
}

function setup(overrides: Record<string, string> = {}) {
  const clock = new FakeClock();
  const config = testConfig(overrides);
  const kv = new FakeKvStore(clock.now);
  const db = new FakeDb();
  const sender = new FakeWhatsAppSender();
  const metrics = new MetricsService();
  const otp = makeOtp(config, clock, kv, db, sender, metrics);
  return { clock, config, kv, db, sender, otp, metrics };
}

function wrongCode(code: string): string {
  return code === '000000' ? '111111' : '000000';
}

function auditOutcomes(db: FakeDb): unknown[] {
  return db.find('platform.otp_audit').map((q) => q.params[3]);
}

describe('OtpService', () => {
  it('menyimpan hanya hash HMAC (bukan kode) dan TTL = 180 s + grace', async () => {
    const { otp, kv, sender } = setup();
    const { challengeId, expiresIn } = await otp.issue({ purpose: 'pos_promo', memberRef: MEMBER, orderRef: ORDER, companyCode: 'PB' });
    expect(expiresIn).toBe(180);
    const code = sender.lastCode();
    expect(code).toMatch(/^\d{6}$/);
    const stored = kv.raw(`otp:${challengeId}`) as Record<string, string>;
    expect(stored.h).toMatch(/^[0-9a-f]{64}$/);
    expect(JSON.stringify(stored)).not.toContain(code);
    // POS tidak menyimpan nomor HP plaintext
    expect(JSON.stringify(stored)).not.toContain(MEMBER);
    expect(await kv.ttl(`otp:${challengeId}`)).toBe(180 + OTP_TOMBSTONE_GRACE_SECONDS);
  });

  it('verify sukses sekali pakai; audit memakai member_hash, bukan nomor HP', async () => {
    const { otp, sender, db, config } = setup();
    const { challengeId } = await otp.issue({ purpose: 'pos_promo', memberRef: MEMBER, orderRef: ORDER, companyCode: 'PB' });
    const code = sender.lastCode();

    const ok = await otp.verify({ purpose: 'pos_promo', challengeId, code, orderRef: ORDER });
    expect(ok).toEqual({
      ok: true,
      purpose: 'pos_promo',
      memberHash: memberHash(config.memberHashPepper, MEMBER),
      memberRef: null,
      posConfigId: null,
      orderRef: ORDER,
      companyCode: 'PB',
      amount: null,
    });

    const again = await otp.verify({ purpose: 'pos_promo', challengeId, code, orderRef: ORDER });
    expect(again).toEqual({ ok: false, reason: 'expired', attemptsLeft: 0 });

    for (const q of db.find('platform.otp_audit')) expect(q.params).not.toContain(MEMBER);
    expect(auditOutcomes(db)).toEqual(['sent', 'verified', 'expired']);
  });

  it('3 kali salah → invalid(2), invalid(1), locked(0); kode benar setelahnya tetap locked', async () => {
    const { otp, sender, db } = setup();
    const { challengeId } = await otp.issue({ purpose: 'login', memberRef: MEMBER });
    const code = sender.lastCode();
    const bad = wrongCode(code);

    expect(await otp.verify({ purpose: 'login', challengeId, code: bad })).toEqual({ ok: false, reason: 'invalid', attemptsLeft: 2 });
    expect(await otp.verify({ purpose: 'login', challengeId, code: bad })).toEqual({ ok: false, reason: 'invalid', attemptsLeft: 1 });
    expect(await otp.verify({ purpose: 'login', challengeId, code: bad })).toEqual({ ok: false, reason: 'locked', attemptsLeft: 0 });
    expect(await otp.verify({ purpose: 'login', challengeId, code })).toEqual({ ok: false, reason: 'locked', attemptsLeft: 0 });
    expect(auditOutcomes(db)).toEqual(['sent', 'failed', 'failed', 'locked', 'locked']);
  });

  it('kedaluwarsa setelah 180 s → expired', async () => {
    const { otp, sender, clock } = setup();
    const { challengeId } = await otp.issue({ purpose: 'login', memberRef: MEMBER });
    const code = sender.lastCode();
    clock.advanceSeconds(181);
    expect(await otp.verify({ purpose: 'login', challengeId, code })).toEqual({ ok: false, reason: 'expired', attemptsLeft: 0 });
  });

  it('challenge tidak dikenal atau beda purpose → expired', async () => {
    const { otp, sender } = setup();
    expect(
      await otp.verify({ purpose: 'login', challengeId: '6f1b2a7c-1d2e-4f00-8a00-000000000000', code: '123456' }),
    ).toEqual({ ok: false, reason: 'expired', attemptsLeft: 0 });

    const { challengeId } = await otp.issue({ purpose: 'login', memberRef: MEMBER });
    const code = sender.lastCode();
    expect(await otp.verify({ purpose: 'pos_promo', challengeId, code, orderRef: ORDER })).toEqual({
      ok: false,
      reason: 'expired',
      attemptsLeft: 0,
    });
  });

  it('pos_promo: order_ref berbeda dihitung sebagai percobaan gagal', async () => {
    const { otp, sender } = setup();
    const { challengeId } = await otp.issue({ purpose: 'pos_promo', memberRef: MEMBER, orderRef: ORDER, companyCode: 'GL' });
    const code = sender.lastCode();
    expect(await otp.verify({ purpose: 'pos_promo', challengeId, code, orderRef: '0b7e4c2a-1111-4c3d-8e9f-5a6b7c8d9e0f' })).toEqual({
      ok: false,
      reason: 'invalid',
      attemptsLeft: 2,
    });
    const ok = await otp.verify({ purpose: 'pos_promo', challengeId, code, orderRef: ORDER });
    expect(ok.ok).toBe(true);
  });

  it('login menyimpan member_ref TERENKRIPSI di Redis (SEC-24) dan mengembalikannya saat verify', async () => {
    const { otp, sender, kv, config } = setup();
    const { challengeId } = await otp.issue({ purpose: 'login', memberRef: MEMBER });
    const stored = kv.raw(`otp:${challengeId}`) as Record<string, string>;
    expect(JSON.stringify(stored)).not.toContain(MEMBER);
    expect(testCipher(config).decrypt(Buffer.from(stored.m, 'base64'), PII_AAD.REDIS_OTP_MEMBER)).toBe(MEMBER);
    const res = await otp.verify({ purpose: 'login', challengeId, code: sender.lastCode() });
    expect(res.ok && res.memberRef).toBe(MEMBER);
  });

  it('SEC-07: amount POS disimpan di challenge dan dikembalikan saat verify', async () => {
    const { otp, sender } = setup();
    const { challengeId } = await otp.issue({ purpose: 'pos_promo', memberRef: MEMBER, orderRef: ORDER, companyCode: 'PB', amount: 125000 });
    const res = await otp.verify({ purpose: 'pos_promo', challengeId, code: sender.lastCode(), orderRef: ORDER });
    expect(res.ok && res.amount).toBe(125000);
  });

  it('purpose dsar memakai budget publik (bukan POS) dan tidak menyimpan member_ref', async () => {
    const { otp, kv } = setup({ OTP_GLOBAL_HOURLY_BUDGET: '1' });
    const { challengeId } = await otp.issue({ purpose: 'dsar', memberRef: MEMBER });
    expect((kv.raw(`otp:${challengeId}`) as Record<string, string>).m).toBeUndefined();
    const err = await catchApiError(() => otp.issue({ purpose: 'dsar', memberRef: MEMBER }));
    expect(err.getStatus()).toBe(429);
  });

  it('format kode salah tetap memakan attempt', async () => {
    const { otp } = setup();
    const { challengeId } = await otp.issue({ purpose: 'login', memberRef: MEMBER });
    expect(await otp.verify({ purpose: 'login', challengeId, code: 'abc' })).toEqual({ ok: false, reason: 'invalid', attemptsLeft: 2 });
  });

  it('pengiriman WA gagal → 503 otp_delivery_failed dan challenge dihapus', async () => {
    const { otp, sender, kv, db } = setup();
    sender.failNext = true;
    const err = await catchApiError(() => otp.issue({ purpose: 'login', memberRef: MEMBER }));
    expect(err.getStatus()).toBe(503);
    expect(err.body).toEqual({ error: { code: 'otp_delivery_failed', message: expect.any(String) } });
    expect(kv.keys().filter((k) => k.startsWith('otp:'))).toHaveLength(0);
    expect(auditOutcomes(db)).toEqual(['failed']);
  });

  it('kegagalan tulis audit tidak memutus alur OTP', async () => {
    const clock = new FakeClock();
    const db = new FakeDb(() => {
      throw new Error('db down');
    });
    const sender = new FakeWhatsAppSender();
    const otp = makeOtp(testConfig(), clock, new FakeKvStore(clock.now), db, sender);
    const { challengeId } = await otp.issue({ purpose: 'login', memberRef: MEMBER });
    const res = await otp.verify({ purpose: 'login', challengeId, code: sender.lastCode() });
    expect(res.ok).toBe(true);
  });

  it('otp_audit menyimpan IP terenkripsi (ip_enc, AAD otp_audit.ip_enc), bukan IP mentah', async () => {
    const { otp, db, config } = setup();
    await otp.issue({ purpose: 'login', memberRef: MEMBER, ip: '203.0.113.7' });
    const q = db.find('INSERT INTO platform.otp_audit')[0];
    expect(q.text).toContain('ip_enc');
    expect(q.text).not.toMatch(/outcome, ip\)/);
    const enc = q.params[4] as Buffer;
    expect(Buffer.isBuffer(enc)).toBe(true);
    expect(enc.includes(Buffer.from('203.0.113.7'))).toBe(false);
    expect(testCipher(config).decrypt(enc, PII_AAD.OTP_AUDIT_IP)).toBe('203.0.113.7');
  });

  it('SEC-06: budget global per jam habis → 429 tanpa kirim WA; budget login terpisah dari POS', async () => {
    const { otp, sender, metrics, clock } = setup({ OTP_GLOBAL_HOURLY_BUDGET: '2', OTP_POS_HOURLY_BUDGET: '5' });
    await otp.issue({ purpose: 'login', memberRef: '6281200000001' });
    await otp.issue({ purpose: 'login', memberRef: '6281200000002' });
    const err = await catchApiError(() => otp.issue({ purpose: 'login', memberRef: '6281200000003' }));
    expect(err.getStatus()).toBe(429);
    expect(err.body).toEqual({ error: { code: 'rate_limited', message: expect.any(String) } });
    expect(sender.otps).toHaveLength(2);
    // POS tetap jalan walau budget login habis
    await expect(otp.issue({ purpose: 'pos_promo', memberRef: MEMBER, orderRef: ORDER, companyCode: 'PB' })).resolves.toBeDefined();
    expect(await metrics.render()).toContain('efn_otp_request_total{purpose="login",outcome="budget_exhausted"} 1');
    // jam berikutnya budget pulih
    clock.advanceSeconds(3600);
    await expect(otp.issue({ purpose: 'login', memberRef: '6281200000003' })).resolves.toBeDefined();
  });

  it('metrik efn_otp_* tercatat tanpa nomor HP di label', async () => {
    const clock = new FakeClock();
    const metrics = new MetricsService();
    const sender = new FakeWhatsAppSender();
    const otp = makeOtp(testConfig(), clock, new FakeKvStore(clock.now), new FakeDb(), sender, metrics);
    const { challengeId } = await otp.issue({ purpose: 'login', memberRef: MEMBER });
    await otp.verify({ purpose: 'login', challengeId, code: sender.lastCode() });
    otp.recordRateLimited('pos_promo');
    const text = await metrics.render();
    expect(text).toContain('efn_otp_request_total{purpose="login",outcome="sent"} 1');
    expect(text).toContain('efn_otp_request_total{purpose="pos_promo",outcome="rate_limited"} 1');
    expect(text).toContain('efn_otp_verify_total{purpose="login",outcome="verified"} 1');
    expect(text).not.toContain(MEMBER);
  });

  describe('purpose pos_redeem (docs/04 §9.3)', () => {
    it('verify POS menerima pos_redeem lewat alsoAccept dan mengembalikan purpose challenge; metrik berlabel pos_redeem', async () => {
      const { otp, sender, metrics, db } = setup();
      const { challengeId } = await otp.issue({ purpose: 'pos_redeem', memberRef: MEMBER, orderRef: ORDER, companyCode: 'PB', amount: 50000 });
      const res = await otp.verify({ purpose: 'pos_promo', alsoAccept: ['pos_redeem'], challengeId, code: sender.lastCode(), orderRef: ORDER });
      expect(res).toMatchObject({ ok: true, purpose: 'pos_redeem', orderRef: ORDER, amount: 50000 });
      const text = await metrics.render();
      expect(text).toContain('efn_otp_request_total{purpose="pos_redeem",outcome="sent"} 1');
      expect(text).toContain('efn_otp_verify_total{purpose="pos_redeem",outcome="verified"} 1');
      expect(db.find('platform.otp_audit').map((q) => q.params[0])).toEqual(['pos_redeem', 'pos_redeem']);
    });

    it('tanpa alsoAccept, challenge pos_redeem tidak bisa diverifikasi sebagai pos_promo (dan sebaliknya)', async () => {
      const { otp, sender } = setup();
      const redeem = await otp.issue({ purpose: 'pos_redeem', memberRef: MEMBER, orderRef: ORDER, companyCode: 'PB' });
      expect(await otp.verify({ purpose: 'pos_promo', challengeId: redeem.challengeId, code: sender.lastCode(), orderRef: ORDER })).toEqual({
        ok: false,
        reason: 'expired',
        attemptsLeft: 0,
      });
      const promo = await otp.issue({ purpose: 'pos_promo', memberRef: MEMBER, orderRef: ORDER, companyCode: 'PB' });
      expect(await otp.verify({ purpose: 'pos_redeem', challengeId: promo.challengeId, code: sender.lastCode(), orderRef: ORDER })).toEqual({
        ok: false,
        reason: 'expired',
        attemptsLeft: 0,
      });
    });

    it('pos_redeem juga terikat order_ref (order lain = percobaan gagal)', async () => {
      const { otp, sender } = setup();
      const { challengeId } = await otp.issue({ purpose: 'pos_redeem', memberRef: MEMBER, orderRef: ORDER, companyCode: 'PB' });
      const res = await otp.verify({
        purpose: 'pos_promo',
        alsoAccept: ['pos_redeem'],
        challengeId,
        code: sender.lastCode(),
        orderRef: '0b7e4c2a-1111-4c3d-8e9f-5a6b7c8d9e0f',
      });
      expect(res).toEqual({ ok: false, reason: 'invalid', attemptsLeft: 2 });
    });

    it('pos_redeem memakai budget POS (OTP_POS_HOURLY_BUDGET), bukan budget publik', async () => {
      const { otp } = setup({ OTP_POS_HOURLY_BUDGET: '1', OTP_GLOBAL_HOURLY_BUDGET: '1000' });
      await otp.issue({ purpose: 'pos_redeem', memberRef: MEMBER, orderRef: ORDER, companyCode: 'PB' });
      const err = await catchApiError(() => otp.issue({ purpose: 'pos_redeem', memberRef: MEMBER, orderRef: ORDER, companyCode: 'PB' }));
      expect(err.getStatus()).toBe(429);
    });
  });

  it('kode OTP selalu 6 digit (randomInt di-pad)', async () => {
    const { otp, sender } = setup();
    for (let i = 0; i < 20; i += 1) await otp.issue({ purpose: 'login', memberRef: MEMBER });
    for (const s of sender.otps) expect(s.code).toMatch(/^\d{6}$/);
  });
});
