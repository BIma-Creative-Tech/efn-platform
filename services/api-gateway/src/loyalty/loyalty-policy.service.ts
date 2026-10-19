import { Inject, Injectable, Logger, Optional } from '@nestjs/common';
import { AppConfig } from '../config/app-config';
import { DB, Db, Queryable } from '../common/db/db';
import { MetricsService } from '../common/metrics/metrics.service';
import { reportLoyaltyAnomaly } from './loyalty-anomaly';
import {
  GLOBAL_POLICY_COMPANY,
  LoyaltyPolicyInput,
  LoyaltyPolicyParams,
  PolicyErrors,
  parsePolicyParams,
  policyFingerprint,
  policyViolations,
} from './loyalty-policy';
import { CompanyCode } from './loyalty.service';

/** Parameter loyalty efektif satu PT (kebijakan aktif atau fallback env). */
export interface EffectiveLoyaltyParams {
  companyCode: CompanyCode;
  /** `policy` = kebijakan Odoo aktif; `env` = fallback (PT tanpa kebijakan / kebijakan tak terpakai). */
  source: 'policy' | 'env';
  policyVersion: number | null;
  /** Versi kebijakan global EFN sumber `pointValueIdr` (null = env `LOYALTY_POINT_VALUE_IDR`). */
  pointValueVersion: number | null;
  earnIdrPerPoint: number;
  pointValueIdr: number;
  redeemMinPoints: number;
  redeemMaxPct: number;
  maxPointsPerOrder: number;
  dailyMaxPointsMember: number;
  posConfigHourlyMax: number;
  companyHourlyMax: number;
  earnOnMoneyOnly: boolean;
}

export interface PublishResult {
  ok: true;
  company_code: CompanyCode;
  version: number;
  /** `stored` = versi baru tersimpan; `duplicate` = versi sama isi sama (retry outbox Odoo). */
  status: 'stored' | 'duplicate';
}

interface PolicyRow {
  company_code: string;
  version: number | string;
  effective_from: Date | string;
  received_at: Date | string;
  params: unknown;
  point_value_idr: number | string | null;
  body_sha256?: string;
}

interface ActivePolicy {
  version: number;
  params: LoyaltyPolicyParams;
}

const WARN_INTERVAL_MS = 10 * 60_000;

/** Parameter env lama (docs/04 §9.1) dalam bentuk `EffectiveLoyaltyParams`. */
export function envLoyaltyParams(c: AppConfig, company: CompanyCode): EffectiveLoyaltyParams {
  return {
    companyCode: company,
    source: 'env',
    policyVersion: null,
    pointValueVersion: null,
    earnIdrPerPoint: c.loyaltyEarnIdrPerPoint,
    pointValueIdr: c.loyaltyPointValueIdr,
    redeemMinPoints: c.loyaltyRedeemMinPoints,
    redeemMaxPct: c.loyaltyRedeemMaxPctOfOrder,
    maxPointsPerOrder: c.loyaltyPosMaxEarnPoints,
    dailyMaxPointsMember: c.loyaltyEarnDailyMaxPoints,
    posConfigHourlyMax: c.loyaltyEarnPosConfigHourlyMaxPoints,
    companyHourlyMax: c.loyaltyEarnCompanyHourlyMaxPoints,
    earnOnMoneyOnly: c.loyaltyEarnExcludePointPayment,
  };
}

/**
 * Kebijakan poin per PT (docs/04 §13.1, W-29/E-44, L-61/L-62).
 *
 * - **Simpan** (`publish`): tabel append-only `platform.loyalty_policy` (UNIQUE company_code+version, trigger tolak
 *   UPDATE/DELETE). Versi sama + sidik sama → `duplicate` (200); sidik beda → `409 policy_conflict`; di luar plafon env
 *   → `422 policy_out_of_bounds` + anomali. Pemeriksaan duplikat SEBELUM plafon: retry versi lama yang dulu diterima
 *   tetap 200 walau plafon diturunkan setelahnya.
 * - **Aktif**: versi TERTINGGI dengan `GREATEST(effective_from, received_at) ≤ t` — kebijakan tidak pernah berlaku
 *   sebelum BFF menerimanya (backdate `effective_from` tidak mengubah transaksi/akrual yang sudah lewat → akrual tanggal
 *   lampau deterministik).
 * - **Efektif** (`effective`): kebijakan aktif PT; tidak ada / di luar plafon saat ini → env lama (fallback) + log
 *   warning (dibatasi 1×/10 menit per PT). `point_value_idr` SELALU dari kebijakan global EFN aktif (L-62), fallback env.
 * - Cache per proses `LOYALTY_POLICY_CACHE_SECONDS` (default 30): kebijakan baru berlaku ≤ N detik di tiap replika.
 */
