import { Inject, Injectable, Logger, OnApplicationBootstrap, OnModuleDestroy, OnModuleInit, Optional } from '@nestjs/common';
import { randomUUID } from 'node:crypto';
import { AppConfig } from '../../config/app-config';
import { AmqpService, MessageMeta, PermanentMessageError } from '../../common/amqp/amqp.service';
import { QUEUES } from '../../common/amqp/queues';
import { DB, Db, Queryable } from '../../common/db/db';
import { claimMessage } from '../../common/db/processed-message';
import { MetricsService } from '../../common/metrics/metrics.service';
import { OutboxService } from '../../common/outbox/outbox.service';
import { reportLoyaltyAnomaly } from '../loyalty-anomaly';
import { LoyaltyPolicyService } from '../loyalty-policy.service';
import { Allocation, CompanyCode, LedgerEntryInput, LoyaltyService, allocateDebit, isCompanyCode, splitCredit } from '../loyalty.service';
import { addMonths, mgmRewardPoints, wibMonthRange } from './mgm-policy';
import { MgmPolicyService } from './mgm-policy.service';
import { MGM_STORE, MgmRewardRow, MgmSourceKind, MgmStore } from './mgm.repository';

/** Ledger: semua entri MGM `source='mgm'` (rekonsiliasi §20.6). */
export const MGM_SOURCE = 'mgm';
export const MGM_REASON = { REWARD: 'mgm_reward', REVERSE: 'mgm_reverse' } as const;
export const MGM_REWARD_CONSUMER = 'bff.mgm-reward';
/** Publisher yang diizinkan untuk `mgm.reward.requested` (properti AMQP `user_id`, divalidasi broker). */
export const MGM_PUBLISHERS: readonly string[] = ['efn-bff'];
/** §20.11: sumber & template `notify.push` reward settle (publisher hanya efn-bff). */
export const MGM_NOTIFY_SOURCE = 'mgm_reward';
export const MGM_NOTIFY_TEMPLATE = 'mgm_reward_settled';

/** Advisory lock job settle (bigint, berbeda dari kunci job lain). */
export const MGM_SETTLE_LOCK_KEY = 0x45464e53; // 'EFNS'
const BATCH = 200;
const EPS = 1e-9;

export interface MgmRewardMessage {
  schema_version: 1;
  idempotency_key: string;
  source_kind: MgmSourceKind;
  source_ref: string;
}

/** Validasi ketat pesan `mgm.reward.requested` (§20.3); bentuk salah → DLQ. */
export function parseMgmRewardMessage(p: unknown): MgmRewardMessage {
  if (typeof p !== 'object' || p === null || Array.isArray(p)) throw new PermanentMessageError('payload bukan objek');
  const o = p as Record<string, unknown>;
  const keys = ['schema_version', 'idempotency_key', 'source_kind', 'source_ref'];
  if (Object.keys(o).some((k) => !keys.includes(k))) throw new PermanentMessageError('field tidak dikenal');
  if (o.schema_version !== 1) throw new PermanentMessageError('schema_version tidak didukung');
  if (o.source_kind !== 'pos' && o.source_kind !== 'online') throw new PermanentMessageError('source_kind tidak valid');
  if (typeof o.source_ref !== 'string' || !/^[A-Za-z0-9_.:-]{1,64}$/.test(o.source_ref)) throw new PermanentMessageError('source_ref tidak valid');
  if (o.idempotency_key !== `mgm:${o.source_kind}:${o.source_ref}`) throw new PermanentMessageError('idempotency_key tidak cocok');
  return { schema_version: 1, idempotency_key: o.idempotency_key, source_kind: o.source_kind, source_ref: o.source_ref };
}

export interface SettleRunResult {
  skipped: boolean;
  reversals: number;
  settled: number;
  capped: number;
  cancelled: number;
}

/**
 * Engine MGM (docs/04 §20.3–§20.5). Satu-satunya penulis entri ledger `source='mgm'`.
 *
 * - `recordSource` / `recordReversal`: dipanggil DI DALAM transaksi earn/reverse POS & online (tanpa I/O lain).
 *   Sumber hanya dicatat bila `MGM_ENABLED` dan pembeli punya referrer; reversal SELALU dicatat bila sumber ada
 *   (retur setelah fitur dimatikan tetap ditarik).
 * - Consumer `mgm.reward.requested`: hitung reward per level → `mgm_reward` pending (deterministik: versi kebijakan,
 *   nilai poin, cap disimpan).
 * - Job settle: reversal dulu (sekali per reward × retur), lalu settle reward jatuh tempo dengan cap bulanan.
 */
