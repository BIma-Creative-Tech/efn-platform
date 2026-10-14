import { Logger } from '@nestjs/common';
import { GUARDS_METADATA } from '@nestjs/common/constants';
import { MemberService } from '../common/member/member.service';
import { MetricsService } from '../common/metrics/metrics.service';
import { PII_AAD } from '../common/pii/crypto';
import { memberHash } from '../common/pii/pii';
import { RateLimiter } from '../common/rate-limit/rate-limiter';
import { ServiceKeyGuard } from '../common/security/service-key.guard';
import { FakeLoyaltyDb } from '../common/testing/fake-loyalty-db';
import {
  FakeClock,
  FakeDb,
  FakeKvStore,
  FakeMemberTable,
  FakeWhatsAppSender,
  catchApiError,
  testCipher,
  testConfig,
} from '../common/testing/fakes';
import { LoyaltyService, allocateDebit, splitCredit } from '../loyalty/loyalty.service';
import { OtpService } from '../otp/otp.service';
import { DiscountTokenService } from './discount-token.service';
import { parsePosOtpRequest, parsePosOtpVerify } from './dto/pos.dto';
import { parsePosEarn, parsePosReverse, parseRedeemCommit, parseRedeemReserve } from './dto/pos-loyalty.dto';
import { LoyaltyReservationSweeper } from './loyalty-reservation.sweeper';
import { PosLoyaltyController } from './pos-loyalty.controller';
import { PosLoyaltyService, expiryFor } from './pos-loyalty.service';
import { PosOtpVerifyResponse, PosService } from './pos.service';

const MEMBER = '6281234567890';
const OTHER = '6285711112222';
const ORDER_A = '3f2b8c1e-7d4a-4e0b-9c55-2a1f0e6d9b10';
const ORDER_B = '0b7e4c2a-1111-4c3d-8e9f-5a6b7c8d9e0f';
const ORDER_C = '7c1d2e3f-2222-4a5b-8c6d-7e8f9a0b1c2d';
const REFUND_1 = 'aa11bb22-3333-4c4d-8e5f-6a7b8c9d0e1f';
const REFUND_2 = 'bb22cc33-4444-4d5e-9f6a-7b8c9d0e1f2a';
const DAY = 86_400;

function setup(overrides: Record<string, string> = {}) {
  const clock = new FakeClock();
  const config = testConfig(overrides);
  const cipher = testCipher(config);
  const kv = new FakeKvStore(clock.now);
  const limiter = new RateLimiter(kv);
  limiter.now = clock.now;
  const metrics = new MetricsService();
  const db = new FakeLoyaltyDb();
  const loyalty = new LoyaltyService(config, db, cipher);
  loyalty.now = clock.now;
  const tokens = new DiscountTokenService(config, kv);
  tokens.now = clock.now;
  const svc = new PosLoyaltyService(config, db, loyalty, tokens, limiter, metrics);
  svc.now = clock.now;
  const sweeper = new LoyaltyReservationSweeper(db, config, metrics);
  sweeper.now = clock.now;
  const hash = memberHash(config.memberHashPepper, MEMBER);

  const earn = (orderRef: string, eligible: number, memberRef = MEMBER, companyCode: 'FNB' | 'RTL' = 'FNB') =>
    svc.earn({ idempotencyKey: `pos_earn:${orderRef}`, orderRef, memberRef, companyCode, eligibleAmount: eligible });
  const token = (orderRef: string, opts: { purpose?: 'pos_promo' | 'pos_redeem'; memberRef?: string; amount?: number } = {}) =>
    tokens.issue({
      purpose: opts.purpose ?? 'pos_redeem',
      orderRef,
      memberHash: memberHash(config.memberHashPepper, opts.memberRef ?? MEMBER),
      companyCode: 'FNB',
      amount: opts.amount ?? 125_000,
    }).token;
  const reserve = (orderRef: string, points: number, opts: { tok?: string; memberRef?: string; amountTotal?: number } = {}) =>
    svc.reserve({
      discountToken: opts.tok ?? token(orderRef),
      orderRef,
      memberRef: opts.memberRef ?? MEMBER,
      points,
      amountTotal: opts.amountTotal ?? 125_000,
    });
  const commit = (
    reservationId: string,
    orderRef: string,
    key = `pos_redeem:${orderRef}`,
    opts: { memberRef?: string; valueIdr?: number; paymentAmount?: number; orderGoodsIncl?: number | null } = {},
  ) =>
    svc.commit({
      reservationId,
      orderRef,
      memberRef: opts.memberRef ?? MEMBER,
      valueIdr: opts.valueIdr ?? db.reservations.get(reservationId)?.value_idr ?? 0,
      paymentAmount: opts.paymentAmount ?? opts.valueIdr ?? db.reservations.get(reservationId)?.value_idr ?? 0,
      orderGoodsIncl: opts.orderGoodsIncl ?? null,
      idempotencyKey: key,
    });
  const reverse = (
    refund: string,
    original: string,
    ratio: number,
    memberRef = MEMBER,
    restoredValue: number | null = null,
    extra: { refundGoodsIncl?: number | null; restoreApproved?: boolean } = {},
  ) =>
    svc.reverse({
      idempotencyKey: `pos_reverse:${refund}`,
      originalOrderRef: original,
      refundOrderRef: refund,
      memberRef,
      refundedEligibleAmount: 0,
      refundRatio: ratio,
      pointsRestoredValue: restoredValue,
      refundGoodsIncl: extra.refundGoodsIncl ?? null,
      restoreApproved: extra.restoreApproved ?? false,
    });
  const now = () => new Date(clock.now());
  return { clock, config, cipher, kv, metrics, db, loyalty, tokens, svc, sweeper, hash, earn, token, reserve, commit, reverse, now };
}

async function code(p: Promise<unknown>): Promise<string> {
  const err = await catchApiError(() => p);
  return `${err.getStatus()} ${(err.body as { error: { code: string } }).error.code}`;
}

describe('alokasi bucket (FIFO kedaluwarsa)', () => {
  const d1 = new Date('2027-01-01T00:00:00Z');
  const d2 = new Date('2027-02-01T00:00:00Z');

  it('debit diambil dari bucket yang paling cepat kedaluwarsa; NULL terakhir; kekurangan → utang NULL', () => {
    const buckets = [
      { expiresAt: null, remaining: 5 },
      { expiresAt: d2, remaining: 40 },
      { expiresAt: d1, remaining: 30 },
    ];
    expect(allocateDebit(buckets, 50)).toEqual([
      { expiresAt: d1, points: 30 },
      { expiresAt: d2, points: 20 },
    ]);
    expect(allocateDebit(buckets, 80)).toEqual([
      { expiresAt: d1, points: 30 },
      { expiresAt: d2, points: 40 },
      { expiresAt: null, points: 10 },
    ]);
  });

  it('bucket bersisa ≤ 0 dilewati', () => {
    expect(allocateDebit([{ expiresAt: d1, remaining: 0 }, { expiresAt: d2, remaining: -5 }], 3)).toEqual([{ expiresAt: null, points: 3 }]);
  });

  it('kredit baru melunasi utang bucket NULL dulu', () => {
    expect(splitCredit([{ expiresAt: null, remaining: -20 }], 30, d1)).toEqual([
      { expiresAt: null, points: 20 },
      { expiresAt: d1, points: 10 },
    ]);
    expect(splitCredit([{ expiresAt: null, remaining: -50 }], 30, d1)).toEqual([{ expiresAt: null, points: 30 }]);
    expect(splitCredit([{ expiresAt: null, remaining: 10 }], 30, d1)).toEqual([{ expiresAt: d1, points: 30 }]);
  });

  it('masa berlaku = 00:00 WIB setelah N hari penuh; 0 hari = tanpa kedaluwarsa', () => {
    // 5 Okt 2026 10:00 WIB (03:00Z) + 365 hari → 6 Okt 2027 00:00 WIB = 5 Okt 2027 17:00Z
    expect(expiryFor(Date.UTC(2026, 9, 5, 3, 0, 0), 365)?.toISOString()).toBe('2027-10-05T17:00:00.000Z');
    // 5 Okt 2026 23:30 WIB (16:30Z) masih hari yang sama
    expect(expiryFor(Date.UTC(2026, 9, 5, 16, 30, 0), 1)?.toISOString()).toBe('2026-10-06T17:00:00.000Z');
    expect(expiryFor(Date.now(), 0)).toBeNull();
  });
});

