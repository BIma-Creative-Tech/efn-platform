import { randomBytes } from 'node:crypto';
import { FakeClock, testCipher, testConfig } from '../../common/testing/fakes';
import { ApiError } from '../../common/errors/api-error';
import { FakeLoyaltyDb } from '../../common/testing/fake-loyalty-db';
import { OutboxService } from '../../common/outbox/outbox.service';
import { PII_AAD } from '../../common/pii/crypto';
import { memberHash } from '../../common/pii/pii';
import { Db, Queryable, QueryResultLike } from '../../common/db/db';
import { PermanentMessageError } from '../../common/amqp/amqp.service';
import { LoyaltyService } from '../loyalty.service';
import {
  MgmPolicyParams,
  addMonths,
  mgmPolicyFingerprint,
  mgmPolicyViolations,
  mgmRewardPoints,
  normalizeReferralCode,
  parseMgmPolicy,
  referralCodeFrom,
  wibMonthRange,
} from './mgm-policy';
import { MgmEngineService, parseMgmRewardMessage } from './mgm-engine.service';
import { MgmPolicyService } from './mgm-policy.service';
import { MgmReferralService } from './mgm-referral.service';
import { parseClaimBody } from './mgm.controller';
import { parsePosEarn, parsePosReverse } from '../../pos/dto/pos-loyalty.dto';
import { eventFingerprint, parseOdooEvent } from '../../orders/order-events.service';
import { parseFnbEvent } from '../../fnb/fnb-pos.service';
import { MemoryMgmStore } from './testing/mgm-fakes';

/** G31 docs/04 §20 — engine MGM: kebijakan, referral, akrual, settle, cap, clawback, privasi. */

const PARAMS: MgmPolicyParams = {
  level1_pct_bp: 200,
  level2_pct_bp: 50,
  flat_points_level3: 5,
  max_depth: 3,
  cap_points_per_referrer_month: 1000,
  min_order_idr: 50_000,
  settle_hold_days: 8,
  relation_months: 12,
};
const DAY = 86_400_000;

/** ApiError dari fungsi sinkron yang melempar ATAU dari nilai error hasil `.catch((e) => e)`. */
function apiErr(x: unknown): ApiError {
  let err = x;
  if (typeof x === 'function') {
    try {
      (x as () => unknown)();
      throw new Error('diharapkan ApiError');
    } catch (e) {
      err = e;
    }
  }
  if (!(err instanceof ApiError)) throw err instanceof Error ? err : new Error(`bukan ApiError: ${String(err)}`);
  return err;
}

function body(over: Record<string, unknown> = {}, params: Record<string, unknown> = {}): Buffer {
  return Buffer.from(
    JSON.stringify({
      schema_version: 1,
      company_code: 'PB',
      version: 1,
      effective_from: '2026-10-01T00:00:00Z',
      approved_by_uid: 7,
      params: { ...PARAMS, ...params },
      ...over,
    }),
  );
}

/** FakeLoyaltyDb + `processed_message`, `outbox`, `platform.member` (cukup untuk engine). */
class MgmDb implements Db {
  readonly inner = new FakeLoyaltyDb();
  readonly claims = new Set<string>();
  readonly outbox: { queue: string; payload: Record<string, unknown>; messageId: string }[] = [];
  /** member.id → { hash, enc(AAD member) } */
  readonly members = new Map<string, { hash: string; enc: Buffer }>();

  async query<R>(text: string, params: readonly unknown[] = []): Promise<QueryResultLike<R>> {
    return this.handle(text, params, this.inner) as Promise<QueryResultLike<R>>;
  }
  async tx<T>(fn: (q: Queryable) => Promise<T>): Promise<T> {
    const claims = new Set(this.claims);
    const outLen = this.outbox.length;
    try {
      return await this.inner.tx((q) => fn({ query: <R>(t: string, p: readonly unknown[] = []) => this.handle(t, p, q) as Promise<QueryResultLike<R>> }));
    } catch (err) {
      this.claims.clear();
      for (const c of claims) this.claims.add(c);
      this.outbox.length = outLen;
      throw err;
    }
  }
  private async handle(text: string, p: readonly unknown[], q: Queryable): Promise<QueryResultLike<Record<string, unknown>>> {
    if (text.includes('INSERT INTO platform.processed_message')) {
      const k = `${String(p[0])}|${String(p[1])}`;
      if (this.claims.has(k)) return { rows: [], rowCount: 0 };
      this.claims.add(k);
      return { rows: [{ consumer: p[0] }], rowCount: 1 };
    }
    if (text.includes('INSERT INTO platform.outbox')) {
      this.outbox.push({ queue: String(p[0]), payload: JSON.parse(String(p[1])), messageId: String(p[2]) });
      return { rows: [], rowCount: 1 };
    }
    if (text.includes('FROM platform.member WHERE id = $1 AND deleted_at IS NULL')) {
      const m = this.members.get(String(p[0]));
      return m ? { rows: [{ member_hash: m.hash, member_ref_enc: m.enc }], rowCount: 1 } : { rows: [], rowCount: 0 };
    }
    return q.query(text, p);
  }
}

