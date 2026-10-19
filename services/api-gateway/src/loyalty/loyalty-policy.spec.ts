import { Logger } from '@nestjs/common';
import { GUARDS_METADATA } from '@nestjs/common/constants';
import { AppConfig } from '../config/app-config';
import { MetricsService } from '../common/metrics/metrics.service';
import { ServiceKeyGuard } from '../common/security/service-key.guard';
import { computeWebhookSignature } from '../common/security/webhook-signature';
import { FakeLoyaltyDb } from '../common/testing/fake-loyalty-db';
import { FakeClock, catchApiError, httpContext, randomSecret, testConfig } from '../common/testing/fakes';
import { LoyaltyPolicySignatureGuard } from '../pos/loyalty-policy.guard';
import { PosLoyaltyFinanceController } from '../pos/pos-loyalty-finance.controller';
import { parseLoyaltyPolicy, policyFingerprint, policyViolations } from './loyalty-policy';
import { LoyaltyPolicyService } from './loyalty-policy.service';

/** G21 docs/04 §13.1 no. 3–4: publikasi kebijakan poin Odoo → BFF, plafon env, kebijakan aktif/fallback. */

const PARAMS = {
  earn_idr_per_point: 10_000,
  redeem_min_points: 50,
  redeem_max_pct: 40,
  max_points_per_order: 3_000,
  daily_max_points_member: 1_500,
  pos_config_hourly_max: 8_000,
  company_hourly_max: 40_000,
  earn_on_money_only: true,
};

function body(over: Record<string, unknown> = {}, params: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    schema_version: 1,
    company_code: 'GL',
    version: 3,
    effective_from: '2026-10-01T00:00:00Z',
    approved_by_uid: 17,
    params: { ...PARAMS, ...params },
    ...over,
  };
}
const raw = (o: unknown): Buffer => Buffer.from(JSON.stringify(o), 'utf8');

function setup(env: Record<string, string> = {}) {
  const clock = new FakeClock();
  const config = testConfig(env);
  const db = new FakeLoyaltyDb();
  db.now = clock.now;
  const metrics = new MetricsService();
  const svc = new LoyaltyPolicyService(config, db, metrics);
  svc.now = clock.now;
  const publish = (o: Record<string, unknown>, company = String(o.company_code), version = String(o.version)) =>
    svc.publish(parseLoyaltyPolicy(raw(o), company, version));
  return { clock, config, db, metrics, svc, publish };
}

async function code(p: Promise<unknown> | (() => unknown)): Promise<string> {
  const err = await catchApiError(typeof p === 'function' ? p : () => p);
  return `${err.getStatus()} ${(err.body as { error: { code: string } }).error.code}`;
}

