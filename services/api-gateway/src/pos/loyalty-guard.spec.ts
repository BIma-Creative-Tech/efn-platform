import { Logger } from '@nestjs/common';
import { MetricsService } from '../common/metrics/metrics.service';
import { memberHash } from '../common/pii/pii';
import { RateLimiter } from '../common/rate-limit/rate-limiter';
import { FakeLoyaltyDb } from '../common/testing/fake-loyalty-db';
import { FakeClock, FakeDb, FakeKvStore, catchApiError, testCipher, testConfig } from '../common/testing/fakes';
import { LOYALTY_ANOMALY_KINDS, formatLoyaltyAnomaly } from '../loyalty/loyalty-anomaly';
import { LoyaltyService } from '../loyalty/loyalty.service';
import { DiscountTokenService } from './discount-token.service';
import { KNOWN_LEDGER_SOURCES, LOYALTY_RECON_LOCK_KEY, LoyaltyReconService } from './loyalty-recon.service';
import { PosLoyaltyService, startOfWibDay } from './pos-loyalty.service';

/**
 * SEC-61 (Gelombang 13c): pagar earn POS (plafon order, harian member, per PT & per kasir per jam), format baris
 * `EFN_LOYALTY_ANOMALY`, dan job rekonsiliasi.
 */

const MEMBER = '6281234567890';
const OTHER = '6285711112222';
let seq = 0;
const order = (): string => `0b7e4c2a-0000-4c3d-8e9f-${String(++seq).padStart(12, '0')}`;

function setup(overrides: Record<string, string> = {}) {
  const clock = new FakeClock(); // 2026-10-05 03:00 UTC = 10:00 WIB
  const config = testConfig({ LOYALTY_EARN_IDR_PER_POINT: '10000', ...overrides });
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
  const earn = (eligible: number, opts: { memberRef?: string; company?: 'FNB' | 'RTL'; posConfigId?: number | null; orderRef?: string } = {}) => {
    const orderRef = opts.orderRef ?? order();
    return svc.earn({
      idempotencyKey: `pos_earn:${orderRef}`,
      orderRef,
      memberRef: opts.memberRef ?? MEMBER,
      companyCode: opts.company ?? 'FNB',
      eligibleAmount: eligible,
      posConfigId: opts.posConfigId ?? null,
    });
  };
  const anomalies = async (): Promise<Record<string, number>> => {
    const m = await metrics.loyaltyAnomaly.get();
    return Object.fromEntries(m.values.map((v) => [String(v.labels.kind), v.value]));
  };
  return { clock, config, kv, metrics, db, svc, earn, anomalies, hash: memberHash(config.memberHashPepper, MEMBER) };
}

async function code(p: Promise<unknown>): Promise<string> {
  const err = await catchApiError(() => p);
  return `${err.getStatus()} ${(err.body as { error: { code: string } }).error.code}`;
}

function captureWarn() {
  const spy = jest.spyOn(Logger.prototype, 'warn').mockImplementation(() => undefined);
  const lines = (): string[] => spy.mock.calls.map((c) => String(c[0])).filter((l) => l.startsWith('EFN_LOYALTY_ANOMALY '));
  const payloads = (): Record<string, unknown>[] => lines().map((l) => JSON.parse(l.slice('EFN_LOYALTY_ANOMALY '.length)) as Record<string, unknown>);
  return { spy, lines, payloads };
}