@Injectable()
export class MgmEngineService implements OnApplicationBootstrap, OnModuleInit, OnModuleDestroy {
  private readonly logger = new Logger(MgmEngineService.name);
  private timer: NodeJS.Timeout | null = null;
  private running = false;
  /** Member yang sudah dikunci di transaksi job berjalan (kunci sekali per member per transaksi). */
  private locked = new Set<string>();
  now: () => number = () => Date.now();

  constructor(
    private readonly config: AppConfig,
    @Inject(DB) private readonly db: Db,
    @Inject(MGM_STORE) private readonly store: MgmStore,
    private readonly policies: MgmPolicyService,
    private readonly loyalty: LoyaltyService,
    private readonly outbox: OutboxService,
    // `X | null` memancarkan metadata `Object` (Nest lalu menyuntik null) → token eksplisit wajib (guard: di-spec.ts)
    @Optional() @Inject(LoyaltyPolicyService) private readonly loyaltyPolicies: LoyaltyPolicyService | null = null,
    @Optional() @Inject(AmqpService) private readonly amqp: AmqpService | null = null,
    @Optional() @Inject(MetricsService) private readonly metrics: MetricsService | null = null,
  ) {}

  onModuleInit(): void {
    const ms = this.config.mgmSettleIntervalMs;
    if (ms > 0 && this.config.mgmEnabled) {
      this.timer = setInterval(() => void this.runOnce(), ms);
      this.timer.unref();
    }
  }

  onModuleDestroy(): void {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
  }

  async onApplicationBootstrap(): Promise<void> {
    if (!this.config.amqpConsumersEnabled || !this.config.mgmEnabled || !this.amqp) return;
    await this.amqp.consume(QUEUES.MGM_REWARD_REQUESTED, async (payload, meta) => {
      await this.handle(payload, meta);
    });
  }

  // ───────────── hook transaksi earn/reverse ─────────────
  /**
   * §20.3: catat sumber akrual + outbox (transaksi pemanggil). No-op bila MGM mati, pembeli tanpa referrer, atau basis 0.
   * Idempoten: PK (source_kind, source_ref).
   */
  async recordSource(
    q: Queryable,
    s: { kind: MgmSourceKind; ref: string; company: CompanyCode; buyerHash: string; basisIdr: number; occurredAt: Date },
  ): Promise<boolean> {
    if (!this.config.mgmEnabled || !Number.isSafeInteger(s.basisIdr) || s.basisIdr <= 0) return false;
    const me = await this.store.memberByHash(q, s.buyerHash);
    if (!me || me.referrer_hash === null) return false;
    const fresh = await this.store.insertSource(q, {
      source_kind: s.kind,
      source_ref: s.ref,
      company_code: s.company,
      buyer_hash: s.buyerHash,
      basis_idr: s.basisIdr,
      occurred_at: s.occurredAt,
    });
    if (!fresh) return false;
    const key = `mgm:${s.kind}:${s.ref}`;
    await this.outbox.enqueue(q, QUEUES.MGM_REWARD_REQUESTED, { schema_version: 1, idempotency_key: key, source_kind: s.kind, source_ref: s.ref }, key);
    return true;
  }

  /**
   * §20.4: catat fakta retur sumber (transaksi pemanggil). Tidak bergantung `MGM_ENABLED`. G31b §20.10: `refundedBasisIdr`
   * (dasar MGM barang retur, Odoo) → `ratio = min(1, refundedBasisIdr / basis_idr)`; 0 = barang yang diretur tidak
   * termasuk dasar MGM → tanpa reversal. Tanpa field → `ratio` pemanggil (v14.0).
   */
  async recordReversal(
    q: Queryable,
    r: { kind: MgmSourceKind; ref: string; reverseKey: string; ratio: number; refundedBasisIdr?: number },
  ): Promise<boolean> {
    const src = await this.store.source(q, r.kind, r.ref, false);
    if (!src) return false;
    const raw = r.refundedBasisIdr !== undefined ? (src.basis_idr > 0 ? r.refundedBasisIdr / src.basis_idr : 0) : r.ratio;
    if (!(raw > 0)) return false;
    const ratio = Math.min(1, Math.round(raw * 1_000_000) / 1_000_000);
    if (!(ratio > 0)) return false;
    return this.store.insertReversal(q, { source_kind: r.kind, source_ref: r.ref, reverse_key: r.reverseKey.slice(0, 200), ratio });
  }

