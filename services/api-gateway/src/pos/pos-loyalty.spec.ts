import { GUARDS_METADATA } from '@nestjs/common/constants';
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
  FakeWhatsAppSender,
  catchApiError,
  testCipher,
  testConfig,
} from '../common/testing/fakes';
import { LoyaltyService, allocateDebit, splitCredit } from '../loyalty/loyalty.service';
import { OtpService } from '../otp/otp.service';
import { DiscountTokenService } from './discount-token.service';
import { parsePosOtpRequest } from './dto/pos.dto';
import { parsePosEarn, parsePosReverse, parseRedeemCommit, parseRedeemReserve } from './dto/pos-loyalty.dto';
import { LoyaltyReservationSweeper } from './loyalty-reservation.sweeper';
import { PosLoyaltyController } from './pos-loyalty.controller';
import { PosLoyaltyService, expiryFor } from './pos-loyalty.service';
import { PosService } from './pos.service';

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
    opts: { memberRef?: string; valueIdr?: number } = {},
  ) =>
    svc.commit({
      reservationId,
      orderRef,
      memberRef: opts.memberRef ?? MEMBER,
      valueIdr: opts.valueIdr ?? db.reservations.get(reservationId)?.value_idr ?? 0,
      idempotencyKey: key,
    });
  const reverse = (refund: string, original: string, ratio: number, memberRef = MEMBER) =>
    svc.reverse({
      idempotencyKey: `pos_reverse:${refund}`,
      originalOrderRef: original,
      refundOrderRef: refund,
      memberRef,
      refundedEligibleAmount: 0,
      refundRatio: ratio,
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
    return res.discount_token;
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
    });
  });

  it.each([
    [{ ...earnBody, idempotency_key: `earn:${ORDER_A}` }],
    [{ ...earnBody, idempotency_key: 'pos_earn:' }],
    [{ ...earnBody, idempotency_key: 'pos_earn:a b' }],
    [{ ...earnBody, company_code: 'XYZ' }],
    [{ ...earnBody, eligible_amount: -1 }],
    [{ ...earnBody, eligible_amount: '1000' }],
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
