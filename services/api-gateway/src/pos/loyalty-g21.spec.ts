import { Logger } from '@nestjs/common';
import { MetricsService } from '../common/metrics/metrics.service';
import { memberHash } from '../common/pii/pii';
import { RateLimiter } from '../common/rate-limit/rate-limiter';
import { FakeLoyaltyDb } from '../common/testing/fake-loyalty-db';
import { FakeClock, FakeKvStore, catchApiError, testCipher, testConfig } from '../common/testing/fakes';
import { parseLoyaltyPolicy } from '../loyalty/loyalty-policy';
import { LoyaltyPolicyService } from '../loyalty/loyalty-policy.service';
import { CompanyCode, LoyaltyService, allocateDebit, splitCredit } from '../loyalty/loyalty.service';
import { DiscountTokenService } from './discount-token.service';
import { LoyaltyAccrualService, classifyFlows, wibDayRange } from './loyalty-accrual.service';
import { PosLoyaltyService } from './pos-loyalty.service';

/**
 * G21 docs/04 §13.1 no. 4–6: earn/redeem memakai kebijakan aktif PT, lot FIFO mencatat PT penerbit, akrual harian
 * per PT (Odoo menarik, L-61). DB = FakeLoyaltyDb (jam DB = FakeClock), Redis = FakeKvStore.
 */

const MEMBER = '6281234567890';
const OTHER = '6285711112222';
const ORDER_A = '3f2b8c1e-7d4a-4e0b-9c55-2a1f0e6d9b10';
const ORDER_B = '0b7e4c2a-1111-4c3d-8e9f-5a6b7c8d9e0f';
const ORDER_C = '7c1d2e3f-2222-4a5b-8c6d-7e8f9a0b1c2d';
const ORDER_D = '9d2e3f4a-3333-4b5c-9d6e-8f9a0b1c2d3e';
const REFUND_1 = 'aa11bb22-3333-4c4d-8e5f-6a7b8c9d0e1f';
const REFUND_2 = 'bb22cc33-4444-4d5e-9f6a-7b8c9d0e1f2a';
const DAY = 86_400;

const BASE_PARAMS = {
  earn_idr_per_point: 10_000,
  redeem_min_points: 50,
  redeem_max_pct: 50,
  max_points_per_order: 5_000,
  daily_max_points_member: 2_000,
  pos_config_hourly_max: 10_000,
  company_hourly_max: 50_000,
  earn_on_money_only: true,
};