describe('SEC-61 pagar earn POS', () => {
  afterEach(() => jest.restoreAllMocks());

  it('default konservatif: plafon order 5 000, harian 2 000, per kasir/jam 10 000, per PT/jam 50 000', () => {
    const c = testConfig();
    expect(c.loyaltyPosMaxEarnPoints).toBe(5_000);
    expect(c.loyaltyEarnDailyMaxPoints).toBe(2_000);
    expect(c.loyaltyEarnPosConfigHourlyMaxPoints).toBe(10_000);
    expect(c.loyaltyEarnCompanyHourlyMaxPoints).toBe(50_000);
    expect(() => testConfig({ LOYALTY_EARN_DAILY_MAX_POINTS: '-1' }).assertStartupSecrets()).toThrow(/LOYALTY_/);
  });

  it('plafon per order → 409 earn_limit_exceeded, anomali tanpa PII, TIDAK ada catatan earn / entri ledger', async () => {
    const ctx = setup({ LOYALTY_EARN_DAILY_MAX_POINTS: '0' });
    const w = captureWarn();
    const ref = order();
    expect(await code(ctx.earn(50_010_000, { orderRef: ref }))).toBe('409 earn_limit_exceeded'); // 5 001 poin
    expect(ctx.db.earns.size).toBe(0);
    expect(ctx.db.ledger).toHaveLength(0);
    expect(w.payloads()).toEqual([
      { kind: 'earn_limit_order', severity: 'warning', company: 'FNB', order_ref: ref, points: 5001, limit: 5000 },
    ]);
    for (const l of w.lines()) {
      expect(l).not.toContain(MEMBER.slice(2));
      expect(l).not.toContain(ctx.hash);
    }
    expect(await ctx.anomalies()).toEqual({ earn_limit_order: 1 });
    const ops = await ctx.metrics.loyaltyPosOps.get();
    expect(ops.values.find((v) => v.labels.outcome === 'earn_limit_exceeded')?.value).toBe(1);
    // tepat di plafon → diterima
    expect((await ctx.earn(50_000_000)).points_earned).toBe(5000);
  });

  it('harian per member (WIB): akumulasi melewati batas → 409; member lain tidak terpengaruh; hari WIB baru → reset', async () => {
    const ctx = setup({ LOYALTY_EARN_DAILY_MAX_POINTS: '100' });
    const w = captureWarn();
    expect((await ctx.earn(600_000)).points_earned).toBe(60);
    const ref40 = order();
    expect((await ctx.earn(400_000, { orderRef: ref40 })).points_earned).toBe(40); // tepat 100
    expect(await code(ctx.earn(10_000))).toBe('409 earn_limit_exceeded');
    expect(w.payloads().at(-1)).toMatchObject({ kind: 'earn_limit_member_day', points: 1, earned_today: 100, limit: 100 });
    // earn 0 poin tetap dicatat (dasar reverse K-6) walau batas tercapai
    expect((await ctx.earn(9_999)).points_earned).toBe(0);
    // replay earn yang dulu diterima tetap 200 (pagar setelah cek replay)
    expect((await ctx.earn(400_000, { orderRef: ref40 })).points_earned).toBe(40);
    expect((await ctx.earn(500_000, { memberRef: OTHER })).points_earned).toBe(50);
    // 10:00 WIB + 14 jam = 00:00 WIB hari berikutnya
    ctx.clock.advanceSeconds(14 * 3600);
    expect((await ctx.earn(10_000)).points_earned).toBe(1);
  });

  it('startOfWibDay: 23:59 WIB dan 00:00 WIB berada di hari berbeda', () => {
    expect(startOfWibDay(Date.UTC(2026, 9, 5, 16, 59)).toISOString()).toBe('2026-10-04T17:00:00.000Z');
    expect(startOfWibDay(Date.UTC(2026, 9, 5, 17, 0)).toISOString()).toBe('2026-10-05T17:00:00.000Z');
  });

  it('per PT per jam (critical): percobaan ditolak tidak memakan kuota; PT lain terpisah; jam berikutnya reset', async () => {
    const ctx = setup({ LOYALTY_EARN_DAILY_MAX_POINTS: '0', LOYALTY_EARN_COMPANY_HOURLY_MAX_POINTS: '100' });
    const w = captureWarn();
    expect((await ctx.earn(600_000)).points_earned).toBe(60);
    expect(await code(ctx.earn(600_000, { memberRef: OTHER }))).toBe('409 earn_limit_exceeded');
    expect(w.payloads().at(-1)).toMatchObject({ kind: 'earn_limit_company_hour', severity: 'critical', company: 'FNB', limit: 100 });
    expect((await ctx.earn(400_000, { memberRef: OTHER })).points_earned).toBe(40); // 60 + 40 = 100 (kompensasi bekerja)
    expect((await ctx.earn(600_000, { company: 'RTL' })).points_earned).toBe(60);
    ctx.clock.advanceSeconds(3600);
    expect((await ctx.earn(600_000, { memberRef: OTHER })).points_earned).toBe(60);
  });

  it('per pos_config per jam (bila dikirim): kasir lain tidak terpengaruh; kuota PT dikembalikan saat ditolak', async () => {
    const ctx = setup({
      LOYALTY_EARN_DAILY_MAX_POINTS: '0',
      LOYALTY_EARN_POS_CONFIG_HOURLY_MAX_POINTS: '50',
      LOYALTY_EARN_COMPANY_HOURLY_MAX_POINTS: '1000',
    });
    const w = captureWarn();
    expect((await ctx.earn(300_000, { posConfigId: 7 })).points_earned).toBe(30);
    expect(await code(ctx.earn(300_000, { posConfigId: 7, memberRef: OTHER }))).toBe('409 earn_limit_exceeded');
    expect(w.payloads().at(-1)).toMatchObject({ kind: 'earn_limit_pos_config_hour', pos_config_id: 7, limit: 50 });
    expect((await ctx.earn(300_000, { posConfigId: 8, memberRef: OTHER })).points_earned).toBe(30);
    expect((await ctx.earn(300_000, { memberRef: OTHER })).points_earned).toBe(30); // tanpa pos_config_id: hanya batas PT
    const coKey = ctx.kv.keys().find((k) => k.startsWith('rl:loy:earnpts:co:FNB:'));
    expect(coKey).toBeDefined();
    expect(ctx.kv.raw(coKey!)).toBe('90'); // 30 + 30 + 30 — percobaan yang ditolak dikompensasi
    expect(ctx.kv.keys().every((k) => !k.includes(MEMBER) && !k.includes(MEMBER.slice(2)))).toBe(true);
  });

  it('batas 0 = mati', async () => {
    const ctx = setup({
      LOYALTY_EARN_DAILY_MAX_POINTS: '0',
      LOYALTY_EARN_POS_CONFIG_HOURLY_MAX_POINTS: '0',
      LOYALTY_EARN_COMPANY_HOURLY_MAX_POINTS: '0',
    });
    for (let i = 0; i < 3; i += 1) expect((await ctx.earn(50_000_000, { posConfigId: 1 })).points_earned).toBe(5000);
    expect(ctx.kv.keys().filter((k) => k.includes('earnpts'))).toEqual([]);
  });
});