  // ───────────── consumer akrual ─────────────
  async handle(payload: unknown, meta: Pick<MessageMeta, 'userId'>): Promise<'applied' | 'duplicate' | 'no_reward'> {
    if (meta.userId === undefined || !MGM_PUBLISHERS.includes(meta.userId)) {
      this.logger.warn('mgm.reward.requested ditolak: publisher tidak diizinkan');
      throw new PermanentMessageError('publisher tidak diizinkan');
    }
    const msg = parseMgmRewardMessage(payload);
    const outcome = await this.db.tx(async (q) => {
      if (!(await claimMessage(q, MGM_REWARD_CONSUMER, msg.idempotency_key))) return 'duplicate' as const;
      const src = await this.store.source(q, msg.source_kind, msg.source_ref, true);
      if (!src) {
        if (this.metrics) reportLoyaltyAnomaly(this.logger, this.metrics, 'mgm_source_missing', { order_ref: msg.source_ref });
        throw new PermanentMessageError('mgm_source tidak ada');
      }
      if (src.processed_at !== null) return 'duplicate' as const;
      const n = await this.accrue(q, src);
      await this.store.markSourceProcessed(q, src.source_kind, src.source_ref, new Date(this.now()));
      return n > 0 ? ('applied' as const) : ('no_reward' as const);
    });
    this.logger.log(`mgm.reward.requested ${msg.source_kind}: ${outcome}`);
    return outcome;
  }

  /** Hitung & simpan reward per level untuk satu sumber. Mengembalikan jumlah baris reward baru. */
  private async accrue(q: Queryable, src: { source_kind: MgmSourceKind; source_ref: string; company_code: CompanyCode; buyer_hash: string; basis_idr: number; occurred_at: Date }): Promise<number> {
    const policy = await this.policies.activeAt(src.company_code, src.occurred_at, q);
    if (!policy || src.basis_idr < policy.params.min_order_idr) return 0;
    const pv = this.loyaltyPolicies ? (await this.loyaltyPolicies.pointValueIdrAt(src.occurred_at, q)).value : this.config.loyaltyPointValueIdr;
    const settleAfter = new Date(src.occurred_at.getTime() + policy.params.settle_hold_days * 86_400_000);
    let child = await this.store.memberByHash(q, src.buyer_hash);
    let created = 0;
    for (let level = 1; level <= policy.params.max_depth && child && child.referrer_hash && child.linked_at; level += 1) {
      // tautan berlaku: dibuat sebelum transaksi dan masih dalam masa relasi
      if (child.linked_at.getTime() > src.occurred_at.getTime()) break;
      if (src.occurred_at.getTime() >= addMonths(child.linked_at, policy.params.relation_months).getTime()) break;
      const referrer = child.referrer_hash;
      if (!(await this.store.activeMemberId(q, referrer))) break;
      const points = mgmRewardPoints(policy.params, level, src.basis_idr, pv);
      if (points > 0) {
        const ok = await this.store.insertReward(q, {
          id: randomUUID(),
          source_kind: src.source_kind,
          source_ref: src.source_ref,
          level,
          referrer_hash: referrer,
          company_code: src.company_code,
          points,
          policy_version: policy.version,
          point_value_idr: pv,
          cap_points: policy.params.cap_points_per_referrer_month,
          occurred_at: src.occurred_at,
          settle_after: settleAfter,
        });
        if (ok) created += 1;
      }
      child = await this.store.memberByHash(q, referrer);
    }
    return created;
  }