function setup(env: Record<string, string> = {}) {
  const clock = new FakeClock(); // 5 Okt 2026 10:00 WIB
  const config = testConfig({ LOYALTY_POLICY_CACHE_SECONDS: '0', ...env });
  const cipher = testCipher(config);
  const kv = new FakeKvStore(clock.now);
  const limiter = new RateLimiter(kv);
  limiter.now = clock.now;
  const metrics = new MetricsService();
  const db = new FakeLoyaltyDb();
  db.now = clock.now;
  const loyalty = new LoyaltyService(config, db, cipher);
  loyalty.now = clock.now;
  const tokens = new DiscountTokenService(config, kv);
  tokens.now = clock.now;
  const policies = new LoyaltyPolicyService(config, db, metrics);
  policies.now = clock.now;
  const svc = new PosLoyaltyService(config, db, loyalty, tokens, limiter, metrics, policies);
  svc.now = clock.now;
  const accruals = new LoyaltyAccrualService(config, db, policies);
  accruals.now = clock.now;

  let version = 0;
  const publish = (company: CompanyCode, params: Partial<typeof BASE_PARAMS> = {}, pointValue?: number) => {
    version += 1;
    const body = {
      schema_version: 1,
      company_code: company,
      version,
      effective_from: '2026-01-01T00:00:00Z',
      approved_by_uid: 5,
      params: { ...BASE_PARAMS, ...params },
      ...(pointValue !== undefined ? { point_value_idr: pointValue } : {}),
    };
    return policies.publish(parseLoyaltyPolicy(Buffer.from(JSON.stringify(body)), company, String(version)));
  };
  const earn = (orderRef: string, eligible: number, company: CompanyCode, memberRef = MEMBER) =>
    svc.earn({ idempotencyKey: `pos_earn:${orderRef}`, orderRef, memberRef, companyCode: company, eligibleAmount: eligible, posConfigId: null });
  const redeem = async (orderRef: string, points: number, company: CompanyCode, memberRef = MEMBER) => {
    const tok = tokens.issue({ purpose: 'pos_redeem', orderRef, memberHash: memberHash(config.memberHashPepper, memberRef), companyCode: company, amount: 125_000 }).token;
    const r = await svc.reserve({ discountToken: tok, orderRef, memberRef, points, amountTotal: 125_000 });
    await svc.commit({ reservationId: r.reservation_id, orderRef, memberRef, valueIdr: r.value_idr, paymentAmount: r.value_idr, orderGoodsIncl: null, idempotencyKey: `pos_redeem:${orderRef}` });
    return r;
  };
  const reverse = (refund: string, original: string, memberRef = MEMBER) =>
    svc.reverse({
      idempotencyKey: `pos_reverse:${refund}`,
      originalOrderRef: original,
      refundOrderRef: refund,
      memberRef,
      refundedEligibleAmount: 0,
      refundRatio: 1,
      pointsRestoredValue: null,
      refundGoodsIncl: null,
      restoreApproved: false,
    });
  const ledger = (pred: (r: (typeof db.ledger)[number]) => boolean = () => true) =>
    db.ledger.filter(pred).map((r) => ({ delta: r.delta_points, company: r.company_code, issuer: r.issuer_company_code, reason: r.reason }));
  /** Akrual tanggal `date` setelah jam dimajukan melewati penutupan (+1 hari 1 jam). */
  const accrualAfterClose = (company: CompanyCode, date: string) => {
    const { end } = wibDayRange(date);
    if (clock.ms < end.getTime() + 3_600_000) clock.ms = end.getTime() + 3_600_000;
    return accruals.accrual(company, date);
  };
  return { clock, config, db, metrics, svc, policies, accruals, publish, earn, redeem, reverse, ledger, accrualAfterClose };
}

async function code(p: Promise<unknown>): Promise<string> {
  const err = await catchApiError(() => p);
  return `${err.getStatus()} ${(err.body as { error: { code: string } }).error.code}`;
}

let warnSpy: jest.SpyInstance;
beforeEach(() => {
  warnSpy = jest.spyOn(Logger.prototype, 'warn').mockImplementation(() => undefined);
  jest.spyOn(Logger.prototype, 'log').mockImplementation(() => undefined);
});
afterEach(() => jest.restoreAllMocks());

describe('alokasi lot (expires_at, penerbit) — FIFO murni', () => {
  const d1 = new Date('2027-01-01T00:00:00Z');
  const d2 = new Date('2027-02-01T00:00:00Z');

  it('debit FIFO lintas penerbit: expires_at terdekat dulu, seri → lot terbit lebih dulu; kekurangan → utang atas penerbit fallback', () => {
    const buckets = [
      { expiresAt: d1, remaining: 10, issuer: 'PB' as const, firstId: 5 },
      { expiresAt: d1, remaining: 20, issuer: 'GL' as const, firstId: 2 },
      { expiresAt: d2, remaining: 5, issuer: 'PB' as const, firstId: 9 },
    ];
    expect(allocateDebit(buckets, 40, 'BCR')).toEqual([
      { expiresAt: d1, points: 20, issuer: 'GL' },
      { expiresAt: d1, points: 10, issuer: 'PB' },
      { expiresAt: d2, points: 5, issuer: 'PB' },
      { expiresAt: null, points: 5, issuer: 'BCR' },
    ]);
  });

  it('kredit melunasi utang per penerbit lot berutang lebih dulu, sisanya lot baru penerbit kredit', () => {
    const buckets = [
      { expiresAt: null, remaining: -8, issuer: 'GL' as const, firstId: 1 },
      { expiresAt: null, remaining: -4, issuer: 'PB' as const, firstId: 3 },
    ];
    expect(splitCredit(buckets, 30, d1, 'BCR')).toEqual([
      { expiresAt: null, points: 8, issuer: 'GL' },
      { expiresAt: null, points: 4, issuer: 'PB' },
      { expiresAt: d1, points: 18, issuer: 'BCR' },
    ]);
  });
});