describe('EFN_LOYALTY_ANOMALY format', () => {
  it('satu baris JSON; kind & severity dari daftar tetap; nilai string disaring (tidak bisa menyuntik baris/field)', () => {
    const line = formatLoyaltyAnomaly('ledger_unknown_source', {
      ledger_source: 'admin"}\nEFN_AUDIT_SINK {"x":1}',
      points: 10,
      bad_KEY: 'x',
      company: 'RTL',
    });
    expect(line.split('\n')).toHaveLength(1);
    expect(line.startsWith('EFN_LOYALTY_ANOMALY {')).toBe(true);
    const payload = JSON.parse(line.slice('EFN_LOYALTY_ANOMALY '.length)) as Record<string, unknown>;
    expect(payload).toEqual({ kind: 'ledger_unknown_source', severity: 'critical', ledger_source: 'adminEFN_AUDIT_SINKx:1', points: 10, company: 'RTL' });
    expect(Object.values(LOYALTY_ANOMALY_KINDS).every((s) => s === 'warning' || s === 'critical')).toBe(true);
  });
});

describe('LoyaltyReconService (SEC-61 rekonsiliasi)', () => {
  type Rows = Record<string, unknown>[];
  function reconSetup(rows: { earns?: Rows; orphans?: Rows; days?: Rows; reverses?: Rows; unknown?: Rows; lock?: boolean; fail?: boolean } = {}) {
    const clock = new FakeClock();
    const config = testConfig({ LOYALTY_RECON_INTERVAL_MS: '0' });
    const metrics = new MetricsService();
    const db = new FakeDb((t) => {
      const r = (x: Rows | undefined) => ({ rows: x ?? [], rowCount: (x ?? []).length });
      if (t.includes('pg_try_advisory_xact_lock')) return r([{ ok: rows.lock ?? true }]);
      if (rows.fail && t.includes('platform.loyalty_pos_earn e')) throw new Error('relation does not exist');
      if (t.includes('LEFT JOIN LATERAL')) return r(rows.earns);
      if (t.includes('NOT EXISTS (SELECT 1 FROM platform.loyalty_pos_earn e')) return r(rows.orphans);
      if (t.includes('HAVING SUM(points_earned) >')) return r(rows.days);
      if (t.includes('WITH touched AS')) return r(rows.reverses);
      if (t.includes('NOT (source = ANY($2::text[]))')) return r(rows.unknown);
      return undefined;
    });
    const svc = new LoyaltyReconService(db, config, metrics);
    svc.now = clock.now;
    return { svc, db, metrics, config, clock };
  }

  afterEach(() => jest.restoreAllMocks());

  it('tidak ada temuan → heartbeat EFN_LOYALTY_RECON ok + gauge dead-man; transaksi READ ONLY sebelum query lain', async () => {
    const ctx = reconSetup();
    const log = jest.spyOn(Logger.prototype, 'log').mockImplementation(() => undefined);
    expect(await ctx.svc.runOnce()).toEqual({ skipped: false, anomalies: 0, byKind: {} });
    expect(ctx.db.queries[0].text).toBe('SET TRANSACTION READ ONLY');
    expect(ctx.db.queries[1].params).toEqual([LOYALTY_RECON_LOCK_KEY]);
    const hb = log.mock.calls.map((c) => String(c[0])).find((l) => l.startsWith('EFN_LOYALTY_RECON '));
    expect(JSON.parse(hb!.slice('EFN_LOYALTY_RECON '.length))).toEqual({ status: 'ok', anomalies: 0, lookback_hours: 26 });
    expect((await ctx.metrics.loyaltyReconLastSuccess.get()).values[0].value).toBe(Math.floor(ctx.clock.now() / 1000));
    // parameter: jendela 26 jam, tarif & plafon saat ini, daftar source penulis BFF
    const earnQ = ctx.db.find('LEFT JOIN LATERAL')[0];
    expect(earnQ.params).toEqual([new Date(ctx.clock.now() - 26 * 3_600_000).toISOString(), 10_000, 5_000, 200]);
    expect(ctx.db.find('NOT (source = ANY($2::text[]))')[0].params[1]).toEqual(KNOWN_LEDGER_SOURCES);
    expect(KNOWN_LEDGER_SOURCES).toEqual(expect.arrayContaining(['pos_earn', 'pos_redeem', 'pos_reverse', 'migration_opening', 'dsar', 'price_hunter']));
    // jendela harian dimulai 00:00 WIB
    expect(ctx.db.find('HAVING SUM(points_earned) >')[0].params[0]).toBe(startOfWibDay(ctx.clock.now() - 26 * 3_600_000).toISOString());
  });

  it('temuan diklasifikasi per jenis → baris EFN_LOYALTY_ANOMALY + counter; tanpa member_hash', async () => {
    const ctx = reconSetup({
      earns: [
        { order_ref: 'o-mismatch', company_code: 'FNB', eligible_amount: '100000', points_earned: 10, ledger_points: '25', rule_points: '10' },
        { order_ref: 'o-rule', company_code: 'RTL', eligible_amount: '50000', points_earned: 900, ledger_points: '900', rule_points: '5' },
        { order_ref: 'o-cap', company_code: 'RTL', eligible_amount: '90000000', points_earned: 9000, ledger_points: '9000', rule_points: '9000' },
      ],
      orphans: [{ order_ref: 'o-orphan', company_code: 'FNB', ledger_points: '500' }],
      days: [{ day: '2026-10-05', orders: '12', points: '2600', order_ref: 'o-big' }],
      reverses: [
        { order_ref: 'o-claw', company_code: 'FNB', earned: '10', redeemed: '0', clawed: '15', restored: '0' },
        { order_ref: 'o-restore', company_code: 'FNB', earned: '10', redeemed: '50', clawed: '10', restored: '80' },
      ],
      unknown: [{ source: 'adjustment', company_code: 'EFN', entries: '3', points: '30000' }],
    });
    const w = captureWarn();
    jest.spyOn(Logger.prototype, 'log').mockImplementation(() => undefined);
    const res = await ctx.svc.runOnce();
    expect(res.byKind).toEqual({
      earn_ledger_mismatch: 1,
      earn_above_rule: 1,
      earn_above_order_cap: 1,
      earn_ledger_orphan: 1,
      member_day_above_limit: 1,
      reverse_exceeds_earn: 1,
      restore_exceeds_redeem: 1,
      ledger_unknown_source: 1,
    });
    expect(res.anomalies).toBe(8);
    const p = w.payloads();
    expect(p.find((x) => x.kind === 'earn_ledger_mismatch')).toEqual({
      kind: 'earn_ledger_mismatch',
      severity: 'critical',
      company: 'FNB',
      order_ref: 'o-mismatch',
      points: 10,
      source: 'recon',
      ledger_points: 25,
    });
    expect(p.find((x) => x.kind === 'member_day_above_limit')).toMatchObject({ day: '2026-10-05', orders: 12, points: 2600, limit: 2000, order_ref: 'o-big' });
    expect(p.find((x) => x.kind === 'ledger_unknown_source')).toMatchObject({ ledger_source: 'adjustment', entries: 3, points: 30000, severity: 'critical' });
    expect(p.find((x) => x.kind === 'restore_exceeds_redeem')).toMatchObject({ redeemed: 50, restored: 80 });
    const counter = await ctx.metrics.loyaltyAnomaly.get();
    expect(counter.values.find((v) => v.labels.kind === 'earn_above_rule')).toMatchObject({ value: 1, labels: { severity: 'critical' } });
    for (const l of w.lines()) expect(l).not.toMatch(/[0-9a-f]{64}/);
  });

  it('replika lain memegang lock → skipped; error query → tidak melempar, counter error, tanpa heartbeat ok', async () => {
    const locked = reconSetup({ lock: false });
    expect((await locked.svc.runOnce()).skipped).toBe(true);
    expect(locked.db.find('LEFT JOIN LATERAL')).toHaveLength(0);
    const failing = reconSetup({ fail: true });
    const err = jest.spyOn(Logger.prototype, 'error').mockImplementation(() => undefined);
    expect((await failing.svc.runOnce()).skipped).toBe(true);
    expect(String(err.mock.calls[0][0])).toContain('EFN_LOYALTY_RECON {"status":"error"}');
    const runs = await failing.metrics.loyaltyReconRuns.get();
    expect(runs.values.map((v) => [v.labels.outcome, v.value])).toEqual([['error', 1]]);
  });
});