  // ───────────── job settle & clawback ─────────────
  /** Tidak pernah melempar (timer). */
  async runOnce(): Promise<SettleRunResult> {
    const skipped: SettleRunResult = { skipped: true, reversals: 0, settled: 0, capped: 0, cancelled: 0 };
    if (this.running || !this.config.mgmEnabled) return skipped;
    this.running = true;
    try {
      return await this.db.tx(async (q) => {
        const lock = await q.query<{ ok: boolean }>('SELECT pg_try_advisory_xact_lock($1) AS ok', [MGM_SETTLE_LOCK_KEY]);
        if (!lock.rows[0]?.ok) return skipped;
        const out: SettleRunResult = { skipped: false, reversals: 0, settled: 0, capped: 0, cancelled: 0 };
        this.locked = new Set<string>();
        const at = new Date(this.now());
        for (const { reward, reversal } of await this.store.pendingReversals(q, BATCH)) {
          if (await this.applyReversal(q, reward, reversal.reverse_key, reversal.ratio, at)) out.reversals += 1;
        }
        for (const reward of await this.store.dueRewards(q, at, BATCH)) {
          const r = await this.settle(q, reward, at);
          out[r] += 1;
        }
        if (out.reversals + out.settled + out.capped + out.cancelled > 0) {
          this.logger.log(`EFN_MGM_SETTLE ${JSON.stringify({ reversals: out.reversals, settled: out.settled, capped: out.capped, cancelled: out.cancelled })}`);
        }
        return out;
      });
    } catch (err) {
      this.logger.error(`Job settle MGM gagal: ${err instanceof Error ? err.message : String(err)}`);
      return skipped;
    } finally {
      this.running = false;
      this.locked = new Set<string>();
    }
  }

  /** §20.4: terapkan satu retur ke satu reward (sekali, ditandai `mgm_reversal_applied`). */
  async applyReversal(q: Queryable, reward: MgmRewardRow, reverseKey: string, ratio: number, at: Date): Promise<boolean> {
    if (!(await this.store.markReversalApplied(q, reward.id, reverseKey, at))) return false;
    const cut = Math.min(reward.points - reward.reversed_points, Math.ceil(reward.points * ratio - EPS));
    if (cut <= 0) return true;
    const reversed = reward.reversed_points + cut;
    if (reward.status === 'pending') {
      await this.store.updateReward(q, reward.id, reversed >= reward.points ? { reversed_points: reversed, status: 'cancelled', status_reason: 'returned' } : { reversed_points: reversed }, at);
      return true;
    }
    if (reward.status !== 'settled') return true;
    // sudah settle: kembalikan kelebihan dari target (points − reversed) lewat ledger debit (FIFO; kekurangan = utang)
    const target = Math.max(0, reward.points - reversed);
    const claw = Math.max(0, reward.settled_points - reward.clawed_points - target);
    // referrer sudah dihapus (erasure) → saldo sudah di-forfeit, tidak ada yang ditarik; hanya catat reversal
    const written = claw > 0 ? await this.writeLedger(q, reward, -claw, `${MGM_REASON.REVERSE}:${reward.id}:${reverseKey}`, at) : false;
    await this.store.updateReward(q, reward.id, { reversed_points: reversed, clawed_points: reward.clawed_points + (written ? claw : 0) }, at);
    return true;
  }

  private async settle(q: Queryable, reward: MgmRewardRow, at: Date): Promise<'settled' | 'capped' | 'cancelled'> {
    if (!(await this.store.activeMemberId(q, reward.referrer_hash))) {
      await this.store.updateReward(q, reward.id, { status: 'cancelled', status_reason: 'referrer_inactive' }, at);
      return 'cancelled';
    }
    const month = wibMonthRange(reward.occurred_at);
    const used = await this.store.settledInMonth(q, reward.referrer_hash, reward.company_code, month.start, month.end);
    const amount = Math.max(0, Math.min(reward.points - reward.reversed_points, reward.cap_points - used));
    if (amount <= 0) {
      await this.store.updateReward(q, reward.id, { status: 'capped', status_reason: reward.points - reward.reversed_points <= 0 ? 'returned' : 'monthly_cap' }, at);
      return 'capped';
    }
    if (!(await this.writeLedger(q, reward, amount, `${MGM_REASON.REWARD}:${reward.id}`, at))) {
      await this.store.updateReward(q, reward.id, { status: 'cancelled', status_reason: 'referrer_inactive' }, at);
      return 'cancelled';
    }
    await this.store.updateReward(
      q,
      reward.id,
      { status: 'settled', settled_points: amount, settled_at: at, status_reason: amount < reward.points - reward.reversed_points ? 'partial_cap' : null },
      at,
    );
    if (this.metrics && isCompanyCode(reward.company_code)) this.metrics.recordLoyaltyPosPoints('earned', reward.company_code, amount);
    await this.notifySettled(q, reward);
    return 'settled';
  }