describe('PosLoyaltyService — earn (§9.2)', () => {
  it('poin = floor(eligible / LOYALTY_EARN_IDR_PER_POINT); entri ledger pos_earn terenkripsi, PT order, masa berlaku', async () => {
    const ctx = setup();
    expect(await ctx.earn(ORDER_A, 125_000)).toEqual({ points_earned: 12, points_balance: 12 });
    const row = ctx.db.ledger[0];
    expect(row).toMatchObject({
      member_ref_hash: ctx.hash,
      company_code: 'FNB',
      delta_points: 12,
      source: 'pos_earn',
      source_ref: ORDER_A,
      idempotency_key: `pos_earn:${ORDER_A}`,
      expires_at: expiryFor(ctx.clock.now(), 365)?.toISOString(),
    });
    expect(ctx.cipher.decrypt(row.member_ref_enc as Buffer, PII_AAD.LOYALTY_LEDGER_MEMBER_REF)).toBe(MEMBER);
    expect(JSON.stringify(ctx.db.ledger.map((r) => ({ ...r, member_ref_enc: null })))).not.toContain(MEMBER);
  });

  it.each([
    [9_999, 0],
    [10_000, 1],
    [19_999.99, 1],
    [0, 0],
  ])('eligible %d → %d poin (pembulatan ke bawah)', async (eligible, points) => {
    const ctx = setup();
    expect((await ctx.earn(ORDER_A, eligible)).points_earned).toBe(points);
    expect(ctx.db.ledger).toHaveLength(points > 0 ? 1 : 0);
  });

  it('parameter dari env: LOYALTY_EARN_IDR_PER_POINT & LOYALTY_POINT_EXPIRY_DAYS=0 (tanpa kedaluwarsa)', async () => {
    const ctx = setup({ LOYALTY_EARN_IDR_PER_POINT: '1000', LOYALTY_POINT_EXPIRY_DAYS: '0' });
    expect((await ctx.earn(ORDER_A, 12_345)).points_earned).toBe(12);
    expect(ctx.db.ledger[0].expires_at).toBeNull();
  });

  it('idempoten: retry key sama → hasil sama tanpa entri baru; key sama untuk member lain → 409 idempotency_conflict', async () => {
    const ctx = setup();
    await ctx.earn(ORDER_A, 125_000);
    expect(await ctx.earn(ORDER_A, 125_000)).toEqual({ points_earned: 12, points_balance: 12 });
    expect(ctx.db.ledger).toHaveLength(1);
    expect(await code(ctx.earn(ORDER_A, 125_000, OTHER))).toBe('409 idempotency_conflict');
    expect(await ctx.metrics.render()).toContain('efn_loyalty_pos_ops_total{op="earn",outcome="replay"} 1');
  });

  it('plafon LOYALTY_POS_MAX_EARN_POINTS → 409 earn_limit_exceeded', async () => {
    const ctx = setup({ LOYALTY_POS_MAX_EARN_POINTS: '10' });
    expect(await code(ctx.earn(ORDER_A, 200_000))).toBe('409 earn_limit_exceeded');
    expect(ctx.db.ledger).toHaveLength(0);
  });

  it('rate limit per member (LOYALTY_RL_MEMBER_LIMIT) → 429', async () => {
    const ctx = setup({ LOYALTY_RL_MEMBER_LIMIT: '2' });
    await ctx.earn(ORDER_A, 10_000);
    await ctx.earn(ORDER_B, 10_000);
    expect(await code(ctx.earn(ORDER_C, 10_000))).toBe('429 rate_limited');
    expect(await code(ctx.earn(ORDER_C, 10_000, OTHER)).catch(() => 'ok')).toBe('ok'); // member lain tidak terpengaruh
  });
});

describe('PosLoyaltyService — redeem reserve (§9.3)', () => {
  async function funded(points = 100, overrides: Record<string, string> = {}) {
    const ctx = setup(overrides);
    await ctx.earn(ORDER_C, points * 10_000);
    return ctx;
  }

  it('reserve sukses: nilai = poin × LOYALTY_POINT_VALUE_IDR, TTL UI 15 menit, hold 24 jam (K-3), tanpa nomor HP di reservasi', async () => {
    const ctx = await funded();
    const res = await ctx.reserve(ORDER_A, 60);
    expect(res).toEqual({
      reservation_id: expect.stringMatching(/^[0-9a-f-]{36}$/),
      points: 60,
      value_idr: 6000,
      expires_at: new Date(ctx.clock.now() + 900_000).toISOString(),
      hold_until: new Date(ctx.clock.now() + 86_400_000).toISOString(),
    });
    const row = ctx.db.reservations.get(res.reservation_id)!;
    expect(row).toMatchObject({ status: 'reserved', member_hash: ctx.hash, company_code: 'FNB', order_ref: ORDER_A, points: 60 });
    expect(JSON.stringify(row)).not.toContain(MEMBER);
    expect(Object.keys(row)).not.toContain('member_ref_enc');
    expect(ctx.db.ledger).toHaveLength(1); // reserve tidak menulis ledger
  });

  it('token purpose pos_promo ditolak (token_invalid) — token promo tidak bisa dipakai redeem', async () => {
    const ctx = await funded();
    expect(await code(ctx.reserve(ORDER_A, 60, { tok: ctx.token(ORDER_A, { purpose: 'pos_promo' }) }))).toBe('409 token_invalid');
  });

  it('token kedaluwarsa / rusak / order lain → token_invalid; member lain → member_mismatch; amount bergeser → amount_mismatch', async () => {
    const ctx = await funded();
    const tok = ctx.token(ORDER_A);
    expect(await code(ctx.reserve(ORDER_A, 60, { tok: `${tok}x` }))).toBe('409 token_invalid');
    expect(await code(ctx.reserve(ORDER_B, 60, { tok }))).toBe('409 token_invalid');
    expect(await code(ctx.reserve(ORDER_A, 60, { tok, memberRef: OTHER }))).toBe('409 member_mismatch');
    expect(await code(ctx.reserve(ORDER_A, 60, { tok, amountTotal: 500_000 }))).toBe('409 amount_mismatch');
    ctx.clock.advanceSeconds(601);
    expect(await code(ctx.reserve(ORDER_A, 60, { tok }))).toBe('409 token_invalid');
    expect(ctx.db.reservations.size).toBe(0);
  });

  it('below_minimum, exceeds_max_pct, insufficient_points', async () => {
    const ctx = await funded(1000);
    expect(await code(ctx.reserve(ORDER_A, 49))).toBe('409 below_minimum');
    // 50% × 125 000 = 62 500 → maks 625 poin @Rp100
    expect((await ctx.reserve(ORDER_A, 625)).value_idr).toBe(62_500);
    expect(await code(ctx.reserve(ORDER_B, 626))).toBe('409 exceeds_max_pct');
    const poor = await funded(40, { LOYALTY_REDEEM_MIN_POINTS: '10' });
    expect(await code(poor.reserve(ORDER_A, 41))).toBe('409 insufficient_points');
    expect(await poor.metrics.render()).toContain('efn_loyalty_pos_ops_total{op="reserve",outcome="insufficient_points"} 1');
  });

  it('parameter dari env: LOYALTY_REDEEM_MIN_POINTS, LOYALTY_REDEEM_MAX_PCT_OF_ORDER, LOYALTY_POINT_VALUE_IDR', async () => {
    const ctx = await funded(1000, { LOYALTY_REDEEM_MIN_POINTS: '5', LOYALTY_REDEEM_MAX_PCT_OF_ORDER: '10', LOYALTY_POINT_VALUE_IDR: '250' });
    expect((await ctx.reserve(ORDER_A, 50)).value_idr).toBe(12_500); // = 10% × 125 000
    expect(await code(ctx.reserve(ORDER_B, 51))).toBe('409 exceeds_max_pct');
    expect(ctx.svc.params()).toMatchObject({ redeem_min_points: 5, redeem_max_pct_of_order: 10, point_value_idr: 250 });
  });

  it('KONKUREN: dua reservasi paralel yang bersama-sama melebihi saldo → tepat satu insufficient_points', async () => {
    const ctx = await funded(100);
    const results = await Promise.allSettled([ctx.reserve(ORDER_A, 60), ctx.reserve(ORDER_B, 60)]);
    expect(results.filter((r) => r.status === 'fulfilled')).toHaveLength(1);
    const rejected = results.find((r) => r.status === 'rejected') as PromiseRejectedResult;
    expect(await code(Promise.reject(rejected.reason))).toBe('409 insufficient_points');
    expect([...ctx.db.reservations.values()].filter((r) => r.status === 'reserved')).toHaveLength(1);
  });

  it('kontrol negatif: tanpa advisory lock, balapan yang sama meloloskan keduanya (membuktikan test di atas bermakna)', async () => {
    const ctx = await funded(100);
    ctx.db.noLocks = true;
    const results = await Promise.allSettled([ctx.reserve(ORDER_A, 60), ctx.reserve(ORDER_B, 60)]);
    expect(results.filter((r) => r.status === 'fulfilled')).toHaveLength(2);
  });

  it('reservasi aktif menahan poin sampai hold_until (24 jam, bukan TTL UI 15 menit); setelahnya tersedia lagi', async () => {
    const ctx = await funded(100);
    await ctx.reserve(ORDER_A, 60);
    expect(await code(ctx.reserve(ORDER_B, 60))).toBe('409 insufficient_points');
    ctx.clock.advanceSeconds(901);
    expect(await code(ctx.reserve(ORDER_B, 60))).toBe('409 insufficient_points');
    ctx.clock.advanceSeconds(DAY);
    expect((await ctx.reserve(ORDER_B, 60)).points).toBe(60);
  });

  it('LOYALTY_RESERVATION_COMMIT_WINDOW_SECONDS mengatur hold_until', async () => {
    const ctx = await funded(100, { LOYALTY_RESERVATION_COMMIT_WINDOW_SECONDS: '3600' });
    expect((await ctx.reserve(ORDER_A, 60)).hold_until).toBe(new Date(ctx.clock.now() + 3_600_000).toISOString());
    expect(ctx.svc.params().reservation_commit_window_seconds).toBe(3600);
  });

  it('retry token+order+poin sama → reservasi yang sama; poin lain → token_invalid; token baru untuk order beraktif → reservation_exists', async () => {
    const ctx = await funded(500);
    const tok = ctx.token(ORDER_A);
    const first = await ctx.reserve(ORDER_A, 60, { tok });
    expect(await ctx.reserve(ORDER_A, 60, { tok })).toEqual(first);
    expect(await code(ctx.reserve(ORDER_A, 70, { tok }))).toBe('409 token_invalid');
    expect(await code(ctx.reserve(ORDER_A, 70))).toBe('409 reservation_exists');
    expect(ctx.db.reservations.size).toBe(1);
  });

  it('K-4: token pos_redeem dikonsumsi saat reserve — setelah release token yang sama ditolak, OTP baru boleh', async () => {
    const ctx = await funded(500);
    const tok = ctx.token(ORDER_A);
    const first = await ctx.reserve(ORDER_A, 60, { tok });
    await ctx.svc.release(first.reservation_id);
    expect(await code(ctx.reserve(ORDER_A, 60, { tok }))).toBe('409 token_invalid');
    const second = await ctx.reserve(ORDER_A, 80);
    expect(second.reservation_id).not.toBe(first.reservation_id);
  });

  it('K-4: token yang sama dipakai paralel untuk dua jumlah poin → hanya satu reservasi', async () => {
    const ctx = await funded(500);
    const tok = ctx.token(ORDER_A);
    const results = await Promise.allSettled([ctx.reserve(ORDER_A, 60, { tok }), ctx.reserve(ORDER_A, 70, { tok })]);
    expect(results.filter((r) => r.status === 'fulfilled')).toHaveLength(1);
    expect(ctx.db.reservations.size).toBe(1);
  });
});