function setup(over: Record<string, string> = {}) {
  const clock = new FakeClock(Date.UTC(2026, 9, 10, 3, 0, 0));
  const config = testConfig({ MGM_ENABLED: 'true', ...over });
  const cipher = testCipher(config);
  const db = new MgmDb();
  db.inner.now = clock.now;
  const store = new MemoryMgmStore();
  const loyalty = new LoyaltyService(config, db, cipher);
  loyalty.now = clock.now;
  const policies = new MgmPolicyService(config, db, store);
  policies.now = clock.now;
  const outbox = new OutboxService(db, { isConnected: () => false } as never, config);
  const engine = new MgmEngineService(config, db, store, policies, loyalty, outbox);
  engine.now = clock.now;
  const referral = new MgmReferralService(config, db, store);
  referral.now = clock.now;
  let n = 0;
  /** Member aktif baru (akun dibuat `createdAt`). */
  const member = (createdAt = new Date(clock.ms)): { id: string; hash: string } => {
    n += 1;
    const ref = `62812000000${String(n).padStart(2, '0')}`;
    const hash = memberHash(config.memberHashPepper, ref);
    const id = `00000000-0000-4000-8000-${String(n).padStart(12, '0')}`;
    db.members.set(id, { hash, enc: cipher.encrypt(ref, PII_AAD.MEMBER_REF) });
    store.activeMembers.set(hash, id);
    store.memberCreated.set(id, createdAt);
    return { id, hash };
  };
  const user = (m: { id: string; hash: string }) => ({ memberId: m.id, memberHash: m.hash });
  const publish = async (params: Partial<MgmPolicyParams> = {}, company = 'PB', version = 1): Promise<void> => {
    await policies.publish(parseMgmPolicy(body({ company_code: company, version }, params), company, String(version)));
  };
  /** referrer ← member: member mengklaim kode referrer. */
  const refer = async (referrer: { id: string; hash: string }, child: { id: string; hash: string }): Promise<void> => {
    const v = await referral.view(user(referrer));
    await referral.claim(user(child), v.code);
  };
  const deliver = async (): Promise<string[]> => {
    const out: string[] = [];
    for (const m of db.outbox.splice(0)) out.push(await engine.handle(m.payload, { userId: 'efn-bff' }));
    return out;
  };
  const sale = async (buyer: { hash: string }, ref: string, basis: number, company = 'PB'): Promise<boolean> =>
    db.tx((q) => engine.recordSource(q, { kind: 'pos', ref, company: company as never, buyerHash: buyer.hash, basisIdr: basis, occurredAt: new Date(clock.ms) }));
  return { clock, config, db, store, loyalty, policies, engine, referral, member, user, publish, refer, deliver, sale };
}