@Injectable()
export class LoyaltyPolicyService {
  private readonly logger = new Logger(LoyaltyPolicyService.name);
  private readonly cache = new Map<string, { at: number; value: ActivePolicy | null; pv: { value: number; version: number } | null }>();
  private readonly warnedAt = new Map<string, number>();
  now: () => number = () => Date.now();

  constructor(
    private readonly config: AppConfig,
    @Inject(DB) private readonly db: Db,
    @Optional() private readonly metrics: MetricsService | null = null,
  ) {}

  // ───────────── publikasi Odoo → BFF ─────────────
  async publish(input: LoyaltyPolicyInput): Promise<PublishResult> {
    const sha = policyFingerprint(input);
    const result = await this.db.tx(async (q) => {
      const prev = await this.storedSha(q, input.companyCode, input.version);
      if (prev !== null) return this.compare(prev, sha, input);
      const violations = policyViolations(input.params, input.pointValueIdr, this.config.loyaltyPolicyCeilings);
      if (violations.length > 0) {
        this.anomaly('policy_out_of_bounds', { company: input.companyCode, version: input.version, fields: violations.join('.') });
        throw PolicyErrors.outOfBounds();
      }
      const ins = await q.query<{ id: string }>(
        `INSERT INTO platform.loyalty_policy
           (company_code, version, effective_from, approved_by_uid, params, point_value_idr, body_sha256, received_at)
         VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, $8)
         ON CONFLICT (company_code, version) DO NOTHING
         RETURNING id`,
        [
          input.companyCode,
          input.version,
          input.effectiveFrom.toISOString(),
          input.approvedByUid,
          JSON.stringify(input.params),
          input.pointValueIdr,
          sha,
          new Date(this.now()).toISOString(),
        ],
      );
      if ((ins.rowCount ?? 0) > 0) return 'stored' as const;
      // balapan dua pengiriman paralel versi yang sama
      const raced = await this.storedSha(q, input.companyCode, input.version);
      if (raced === null) throw new Error('loyalty_policy: INSERT DO NOTHING tanpa baris');
      return this.compare(raced, sha, input);
    });
    this.cache.clear();
    this.logger.log(
      `EFN_LOYALTY_POLICY ${JSON.stringify({ company: input.companyCode, version: input.version, status: result, effective_from: input.effectiveFrom.toISOString() })}`,
    );
    return { ok: true, company_code: input.companyCode, version: input.version, status: result };
  }

  private compare(prev: string, sha: string, input: LoyaltyPolicyInput): 'duplicate' {
    if (prev === sha) return 'duplicate';
    this.anomaly('policy_conflict', { company: input.companyCode, version: input.version });
    throw PolicyErrors.conflict();
  }

  private async storedSha(q: Queryable, company: CompanyCode, version: number): Promise<string | null> {
    const res = await q.query<{ body_sha256: string }>(
      `SELECT body_sha256 FROM platform.loyalty_policy WHERE company_code = $1 AND version = $2`,
      [company, version],
    );
    return res.rows[0]?.body_sha256 ?? null;
  }

  // ───────────── kebijakan aktif ─────────────
  /** Kebijakan aktif `company` pada `at` (tanpa cache bila `at` bukan "sekarang"). Baris rusak → null + error log. */
  async activeAt(company: CompanyCode, at: Date, q: Queryable = this.db): Promise<ActivePolicy | null> {
    const res = await q.query<PolicyRow>(
      `SELECT company_code, version, effective_from, received_at, params, point_value_idr
         FROM platform.loyalty_policy
        WHERE company_code = $1 AND GREATEST(effective_from, received_at) <= $2
        ORDER BY version DESC
        LIMIT 1`,
      [company, at.toISOString()],
    );
    const row = res.rows[0];
    if (!row) return null;
    try {
      const params = parsePolicyParams(typeof row.params === 'string' ? JSON.parse(row.params) : row.params);
      return { version: Number(row.version), params };
    } catch {
      this.logger.error(`loyalty_policy ${company} v${String(row.version)}: params tersimpan tidak valid — fallback env`);
      return null;
    }
  }