describe('PosLoyaltyService — commit / release (§9.3)', () => {
  async function reserved(points = 60, balance = 100) {
    const ctx = setup();
    await ctx.earn(ORDER_C, balance * 10_000);
    const r = await ctx.reserve(ORDER_A, points);
    return { ...ctx, r };
  }

  it('commit → entri ledger negatif pos_redeem (PT order, member_ref_enc AAD ledger), saldo turun, status committed', async () => {
    const ctx = await reserved();
    expect(await ctx.commit(ctx.r.reservation_id, ORDER_A)).toEqual({ points_redeemed: 60, points_balance: 40 });
    const debit = ctx.db.ledger.find((l) => l.source === 'pos_redeem')!;
    expect(debit).toMatchObject({ delta_points: -60, company_code: 'FNB', source_ref: ORDER_A, idempotency_key: `pos_redeem:${ORDER_A}` });
    expect(ctx.cipher.decrypt(debit.member_ref_enc as Buffer, PII_AAD.LOYALTY_LEDGER_MEMBER_REF)).toBe(MEMBER);
    expect(ctx.db.reservations.get(ctx.r.reservation_id)).toMatchObject({ status: 'committed', commit_idempotency_key: `pos_redeem:${ORDER_A}` });
    const text = await ctx.metrics.render();
    expect(text).toContain('efn_loyalty_pos_points_total{kind="redeemed",company="FNB"} 60');
    expect(text).toContain('efn_loyalty_pos_points_total{kind="earned",company="FNB"} 100');
  });

  it('commit idempoten: retry key sama → hasil sama tanpa entri baru; key lain → idempotency_conflict', async () => {
    const ctx = await reserved();
    await ctx.commit(ctx.r.reservation_id, ORDER_A);
    expect(await ctx.commit(ctx.r.reservation_id, ORDER_A)).toEqual({ points_redeemed: 60, points_balance: 40 });
    expect(ctx.db.ledger.filter((l) => l.source === 'pos_redeem')).toHaveLength(1);
    expect(await code(ctx.commit(ctx.r.reservation_id, ORDER_A, `pos_redeem:${ORDER_B}`))).toBe('409 idempotency_conflict');
  });

  it('commit: order lain → order_mismatch; reservasi tidak dikenal → 404', async () => {
    const ctx = await reserved();
    expect(await code(ctx.commit(ctx.r.reservation_id, ORDER_B))).toBe('409 order_mismatch');
    expect(await code(ctx.commit('11111111-2222-4333-8444-555555555555', ORDER_A))).toBe('404 reservation_not_found');
  });

  it('K-3: commit memverifikasi member_ref & value_idr terhadap reservasi', async () => {
    const ctx = await reserved();
    expect(await code(ctx.commit(ctx.r.reservation_id, ORDER_A, undefined, { memberRef: OTHER }))).toBe('409 member_mismatch');
    expect(await code(ctx.commit(ctx.r.reservation_id, ORDER_A, undefined, { valueIdr: 6001 }))).toBe('409 value_mismatch');
    expect(ctx.db.ledger.filter((l) => l.source === 'pos_redeem')).toHaveLength(0);
    expect((await ctx.commit(ctx.r.reservation_id, ORDER_A, undefined, { valueIdr: 6000 })).points_redeemed).toBe(60);
  });

  it('FIFO: redeem memakai bucket yang paling cepat kedaluwarsa; saldo tetap benar setelah bucket itu kedaluwarsa', async () => {
    const ctx = setup({ LOYALTY_POINT_EXPIRY_DAYS: '30', LOYALTY_REDEEM_MIN_POINTS: '1' });
    await ctx.earn(ORDER_B, 300_000); // 30 poin, kedaluwarsa E1
    ctx.clock.advanceSeconds(10 * DAY);
    await ctx.earn(ORDER_C, 400_000); // 40 poin, kedaluwarsa E2 = E1 + 10 hari
    const r = await ctx.reserve(ORDER_A, 50);
    await ctx.commit(r.reservation_id, ORDER_A);
    const debits = ctx.db.ledger.filter((l) => l.source === 'pos_redeem').map((l) => [l.delta_points, l.expires_at]);
    const [e1, e2] = ctx.db.ledger.filter((l) => l.source === 'pos_earn').map((l) => l.expires_at);
    expect(debits).toEqual([
      [-30, e1],
      [-20, e2],
    ]);
    expect(ctx.db.ledger.filter((l) => l.source === 'pos_redeem').map((l) => l.idempotency_key)).toEqual([
      `pos_redeem:${ORDER_A}`,
      `pos_redeem:${ORDER_A}#2`,
    ]);
    ctx.clock.advanceSeconds(25 * DAY); // E1 lewat, E2 belum
    expect(await ctx.loyalty.balanceByHash(ctx.hash)).toBe(20); // SUM naif tanpa alokasi akan memberi -10
  });

  it('K-3: commit setelah TTL UI tetapi < 24 jam (offline-sync terlambat) → dibukukan, outcome late; poin tetap tertahan', async () => {
    const ctx = await reserved(60, 100);
    ctx.clock.advanceSeconds(23 * 3600);
    expect((await ctx.sweeper.sweepOnce()).expired).toBe(0);
    expect(await code(ctx.reserve(ORDER_B, 50))).toBe('409 insufficient_points'); // 60 masih ditahan
    expect(await ctx.commit(ctx.r.reservation_id, ORDER_A)).toEqual({ points_redeemed: 60, points_balance: 40 });
    expect(await ctx.metrics.render()).toContain('efn_loyalty_pos_ops_total{op="commit",outcome="late"} 1');
  });

  it('K-3: lewat 24 jam → reservasi dilepas job; commit → 409 reservation_expired (juga sebelum job berjalan)', async () => {
    const ctx = await reserved(60, 100);
    const ctx2 = await reserved(60, 100);
    ctx.clock.advanceSeconds(DAY + 1);
    ctx2.clock.advanceSeconds(DAY + 1);
    expect(await code(ctx2.commit(ctx2.r.reservation_id, ORDER_A))).toBe('409 reservation_expired'); // job belum jalan
    expect((await ctx.sweeper.sweepOnce()).expired).toBe(1);
    expect(await code(ctx.commit(ctx.r.reservation_id, ORDER_A))).toBe('409 reservation_expired');
    expect(ctx.db.ledger.filter((l) => l.source === 'pos_redeem')).toHaveLength(0);
  });

  it('release: idempoten, poin tersedia lagi; commit setelah release → reservation_released; release committed → already_committed', async () => {
    const ctx = await reserved(60, 100);
    expect(await ctx.svc.release(ctx.r.reservation_id)).toEqual({ ok: true });
    expect(await ctx.svc.release(ctx.r.reservation_id)).toEqual({ ok: true });
    expect(await code(ctx.commit(ctx.r.reservation_id, ORDER_A))).toBe('409 reservation_released');
    const again = await ctx.reserve(ORDER_B, 100);
    await ctx.commit(again.reservation_id, ORDER_B);
    expect(await code(ctx.svc.release(again.reservation_id))).toBe('409 already_committed');
    expect(await code(ctx.svc.release('11111111-2222-4333-8444-555555555555'))).toBe('404 reservation_not_found');
  });
});