describe('MGM kebijakan (§20.1)', () => {
  it('parse ketat: field asing, versi/PT path beda, schema lain → 400', () => {
    expect(parseMgmPolicy(body(), 'PB', '1').params).toEqual(PARAMS);
    for (const [b, c, v] of [
      [body({ extra: 1 }), 'PB', '1'],
      [body({}, { unknown: 1 }), 'PB', '1'],
      [body({ version: 2 }), 'PB', '1'],
      [body(), 'GL', '1'],
      [body({ schema_version: 2 }), 'PB', '1'],
      [body({}, { level1_pct_bp: 1.5 }), 'PB', '1'],
      [Buffer.from('not json'), 'PB', '1'],
    ] as [Buffer, string, string][]) {
      expect(apiErr(() => parseMgmPolicy(b, c, v)).getStatus()).toBe(400);
    }
    const missing: Record<string, unknown> = { ...PARAMS };
    delete missing.level2_pct_bp;
    expect(apiErr(() => parseMgmPolicy(body({ params: missing }), 'PB', '1')).getStatus()).toBe(400);
  });

  it('plafon: kedalaman 2..5 wajib, L2 ≤ L1, cap ≥ 1, plafon env', () => {
    const c = testConfig().mgmPolicyCeilings;
    expect(mgmPolicyViolations(PARAMS, c)).toEqual([]);
    expect(mgmPolicyViolations({ ...PARAMS, max_depth: 1 }, c)).toEqual(['max_depth']);
    expect(mgmPolicyViolations({ ...PARAMS, max_depth: 6 }, c)).toEqual(['max_depth']);
    expect(mgmPolicyViolations({ ...PARAMS, level2_pct_bp: 300 }, c)).toEqual(['level2_pct_bp']);
    expect(mgmPolicyViolations({ ...PARAMS, level1_pct_bp: 1001, level2_pct_bp: 0 }, c)).toEqual(['level1_pct_bp']);
    expect(mgmPolicyViolations({ ...PARAMS, cap_points_per_referrer_month: 0 }, c)).toEqual(['cap_points_per_referrer_month']);
    expect(mgmPolicyViolations({ ...PARAMS, settle_hold_days: 0, relation_months: 37 }, c)).toEqual(['settle_hold_days', 'relation_months']);
  });

  it('publish: stored → duplicate (isi sama) → 409 (isi beda) → 422 di luar plafon; aktif = GREATEST(effective, received)', async () => {
    const ctx = setup();
    const p = parseMgmPolicy(body(), 'PB', '1');
    expect((await ctx.policies.publish(p)).status).toBe('stored');
    expect((await ctx.policies.publish(p)).status).toBe('duplicate');
    expect(apiErr(await ctx.policies.publish(parseMgmPolicy(body({}, { level1_pct_bp: 300 }), 'PB', '1')).catch((e) => e)).getStatus()).toBe(409);
    const oob = await ctx.policies.publish(parseMgmPolicy(body({ version: 2 }, { max_depth: 6 }), 'PB', '2')).catch((e) => e);
    expect(apiErr(oob).body).toMatchObject({ error: { code: 'policy_out_of_bounds' } });
    // berlaku sejak diterima BFF (effective_from lampau tidak berlaku surut)
    expect(await ctx.policies.activeAt('PB', new Date(ctx.clock.ms - 1000))).toBeNull();
    expect((await ctx.policies.activeAt('PB', new Date(ctx.clock.ms)))?.version).toBe(1);
    expect(await ctx.policies.activeAt('GL', new Date(ctx.clock.ms))).toBeNull(); // PT tanpa kebijakan = tidak ada reward
    expect(mgmPolicyFingerprint(p)).toMatch(/^[0-9a-f]{64}$/);
  });
});

describe('MGM rumus (§20.3)', () => {
  it('L1/L2 persen floor, L3..max flat, di luar kedalaman / di bawah minimal = 0', () => {
    expect([1, 2, 3, 4].map((l) => mgmRewardPoints(PARAMS, l, 1_000_000, 100))).toEqual([200, 50, 5, 0]);
    expect(mgmRewardPoints(PARAMS, 1, 49_999, 100)).toBe(0);
    expect(mgmRewardPoints(PARAMS, 1, 50_050, 100)).toBe(10);
    expect(mgmRewardPoints(PARAMS, 1, 9_007_199_254_740_000, 100)).toBe(Number((9_007_199_254_740_000n * 200n) / 1_000_000n));
  });
  it('bulan kalender & bulan WIB', () => {
    expect(addMonths(new Date('2026-01-31T05:00:00Z'), 1).toISOString()).toBe('2026-02-28T05:00:00.000Z');
    const m = wibMonthRange(new Date('2026-10-31T18:00:00Z')); // 1 Nov 01:00 WIB
    expect([m.start.toISOString(), m.end.toISOString()]).toEqual(['2026-10-31T17:00:00.000Z', '2026-11-30T17:00:00.000Z']);
  });
  it('kode referral: alfabet tanpa karakter ambigu, normalisasi input', () => {
    const code = referralCodeFrom((n) => randomBytes(n));
    expect(code).toMatch(/^[ABCDEFGHJKMNPQRSTUVWXYZ23456789]{8}$/);
    expect(normalizeReferralCode(' abcd-2345 ')).toBe('ABCD2345');
    expect(normalizeReferralCode('ABCD1234')).toBeNull(); // 1 tidak ada di alfabet
    expect(normalizeReferralCode(42)).toBeNull();
  });
});

