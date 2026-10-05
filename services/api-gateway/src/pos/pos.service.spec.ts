import { MetricsService } from '../common/metrics/metrics.service';
import { sign } from 'jsonwebtoken';
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
import { LoyaltyService } from '../loyalty/loyalty.service';
import { OtpService } from '../otp/otp.service';
import { DiscountTokenService } from './discount-token.service';
import { parseDiscountTokenConsume, parsePosOtpRequest } from './dto/pos.dto';
import { PosService } from './pos.service';

const MEMBER = '6281234567890';
const OTHER_MEMBER = '6285711112222';
const ORDER = '3f2b8c1e-7d4a-4e0b-9c55-2a1f0e6d9b10'; // pos.order.uuid (opaque)
const OTHER_ORDER = '0b7e4c2a-1111-4c3d-8e9f-5a6b7c8d9e0f';

function setup() {
  const clock = new FakeClock();
  const config = testConfig();
  const kv = new FakeKvStore(clock.now);
  const db = new FakeDb((text) =>
    text.includes('SUM(delta_points)') ? { rows: [{ balance: '1200' }], rowCount: 1 } : undefined,
  );
  const sender = new FakeWhatsAppSender();
  const limiter = new RateLimiter(kv);
  limiter.now = clock.now;
  const otp = new OtpService(config, kv, db, sender, new MetricsService(), limiter, testCipher(config));
  otp.now = clock.now;
  const tokens = new DiscountTokenService(config, kv);
  tokens.now = clock.now;
  const loyalty = new LoyaltyService(config, db, testCipher(config));
  loyalty.now = clock.now;
  const pos = new PosService(config, otp, tokens, limiter, loyalty);
  return { clock, config, kv, db, sender, otp, tokens, pos };
}

async function obtainToken(ctx: ReturnType<typeof setup>, orderRef = ORDER): Promise<string> {
  const req = await ctx.pos.requestOtp(
    { memberRef: MEMBER, orderRef, amount: 125000, companyCode: 'FNB', posConfigId: 3 },
    '10.0.0.5',
  );
  const res = await ctx.pos.verifyOtp(
    { challengeId: req.challenge_id, orderRef, code: ctx.sender.lastCode() },
    '10.0.0.5',
  );
  return res.discount_token;
}

function consume(ctx: ReturnType<typeof setup>, token: string, orderRef = ORDER, memberRef = MEMBER, amountTotal = 125000) {
  return ctx.pos.consumeToken({ discountToken: token, orderRef, memberRef, amountTotal });
}