describe('LoyaltyReservationSweeper', () => {
  it('reservasi lewat hold_until → expired (metrik); baris akhir > retensi dihapus; reserved aktif tidak disentuh', async () => {
    const ctx = setup({ LOYALTY_RESERVATION_RETENTION_DAYS: '1' });
    await ctx.earn(ORDER_C, 1_000_000);
    const a = await ctx.reserve(ORDER_A, 60);
    ctx.clock.advanceSeconds(DAY + 1);
    const b = await ctx.reserve(ORDER_B, 60);
    expect(await ctx.sweeper.sweepOnce()).toEqual({ expired: 1, purged: 0, skipped: false });
    expect(ctx.db.reservations.get(a.reservation_id)?.status).toBe('expired');
    expect(ctx.db.reservations.get(b.reservation_id)?.status).toBe('reserved');
    expect(await ctx.metrics.render()).toContain('efn_loyalty_reservation_expired_total 1');
    ctx.clock.advanceSeconds(2 * DAY);
    const res = await ctx.sweeper.sweepOnce();
    expect(res).toEqual({ expired: 1, purged: 1, skipped: false });
    expect(ctx.db.reservations.has(a.reservation_id)).toBe(false);
  });
});

describe('PosLoyaltyService — reverse (§9.4)', () => {
  /** Order A: earn 25 poin (Rp250 000) + redeem 50 poin. Saldo awal 100 dari order C. */
  async function sold() {
    const ctx = setup();
    await ctx.earn(ORDER_C, 1_000_000);
    const r = await ctx.reserve(ORDER_A, 50, { tok: ctx.token(ORDER_A, { amount: 250_000 }), amountTotal: 250_000 });
    await ctx.commit(r.reservation_id, ORDER_A);
    await ctx.earn(ORDER_A, 250_000);
    expect(await ctx.loyalty.balanceByHash(ctx.hash)).toBe(75);
    return ctx;
  }

  it('refund penuh: poin redeem dikembalikan, poin earn ditarik → saldo kembali seperti sebelum order', async () => {
    const ctx = await sold();
    expect(await ctx.reverse(REFUND_1, ORDER_A, 1)).toEqual({ points_clawed_back: 25, points_restored: 50, points_balance: 100 });
    const rows = ctx.db.ledger.filter((l) => l.source === 'pos_reverse');
    expect(rows.every((l) => l.source_ref === ORDER_A && l.company_code === 'FNB')).toBe(true);
    expect(rows.map((l) => l.reason).sort()).toEqual(['pos_refund_clawback', 'pos_refund_restore']);
  });

  it('refund parsial proporsional: clawback dibulatkan ke atas, restore ke bawah; kumulatif tidak melebihi total asal', async () => {
    const ctx = await sold();
    expect(await ctx.reverse(REFUND_1, ORDER_A, 0.5)).toEqual({ points_clawed_back: 13, points_restored: 25, points_balance: 87 });
    expect(await ctx.reverse(REFUND_2, ORDER_A, 0.5)).toEqual({ points_clawed_back: 12, points_restored: 25, points_balance: 100 });
    expect(await ctx.reverse('cc33dd44-5555-4e6f-8a7b-8c9d0e1f2a3b', ORDER_A, 0.5)).toEqual({
      points_clawed_back: 0,
      points_restored: 0,
      points_balance: 100,
    });
  });

  it('galat floating point tidak menambah poin: 30 × 0,1 → clawback 3 (bukan 4)', async () => {
    const ctx = setup();
    await ctx.earn(ORDER_A, 300_000);
    expect((await ctx.reverse(REFUND_1, ORDER_A, 0.1)).points_clawed_back).toBe(3);
  });

  it('idempoten: retry key sama → respons sama tanpa entri baru; key sama dengan member lain → idempotency_conflict', async () => {
    const ctx = await sold();
    const first = await ctx.reverse(REFUND_1, ORDER_A, 0.5);
    const n = ctx.db.ledger.length;
    expect(await ctx.reverse(REFUND_1, ORDER_A, 0.5)).toEqual(first);
    expect(ctx.db.ledger).toHaveLength(n);
    expect(await code(ctx.reverse(REFUND_1, ORDER_A, 0.5, OTHER))).toBe('409 idempotency_conflict');
  });

  it('K-6: earn order asal belum tercatat → 409 earn_pending (Odoo retry); member lain → member_mismatch', async () => {
    const ctx = await sold();
    expect(await code(ctx.reverse(REFUND_1, ORDER_B, 1))).toBe('409 earn_pending');
    expect(await code(ctx.reverse(REFUND_2, ORDER_A, 1, OTHER))).toBe('409 member_mismatch');
    // redeem sudah di-commit tetapi earn order itu belum sampai → tetap earn_pending (tidak ada restore setengah)
    const ctx2 = setup({ LOYALTY_REDEEM_MIN_POINTS: '1' });
    await ctx2.earn(ORDER_C, 500_000);
    const r = await ctx2.reserve(ORDER_A, 20);
    await ctx2.commit(r.reservation_id, ORDER_A);
    expect(await code(ctx2.reverse(REFUND_1, ORDER_A, 1))).toBe('409 earn_pending');
    expect(ctx2.db.ledger.filter((l) => l.source === 'pos_reverse')).toHaveLength(0);
  });

  it('K-6: earn 0 poin tetap tercatat → reverse tidak earn_pending (restore redeem saja)', async () => {
    const ctx = setup({ LOYALTY_REDEEM_MIN_POINTS: '1' });
    await ctx.earn(ORDER_C, 500_000);
    const r = await ctx.reserve(ORDER_A, 20);
    await ctx.commit(r.reservation_id, ORDER_A);
    expect(await ctx.earn(ORDER_A, 9_999)).toEqual({ points_earned: 0, points_balance: 30 });
    expect(ctx.db.earns.get(ORDER_A)).toMatchObject({ points_earned: 0, member_hash: ctx.hash, eligible_amount: 9_999 });
    expect(await ctx.reverse(REFUND_1, ORDER_A, 1)).toEqual({ points_clawed_back: 0, points_restored: 20, points_balance: 50 });
  });

  it('saldo negatif dicatat (utang) dan dilunasi earn berikutnya; utang tidak hidup lagi saat kredit pelunas kedaluwarsa', async () => {
    const ctx = setup({ LOYALTY_REDEEM_MIN_POINTS: '1', LOYALTY_POINT_EXPIRY_DAYS: '30' });
    await ctx.earn(ORDER_A, 200_000); // +20
    const r = await ctx.reserve(ORDER_B, 20, { tok: ctx.token(ORDER_B) });
    await ctx.commit(r.reservation_id, ORDER_B); // −20 → 0
    expect(await ctx.reverse(REFUND_1, ORDER_A, 1)).toEqual({ points_clawed_back: 20, points_restored: 0, points_balance: -20 });
    expect(ctx.db.ledger.find((l) => l.reason === 'pos_refund_clawback')?.expires_at).toBeNull();
    const next = await ctx.earn(ORDER_C, 300_000); // +30: 20 melunasi utang (NULL), 10 bermasa berlaku
    expect(next).toEqual({ points_earned: 30, points_balance: 10 });
    expect(ctx.db.ledger.filter((l) => l.source_ref === ORDER_C).map((l) => [l.delta_points, l.expires_at === null])).toEqual([
      [20, true],
      [10, false],
    ]);
    ctx.clock.advanceSeconds(40 * DAY);
    expect(await ctx.loyalty.balanceByHash(ctx.hash)).toBe(0);
  });

  it('restore ke bucket redeem asal yang sudah kedaluwarsa → masa berlaku baru (poin tidak langsung hangus)', async () => {
    const ctx = setup({ LOYALTY_REDEEM_MIN_POINTS: '1', LOYALTY_POINT_EXPIRY_DAYS: '30' });
    await ctx.earn(ORDER_C, 500_000); // +50 (E1)
    const r = await ctx.reserve(ORDER_A, 50, { tok: ctx.token(ORDER_A) });
    await ctx.commit(r.reservation_id, ORDER_A);
    await ctx.earn(ORDER_A, 0); // order dibayar penuh dengan poin: earn 0 tetap dipanggil (K-6)
    ctx.clock.advanceSeconds(40 * DAY); // E1 lewat
    const res = await ctx.reverse(REFUND_1, ORDER_A, 1);
    expect(res).toEqual({ points_clawed_back: 0, points_restored: 50, points_balance: 50 });
    const restore = ctx.db.ledger.find((l) => l.reason === 'pos_refund_restore')!;
    expect(restore.expires_at).toBe(expiryFor(ctx.clock.now(), 30)?.toISOString());
  });

  it('kegagalan di tengah transaksi di-rollback (tidak ada entri setengah jadi)', async () => {
    const ctx = setup({ LOYALTY_POINT_EXPIRY_DAYS: '30', LOYALTY_REDEEM_MIN_POINTS: '1' });
    await ctx.earn(ORDER_B, 300_000);
    ctx.clock.advanceSeconds(10 * DAY);
    await ctx.earn(ORDER_C, 400_000);
    const r = await ctx.reserve(ORDER_A, 50);
    // Kunci bagian kedua sudah dipakai entri lain → bagian pertama tertulis, bagian kedua DO NOTHING → konflik → rollback
    ctx.db.ledger.push({ ...ctx.db.ledger[0], id: 999, member_ref_hash: 'f'.repeat(64), idempotency_key: `pos_redeem:${ORDER_A}#2`, source: 'adjustment', source_ref: null });
    const before = ctx.db.ledger.length;
    expect(await code(ctx.commit(r.reservation_id, ORDER_A))).toBe('409 idempotency_conflict');
    expect(ctx.db.ledger).toHaveLength(before);
    expect(ctx.db.reservations.get(r.reservation_id)?.status).toBe('reserved');
    expect(ctx.db.rollbacks).toBe(1);
  });
});