describe('earn/redeem memakai kebijakan aktif PT (§13.1 no. 4)', () => {
  it('earn: laju & batas kebijakan PT; PT tanpa kebijakan → env; versi kebijakan dicatat di loyalty_pos_earn', async () => {
    const ctx = setup();
    await ctx.publish('GL', { earn_idr_per_point: 20_000, max_points_per_order: 200 });
    expect((await ctx.earn(ORDER_A, 200_000, 'GL')).points_earned).toBe(10);
    expect((await ctx.earn(ORDER_B, 200_000, 'PB')).points_earned).toBe(20);
    expect(ctx.db.earns.get(ORDER_A)?.policy_version).toBe(1);
    expect(ctx.db.earns.get(ORDER_B)?.policy_version).toBeNull();
    // max_points_per_order kebijakan (200) < env (5000)
    expect(await code(ctx.earn(ORDER_C, 5_000_000, 'GL'))).toBe('409 earn_limit_exceeded');
    expect(await ctx.metrics.render()).toContain('efn_loyalty_anomaly_total{kind="earn_limit_order",severity="warning"} 1');
    expect(warnSpy.mock.calls.some((c) => String(c[0]).includes('Kebijakan poin PB belum ada'))).toBe(true);
  });

  it('reserve: minimum & persen kebijakan PT penebus; nilai tukar dari kebijakan global EFN (L-62); params per PT', async () => {
    const ctx = setup();
    await ctx.publish('EFN', {}, 80);
    await ctx.publish('GL', { redeem_min_points: 60, redeem_max_pct: 40 });
    await ctx.earn(ORDER_C, 2_000_000, 'GL');
    expect(await code(ctx.redeem(ORDER_A, 55, 'GL'))).toBe('409 below_minimum');
    const r = await ctx.redeem(ORDER_A, 60, 'GL');
    expect(r.value_idr).toBe(60 * 80);
    expect(await ctx.svc.paramsFor('GL')).toMatchObject({
      company_code: 'GL',
      source: 'policy',
      policy_version: 2,
      point_value_idr: 80,
      point_value_version: 1,
      redeem_min_points: 60,
      redeem_max_pct_of_order: 40,
      earn_exclude_point_payment: true,
      max_points_per_order: 5_000,
    });
    expect(await ctx.svc.paramsFor('PB')).toMatchObject({ source: 'env', policy_version: null, point_value_idr: 80, redeem_min_points: 50 });
    // kompatibel mundur: tanpa company_code = env murni
    expect(ctx.svc.params()).toMatchObject({ point_value_idr: 100 });
  });
});