describe('MGM referral (§20.2)', () => {
  it('kill switch: MGM_ENABLED=false → 503 mgm_unavailable', async () => {
    const ctx = setup({ MGM_ENABLED: 'false' });
    const a = ctx.member();
    expect(apiErr(await ctx.referral.view(ctx.user(a)).catch((e) => e)).body).toMatchObject({ error: { code: 'mgm_unavailable' } });
  });

  it('kode dibuat sekali; klaim menautkan; tidak bisa ganti; diri sendiri / siklus / kode tak dikenal ditolak', async () => {
    const ctx = setup();
    const [a, b, c] = [ctx.member(), ctx.member(), ctx.member()];
    const va = await ctx.referral.view(ctx.user(a));
    expect(va).toMatchObject({ referred: false, network: { level1: 0, level2: 0, level3_plus: 0 }, rewards: { pending_points: 0, settled_points_month: 0 } });
    expect((await ctx.referral.view(ctx.user(a))).code).toBe(va.code);
    const code = (e: unknown): string => (apiErr(e).body as { error: { code: string } }).error.code;
    expect(code(await ctx.referral.claim(ctx.user(a), va.code).catch((e) => e))).toBe('referral_self');
    await ctx.referral.claim(ctx.user(b), va.code.toLowerCase());
    expect(code(await ctx.referral.claim(ctx.user(b), va.code).catch((e) => e))).toBe('referral_already_linked');
    const vb = await ctx.referral.view(ctx.user(b));
    await ctx.referral.claim(ctx.user(c), vb.code);
    // a → b → c; a memakai kode c = siklus
    expect(code(await ctx.referral.claim(ctx.user(a), (await ctx.referral.view(ctx.user(c))).code).catch((e) => e))).toBe('referral_cycle');
    expect(code(await ctx.referral.claim(ctx.user(ctx.member()), 'ZZZZZZZZ').catch((e) => e))).toBe('referral_code_not_found');
    expect(apiErr(await ctx.referral.claim(ctx.user(a), 'x').catch((e) => e)).getStatus()).toBe(400);
    expect((await ctx.referral.view(ctx.user(a))).network).toEqual({ level1: 1, level2: 1, level3_plus: 0 });
    expect(ctx.store.graphLocks).toBeGreaterThan(0);
    // respons tidak memuat identitas (hash/nomor) siapa pun
    expect(JSON.stringify(await ctx.referral.view(ctx.user(b)))).not.toMatch(/[0-9a-f]{64}|628/);
  });

  it('jendela klaim: lewat MGM_CLAIM_WINDOW_DAYS sejak akun dibuat → 422; pemilik kode dihapus → 404', async () => {
    const ctx = setup({ MGM_CLAIM_WINDOW_DAYS: '7' });
    const a = ctx.member();
    const old = ctx.member(new Date(ctx.clock.ms - 8 * DAY));
    const va = await ctx.referral.view(ctx.user(a));
    expect(await ctx.referral.view(ctx.user(old))).not.toHaveProperty('claim_until');
    expect(apiErr(await ctx.referral.claim(ctx.user(old), va.code).catch((e) => e)).getStatus()).toBe(422);
    ctx.store.activeMembers.delete(a.hash);
    expect(apiErr(await ctx.referral.claim(ctx.user(ctx.member()), va.code).catch((e) => e)).getStatus()).toBe(404);
  });

  it('body klaim: hanya {code}, ≤ 1 KB', () => {
    const req = { rawBody: Buffer.from('{}'), headers: {} } as never;
    expect(parseClaimBody({ code: 'ABCD2345' }, req)).toBe('ABCD2345');
    expect(apiErr(() => parseClaimBody({ code: 'A', x: 1 }, req)).getStatus()).toBe(400);
    expect(apiErr(() => parseClaimBody({ code: 'A' }, { rawBody: Buffer.alloc(2000), headers: {} } as never)).getStatus()).toBe(413);
  });
});