describe('OTP purpose pos_redeem end-to-end (PosService → token → reserve / consume)', () => {
  function otpSetup() {
    const ctx = setup();
    const sender = new FakeWhatsAppSender();
    const limiter = new RateLimiter(ctx.kv);
    limiter.now = ctx.clock.now;
    const otp = new OtpService(ctx.config, ctx.kv, new FakeDb(), sender, ctx.metrics, limiter, ctx.cipher);
    otp.now = ctx.clock.now;
    const pos = new PosService(ctx.config, otp, ctx.tokens, limiter, ctx.loyalty);
    return { ...ctx, sender, pos };
  }

  async function obtain(ctx: ReturnType<typeof otpSetup>, purpose: 'pos_promo' | 'pos_redeem'): Promise<string> {
    const req = await ctx.pos.requestOtp(
      parsePosOtpRequest({ member_ref: MEMBER, order_ref: ORDER_A, amount: 125_000, company_code: 'FNB', pos_config_id: 3, purpose }),
      null,
    );
    const res = await ctx.pos.verifyOtp({ challengeId: req.challenge_id, orderRef: ORDER_A, code: ctx.sender.lastCode() }, null);
    return (res as PosOtpVerifyResponse).discount_token;
  }

  it('token pos_redeem → reserve OK, tetapi consume promo menolak (invalid)', async () => {
    const ctx = otpSetup();
    await ctx.earn(ORDER_C, 1_000_000);
    const tok = await obtain(ctx, 'pos_redeem');
    expect((await ctx.reserve(ORDER_A, 60, { tok })).points).toBe(60);
    const err = await catchApiError(() =>
      ctx.pos.consumeToken({ discountToken: tok, orderRef: ORDER_A, memberRef: MEMBER, amountTotal: 125_000 }),
    );
    expect(err.getStatus()).toBe(409);
    expect(err.body).toEqual({ ok: false, reason: 'invalid' });
  });

  it('token pos_promo (default tanpa purpose) → consume OK, reserve token_invalid', async () => {
    const ctx = otpSetup();
    await ctx.earn(ORDER_C, 1_000_000);
    const tok = await obtain(ctx, 'pos_promo');
    expect(await code(ctx.reserve(ORDER_A, 60, { tok }))).toBe('409 token_invalid');
    expect(await ctx.pos.consumeToken({ discountToken: tok, orderRef: ORDER_A, memberRef: MEMBER, amountTotal: 125_000 })).toEqual({ ok: true });
  });

  it('DTO OTP request: purpose default pos_promo; purpose tak dikenal → 400', async () => {
    const base = { member_ref: MEMBER, order_ref: ORDER_A, amount: 1, company_code: 'FNB', pos_config_id: 1 };
    expect(parsePosOtpRequest(base).purpose).toBe('pos_promo');
    expect(parsePosOtpRequest({ ...base, purpose: 'pos_redeem' }).purpose).toBe('pos_redeem');
    for (const purpose of ['login', 'dsar', 'redeem', 1]) {
      expect(await code(Promise.resolve().then(() => parsePosOtpRequest({ ...base, purpose })))).toBe('400 invalid_request');
    }
  });
});

describe('DTO & controller loyalty POS', () => {
  const earnBody = { idempotency_key: `pos_earn:${ORDER_A}`, order_ref: ORDER_A, member_ref: MEMBER, company_code: 'FNB', eligible_amount: 125000 };

  it('controller dilindungi ServiceKeyGuard', () => {
    expect(Reflect.getMetadata(GUARDS_METADATA, PosLoyaltyController)).toContain(ServiceKeyGuard);
  });

  it('earn: body valid diparse; nomor 08… dinormalisasi', () => {
    expect(parsePosEarn({ ...earnBody, member_ref: '081234567890' })).toEqual({
      idempotencyKey: `pos_earn:${ORDER_A}`,
      orderRef: ORDER_A,
      memberRef: MEMBER,
      companyCode: 'FNB',
      eligibleAmount: 125000,
      posConfigId: null,
    });
    // SEC-61: pos_config_id opsional (kunci batas per kasir per jam)
    expect(parsePosEarn({ ...earnBody, pos_config_id: 7 }).posConfigId).toBe(7);
  });

  it.each([
    [{ ...earnBody, idempotency_key: `earn:${ORDER_A}` }],
    [{ ...earnBody, idempotency_key: 'pos_earn:' }],
    [{ ...earnBody, idempotency_key: 'pos_earn:a b' }],
    [{ ...earnBody, company_code: 'XYZ' }],
    [{ ...earnBody, eligible_amount: -1 }],
    [{ ...earnBody, eligible_amount: '1000' }],
    [{ ...earnBody, pos_config_id: 0 }],
    [{ ...earnBody, pos_config_id: '7' }],
    [{ ...earnBody, pos_config_id: 1.5 }],
  ])('earn: body tidak valid → 400 (%j)', async (body) => {
    expect(await code(Promise.resolve().then(() => parsePosEarn(body)))).toBe('400 invalid_request');
  });

  it('nomor non-Indonesia → 400 invalid_phone', async () => {
    expect(await code(Promise.resolve().then(() => parsePosEarn({ ...earnBody, member_ref: '14155550100' })))).toBe('400 invalid_phone');
  });

  it('reserve/commit/reverse: validasi bentuk', async () => {
    const bad = (fn: () => unknown) => code(Promise.resolve().then(fn));
    expect(await bad(() => parseRedeemReserve({ discount_token: 't', order_ref: ORDER_A, member_ref: MEMBER, points: 1.5, amount_total: 1 }))).toBe(
      '400 invalid_request',
    );
    expect(await bad(() => parseRedeemCommit({ reservation_id: 'x', order_ref: ORDER_A, member_ref: MEMBER, value_idr: 1, idempotency_key: `pos_redeem:${ORDER_A}` }))).toBe(
      '400 invalid_request',
    );
    expect(
      await bad(() =>
        parseRedeemCommit({ reservation_id: '11111111-2222-4333-8444-555555555555', order_ref: ORDER_A, member_ref: MEMBER, value_idr: 1, idempotency_key: `pos_earn:${ORDER_A}` }),
      ),
    ).toBe('400 invalid_request');
    expect(
      await bad(() =>
        parseRedeemCommit({ reservation_id: '11111111-2222-4333-8444-555555555555', order_ref: ORDER_A, member_ref: MEMBER, value_idr: 1.5, idempotency_key: `pos_redeem:` }),
      ),
    ).toBe('400 invalid_request');
    expect(
      await bad(() =>
        parseRedeemCommit({ reservation_id: '11111111-2222-4333-8444-555555555555', order_ref: ORDER_A, value_idr: 1, idempotency_key: `pos_redeem:` }),
      ),
    ).toBe('400 invalid_phone');
    const rev = {
      idempotency_key: `pos_reverse:${REFUND_1}`,
      original_order_ref: ORDER_A,
      refund_order_ref: REFUND_1,
      member_ref: MEMBER,
      refunded_eligible_amount: 1000,
      refund_ratio: 0.5,
    };
    expect(parsePosReverse(rev).refundRatio).toBe(0.5);
    for (const refund_ratio of [0, -0.1, 1.01, '0.5']) {
      expect(await bad(() => parsePosReverse({ ...rev, refund_ratio }))).toBe('400 invalid_request');
    }
    expect(await bad(() => parsePosReverse({ ...rev, refund_order_ref: ORDER_A }))).toBe('400 invalid_request');
  });
});