  /**
   * §20.11: satu `notify.push` per reward settle — TANPA parameter (tanpa poin/level/PT/downline) dan hanya bila
   * referrer punya consent marketing aktif (referral = komunikasi pemasaran). Transaksi sama dengan settle (outbox).
   */
  private async notifySettled(q: Queryable, reward: MgmRewardRow): Promise<void> {
    if (!(await this.store.hasMarketingConsent(q, reward.referrer_hash))) return;
    const key = `notify:${MGM_NOTIFY_SOURCE}:${reward.id}`;
    await this.outbox.enqueue(
      q,
      QUEUES.NOTIFY_PUSH,
      {
        schema_version: 2,
        idempotency_key: key,
        member_hash: reward.referrer_hash,
        source: MGM_NOTIFY_SOURCE,
        source_ref: reward.id,
        template: MGM_NOTIFY_TEMPLATE,
        params: {},
      },
      key,
    );
  }

  /** Entri ledger reward (+) / clawback (−) untuk referrer, lot penerbit = PT transaksi. */
  private async writeLedger(q: Queryable, reward: MgmRewardRow, delta: number, key: string, at: Date): Promise<boolean> {
    const hash = reward.referrer_hash;
    if (!this.locked.has(hash)) {
      await this.loyalty.lockMember(q, hash);
      this.locked.add(hash);
    }
    const enc = await this.encFor(q, hash);
    // referrer bukan member aktif (erasure: saldo sudah di-forfeit, hash = tombstone) → tidak ada entri ledger
    if (!enc) return false;
    const buckets = await this.loyalty.buckets(q, hash, at);
    let allocs: Allocation[];
    if (delta > 0) {
      allocs = splitCredit(buckets, delta, expiryFor(at.getTime(), this.config.loyaltyPointMaxAgeDays), reward.company_code);
    } else {
      allocs = allocateDebit(buckets, -delta, reward.company_code);
      const available = buckets.filter((b) => b.remaining > 0).reduce((s, b) => s + b.remaining, 0);
      if (available < -delta && this.metrics) {
        reportLoyaltyAnomaly(this.logger, this.metrics, 'mgm_clawback_debt', { company: reward.company_code, points: -delta - available });
      }
    }
    let i = 0;
    for (const a of allocs) {
      if (a.points <= 0) continue;
      const entry: LedgerEntryInput = {
        idempotencyKey: i === 0 ? key : `${key}#${i + 1}`,
        memberHash: hash,
        memberRefEnc: enc,
        delta: delta > 0 ? a.points : -a.points,
        reason: delta > 0 ? MGM_REASON.REWARD : MGM_REASON.REVERSE,
        source: MGM_SOURCE,
        sourceRef: reward.id,
        companyCode: reward.company_code,
        expiresAt: a.expiresAt,
        issuerCompanyCode: a.issuer ?? reward.company_code,
      };
      i += 1;
      const inserted = await this.loyalty.insertEntry(entry, q);
      if (!inserted && i === 1) return true; // replay
    }
    return true;
  }

  /** member_ref_enc (AAD ledger) referrer: dari akun aktif. */
  private async encFor(q: Queryable, hash: string): Promise<Buffer | null> {
    const id = await this.store.activeMemberId(q, hash);
    if (!id) return null;
    const m = await this.loyalty.ledgerEncForMember(q, id);
    return m && m.hash === hash ? m.enc : null;
  }
}

/** Masa berlaku lot kredit (sama dengan earn POS/online: `LOYALTY_POINT_MAX_AGE_DAYS`, 0 = tanpa kedaluwarsa). */
function expiryFor(nowMs: number, days: number): Date | null {
  if (days <= 0) return null;
  const WIB = 7 * 3600 * 1000;
  const DAY = 86_400_000;
  const startOfDayWib = Math.floor((nowMs + WIB) / DAY) * DAY - WIB;
  return new Date(startOfDayWib + (days + 1) * DAY);
}