describe('MGM akrual → settle → clawback (§20.3–§20.5)', () => {
  async function chain() {
    const ctx = setup();
    await ctx.publish();
    const [l3, l2, l1, buyer] = [ctx.member(), ctx.member(), ctx.member(), ctx.member()];
    await ctx.refer(l3, l2);
    await ctx.refer(l2, l1);
    await ctx.refer(l1, buyer);
    ctx.clock.ms += 60_000;
    return { ctx, l1, l2, l3, buyer };
  }
  const bal = (ctx: ReturnType<typeof setup>, h: string): number => ctx.db.inner.balance(h, new Date(ctx.clock.ms));

  it('pembeli tanpa referrer / MGM mati / basis 0 → tidak ada sumber; pesan tanpa data member', async () => {
    const { ctx, buyer } = await chain();
    expect(await ctx.sale(ctx.member(), 'o-none', 1_000_000)).toBe(false);
    expect(await ctx.sale(buyer, 'o-zero', 0)).toBe(false);
    expect(await ctx.sale(buyer, 'o-1', 1_000_000)).toBe(true);
    expect(await ctx.sale(buyer, 'o-1', 1_000_000)).toBe(false); // idempoten
    expect(ctx.db.outbox).toEqual([
      { queue: 'mgm.reward.requested', messageId: 'mgm:pos:o-1', payload: { schema_version: 1, idempotency_key: 'mgm:pos:o-1', source_kind: 'pos', source_ref: 'o-1' } },
    ]);
    const off = setup({ MGM_ENABLED: 'false' });
    expect(await off.sale(off.member(), 'x', 1_000_000)).toBe(false);
  });

  it('rantai 3 level: 200/50/5 poin pending; settle setelah H+8 ke ledger (source mgm); idempoten', async () => {
    const { ctx, l1, l2, l3, buyer } = await chain();
    await ctx.sale(buyer, 'o-1', 1_000_000);
    expect(await ctx.deliver()).toEqual(['applied']);
    const rows = [...ctx.store.rewards.values()].sort((a, b) => a.level - b.level);
    expect(rows.map((r) => [r.level, r.referrer_hash === [l1, l2, l3][r.level - 1].hash, r.points, r.status])).toEqual([
      [1, true, 200, 'pending'],
      [2, true, 50, 'pending'],
      [3, true, 5, 'pending'],
    ]);
    expect((await ctx.referral.view(ctx.user(l1))).rewards.pending_points).toBe(200);
    // replay pesan → duplicate (processed_message)
    expect(await ctx.engine.handle({ schema_version: 1, idempotency_key: 'mgm:pos:o-1', source_kind: 'pos', source_ref: 'o-1' }, { userId: 'efn-bff' })).toBe('duplicate');
    ctx.clock.ms += 7 * DAY;
    expect((await ctx.engine.runOnce()).settled).toBe(0);
    ctx.clock.ms += 2 * DAY;
    expect(await ctx.engine.runOnce()).toEqual({ skipped: false, reversals: 0, settled: 3, capped: 0, cancelled: 0 });
    expect([bal(ctx, l1.hash), bal(ctx, l2.hash), bal(ctx, l3.hash), bal(ctx, buyer.hash)]).toEqual([200, 50, 5, 0]);
    const ledger = ctx.db.inner.ledger.filter((r) => r.source === 'mgm');
    expect(ledger.every((r) => r.reason === 'mgm_reward' && r.company_code === 'PB' && r.issuer_company_code === 'PB')).toBe(true);
    expect((await ctx.engine.runOnce()).settled).toBe(0); // tidak dobel
  });

  it('cap bulanan per referrer per PT: sisa di-settle sebagian, berikutnya capped', async () => {
    const ctx = setup();
    await ctx.publish({ cap_points_per_referrer_month: 300 });
    const [ref, buyer] = [ctx.member(), ctx.member()];
    await ctx.refer(ref, buyer);
    ctx.clock.ms += 1000;
    for (const o of ['a', 'b']) await ctx.sale(buyer, o, 1_000_000); // 200 + 200
    await ctx.deliver();
    ctx.clock.ms += 9 * DAY;
    expect(await ctx.engine.runOnce()).toMatchObject({ settled: 2, capped: 0 });
    expect(ctx.db.inner.balance(ref.hash, new Date(ctx.clock.ms))).toBe(300);
    await ctx.sale(buyer, 'c', 1_000_000); // bulan berikutnya? tidak: masih Oktober WIB
    await ctx.deliver();
    ctx.clock.ms += 9 * DAY;
    const r = await ctx.engine.runOnce();
    expect(r.capped + r.settled).toBe(1);
  });

  it('retur sebelum settle mengurangi/membatalkan pending; retur setelah settle = clawback ledger; tiap retur sekali', async () => {
    const { ctx, l1, l2, buyer } = await chain();
    await ctx.sale(buyer, 'o-1', 1_000_000);
    await ctx.sale(buyer, 'o-2', 1_000_000);
    await ctx.deliver();
    // o-1 diretur 50 % sebelum settle → L1 200 → 100
    await ctx.db.tx((q) => ctx.engine.recordReversal(q, { kind: 'pos', ref: 'o-1', reverseKey: 'pos_reverse:r1', ratio: 0.5 }));
    expect(await ctx.db.tx((q) => ctx.engine.recordReversal(q, { kind: 'pos', ref: 'o-1', reverseKey: 'pos_reverse:r1', ratio: 0.5 }))).toBe(false);
    expect(await ctx.db.tx((q) => ctx.engine.recordReversal(q, { kind: 'pos', ref: 'unknown', reverseKey: 'k', ratio: 1 }))).toBe(false);
    ctx.clock.ms += 9 * DAY;
    const r = await ctx.engine.runOnce();
    expect(r.reversals).toBe(3);
    expect(bal(ctx, l1.hash)).toBe(100 + 200);
    // o-2 diretur penuh SETELAH settle → clawback 200 dari L1, 50 dari L2
    await ctx.db.tx((q) => ctx.engine.recordReversal(q, { kind: 'pos', ref: 'o-2', reverseKey: 'pos_reverse:r2', ratio: 1 }));
    await ctx.engine.runOnce();
    expect([bal(ctx, l1.hash), bal(ctx, l2.hash)]).toEqual([100, 25]);
    expect(ctx.db.inner.ledger.filter((x) => x.reason === 'mgm_reverse').reduce((s, x) => s + x.delta_points, 0)).toBe(-255);
    await ctx.engine.runOnce(); // tidak diterapkan dua kali
    expect(bal(ctx, l1.hash)).toBe(100);
    const o2 = [...ctx.store.rewards.values()].find((w) => w.source_ref === 'o-2' && w.level === 1);
    expect(o2).toMatchObject({ status: 'settled', settled_points: 200, reversed_points: 200, clawed_points: 200 });
  });

  it('retur penuh sebelum settle → cancelled; reward tidak pernah settle', async () => {
    const { ctx, l1, buyer } = await chain();
    await ctx.sale(buyer, 'o-1', 1_000_000);
    await ctx.deliver();
    await ctx.db.tx((q) => ctx.engine.recordReversal(q, { kind: 'pos', ref: 'o-1', reverseKey: 'k', ratio: 1 }));
    ctx.clock.ms += 9 * DAY;
    await ctx.engine.runOnce();
    expect([...ctx.store.rewards.values()].every((w) => w.status === 'cancelled' && w.status_reason === 'returned')).toBe(true);
    expect(bal(ctx, l1.hash)).toBe(0);
  });

  it('masa relasi habis / tautan setelah transaksi / referrer dihapus memutus rantai', async () => {
    const { ctx, l1, l2, buyer } = await chain();
    ctx.store.activeMembers.delete(l2.hash); // L2 dihapus → rantai berhenti di L1
    await ctx.sale(buyer, 'o-1', 1_000_000);
    await ctx.deliver();
    expect([...ctx.store.rewards.values()].map((w) => w.level)).toEqual([1]);
    ctx.clock.ms += 13 * 31 * DAY; // > 12 bulan sejak tautan
    await ctx.sale(buyer, 'o-late', 1_000_000);
    expect(await ctx.deliver()).toEqual(['no_reward']);
    // referrer L1 dihapus sebelum settle → cancelled
    ctx.store.activeMembers.delete(l1.hash);
    await ctx.engine.runOnce();
    expect([...ctx.store.rewards.values()].find((w) => w.source_ref === 'o-1')).toMatchObject({ status: 'cancelled', status_reason: 'referrer_inactive' });
  });

  it('di bawah min_order_idr / PT tanpa kebijakan → no_reward', async () => {
    const { ctx, buyer } = await chain();
    await ctx.sale(buyer, 'small', 49_999);
    await ctx.sale(buyer, 'gl', 1_000_000, 'GL');
    expect(await ctx.deliver()).toEqual(['no_reward', 'no_reward']);
  });

  it('consumer: publisher selain efn-bff / payload rusak / sumber hilang → DLQ', async () => {
    const { ctx } = await chain();
    const ok = { schema_version: 1, idempotency_key: 'mgm:pos:x', source_kind: 'pos', source_ref: 'x' };
    await expect(ctx.engine.handle(ok, { userId: 'efn-ocr' })).rejects.toBeInstanceOf(PermanentMessageError);
    await expect(ctx.engine.handle(ok, { userId: 'efn-bff' })).rejects.toBeInstanceOf(PermanentMessageError);
    for (const bad of [{ ...ok, extra: 1 }, { ...ok, idempotency_key: 'mgm:pos:y' }, { ...ok, source_kind: 'qr' }, { ...ok, member_hash: 'a'.repeat(64) }]) {
      expect(() => parseMgmRewardMessage(bad)).toThrow(PermanentMessageError);
    }
  });

  it('job mati bila MGM_ENABLED=false; reversal tetap dicatat walau MGM dimatikan setelah akrual', async () => {
    const { ctx, buyer } = await chain();
    await ctx.sale(buyer, 'o-1', 1_000_000);
    await ctx.deliver();
    const off = new MgmEngineService(testConfig({ MGM_ENABLED: 'false' }), ctx.db, ctx.store, ctx.policies, ctx.loyalty, {} as never);
    expect((await off.runOnce()).skipped).toBe(true);
    expect(await ctx.db.tx((q) => off.recordReversal(q, { kind: 'pos', ref: 'o-1', reverseKey: 'k', ratio: 0.25 }))).toBe(true);
  });

  it('erasure: kode & relasi subjek hilang, reward pending subjek dibatalkan, hash diganti tombstone', async () => {
    const { ctx, l1, buyer } = await chain();
    await ctx.sale(buyer, 'o-1', 1_000_000);
    await ctx.deliver();
    await ctx.store.erase(ctx.db, l1.hash, 'f'.repeat(64));
    expect(ctx.store.members.has(l1.hash)).toBe(false);
    expect(ctx.store.members.get(buyer.hash)).toMatchObject({ referrer_hash: null });
    const w = [...ctx.store.rewards.values()].find((x) => x.level === 1);
    expect(w).toMatchObject({ status: 'cancelled', status_reason: 'referrer_erased', referrer_hash: 'f'.repeat(64) });
    // pembeli tanpa upline (tautan tetap ada) → tidak bisa klaim ulang, tidak ada sumber baru
    expect(await ctx.sale(buyer, 'o-2', 1_000_000)).toBe(false);
  });
});