describe('G18 kontrak §9.9 v6 — poin = alat bayar (K-18-2..K-18-5)', () => {
  it('K-18-2 / Rp1: amount_total reserve, value_idr, payment_amount, points_restored_value wajib integer; dasar earn/refund dibulatkan ke bawah', async () => {
    const reserveBody = { discount_token: 't', order_ref: ORDER_A, member_ref: MEMBER, points: 10, amount_total: 125_000 };
    expect(parseRedeemReserve(reserveBody).amountTotal).toBe(125_000);
    expect((await catchApiError(() => parseRedeemReserve({ ...reserveBody, amount_total: 125_000.5 }))).getStatus()).toBe(400);
    const earnBody = { idempotency_key: `pos_earn:${ORDER_A}`, order_ref: ORDER_A, member_ref: MEMBER, company_code: 'FNB', eligible_amount: 44_703.97 };
    expect(parsePosEarn(earnBody).eligibleAmount).toBe(44_703);
    const commitBody = {
      reservation_id: '11111111-2222-4333-8444-555555555555',
      order_ref: ORDER_A,
      member_ref: MEMBER,
      value_idr: 5000,
      payment_amount: 5000,
      idempotency_key: `pos_redeem:${ORDER_A}`,
    };
    expect(parseRedeemCommit(commitBody).paymentAmount).toBe(5000);
    const noPayment: Record<string, unknown> = { ...commitBody };
    delete noPayment.payment_amount;
    expect((await catchApiError(() => parseRedeemCommit(noPayment))).getStatus()).toBe(400);
    expect((await catchApiError(() => parseRedeemCommit({ ...commitBody, payment_amount: 4999.5 }))).getStatus()).toBe(400);
    const rev = {
      idempotency_key: `pos_reverse:${REFUND_1}`,
      original_order_ref: ORDER_A,
      refund_order_ref: REFUND_1,
      member_ref: MEMBER,
      refunded_eligible_amount: 10_000.7,
      refund_ratio: 0.5,
    };
    expect(parsePosReverse(rev)).toMatchObject({ pointsRestoredValue: null, refundedEligibleAmount: 10_000 });
    expect(parsePosReverse({ ...rev, points_restored_value: 2550 }).pointsRestoredValue).toBe(2550);
    for (const v of [-1, 10.5, '2550']) {
      expect((await catchApiError(() => parsePosReverse({ ...rev, points_restored_value: v }))).getStatus()).toBe(400);
    }
  });

  it('K-18-3: payment_amount ≠ value_idr reservasi → 409 payment_mismatch + anomali critical; reservasi tetap reserved', async () => {
    const ctx = setup({ LOYALTY_REDEEM_MIN_POINTS: '1' });
    await ctx.earn(ORDER_C, 1_000_000);
    const r = await ctx.reserve(ORDER_A, 50); // value 5000
    expect(r.value_idr).toBe(5000);
    expect(await code(ctx.commit(r.reservation_id, ORDER_A, undefined, { paymentAmount: 4900 }))).toBe('409 payment_mismatch');
    expect(ctx.db.reservations.get(r.reservation_id)?.status).toBe('reserved');
    expect(await ctx.metrics.render()).toContain('efn_loyalty_anomaly_total{kind="commit_payment_mismatch",severity="critical"} 1');
    expect((await ctx.commit(r.reservation_id, ORDER_A)).points_redeemed).toBe(50);
  });

  it('K-18-4: flag true → earn > porsi uang order berpoin ditolak; flag false → pagar mati; params memuat flag', async () => {
    const ctx = setup({ LOYALTY_REDEEM_MIN_POINTS: '1' });
    expect(ctx.svc.params().earn_exclude_point_payment).toBe(true);
    await ctx.earn(ORDER_C, 1_000_000);
    const r = await ctx.reserve(ORDER_A, 50, { amountTotal: 125_000 });
    await ctx.commit(r.reservation_id, ORDER_A);
    // batas = floor(125000 × 1,20) − 5000 = 145000
    expect(await code(ctx.earn(ORDER_A, 145_001))).toBe('409 earn_limit_exceeded');
    expect(await ctx.metrics.render()).toContain('efn_loyalty_anomaly_total{kind="earn_exceeds_money_portion",severity="warning"} 1');
    // contoh PoC #2 skala: eligible × faktor jauh di bawah batas → diterima
    expect((await ctx.earn(ORDER_A, 100_000)).points_earned).toBe(10);
    // order tanpa redeem tidak terpengaruh
    expect((await ctx.earn(ORDER_B, 300_000)).points_earned).toBe(30);

    const off = setup({ LOYALTY_REDEEM_MIN_POINTS: '1', LOYALTY_EARN_EXCLUDE_POINT_PAYMENT: 'false' });
    expect(off.svc.params().earn_exclude_point_payment).toBe(false);
    await off.earn(ORDER_C, 1_000_000);
    const r2 = await off.reserve(ORDER_A, 50, { amountTotal: 125_000 });
    await off.commit(r2.reservation_id, ORDER_A);
    expect((await off.earn(ORDER_A, 200_000)).points_earned).toBe(20);
  });

  it('K-18-5: restore dari points_restored_value (floor ÷ nilai poin), clawback tetap refund_ratio; melebihi sisa → dipotong + anomali; event lama tanpa field → ratio', async () => {
    const ctx = setup({ LOYALTY_REDEEM_MIN_POINTS: '1' });
    await ctx.earn(ORDER_C, 1_000_000);
    const r = await ctx.reserve(ORDER_A, 50); // 5000 IDR
    await ctx.commit(r.reservation_id, ORDER_A);
    await ctx.earn(ORDER_A, 100_000); // +10
    const log = jest.spyOn(Logger.prototype, 'log').mockImplementation(() => undefined);
    const warn = jest.spyOn(Logger.prototype, 'warn').mockImplementation(() => undefined);
    try {
      // refund sebagian: nilai poin 2550 → 25 poin (sisa Rp50 memihak PT), clawback ceil(10 × 0,5) = 5
      const a = await ctx.reverse(REFUND_1, ORDER_A, 0.5, MEMBER, 2550);
      expect(a).toMatchObject({ points_restored: 25, points_clawed_back: 5 });
      const line = log.mock.calls.map((c) => String(c[0])).find((l) => l.startsWith('EFN_LOYALTY_RESTORE '))!;
      expect(JSON.parse(line.slice('EFN_LOYALTY_RESTORE '.length))).toEqual({ order_ref: ORDER_A, value_idr: 2550, points: 25, remainder_idr: 50 });
      expect(line).not.toContain(MEMBER);
      // refund kedua 2950 (diharapkan 5000 − 2550 = 2450, dalam toleransi 10 % = Rp500) → 29 poin diminta, sisa 25 →
      // dipotong 25 + anomali restore_exceeds_redeem
      const b = await ctx.reverse(REFUND_2, ORDER_A, 0.5, MEMBER, 2950);
      expect(b.points_restored).toBe(25);
      expect(warn.mock.calls.some((c) => String(c[0]).includes('"kind":"restore_exceeds_redeem"'))).toBe(true);
      const restored = ctx.db.ledger.filter((l) => l.reason === 'pos_refund_restore').reduce((s, l) => s + l.delta_points, 0);
      expect(restored).toBe(50);
      // K-18-6: tersimpan persisten untuk rekonsiliasi ETL (bentuk _PLATFORM_RESTORE_ROWS)
      expect(ctx.db.reverses.get(REFUND_1)).toMatchObject({ original_order_ref: ORDER_A, company_code: 'FNB', points_restored_value: 2550, points_restored: 25 });
      expect(ctx.db.reverses.get(REFUND_2)).toMatchObject({ points_restored_value: 2950, points_restored: 25, refund_ratio: 0.5 });
      // retry identik → replay; retry dengan nilai berbeda → 409 idempotency_conflict
      expect((await ctx.reverse(REFUND_1, ORDER_A, 0.5, MEMBER, 2550)).points_restored).toBe(25);
      expect(await code(ctx.reverse(REFUND_1, ORDER_A, 0.5, MEMBER, 9999))).toBe('409 idempotency_conflict');
      expect(ctx.db.reverses.size).toBe(2);
      expect(ctx.db.ledger.every((l) => Number.isInteger(l.delta_points))).toBe(true);
    } finally {
      log.mockRestore();
      warn.mockRestore();
    }

    // event lama tanpa points_restored_value → refund_ratio (kompatibel mundur)
    const old = setup({ LOYALTY_REDEEM_MIN_POINTS: '1' });
    await old.earn(ORDER_C, 1_000_000);
    const r2 = await old.reserve(ORDER_A, 50);
    await old.commit(r2.reservation_id, ORDER_A);
    await old.earn(ORDER_A, 100_000);
    expect(await old.reverse(REFUND_1, ORDER_A, 0.5)).toMatchObject({ points_restored: 25, points_clawed_back: 5 });
    expect(old.db.reverses.size).toBe(0); // event v4 tanpa nilai: tidak ada baris rekonsiliasi

    // order asal tanpa redeem: refund nilai poin 0 → tidak ada restore walau ratio 1
    const zero = setup({ LOYALTY_REDEEM_MIN_POINTS: '1' });
    await zero.earn(ORDER_A, 100_000);
    expect(await zero.reverse(REFUND_1, ORDER_A, 1, MEMBER, 0)).toMatchObject({ points_restored: 0, points_clawed_back: 10 });
    expect(zero.db.reverses.get(REFUND_1)).toMatchObject({ points_restored_value: 0, points_restored: 0 });
  });
});