describe('lot earn FIFO dengan PT penerbit (§13.1 no. 5)', () => {
  async function crossCompany() {
    const ctx = setup({ LOYALTY_POINT_EXPIRY_DAYS: '30' });
    await ctx.publish('EFN', {}, 80);
    await ctx.earn(ORDER_B, 300_000, 'GL'); // +30 lot GL, E1 = 5 Nov 00:00 WIB
    ctx.clock.advanceSeconds(10 * DAY);
    await ctx.earn(ORDER_C, 400_000, 'PB'); // +40 lot PB, E2 = E1 + 10 hari
    await ctx.redeem(ORDER_A, 50, 'PB'); // di PB: 30 dari lot GL (lintas PT) + 20 dari lot PB
    return ctx;
  }

  it('redeem di PB mengonsumsi lot GL lebih dulu (FIFO kedaluwarsa) dan mencatat issuer per bagian', async () => {
    const ctx = await crossCompany();
    expect(ctx.ledger((r) => r.source === 'pos_earn')).toEqual([
      { delta: 30, company: 'GL', issuer: 'GL', reason: 'pos_sale' },
      { delta: 40, company: 'PB', issuer: 'PB', reason: 'pos_sale' },
    ]);
    expect(ctx.ledger((r) => r.source === 'pos_redeem')).toEqual([
      { delta: -30, company: 'PB', issuer: 'GL', reason: 'pos_redeem' },
      { delta: -20, company: 'PB', issuer: 'PB', reason: 'pos_redeem' },
    ]);
  });

  it('reverse: restore kembali ke lot penerbit asal; clawback ke lot earn asal; kedaluwarsa per penerbit', async () => {
    const ctx = await crossCompany();
    await ctx.earn(ORDER_A, 0, 'PB'); // K-6: earn 0 poin order redeem
    expect((await ctx.reverse(REFUND_1, ORDER_A)).points_restored).toBe(50);
    const restores = ctx.ledger((r) => r.reason === 'pos_refund_restore');
    expect(restores).toHaveLength(2);
    expect(restores).toEqual(
      expect.arrayContaining([
        { delta: 20, company: 'PB', issuer: 'PB', reason: 'pos_refund_restore' },
        { delta: 30, company: 'PB', issuer: 'GL', reason: 'pos_refund_restore' },
      ]),
    );
    expect((await ctx.reverse(REFUND_2, ORDER_C)).points_clawed_back).toBe(40);
    expect(ctx.ledger((r) => r.reason === 'pos_refund_clawback')).toEqual([{ delta: -40, company: 'PB', issuer: 'PB', reason: 'pos_refund_clawback' }]);
  });
});