  /** `point_value_idr` global EFN aktif pada `at` (L-62); null = belum ada kebijakan global bernilai tukar. */
  async pointValueAt(at: Date, q: Queryable = this.db): Promise<{ value: number; version: number } | null> {
    const res = await q.query<PolicyRow>(
      `SELECT company_code, version, effective_from, received_at, params, point_value_idr
         FROM platform.loyalty_policy
        WHERE company_code = $1 AND point_value_idr IS NOT NULL AND GREATEST(effective_from, received_at) <= $2
        ORDER BY version DESC
        LIMIT 1`,
      [GLOBAL_POLICY_COMPANY, at.toISOString()],
    );
    const row = res.rows[0];
    if (!row || row.point_value_idr === null) return null;
    const value = Number(row.point_value_idr);
    if (!Number.isSafeInteger(value) || value < 1) return null;
    if (value > this.config.loyaltyPolicyCeilings.pointValueIdrMax) {
      this.warnOnce(`pv:${row.version}`, () =>
        this.anomaly('policy_out_of_bounds', { company: GLOBAL_POLICY_COMPANY, version: Number(row.version), fields: 'point_value_idr', stage: 'use' }),
      );
      return null;
    }
    return { value, version: Number(row.version) };
  }

  /** Nilai tukar yang dipakai pada `at` (kebijakan global, fallback env). */
  async pointValueIdrAt(at: Date, q: Queryable = this.db): Promise<{ value: number; version: number | null }> {
    const pv = await this.pointValueAt(at, q);
    return pv ?? { value: this.config.loyaltyPointValueIdr, version: null };
  }

  /** Parameter efektif PT SEKARANG (cache `LOYALTY_POLICY_CACHE_SECONDS`). */
  async effective(company: CompanyCode): Promise<EffectiveLoyaltyParams> {
    const nowMs = this.now();
    const ttl = this.config.loyaltyPolicyCacheSeconds * 1000;
    let entry = this.cache.get(company);
    if (!entry || ttl === 0 || nowMs - entry.at >= ttl) {
      const at = new Date(nowMs);
      const [value, pv] = [await this.activeAt(company, at), await this.pointValueAt(at)];
      entry = { at: nowMs, value, pv };
      if (ttl > 0) this.cache.set(company, entry);
    }
    return this.toEffective(company, entry.value, entry.pv);
  }

  /** Parameter env lama (fallback; juga untuk `GET params` tanpa `company_code`). */
  envParams(company: CompanyCode): EffectiveLoyaltyParams {
    return envLoyaltyParams(this.config, company);
  }

  private toEffective(company: CompanyCode, active: ActivePolicy | null, pv: { value: number; version: number } | null): EffectiveLoyaltyParams {
    const env = this.envParams(company);
    const withPv = (p: EffectiveLoyaltyParams): EffectiveLoyaltyParams =>
      pv ? { ...p, pointValueIdr: pv.value, pointValueVersion: pv.version } : p;
    if (!active) {
      this.warnOnce(`none:${company}`, () => this.logger.warn(`Kebijakan poin ${company} belum ada — memakai parameter env (fallback, §13.1 no. 4)`));
      return withPv(env);
    }
    // Pertahanan berlapis: plafon diturunkan setelah kebijakan disimpan → kebijakan tidak dipakai
    const violations = policyViolations(active.params, null, this.config.loyaltyPolicyCeilings);
    if (violations.length > 0) {
      this.warnOnce(`oob:${company}:${active.version}`, () =>
        this.anomaly('policy_out_of_bounds', { company, version: active.version, fields: violations.join('.'), stage: 'use' }),
      );
      return withPv(env);
    }
    const p = active.params;
    return withPv({
      companyCode: company,
      source: 'policy',
      policyVersion: active.version,
      pointValueVersion: null,
      earnIdrPerPoint: p.earn_idr_per_point,
      pointValueIdr: env.pointValueIdr,
      redeemMinPoints: p.redeem_min_points,
      redeemMaxPct: p.redeem_max_pct,
      maxPointsPerOrder: p.max_points_per_order,
      dailyMaxPointsMember: p.daily_max_points_member,
      posConfigHourlyMax: p.pos_config_hourly_max,
      companyHourlyMax: p.company_hourly_max,
      earnOnMoneyOnly: p.earn_on_money_only,
    });
  }

  private warnOnce(key: string, fn: () => void): void {
    const nowMs = this.now();
    const last = this.warnedAt.get(key);
    if (last !== undefined && nowMs - last < WARN_INTERVAL_MS) return;
    this.warnedAt.set(key, nowMs);
    fn();
  }

  private anomaly(kind: 'policy_out_of_bounds' | 'policy_conflict', fields: Record<string, string | number>): void {
    if (this.metrics) reportLoyaltyAnomaly(this.logger, this.metrics, kind, fields);
    else this.logger.warn(`EFN_LOYALTY_ANOMALY ${JSON.stringify({ kind, ...fields })}`);
  }
}