describe('G18 K-18-8: OTP POS pos_enroll — pernyataan usia 17+ saat kasir mendaftarkan member', () => {
  function enrollSetup() {
    const ctx = setup();
    const sender = new FakeWhatsAppSender();
    const limiter = new RateLimiter(ctx.kv);
    limiter.now = ctx.clock.now;
    const members = new FakeMemberTable();
    const db = new FakeDb((t, p) => members.handle(t, p));
    const otp = new OtpService(ctx.config, ctx.kv, db, sender, ctx.metrics, limiter, ctx.cipher);
    otp.now = ctx.clock.now;
    const pos = new PosService(ctx.config, otp, ctx.tokens, limiter, ctx.loyalty, new MemberService(ctx.config, db, ctx.cipher));
    return { ...ctx, sender, pos, members, mdb: db };
  }
  const body = { member_ref: MEMBER, order_ref: ORDER_A, amount: 0, company_code: 'RTL', pos_config_id: 7, purpose: 'pos_enroll' };

  it('tanpa age_confirmed → 400 age_confirmation_required, OTP tidak dikirim; bukan boolean → 400 invalid_request', async () => {
    const ctx = enrollSetup();
    const err = await catchApiError(() => ctx.pos.requestOtp(parsePosOtpRequest(body), null));
    expect(err.getStatus()).toBe(400);
    expect(err.body).toEqual({ error: { code: 'age_confirmation_required', message: expect.stringContaining('17 tahun') } });
    expect(ctx.sender.otps).toHaveLength(0);
    expect((await catchApiError(() => parsePosOtpRequest({ ...body, age_confirmed: 'ya' }))).body).toEqual({
      error: { code: 'invalid_request', message: expect.any(String) },
    });
    // purpose lain tidak butuh pernyataan
    await expect(ctx.pos.requestOtp(parsePosOtpRequest({ ...body, purpose: 'pos_promo' }), null)).resolves.toBeDefined();
  });

  it('dengan age_confirmed → verify mendaftarkan member (age_confirmed_at, source=pos, pos_config_id); tanpa discount_token', async () => {
    const ctx = enrollSetup();
    const req = await ctx.pos.requestOtp(parsePosOtpRequest({ ...body, age_confirmed: true }), null);
    const res = await ctx.pos.verifyOtp(parsePosOtpVerify({ challenge_id: req.challenge_id, order_ref: ORDER_A, code: ctx.sender.lastCode(), age_confirmed: true }), null);
    expect(res).toEqual({ ok: true, enrolled: true });
    const ins = ctx.mdb.find('INSERT INTO platform.member')[0];
    expect(ins.text).toContain('age_confirmed_source');
    expect(ins.params.slice(3)).toEqual([true, 'pos', 7]);
    expect(ctx.members.byHash(memberHash(ctx.config.memberHashPepper, MEMBER))).toBeDefined();
    // member lama tanpa pernyataan dilengkapi sekali (UPDATE bersyarat)
    ctx.clock.advanceSeconds(3600);
    const req2 = await ctx.pos.requestOtp(parsePosOtpRequest({ ...body, age_confirmed: true, pos_config_id: 9 }), null);
    await ctx.pos.verifyOtp(parsePosOtpVerify({ challenge_id: req2.challenge_id, order_ref: ORDER_A, code: ctx.sender.lastCode() }), null);
    const upd = ctx.mdb.find('SET age_confirmed_at = now()');
    expect(upd[0].params).toEqual([expect.any(String), 'pos', 9]);
    expect(upd[0].text).toContain('age_confirmed_at IS NULL');
  });
});

describe('Review r11 — SEC-118 / SEC-123 / SEC-124 (L-25f/k/l)', () => {
  async function redeemed(ctx: ReturnType<typeof setup>) {
    await ctx.earn(ORDER_C, 1_000_000);
    const r = await ctx.reserve(ORDER_A, 50); // V = 5000
    await ctx.commit(r.reservation_id, ORDER_A);
    await ctx.earn(ORDER_A, 100_000);
    return r;
  }

  it('SEC-118: points_restored_value dibandingkan restore yang diharapkan BFF (V × rasio kumulatif − restore lalu); di luar toleransi → 409 + anomali, tanpa entri', async () => {
    const ctx = setup({ LOYALTY_REDEEM_MIN_POINTS: '1' });
    await redeemed(ctx);
    const warn = jest.spyOn(Logger.prototype, 'warn').mockImplementation(() => undefined);
    try {
      const before = ctx.db.ledger.length;
      // rasio 0,2 → diharapkan 1000; Odoo melaporkan 4000 (selisih 3000 > max(100, 500))
      expect(await code(ctx.reverse(REFUND_1, ORDER_A, 0.2, MEMBER, 4000))).toBe('409 restore_value_mismatch');
      expect(ctx.db.ledger).toHaveLength(before);
      expect(ctx.db.reverses.size).toBe(0);
      const line = warn.mock.calls.map((c) => String(c[0])).find((l) => l.includes('restore_value_unexpected'))!;
      expect(JSON.parse(line.slice('EFN_LOYALTY_ANOMALY '.length))).toMatchObject({
        severity: 'critical',
        points_restored_value: 4000,
        expected_value: 1000,
        tolerance: 500,
      });
      expect(line).not.toContain(MEMBER);
      // nilai benar (dalam toleransi) diterima; rasio kumulatif ikut dihitung untuk refund berikutnya
      expect((await ctx.reverse(REFUND_1, ORDER_A, 0.2, MEMBER, 1000)).points_restored).toBe(10);
      // refund ke-2 rasio 0,8 → kumulatif 1 → diharapkan 5000 − 1000 = 4000
      expect(await code(ctx.reverse(REFUND_2, ORDER_A, 0.8, MEMBER, 1000))).toBe('409 restore_value_mismatch');
      expect((await ctx.reverse(REFUND_2, ORDER_A, 0.8, MEMBER, 4000)).points_restored).toBe(40);
    } finally {
      warn.mockRestore();
    }
    // order tanpa redeem: Odoo melaporkan nilai poin > 0 → ditahan
    const none = setup({ LOYALTY_REDEEM_MIN_POINTS: '1' });
    await none.earn(ORDER_A, 100_000);
    const w2 = jest.spyOn(Logger.prototype, 'warn').mockImplementation(() => undefined);
    try {
      expect(await code(none.reverse(REFUND_1, ORDER_A, 1, MEMBER, 2000))).toBe('409 restore_value_mismatch');
    } finally {
      w2.mockRestore();
    }
    expect(() => testConfig({ LOYALTY_RESTORE_TOLERANCE_PCT: '101' }).loyaltyRestoreTolerancePct).toThrow();
  });

  it('SEC-123: earn order yang commit redeem-nya belum masuk (reservasi reserved) → 409 earn_pending; setelah commit → pagar porsi uang berlaku', async () => {
    const ctx = setup({ LOYALTY_REDEEM_MIN_POINTS: '1' });
    await ctx.earn(ORDER_C, 1_000_000);
    const r = await ctx.reserve(ORDER_A, 50, { amountTotal: 125_000 });
    expect(await code(ctx.earn(ORDER_A, 200_000))).toBe('409 earn_pending');
    expect(ctx.db.earns.has(ORDER_A)).toBe(false);
    await ctx.commit(r.reservation_id, ORDER_A);
    expect(await code(ctx.earn(ORDER_A, 200_000))).toBe('409 earn_limit_exceeded');
    expect((await ctx.earn(ORDER_A, 100_000)).points_earned).toBe(10);
    // reservasi lewat hold_until (tidak akan di-commit) tidak menahan earn order lain
    const r2 = await ctx.reserve(ORDER_B, 10);
    ctx.clock.advanceSeconds(2 * DAY);
    expect((await ctx.earn(ORDER_B, 50_000)).points_earned).toBe(5);
    expect(r2.reservation_id).toBeDefined();
  });

  it('SEC-124: sweeper hanya mem-purge released/expired setelah retensi; committed disimpan 10 th', async () => {
    const ctx = setup({ LOYALTY_REDEEM_MIN_POINTS: '1', LOYALTY_RESERVATION_RETENTION_DAYS: '1' });
    const r = await redeemed(ctx);
    const rel = await ctx.reserve(ORDER_B, 10);
    await ctx.svc.release(rel.reservation_id);
    ctx.clock.advanceSeconds(400 * DAY);
    await ctx.sweeper.sweepOnce();
    expect(ctx.db.reservations.has(rel.reservation_id)).toBe(false);
    expect(ctx.db.reservations.get(r.reservation_id)?.status).toBe('committed');
    ctx.clock.advanceSeconds(3651 * DAY);
    await ctx.sweeper.sweepOnce();
    expect(ctx.db.reservations.has(r.reservation_id)).toBe(false);
    expect(testConfig().loyaltyCommittedReservationRetentionDays).toBe(3650);
    expect(() => testConfig({ LOYALTY_COMMITTED_RESERVATION_RETENTION_DAYS: '30' }).loyaltyCommittedReservationRetentionDays).toThrow();
  });
});