describe('akrual harian GET /v1/pos/loyalty/accruals (§13.1 no. 6)', () => {
  it('nilai integer IDR per PT: earn, penukaran per penerbit, saldo penutup; tanggal lampau deterministik', async () => {
    const ctx = setup({ LOYALTY_POINT_EXPIRY_DAYS: '30' });
    await ctx.publish('EFN', {}, 80);
    await ctx.earn(ORDER_B, 300_000, 'GL'); // 5 Okt
    ctx.clock.advanceSeconds(10 * DAY); // 15 Okt
    await ctx.earn(ORDER_C, 400_000, 'PB');
    await ctx.redeem(ORDER_A, 50, 'PB');

    const gl05 = await ctx.accrualAfterClose('GL', '2026-10-05');
    expect(gl05).toMatchObject({ company_code: 'GL', date: '2026-10-05', earned_value_idr: 30 * 80, closing_liability_idr: 30 * 80, point_value_idr: 80 });
    const pb15 = await ctx.accrualAfterClose('PB', '2026-10-15');
    expect(pb15).toMatchObject({
      earned_value_idr: 40 * 80,
      earn_reversed_value_idr: 0,
      redeemed_value_idr_by_issuer: { GL: 30 * 80, PB: 20 * 80 },
      restored_value_idr: 0,
      expired_value_idr: 0,
      closing_liability_idr: 20 * 80,
      redeemed_value_idr_as_issuer: 20 * 80,
      // L-66: lot PB hanya ditukar di PB sendiri → map kosong (kunci PT sendiri tidak pernah ada)
      redeemed_value_idr_as_issuer_by_redeemer: {},
      restored_value_idr_as_issuer_by_redeemer: {},
      forfeited_value_idr: 0,
    });
    const gl15 = await ctx.accrualAfterClose('GL', '2026-10-15');
    expect(gl15).toMatchObject({
      earned_value_idr: 0,
      redeemed_value_idr_by_issuer: {},
      redeemed_value_idr_as_issuer: 30 * 80,
      // L-66: 30 poin lot GL ditukar di PB; Σ map + bagian GL sendiri (0) = as_issuer
      redeemed_value_idr_as_issuer_by_redeemer: { PB: 30 * 80 },
      closing_liability_idr: 0,
    });
    for (const v of Object.values(pb15)) if (typeof v === 'number') expect(Number.isSafeInteger(v)).toBe(true);
    expect(JSON.stringify(pb15)).not.toMatch(/[0-9a-f]{64}|6281/); // tanpa member_hash / nomor HP

    // Aktivitas sesudah tanggal itu (restore 16 Okt, kebijakan nilai tukar baru) tidak mengubah 15 Okt
    await ctx.earn(ORDER_A, 0, 'PB');
    await ctx.reverse(REFUND_1, ORDER_A);
    await ctx.publish('EFN', {}, 90);
    const again = await ctx.accruals.accrual('PB', '2026-10-15');
    expect({ ...again, generated_at: null }).toEqual({ ...pb15, generated_at: null });
    const pb16 = await ctx.accrualAfterClose('PB', '2026-10-16');
    expect(pb16).toMatchObject({
      restored_value_idr: 50 * 90,
      restored_value_idr_by_issuer: { GL: 30 * 90, PB: 20 * 90 },
      restored_value_idr_as_issuer: 20 * 90,
      restored_value_idr_as_issuer_by_redeemer: {},
    });
    const gl16 = await ctx.accrualAfterClose('GL', '2026-10-16');
    expect(gl16).toMatchObject({
      restored_value_idr: 0,
      restored_value_idr_as_issuer: 30 * 90,
      restored_value_idr_as_issuer_by_redeemer: { PB: 30 * 90 },
      redeemed_value_idr_as_issuer_by_redeemer: {},
      closing_liability_idr: 30 * 90,
    });
    // L-66 konsistensi untuk setiap respons: Σ map + bagian PT sendiri = total sebagai penerbit
    for (const [company, a] of [['GL', gl15], ['PB', pb15], ['PB', pb16], ['GL', gl16]] as const) {
      const sum = (m: Record<string, number>) => Object.values(m).reduce((s, n) => s + n, 0);
      expect(sum(a.redeemed_value_idr_as_issuer_by_redeemer) + (a.redeemed_value_idr_by_issuer[company] ?? 0)).toBe(a.redeemed_value_idr_as_issuer);
      expect(sum(a.restored_value_idr_as_issuer_by_redeemer) + (a.restored_value_idr_by_issuer[company] ?? 0)).toBe(a.restored_value_idr_as_issuer);
      expect(Object.keys(a.redeemed_value_idr_as_issuer_by_redeemer)).not.toContain(company);
      expect(Object.keys(a.restored_value_idr_as_issuer_by_redeemer)).not.toContain(company);
    }
  });

  it('kedaluwarsa = sisa lot penerbit pada tanggal hangus; clawback ke lot yang sudah hangus tidak menggeser akrual', async () => {
    const ctx = setup({ LOYALTY_POINT_EXPIRY_DAYS: '30' });
    await ctx.publish('EFN', {}, 80);
    await ctx.earn(ORDER_D, 100_000, 'GL', OTHER); // +10, hangus 5 Nov 00:00 WIB
    const nov05 = await ctx.accrualAfterClose('GL', '2026-11-05');
    expect(nov05).toMatchObject({ expired_value_idr: 10 * 80, closing_liability_idr: 0 });
    // lot hangus tepat 00:00 WIB 5 Nov masih bagian saldo penutup 4 Nov (identitas penutup ↔ expired)
    expect(await ctx.accrualAfterClose('GL', '2026-11-04')).toMatchObject({ closing_liability_idr: 10 * 80, expired_value_idr: 0 });
    ctx.clock.advanceSeconds(5 * DAY); // 10 Nov 18:00Z = 11 Nov 01:00 WIB
    expect((await ctx.reverse(REFUND_2, ORDER_D, OTHER)).points_clawed_back).toBe(10);
    const nov11 = await ctx.accrualAfterClose('GL', '2026-11-11');
    expect(nov11).toMatchObject({ earn_reversed_value_idr: 0, closing_liability_idr: 0 });
    expect((await ctx.accruals.accrual('GL', '2026-11-05')).expired_value_idr).toBe(10 * 80);
  });

  it('validasi: tanggal belum ditutup / masa depan → 422 date_not_closed; format/PT salah → 400', async () => {
    const ctx = setup();
    expect(await code(ctx.accruals.accrual('GL', '2026-10-05'))).toBe('422 date_not_closed'); // hari ini (WIB)
    expect(await code(ctx.accruals.accrual('GL', '2026-12-01'))).toBe('422 date_not_closed');
    ctx.clock.ms = wibDayRange('2026-10-05').end.getTime() + 60_000; // 1 menit setelah tengah malam < jeda 300 s
    expect(await code(ctx.accruals.accrual('GL', '2026-10-05'))).toBe('422 date_not_closed');
    for (const [c, d] of [['GL', '2026-02-30'], ['GL', '05-10-2026'], ['XX', '2026-10-01'], ['GL', '2025-12-31'], [undefined, '2026-10-01'], ['GL', ['2026-10-01']]] as const) {
      expect(await code(ctx.accruals.accrual(c, d))).toBe('400 invalid_request');
    }
    expect(wibDayRange('2026-10-05')).toEqual({ start: new Date('2026-10-04T17:00:00.000Z'), end: new Date('2026-10-05T17:00:00.000Z') });
  });

  it('klasifikasi arus: restore/earn/redeem/clawback/forfeit menurut PT transaksi vs penerbit', () => {
    const rows = [
      { issuer: 'GL', company_code: 'GL', reason: 'pos_sale', credit: true, flow_points: 10 },
      { issuer: 'GL', company_code: 'PB', reason: 'pos_redeem', credit: false, flow_points: -4 },
      { issuer: 'PB', company_code: 'GL', reason: 'pos_redeem', credit: false, flow_points: -3 },
      { issuer: 'GL', company_code: 'GL', reason: 'pos_refund_clawback', credit: 'false', flow_points: '-2' },
      { issuer: 'GL', company_code: 'GL', reason: 'dsar_forfeit', credit: 'f', flow_points: -1 },
      { issuer: 'PB', company_code: 'GL', reason: 'pos_refund_restore', credit: 't', flow_points: 3 },
    ];
    const out = classifyFlows('GL', rows);
    expect(out).toMatchObject({ earned: 10, earnReversed: 2, forfeited: 1, restored: 3, redeemedAsIssuer: 4, restoredAsIssuer: 0 });
    expect(Object.fromEntries(out.redeemedByIssuer)).toEqual({ PB: 3 });
    expect(Object.fromEntries(out.restoredByIssuer)).toEqual({ PB: 3 });
    // L-66: lot GL ditukar di PB → { PB: 4 }; lot GL dipulihkan di PT lain tidak ada → {}
    expect(Object.fromEntries(out.redeemedAsIssuerByRedeemer)).toEqual({ PB: 4 });
    expect(Object.fromEntries(out.restoredAsIssuerByRedeemer)).toEqual({});
    const own = classifyFlows('GL', [
      { issuer: 'GL', company_code: 'GL', reason: 'pos_redeem', credit: false, flow_points: -6 },
      { issuer: 'GL', company_code: 'BCR', reason: 'pos_redeem', credit: false, flow_points: -2 },
      { issuer: 'GL', company_code: 'BCR', reason: 'pos_refund_restore', credit: true, flow_points: 1 },
    ]);
    expect(own.redeemedAsIssuer).toBe(8);
    expect(Object.fromEntries(own.redeemedAsIssuerByRedeemer)).toEqual({ BCR: 2 }); // 2 + 6 (GL sendiri) = 8
    expect(Object.fromEntries(own.restoredAsIssuerByRedeemer)).toEqual({ BCR: 1 });
  });
});
