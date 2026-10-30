import { Inject, Injectable, Logger, Optional } from '@nestjs/common';
import { AppConfig } from '../../config/app-config';
import { DB, Db, Queryable } from '../../common/db/db';
import { MetricsService } from '../../common/metrics/metrics.service';
import { reportLoyaltyAnomaly } from '../loyalty-anomaly';
import { CompanyCode } from '../loyalty.service';
import { MgmPolicyErrors, MgmPolicyInput, MgmPolicyParams, mgmPolicyFingerprint, mgmPolicyViolations, parseMgmParams } from './mgm-policy';
import { MGM_STORE, MgmStore } from './mgm.repository';

export interface MgmPublishResult {
  ok: true;
  company_code: CompanyCode;
  version: number;
  status: 'stored' | 'duplicate';
}

export interface ActiveMgmPolicy {
  version: number;
  params: MgmPolicyParams;
}

const WARN_INTERVAL_MS = 10 * 60_000;

/**
 * Kebijakan MGM per PT (docs/04 §20.1). Pola `LoyaltyPolicyService` G21:
 * - publish: append-only; versi sama + sidik sama → `duplicate`; sidik beda → 409; di luar batas/plafon → 422 + anomali.
 *   Cek duplikat SEBELUM plafon (retry versi lama yang dulu diterima tetap 200 walau plafon diturunkan).
 * - aktif pada `t` = versi tertinggi dengan `GREATEST(effective_from, received_at) ≤ t`. TANPA fallback env: PT tanpa
 *   kebijakan aktif (atau kebijakan di luar plafon saat ini) = tidak ada reward (fail-closed).
 */
@Injectable()
export class MgmPolicyService {
  private readonly logger = new Logger(MgmPolicyService.name);
  private readonly warnedAt = new Map<string, number>();
  now: () => number = () => Date.now();

  constructor(
    private readonly config: AppConfig,
    @Inject(DB) private readonly db: Db,
    @Inject(MGM_STORE) private readonly store: MgmStore,
    @Optional() @Inject(MetricsService) private readonly metrics: MetricsService | null = null,
  ) {}

  async publish(input: MgmPolicyInput): Promise<MgmPublishResult> {
    const sha = mgmPolicyFingerprint(input);
    const status = await this.db.tx(async (q): Promise<'stored' | 'duplicate'> => {
      const prev = await this.store.policySha(q, input.companyCode, input.version);
      if (prev !== null) return this.compare(prev, sha, input);
      const bad = mgmPolicyViolations(input.params, this.config.mgmPolicyCeilings);
      if (bad.length > 0) {
        this.anomaly({ company: input.companyCode, version: input.version, fields: bad.join('.'), stage: 'publish' });
        throw MgmPolicyErrors.outOfBounds();
      }
      const ok = await this.store.insertPolicy(q, {
        company: input.companyCode,
        version: input.version,
        effectiveFrom: input.effectiveFrom,
        approvedByUid: input.approvedByUid,
        params: input.params,
        sha,
        receivedAt: new Date(this.now()),
      });
      if (ok) return 'stored';
      const raced = await this.store.policySha(q, input.companyCode, input.version);
      if (raced === null) throw new Error('mgm_policy: INSERT DO NOTHING tanpa baris');
      return this.compare(raced, sha, input);
    });
    this.logger.log(`EFN_MGM_POLICY ${JSON.stringify({ company: input.companyCode, version: input.version, status })}`);
    return { ok: true, company_code: input.companyCode, version: input.version, status };
  }

  private compare(prev: string, sha: string, input: MgmPolicyInput): 'duplicate' {
    if (prev === sha) return 'duplicate';
    reportOrLog(this.logger, this.metrics, 'policy_conflict', { company: input.companyCode, version: input.version, scope: 'mgm' });
    throw MgmPolicyErrors.conflict();
  }

  /** Kebijakan MGM PT yang aktif pada `at` dan masih dalam plafon saat ini; selain itu null (tidak ada reward). */
  async activeAt(company: CompanyCode, at: Date, q: Queryable = this.db): Promise<ActiveMgmPolicy | null> {
    const row = await this.store.activePolicy(q, company, at);
    if (!row) return null;
    let params: MgmPolicyParams;
    try {
      params = parseMgmParams(row.params);
    } catch {
      this.logger.error(`mgm_policy ${company} v${row.version}: params tersimpan tidak valid — tidak ada reward`);
      return null;
    }
    const bad = mgmPolicyViolations(params, this.config.mgmPolicyCeilings);
    if (bad.length > 0) {
      this.warnOnce(`${company}:${row.version}`, () =>
        this.anomaly({ company, version: row.version, fields: bad.join('.'), stage: 'use' }),
      );
      return null;
    }
    return { version: row.version, params };
  }

  private anomaly(fields: Record<string, string | number>): void {
    reportOrLog(this.logger, this.metrics, 'policy_out_of_bounds', { ...fields, scope: 'mgm' });
  }

  private warnOnce(key: string, fn: () => void): void {
    const nowMs = this.now();
    const last = this.warnedAt.get(key);
    if (last !== undefined && nowMs - last < WARN_INTERVAL_MS) return;
    this.warnedAt.set(key, nowMs);
    fn();
  }
}

function reportOrLog(
  logger: Logger,
  metrics: MetricsService | null,
  kind: 'policy_out_of_bounds' | 'policy_conflict',
  fields: Record<string, string | number>,
): void {
  if (metrics) reportLoyaltyAnomaly(logger, metrics, kind, fields);
  else logger.warn(`EFN_LOYALTY_ANOMALY ${JSON.stringify({ kind, ...fields })}`);
}