describe('G31b dasar MGM per baris (§20.10)', () => {
  const ORDER = '3f2b8c1e-7d4a-4e0b-9c55-2a1f0e6d9b10';
  const REFUND = '4a3c9d2f-8e5b-4f1c-8d66-3b2a1f0e7c21';
  const earn = { idempotency_key: `pos_earn:${ORDER}`, order_ref: ORDER, member_ref: '6281234567890', company_code: 'PB', eligible_amount: 130000 };
  const rev = {
    idempotency_key: `pos_reverse:${REFUND}`,
    original_order_ref: ORDER,
    refund_order_ref: REFUND,
    member_ref: '6281234567890',
    refunded_eligible_amount: 30000,
    refund_ratio: 0.230769,
    points_restored_value: 0,
  };

  it('POS: field opsional (DTO lama tidak berubah), ≤ dasar earn, bukan angka → 400', async () => {
    expect(parsePosEarn(earn)).not.toHaveProperty('mgmEligibleAmount');
    expect(parsePosEarn({ ...earn, mgm_eligible_amount: 100000 }).mgmEligibleAmount).toBe(100000);
    expect(parsePosEarn({ ...earn, mgm_eligible_amount: 0 }).mgmEligibleAmount).toBe(0);
    expect(parsePosReverse({ ...rev, mgm_refunded_eligible_amount: 0 }).mgmRefundedEligibleAmount).toBe(0);
    expect(parsePosReverse(rev)).not.toHaveProperty('mgmRefundedEligibleAmount');
    for (const bad of [{ ...earn, mgm_eligible_amount: 130001 }, { ...earn, mgm_eligible_amount: '1' }, { ...earn, mgm_eligible_amount: -1 }]) {
      expect(apiErr(() => parsePosEarn(bad)).getStatus()).toBe(400);
    }
    expect(apiErr(() => parsePosReverse({ ...rev, mgm_refunded_eligible_amount: 30001 })).getStatus()).toBe(400);
  });

  it('online: loyalty.mgm_eligible_amount & refund.mgm_eligible_amount_refunded; sidik event lama tidak berubah', () => {
    const REF = '3f2b8c1e-5d4a-4e7b-9c3d-0a1b2c3d4e5f';
    const base = {
      schema_version: 1, idempotency_key: `online:${REF}:picked_up:5`, event_id: '11111111-2222-4333-8444-555555555555', seq: 5,
      platform_order_ref: REF, checkout_ref: '7a6b5c4d-3e2f-4a1b-8c9d-0e1f2a3b4c5d', channel_code: 'GL', company_code: 'GL',
      event: 'picked_up', state: 'picked_up', occurred_at: '2026-10-30T03:00:00Z', refund: null,
    };
    const buf = (o: Record<string, unknown>): Buffer => Buffer.from(JSON.stringify({ ...base, ...o }));
    const plain = parseOdooEvent(buf({ loyalty: { eligible_amount: 20998 } }));
    expect(plain).not.toHaveProperty('loyalty_mgm_eligible_amount');
    const withMgm = parseOdooEvent(buf({ loyalty: { eligible_amount: 20998, mgm_eligible_amount: 1000 } }));
    expect(withMgm.loyalty_mgm_eligible_amount).toBe(1000);
    expect(eventFingerprint(withMgm)).not.toBe(eventFingerprint(plain));
    expect(() => parseOdooEvent(buf({ loyalty: { eligible_amount: 100, mgm_eligible_amount: 101 } }))).toThrow();
    const refund = { refund_ref: 'R-1', amount: 1000, kind: 'lines', initiator: 'odoo', eligible_amount_refunded: 500 };
    const rf = parseOdooEvent(buf({ event: 'refunded', state: 'refunded', idempotency_key: `online:${REF}:refunded:6`, seq: 6, refund: { ...refund, mgm_eligible_amount_refunded: 0 } }));
    expect(rf.refund?.mgm_eligible_amount_refunded).toBe(0);
    const rfPlain = parseOdooEvent(buf({ event: 'refunded', state: 'refunded', idempotency_key: `online:${REF}:refunded:6`, seq: 6, refund }));
    expect(rfPlain.refund).not.toHaveProperty('mgm_eligible_amount_refunded');
    expect(eventFingerprint(rf)).not.toBe(eventFingerprint(rfPlain));
    expect(() => parseOdooEvent(buf({ event: 'refunded', state: 'refunded', idempotency_key: `online:${REF}:refunded:6`, seq: 6, refund: { ...refund, mgm_eligible_amount_refunded: 501 } }))).toThrow();
  });

  it('QR close: orders[].mgm_eligible_amount opsional, ≤ eligible_amount', () => {
    const SID = '5b4d0e3a-9f6c-4a2d-9e77-4c3b2a1f0d32';
    const ev = (order: Record<string, unknown>): Buffer =>
      Buffer.from(JSON.stringify({
        schema_version: 1, idempotency_key: `fnb:${SID}:session_closed:3`, seq: 3, table_session_id: SID, company_code: 'PB', config_id: 1,
        event: 'session_closed', occurred_at: '2026-10-30T03:00:00Z', close: { reason: 'paid', orders: [order] },
      }));
    const o = { order_uuid: ORDER, reason: 'paid', eligible_amount: 100000, pos_member_attached: false, company_code: 'PB' };
    expect(parseFnbEvent(ev(o)).close?.orders[0].mgm_eligible_amount).toBeNull();
    expect(parseFnbEvent(ev({ ...o, mgm_eligible_amount: 40000 })).close?.orders[0].mgm_eligible_amount).toBe(40000);
    expect(() => parseFnbEvent(ev({ ...o, mgm_eligible_amount: 100001 }))).toThrow();
  });

  it('engine: basis = dasar MGM; retur dengan dasar MGM → rasio terhadap basis; 0 → tanpa reversal', async () => {
    const ctx = setup();
    await ctx.publish();
    const [ref, buyer] = [ctx.member(), ctx.member()];
    await ctx.refer(ref, buyer);
    ctx.clock.ms += 1000;
    // dasar earn 1,3 jt, dasar MGM 1 jt → L1 200 poin (bukan 260)
    await ctx.db.tx((q) => ctx.engine.recordSource(q, { kind: 'pos', ref: 'o-m', company: 'PB', buyerHash: buyer.hash, basisIdr: 1_000_000, occurredAt: new Date(ctx.clock.ms) }));
    await ctx.deliver();
    expect([...ctx.store.rewards.values()][0]).toMatchObject({ level: 1, points: 200 });
    expect(await ctx.db.tx((q) => ctx.engine.recordReversal(q, { kind: 'pos', ref: 'o-m', reverseKey: 'k0', ratio: 0.23, refundedBasisIdr: 0 }))).toBe(false);
    expect(await ctx.db.tx((q) => ctx.engine.recordReversal(q, { kind: 'pos', ref: 'o-m', reverseKey: 'k1', ratio: 0.38, refundedBasisIdr: 500_000 }))).toBe(true);
    expect(ctx.store.reversals.map((v) => [v.reverse_key, v.ratio])).toEqual([['k1', 0.5]]);
    expect(await ctx.db.tx((q) => ctx.engine.recordReversal(q, { kind: 'pos', ref: 'o-m', reverseKey: 'k2', ratio: 0.1, refundedBasisIdr: 9_000_000 }))).toBe(true);
    expect(ctx.store.reversals[1].ratio).toBe(1);
    ctx.clock.ms += 9 * DAY;
    await ctx.engine.runOnce();
    expect([...ctx.store.rewards.values()][0]).toMatchObject({ status: 'cancelled', reversed_points: 200 });
  });
});