describe('PosService', () => {
  it('alur lengkap: request 202-body, verify → token, consume sekali', async () => {
    const ctx = setup();
    const req = await ctx.pos.requestOtp(
      { memberRef: MEMBER, orderRef: ORDER, amount: 125000, companyCode: 'FNB', posConfigId: 3 },
      null,
    );
    expect(req.expires_in).toBe(180);
    expect(req.challenge_id).toMatch(/^[0-9a-f-]{36}$/);

    const verified = await ctx.pos.verifyOtp({ challengeId: req.challenge_id, orderRef: ORDER, code: ctx.sender.lastCode() }, null);
    expect(verified.ok).toBe(true);
    expect(verified.expires_in).toBe(600);

    expect(await consume(ctx, verified.discount_token)).toEqual({ ok: true });
  });

  it('consume ulang dengan jti + order_ref + member yang sama → 200 idempotent (retry Odoo)', async () => {
    const ctx = setup();
    const token = await obtainToken(ctx);
    expect(await consume(ctx, token)).toEqual({ ok: true });
    expect(await consume(ctx, token)).toEqual({ ok: true });
    // marker berisi order_ref, bukan data pribadi
    const usedKeys = ctx.kv.keys().filter((k) => k.startsWith('dtok:used:'));
    expect(usedKeys).toHaveLength(1);
    expect(ctx.kv.raw(usedKeys[0])).toBe(ORDER);
  });

  it('SEC-07: member_ref order ≠ member token → 409 member_mismatch, token TIDAK terbakar', async () => {
    const ctx = setup();
    const token = await obtainToken(ctx);
    const err = await catchApiError(() => consume(ctx, token, ORDER, OTHER_MEMBER));
    expect(err.getStatus()).toBe(409);
    expect(err.body).toEqual({ ok: false, reason: 'member_mismatch' });
    expect(ctx.kv.keys().filter((k) => k.startsWith('dtok:used:'))).toHaveLength(0);
    expect(await consume(ctx, token)).toEqual({ ok: true });
    // replay dengan member lain setelah dipakai juga ditolak (bukan idempotent ok)
    const replay = await catchApiError(() => consume(ctx, token, ORDER, OTHER_MEMBER));
    expect(replay.body).toEqual({ ok: false, reason: 'member_mismatch' });
  });

  it('consume: member_ref wajib dan hanya nomor Indonesia (400 invalid_phone); amount_total wajib', async () => {
    let err = await catchApiError(() => parseDiscountTokenConsume({ discount_token: 't', order_ref: ORDER, amount_total: 1 }));
    expect(err.body).toEqual({ error: { code: 'invalid_phone', message: expect.any(String) } });
    err = await catchApiError(() =>
      parseDiscountTokenConsume({ discount_token: 't', order_ref: ORDER, member_ref: '+14155550100', amount_total: 1 }),
    );
    expect(err.getStatus()).toBe(400);
    err = await catchApiError(() => parseDiscountTokenConsume({ discount_token: 't', order_ref: ORDER, member_ref: MEMBER }));
    expect(err.body).toEqual({ error: { code: 'invalid_request', message: expect.any(String) } });
    expect(
      parseDiscountTokenConsume({ discount_token: 't', order_ref: ORDER, member_ref: '0812-3456-7890', amount_total: 99000 }),
    ).toEqual({ discountToken: 't', orderRef: ORDER, memberRef: MEMBER, amountTotal: 99000 });
  });

  it('SEC-07: amount_total < 50% atau > amount + 20% → 409 amount_mismatch, token TIDAK terbakar; batas inklusif', async () => {
    const ctx = setup();
    const token = await obtainToken(ctx); // amount OTP = 125000
    for (const bad of [62_499, 150_001]) {
      const err = await catchApiError(() => consume(ctx, token, ORDER, MEMBER, bad));
      expect(err.getStatus()).toBe(409);
      expect(err.body).toEqual({ ok: false, reason: 'amount_mismatch' });
    }
    expect(ctx.kv.keys().filter((k) => k.startsWith('dtok:used:'))).toHaveLength(0);
    expect(await consume(ctx, token, ORDER, MEMBER, 150_000)).toEqual({ ok: true });
    expect(await consume(ctx, await obtainToken(ctx, OTHER_ORDER), OTHER_ORDER, MEMBER, 62_500)).toEqual({ ok: true });
  });

  it('SEC-07: toleransi dari POS_OTP_AMOUNT_TOLERANCE_PCT; token tanpa klaim amount → invalid', async () => {
    const clock = new FakeClock();
    const config = testConfig({ POS_OTP_AMOUNT_TOLERANCE_PCT: '0' });
    const kv = new FakeKvStore(clock.now);
    const tokens = new DiscountTokenService(config, kv);
    tokens.now = clock.now;
    const hash = 'a'.repeat(64);
    const t = tokens.issue({ orderRef: ORDER, memberHash: hash, companyCode: 'FNB', amount: 100_000 });
    expect(await tokens.consume(t.token, ORDER, hash, 100_001)).toEqual({ ok: false, reason: 'amount_mismatch' });
    expect(await tokens.consume(t.token, ORDER, hash, 100_000)).toEqual({ ok: true });

    const now = Math.floor(clock.ms / 1000);
    const legacy = sign(
      { jti: 'j', purpose: 'pos_promo', order_ref: ORDER, member_hash: hash, company_code: 'FNB', iat: now, exp: now + 600 },
      config.discountTokenSecret,
      { algorithm: 'HS256' },
    );
    expect(await tokens.consume(legacy, ORDER, hash, 1)).toEqual({ ok: false, reason: 'invalid' });
  });

  it('request OTP: nomor non-62 → 400 invalid_phone', async () => {
    const err = await catchApiError(() =>
      parsePosOtpRequest({ member_ref: '+447700900123', order_ref: ORDER, amount: 1, company_code: 'FNB', pos_config_id: 1 }),
    );
    expect(err.body).toEqual({ error: { code: 'invalid_phone', message: expect.any(String) } });
  });

  it('token replay ke order lain setelah dipakai → 409 already_used', async () => {
    const ctx = setup();
    const token = await obtainToken(ctx);
    await consume(ctx, token);
    const err = await catchApiError(() => consume(ctx, token, OTHER_ORDER));
    expect(err.getStatus()).toBe(409);
    expect(err.body).toEqual({ ok: false, reason: 'already_used' });
  });

  it('consume paralel: pemenang SET NX menentukan; order sama tetap ok', async () => {
    const ctx = setup();
    const token = await obtainToken(ctx);
    const results = await Promise.all([consume(ctx, token), consume(ctx, token)]);
    expect(results).toEqual([{ ok: true }, { ok: true }]);
  });

  it('order mismatch → 409 order_mismatch, dan token TIDAK terbakar untuk order yang benar', async () => {
    const ctx = setup();
    const token = await obtainToken(ctx);
    const err = await catchApiError(() => consume(ctx, token, OTHER_ORDER));
    expect(err.getStatus()).toBe(409);
    expect(err.body).toEqual({ ok: false, reason: 'order_mismatch' });
    expect(await consume(ctx, token)).toEqual({ ok: true });
  });

  it('token kedaluwarsa (>600 s) → 409 expired', async () => {
    const ctx = setup();
    const token = await obtainToken(ctx);
    ctx.clock.advanceSeconds(601);
    const err = await catchApiError(() => consume(ctx, token));
    expect(err.body).toEqual({ ok: false, reason: 'expired' });
  });

  it('token dengan tanda tangan lain / alg none / purpose lain / tanpa member_hash → 409 invalid', async () => {
    const ctx = setup();
    const now = Math.floor(ctx.clock.ms / 1000);
    const claims = { jti: 'x', purpose: 'pos_promo', order_ref: ORDER, member_hash: 'h', company_code: 'FNB', iat: now, exp: now + 600 };

    const forged = sign(claims, 'kunci-penyerang-yang-berbeda-0123456789', { algorithm: 'HS256' });
    const header = Buffer.from(JSON.stringify({ alg: 'none', typ: 'JWT' })).toString('base64url');
    const payload = Buffer.from(JSON.stringify(claims)).toString('base64url');
    const algNone = `${header}.${payload}.`;
    const wrongPurpose = sign({ ...claims, purpose: 'login' }, ctx.config.discountTokenSecret, { algorithm: 'HS256' });
    const { member_hash: _omit, ...noMember } = claims;
    void _omit;
    const noMemberHash = sign(noMember, ctx.config.discountTokenSecret, { algorithm: 'HS256' });

    for (const t of [forged, algNone, wrongPurpose, noMemberHash, 'bukan.jwt.sama-sekali']) {
      const err = await catchApiError(() => consume(ctx, t));
      expect(err.body).toEqual({ ok: false, reason: 'invalid' });
    }
  });

  it('verify kode salah → 400 { ok:false, reason:"invalid", attempts_left:2 }', async () => {
    const ctx = setup();
    const req = await ctx.pos.requestOtp(
      { memberRef: MEMBER, orderRef: ORDER, amount: 1, companyCode: 'RTL', posConfigId: 1 },
      null,
    );
    const bad = ctx.sender.lastCode() === '000000' ? '111111' : '000000';
    const err = await catchApiError(() => ctx.pos.verifyOtp({ challengeId: req.challenge_id, orderRef: ORDER, code: bad }, null));
    expect(err.getStatus()).toBe(400);
    expect(err.body).toEqual({ ok: false, reason: 'invalid', attempts_left: 2 });
  });

  it('rate limit per member: request ke-4 dalam 15 menit → 429 + Retry-After', async () => {
    const ctx = setup();
    const dto = { memberRef: MEMBER, orderRef: ORDER, amount: 1, companyCode: 'FNB' as const, posConfigId: 3 };
    for (let i = 0; i < 3; i += 1) await ctx.pos.requestOtp(dto, null);
    const err = await catchApiError(() => ctx.pos.requestOtp(dto, null));
    expect(err.getStatus()).toBe(429);
    expect(err.body).toEqual({ error: { code: 'rate_limited', message: expect.any(String) } });
    expect(Number(err.headers['Retry-After'])).toBeGreaterThan(0);
    expect(ctx.sender.otps).toHaveLength(3);
    // key rate limit tidak memuat nomor HP plaintext
    expect(ctx.kv.keys().some((k) => k.includes(MEMBER))).toBe(false);
  });

  it('rate limit per pos_config: 20 / 15 menit', async () => {
    const ctx = setup();
    for (let i = 0; i < 20; i += 1) {
      const member = `62812000000${String(i).padStart(2, '0')}`;
      await ctx.pos.requestOtp({ memberRef: member, orderRef: `O-${i}`, amount: 1, companyCode: 'FNB', posConfigId: 7 }, null);
    }
    const err = await catchApiError(() =>
      ctx.pos.requestOtp({ memberRef: '6289999999999', orderRef: 'O-x', amount: 1, companyCode: 'FNB', posConfigId: 7 }, null),
    );
    expect(err.getStatus()).toBe(429);
  });

  it('saldo loyalty dari ledger', async () => {
    const ctx = setup();
    expect(await ctx.pos.balance(MEMBER)).toEqual({ points_balance: 1200 });
  });
});