describe('Re-review G18 SEC-128 — cek restore berbasis nilai incl pajak, tidak bergantung kategori earn', () => {
  async function sale(ctx: ReturnType<typeof setup>, orderGoodsIncl: number | null = 125_000) {
    await ctx.earn(ORDER_C, 1_000_000);
    const r = await ctx.reserve(ORDER_A, 50, { amountTotal: 125_000 }); // V = 5000
    await ctx.commit(r.reservation_id, ORDER_A, undefined, { orderGoodsIncl });
    await ctx.earn(ORDER_A, 100_000);
    return r;
  }
  const spyWarn = () => jest.spyOn(Logger.prototype, 'warn').mockImplementation(() => undefined);

  it('DTO: order_goods_incl (commit) & refund_goods_incl / restore_approved (reverse) opsional, integer/boolean', async () => {
    const commitBody = {
      reservation_id: '11111111-2222-4333-8444-555555555555',
      order_ref: ORDER_A,
      member_ref: MEMBER,
      value_idr: 5000,
      payment_amount: 5000,
      idempotency_key: `pos_redeem:${ORDER_A}`,
    };
    expect(parseRedeemCommit(commitBody).orderGoodsIncl).toBeNull();
    expect(parseRedeemCommit({ ...commitBody, order_goods_incl: 125_000 }).orderGoodsIncl).toBe(125_000);
    for (const v of [0, 1.5, '125000']) expect((await catchApiError(() => parseRedeemCommit({ ...commitBody, order_goods_incl: v }))).getStatus()).toBe(400);
    const rev = {
      idempotency_key: `pos_reverse:${REFUND_1}`,
      original_order_ref: ORDER_A,
      refund_order_ref: REFUND_1,
      member_ref: MEMBER,
      refunded_eligible_amount: 0,
      refund_ratio: 0.000001,
    };
    expect(parsePosReverse(rev)).toMatchObject({ refundGoodsIncl: null, restoreApproved: false });
    expect(parsePosReverse({ ...rev, refund_goods_incl: 25_000, restore_approved: true })).toMatchObject({ refundGoodsIncl: 25_000, restoreApproved: true });
    for (const bad of [{ refund_goods_incl: -1 }, { refund_goods_incl: 2.5 }, { restore_approved: 'ya' }]) {
      expect((await catchApiError(() => parsePosReverse({ ...rev, ...bad }))).getStatus()).toBe(400);
    }
  });

  it('refund hanya barang dikecualikan earn (rasio eligible 0) → diterima bila nilai sesuai proporsi incl pajak; toleransi Rp1; kumulatif', async () => {
    const ctx = setup({ LOYALTY_REDEEM_MIN_POINTS: '1' });
    await sale(ctx);
    const warn = spyWarn();
    try {
      // refund rokok Rp25.000 (incl pajak) dari order Rp125.000 → round(25000 × 5000 / 125000) = 1000
      expect(await code(ctx.reverse(REFUND_1, ORDER_A, 0.000001, MEMBER, 1002, { refundGoodsIncl: 25_000 }))).toBe('409 restore_value_mismatch');
      const line = warn.mock.calls.map((c) => String(c[0])).find((l) => l.includes('restore_value_unexpected'))!;
      expect(JSON.parse(line.slice('EFN_LOYALTY_ANOMALY '.length))).toMatchObject({ expected_value: 1000, tolerance: 1, basis: 'goods_incl' });
      const a = await ctx.reverse(REFUND_1, ORDER_A, 0.000001, MEMBER, 1001, { refundGoodsIncl: 25_000 });
      expect(a).toMatchObject({ points_restored: 10, points_clawed_back: 1 });
      expect(ctx.db.reverses.get(REFUND_1)).toMatchObject({ refund_goods_incl: 25_000, restore_approved: false });
      // sisa Rp100.000 → kumulatif 125.000 → 5000 − 1001 = 3999
      expect(await code(ctx.reverse(REFUND_2, ORDER_A, 0.5, MEMBER, 2500, { refundGoodsIncl: 100_000 }))).toBe('409 restore_value_mismatch');
      expect((await ctx.reverse(REFUND_2, ORDER_A, 0.5, MEMBER, 3999, { refundGoodsIncl: 100_000 })).points_restored).toBe(39);
    } finally {
      warn.mockRestore();
    }
  });

  it('hold/review: restore_approved (persetujuan supervisor Odoo) melewati cek nilai, tetap dibatasi sisa redeem, selalu dicatat', async () => {
    const ctx = setup({ LOYALTY_REDEEM_MIN_POINTS: '1' });
    await sale(ctx);
    const warn = spyWarn();
    try {
      expect(await code(ctx.reverse(REFUND_1, ORDER_A, 0.2, MEMBER, 9000, { refundGoodsIncl: 25_000 }))).toBe('409 restore_value_mismatch');
      const res = await ctx.reverse(REFUND_1, ORDER_A, 0.2, MEMBER, 9000, { refundGoodsIncl: 25_000, restoreApproved: true });
      expect(res.points_restored).toBe(50); // diminta 90, dibatasi sisa redeem 50
      const kinds = warn.mock.calls.map((c) => String(c[0]));
      expect(kinds.some((l) => l.includes('"kind":"restore_value_override"'))).toBe(true);
      expect(kinds.some((l) => l.includes('"kind":"restore_exceeds_redeem"'))).toBe(true);
      expect(ctx.db.reverses.get(REFUND_1)).toMatchObject({ restore_approved: true, points_restored: 50 });
    } finally {
      warn.mockRestore();
    }
  });

  it('event lama tanpa refund_goods_incl: rasio eligible 0 → tidak ditolak (anomali unverified); commit lama tanpa order_goods_incl → penyebut amount_total + toleransi', async () => {
    const legacy = setup({ LOYALTY_REDEEM_MIN_POINTS: '1' });
    await sale(legacy, null);
    const warn = spyWarn();
    try {
      expect((await legacy.reverse(REFUND_1, ORDER_A, 0, MEMBER, 1000)).points_restored).toBe(10);
      expect(warn.mock.calls.some((c) => String(c[0]).includes('"kind":"restore_value_unverified"'))).toBe(true);
      // penyebut amount_total reservasi (125.000), toleransi max(nilai poin, 20 % × V = 1000)
      const fb = setup({ LOYALTY_REDEEM_MIN_POINTS: '1' });
      await sale(fb, null);
      expect((await fb.reverse(REFUND_1, ORDER_A, 0.1, MEMBER, 1800, { refundGoodsIncl: 25_000 })).points_restored).toBe(18);
      expect(await code(fb.reverse(REFUND_2, ORDER_A, 0.1, MEMBER, 4000, { refundGoodsIncl: 10_000 }))).toBe('409 restore_value_mismatch');
    } finally {
      warn.mockRestore();
    }
  });

  it('commit: order_goods_incl < payment_amount → 409 payment_mismatch; disimpan sebagai penyebut', async () => {
    const ctx = setup({ LOYALTY_REDEEM_MIN_POINTS: '1' });
    await ctx.earn(ORDER_C, 1_000_000);
    const r = await ctx.reserve(ORDER_A, 50);
    const warn = spyWarn();
    try {
      expect(await code(ctx.commit(r.reservation_id, ORDER_A, undefined, { orderGoodsIncl: 4999 }))).toBe('409 payment_mismatch');
    } finally {
      warn.mockRestore();
    }
    await ctx.commit(r.reservation_id, ORDER_A, undefined, { orderGoodsIncl: 120_000 });
    expect(ctx.db.reservations.get(r.reservation_id)?.order_goods_incl).toBe(120_000);
  });
});