describe('parseLoyaltyPolicy — skema ketat (§13.1 no. 3)', () => {
  it('body valid → input terketik; format Datetime Odoo (UTC tanpa zona) diterima', () => {
    const p = parseLoyaltyPolicy(raw(body()), 'GL', '3');
    expect(p).toMatchObject({ companyCode: 'GL', version: 3, approvedByUid: 17, pointValueIdr: null, params: PARAMS });
    expect(p.effectiveFrom.toISOString()).toBe('2026-10-01T00:00:00.000Z');
    expect(parseLoyaltyPolicy(raw(body({ effective_from: '2026-10-01 07:30:00' })), 'GL', '3').effectiveFrom.toISOString()).toBe(
      '2026-10-01T07:30:00.000Z',
    );
    expect(parseLoyaltyPolicy(raw(body({ company_code: 'EFN', point_value_idr: 100 })), 'EFN', '3').pointValueIdr).toBe(100);
  });

  it('body persis buatan Odoo `_efn_publish_body` + `signing.canonical_json` (sort_keys, tanpa spasi) untuk EFN dan PT', () => {
    const sortKeys = (v: unknown): unknown =>
      v && typeof v === 'object' && !Array.isArray(v)
        ? Object.fromEntries(Object.keys(v as object).sort().map((k) => [k, sortKeys((v as Record<string, unknown>)[k])]))
        : v;
    const efn = Buffer.from(JSON.stringify(sortKeys(body({ company_code: 'EFN', version: 1, point_value_idr: 100, effective_from: '2026-10-05T03:00:00Z' }))));
    expect(efn.toString()).toMatch(/^\{"approved_by_uid":17,"company_code":"EFN","effective_from":"2026-10-05T03:00:00Z","params":\{"company_hourly_max"/);
    expect(parseLoyaltyPolicy(efn, 'EFN', '1')).toMatchObject({ pointValueIdr: 100, params: PARAMS });
    expect(parseLoyaltyPolicy(Buffer.from(JSON.stringify(sortKeys(body()))), 'GL', '3').pointValueIdr).toBeNull();
  });

  it.each([
    ['field tambahan tingkat atas', body({ extra: 1 })],
    ['field tambahan params', body({}, { bonus: 1 })],
    ['param hilang', { ...body(), params: { ...PARAMS, company_hourly_max: undefined } }],
    ['desimal', body({}, { earn_idr_per_point: 10_000.5 })],
    ['negatif', body({}, { daily_max_points_member: -1 })],
    ['string angka', body({}, { redeem_max_pct: '40' })],
    ['pct > 100', body({}, { redeem_max_pct: 101 })],
    ['earn_on_money_only bukan boolean', body({}, { earn_on_money_only: 1 })],
    ['schema_version lain', body({ schema_version: 2 })],
    ['point_value_idr untuk PT non-EFN (L-62)', body({ point_value_idr: 100 })],
    ['effective_from tanpa zona ISO', body({ effective_from: '2026-10-01T00:00:00' })],
    ['approved_by_uid 0', body({ approved_by_uid: 0 })],
  ])('%s → 400', async (_label, b) => {
    expect(await code(() => parseLoyaltyPolicy(raw(b), 'GL', '3'))).toBe('400 invalid_request');
  });

  it('path ≠ body (company/version), path tidak valid, body bukan JSON / terlalu besar → 400', async () => {
    expect(await code(() => parseLoyaltyPolicy(raw(body()), 'PB', '3'))).toBe('400 invalid_request');
    expect(await code(() => parseLoyaltyPolicy(raw(body()), 'GL', '4'))).toBe('400 invalid_request');
    expect(await code(() => parseLoyaltyPolicy(raw(body()), 'XX', '3'))).toBe('400 invalid_request');
    expect(await code(() => parseLoyaltyPolicy(raw(body()), 'GL', '03'))).toBe('400 invalid_request');
    expect(await code(() => parseLoyaltyPolicy(Buffer.from('{'), 'GL', '3'))).toBe('400 invalid_request');
    expect(await code(() => parseLoyaltyPolicy(Buffer.alloc(9000, 0x20), 'GL', '3'))).toBe('400 invalid_request');
    expect(await code(() => parseLoyaltyPolicy(undefined, 'GL', '3'))).toBe('400 invalid_request');
  });

  it('sidik kanonik: urutan key/spasi tidak relevan; isi beda → sidik beda', () => {
    const a = parseLoyaltyPolicy(raw(body()), 'GL', '3');
    const reordered = Buffer.from(`{"params":${JSON.stringify(PARAMS)},"version":3, "company_code":"GL","schema_version":1,"approved_by_uid":17,"effective_from":"2026-10-01 00:00:00"}`);
    expect(policyFingerprint(parseLoyaltyPolicy(reordered, 'GL', '3'))).toBe(policyFingerprint(a));
    expect(policyFingerprint(parseLoyaltyPolicy(raw(body({}, { redeem_max_pct: 41 })), 'GL', '3'))).not.toBe(policyFingerprint(a));
  });
});

describe('plafon LOYALTY_POLICY_CEILING_* (§13.1 no. 4)', () => {
  it('default = env lama; arah plafon per field; 0 = tanpa batas ditolak bila plafon > 0', () => {
    const c = testConfig().loyaltyPolicyCeilings;
    expect(c).toEqual({
      earnIdrPerPointMin: 10_000,
      pointValueIdrMax: 100,
      redeemMinPointsMin: 50,
      redeemMaxPctMax: 50,
      maxPointsPerOrderMax: 5_000,
      dailyMaxPointsMemberMax: 2_000,
      posConfigHourlyMax: 10_000,
      companyHourlyMax: 50_000,
      earnOnMoneyOnlyRequired: true,
    });
    expect(policyViolations(PARAMS, null, c)).toEqual([]);
    const bad = { ...PARAMS, earn_idr_per_point: 9_999, redeem_min_points: 10, redeem_max_pct: 60, max_points_per_order: 5_001, daily_max_points_member: 0, pos_config_hourly_max: 10_001, company_hourly_max: 0, earn_on_money_only: false };
    expect(policyViolations(bad, 101, c)).toEqual([
      'earn_idr_per_point',
      'redeem_min_points',
      'redeem_max_pct',
      'max_points_per_order',
      'daily_max_points_member',
      'pos_config_hourly_max',
      'company_hourly_max',
      'earn_on_money_only',
      'point_value_idr',
    ]);
    // plafon 0 (env lama 0 = mati) → nilai apa pun diterima
    const open = testConfig({ LOYALTY_EARN_DAILY_MAX_POINTS: '0' }).loyaltyPolicyCeilings;
    expect(policyViolations({ ...PARAMS, daily_max_points_member: 0 }, null, open)).toEqual([]);
  });

  it('env fallback di luar plafon eksplisit → gagal start (salah konfigurasi)', () => {
    expect(() => testConfig({ LOYALTY_POLICY_CEILING_REDEEM_MAX_PCT: '30' }).assertStartupSecrets()).toThrow(/LOYALTY_REDEEM_MAX_PCT_OF_ORDER/);
    expect(() => testConfig({ LOYALTY_POLICY_CEILING_REDEEM_MAX_PCT: '80' }).assertStartupSecrets()).not.toThrow();
  });

  it('secret loyalty_policy_signing_key: wajib di luar APP_ENV=dev, harus ≠ odoo_bff_api_key, literal ditolak', () => {
    expect(() => testConfig({ LOYALTY_POLICY_SIGNING_KEY: '' }).assertStartupSecrets()).toThrow(/LOYALTY_POLICY_SIGNING_KEY/);
    const same = randomSecret();
    expect(() => testConfig({ LOYALTY_POLICY_SIGNING_KEY: same, ODOO_BFF_API_KEY: same }).loyaltyPolicySigningKey).toThrow(/berbeda/);
    expect(testConfig().loyaltyPolicySigningKey.length).toBeGreaterThanOrEqual(32);
    // = signing.py MIN_KEY_BYTES (32): kunci 31 byte ditolak
    expect(() => testConfig({ LOYALTY_POLICY_SIGNING_KEY: 'k'.repeat(31) }).loyaltyPolicySigningKey).toThrow(/32 byte/);
    // SEC-29: env literal hanya APP_ENV=dev; jalur resmi = LOYALTY_POLICY_SIGNING_KEY_FILE (/run/secrets/loyalty_policy_signing_key)
    const v = randomSecret();
    expect(() => new AppConfig({ LOYALTY_POLICY_SIGNING_KEY: v }).loyaltyPolicySigningKey).toThrow(/ditolak di luar APP_ENV=dev/);
    const viaFile = new AppConfig({ LOYALTY_POLICY_SIGNING_KEY_FILE: '/run/secrets/loyalty_policy_signing_key' }, (p) => {
      if (p === '/run/secrets/loyalty_policy_signing_key') return Buffer.from(`${v}\n`);
      throw new Error('ENOENT');
    });
    expect(viaFile.loyaltyPolicySigningKey).toBe(v);
  });
});

describe('LoyaltyPolicyService.publish — append-only, 409, 422, idempoten', () => {
  it('versi baru tersimpan; retry isi sama → duplicate (200); isi beda → 409 policy_conflict + anomali critical', async () => {
    const ctx = setup();
    expect(await ctx.publish(body())).toEqual({ ok: true, company_code: 'GL', version: 3, status: 'stored' });
    expect(await ctx.publish(body())).toMatchObject({ status: 'duplicate' });
    const warn = jest.spyOn(Logger.prototype, 'warn').mockImplementation(() => undefined);
    try {
      expect(await code(ctx.publish(body({}, { redeem_max_pct: 41 })))).toBe('409 policy_conflict');
    } finally {
      warn.mockRestore();
    }
    expect(ctx.db.policies).toHaveLength(1);
    expect(ctx.db.policies[0]).toMatchObject({ company_code: 'GL', version: 3, approved_by_uid: 17, point_value_idr: null });
    expect(await ctx.metrics.render()).toContain('efn_loyalty_anomaly_total{kind="policy_conflict",severity="critical"} 1');
  });

  it('di luar plafon → 422 policy_out_of_bounds, tidak tersimpan; retry versi lama yang sudah diterima tetap 200 walau plafon turun', async () => {
    const ctx = setup();
    const warn = jest.spyOn(Logger.prototype, 'warn').mockImplementation(() => undefined);
    try {
      expect(await code(ctx.publish(body({ version: 4 }, { max_points_per_order: 9_999 })))).toBe('422 policy_out_of_bounds');
      expect(await code(ctx.publish(body({ company_code: 'EFN', version: 1, point_value_idr: 150 })))).toBe('422 policy_out_of_bounds');
    } finally {
      warn.mockRestore();
    }
    expect(ctx.db.policies).toHaveLength(0);
    await ctx.publish(body());
    const lowered = new LoyaltyPolicyService(testConfig({ LOYALTY_POLICY_CEILING_MAX_POINTS_PER_ORDER: '5000', LOYALTY_POS_MAX_EARN_POINTS: '1000' }), ctx.db);
    expect((await lowered.publish(parseLoyaltyPolicy(raw(body()), 'GL', '3'))).status).toBe('duplicate');
  });

  it('tabel append-only: tidak ada UPDATE/DELETE dari service; fake DB menolaknya seperti trigger', async () => {
    const ctx = setup();
    await ctx.publish(body());
    await ctx.publish(body());
    const issued = [...ctx.db.queries];
    expect(issued.some((q) => /UPDATE platform\.loyalty_policy|DELETE FROM platform\.loyalty_policy/.test(q))).toBe(false);
    await expect(ctx.db.query('UPDATE platform.loyalty_policy SET version = 9')).rejects.toThrow(/append-only/);
    await expect(ctx.db.query('DELETE FROM platform.loyalty_policy')).rejects.toThrow(/append-only/);
  });
});

describe('LoyaltyPolicyService.effective — kebijakan aktif per PT, fallback env', () => {
  it('versi tertinggi yang berlaku; effective_from masa depan belum berlaku; kebijakan tidak berlaku sebelum diterima (backdate)', async () => {
    const ctx = setup({ LOYALTY_POLICY_CACHE_SECONDS: '0' });
    await ctx.publish(body({ version: 1, effective_from: '2026-01-01T00:00:00Z' }, { redeem_max_pct: 20 }));
    await ctx.publish(body({ version: 2, effective_from: '2026-10-06T00:00:00Z' }, { redeem_max_pct: 30 })); // besok
    let e = await ctx.svc.effective('GL');
    expect(e).toMatchObject({ source: 'policy', policyVersion: 1, redeemMaxPct: 20 });
    ctx.clock.advanceSeconds(86_400);
    e = await ctx.svc.effective('GL');
    expect(e).toMatchObject({ policyVersion: 2, redeemMaxPct: 30 });
    // backdate: berlaku sejak diterima, bukan sejak effective_from
    const at = new Date(ctx.clock.now() - 3_600_000);
    await ctx.publish(body({ version: 3, effective_from: '2026-01-01T00:00:00Z' }, { redeem_max_pct: 35 }));
    expect((await ctx.svc.activeAt('GL', at))?.version).toBe(2);
    expect((await ctx.svc.effective('GL')).policyVersion).toBe(3);
  });

  it('PT tanpa kebijakan → env + warning (dibatasi); point_value_idr dari kebijakan global EFN (L-62) untuk semua PT', async () => {
    const ctx = setup({ LOYALTY_POLICY_CACHE_SECONDS: '0', LOYALTY_POLICY_CEILING_POINT_VALUE_IDR: '200' });
    const warn = jest.spyOn(Logger.prototype, 'warn').mockImplementation(() => undefined);
    try {
      expect(await ctx.svc.effective('PB')).toMatchObject({ source: 'env', policyVersion: null, earnIdrPerPoint: 10_000, pointValueIdr: 100 });
      await ctx.svc.effective('PB');
      expect(warn.mock.calls.filter((c) => String(c[0]).includes('Kebijakan poin PB belum ada'))).toHaveLength(1);
    } finally {
      warn.mockRestore();
    }
    await ctx.publish(body({ company_code: 'EFN', version: 1, point_value_idr: 150 }));
    expect(await ctx.svc.effective('PB')).toMatchObject({ source: 'env', pointValueIdr: 150, pointValueVersion: 1 });
    await ctx.publish(body());
    expect(await ctx.svc.effective('GL')).toMatchObject({ source: 'policy', pointValueIdr: 150, maxPointsPerOrder: 3_000 });
  });

  it('cache LOYALTY_POLICY_CACHE_SECONDS; publish membersihkan cache lokal', async () => {
    const ctx = setup({ LOYALTY_POLICY_CACHE_SECONDS: '30' });
    await ctx.svc.effective('GL');
    const n = ctx.db.queries.length;
    await ctx.svc.effective('GL');
    expect(ctx.db.queries.length).toBe(n);
    await ctx.publish(body());
    expect((await ctx.svc.effective('GL')).policyVersion).toBe(3);
  });

  it('plafon diturunkan setelah kebijakan disimpan → kebijakan tidak dipakai (fallback env + anomali)', async () => {
    const ctx = setup({ LOYALTY_POLICY_CACHE_SECONDS: '0' });
    await ctx.publish(body({}, { max_points_per_order: 4_000 }));
    const strict = new LoyaltyPolicyService(
      testConfig({ LOYALTY_POLICY_CEILING_MAX_POINTS_PER_ORDER: '3500', LOYALTY_POS_MAX_EARN_POINTS: '3500', LOYALTY_POLICY_CACHE_SECONDS: '0' }),
      ctx.db,
      ctx.metrics,
    );
    strict.now = ctx.clock.now;
    const warn = jest.spyOn(Logger.prototype, 'warn').mockImplementation(() => undefined);
    try {
      expect(await strict.effective('GL')).toMatchObject({ source: 'env', maxPointsPerOrder: 3_500 });
    } finally {
      warn.mockRestore();
    }
  });
});

describe('LoyaltyPolicySignatureGuard — X-EFN-Signature (§13.1 no. 3)', () => {
  const B = raw(body());
  function run(config = testConfig(), header?: string, rawBody: Buffer | undefined = B, nowMs = Date.UTC(2026, 9, 5, 3, 0, 0)) {
    const guard = new LoyaltyPolicySignatureGuard(config);
    guard.now = () => nowMs;
    return guard.canActivate(httpContext({ headers: header === undefined ? {} : { 'x-efn-signature': header }, rawBody }));
  }
  const t = Math.floor(Date.UTC(2026, 9, 5, 3, 0, 0) / 1000);

  it('tanda tangan valid (t + body) → lolos; controller dilindungi ServiceKeyGuard lalu guard ini', () => {
    const config = testConfig();
    expect(run(config, `t=${t},v1=${computeWebhookSignature(config.loyaltyPolicySigningKey, t, B)}`)).toBe(true);
    expect(Reflect.getMetadata(GUARDS_METADATA, PosLoyaltyFinanceController)).toContain(ServiceKeyGuard);
    const method = Object.getOwnPropertyDescriptor(PosLoyaltyFinanceController.prototype, 'publish')?.value as object;
    expect(Reflect.getMetadata(GUARDS_METADATA, method)).toContain(LoyaltyPolicySignatureGuard);
  });

  it('HMAC salah / kunci lain / body diubah / tanpa header / replay > 300 s → 401 invalid_signature', async () => {
    const config = testConfig();
    const key = config.loyaltyPolicySigningKey;
    const sig = computeWebhookSignature(key, t, B);
    const warn = jest.spyOn(Logger.prototype, 'warn').mockImplementation(() => undefined);
    try {
      expect(await code(() => run(config, `t=${t},v1=${'0'.repeat(64)}`))).toBe('401 invalid_signature');
      expect(await code(() => run(config, `t=${t},v1=${computeWebhookSignature(randomSecret(), t, B)}`))).toBe('401 invalid_signature');
      expect(await code(() => run(config, `t=${t},v1=${sig}`, raw(body({ version: 4 }))))).toBe('401 invalid_signature');
      expect(await code(() => run(config, undefined))).toBe('401 invalid_signature');
      expect(await code(() => run(config, `t=${t},v1=${sig}`, B, (t + 301) * 1000))).toBe('401 invalid_signature');
      expect(run(config, `t=${t},v1=${sig}`, B, (t + 299) * 1000)).toBe(true);
    } finally {
      warn.mockRestore();
    }
  });

  it('kunci belum dikonfigurasi (dev) → 503, tanpa fallback tanpa tanda tangan', async () => {
    const err = jest.spyOn(Logger.prototype, 'error').mockImplementation(() => undefined);
    try {
      expect(await code(() => run(testConfig({ LOYALTY_POLICY_SIGNING_KEY: '' }), `t=${t},v1=${'0'.repeat(64)}`))).toBe('503 service_unavailable');
    } finally {
      err.mockRestore();
    }
  });
});
