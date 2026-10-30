import { Inject, Injectable, Logger, Optional } from '@nestjs/common';
import { randomUUID } from 'node:crypto';
import { AppConfig } from '../config/app-config';
import { DB, Db, Queryable } from '../common/db/db';
import { MetricsService } from '../common/metrics/metrics.service';
import { MgmEngineService } from '../loyalty/mgm/mgm-engine.service';
import { touchActivity } from '../loyalty/loyalty-activity';
import { reportLoyaltyAnomaly } from '../loyalty/loyalty-anomaly';
import { EffectiveLoyaltyParams, LoyaltyPolicyService, envLoyaltyParams } from '../loyalty/loyalty-policy.service';
import { Allocation, CompanyCode, LedgerBucket, LedgerEntryInput, LoyaltyService, allocateDebit, isCompanyCode, splitCredit } from '../loyalty/loyalty.service';
import { OnlineErrors } from '../odoo-rpc/online-errors';
import { expiryFor } from '../pos/pos-loyalty.service';
import { OrderRow, PartRow, RefundRow } from './online.model';
import { OnlineRepo } from './online.repository';

/** Ledger poin online (§14.C.3, L-77 no. 2): `source='online'`, `source_ref = part_id` untuk keempat alasan. */
export const ONLINE_SOURCE = 'online';
export const ONLINE_REASON = { REDEEM: 'online_redeem', RESTORE: 'online_restore', EARN: 'online_earn', REVERSE: 'online_reverse' } as const;
const EPS = 1e-9;

/** Blok informatif quote (C.2). */
export interface QuotePoints {
  balance_available: number;
  point_value_idr: number;
  groups: { channel_code: string; redeem_allowed: boolean; min_points: number; max_points: number; max_value_idr: number }[];
}
export interface RedeemPlan {
  channel_code: string;
  company_code: CompanyCode;
  points: number;
  value_idr: number;
}

/**
 * Port poin online yang dipakai checkout/saga/event (semua metode bertanda `r` berjalan DI DALAM transaksi pemanggil).
 * Implementasi PostgreSQL: `PgOnlinePoints`; test saga memakai fake.
 */
export interface OnlinePointsPort {
  quoteBlock(memberId: string, memberHash: string | null, groups: { channel_code: string; company_code: string; amount_payable: number; redeem: boolean }[]): Promise<QuotePoints>;
  /** Validasi permintaan klien terhadap kebijakan PT (di luar transaksi). Melempar error kontrak C.2. */
  plan(groups: { channel_code: string; company_code: string; amount_payable: number; redeem: boolean }[], req: { channel_code: string; points: number }[]): Promise<RedeemPlan[]>;
  /** Reserve (C.3) di transaksi pembuatan order — saldo tersedia di bawah kunci member ledger. */
  reserve(r: OnlineRepo, memberId: string, partId: string, plan: RedeemPlan, amountPayable: number, holdUntil: Date): Promise<string>;
  commitOrder(r: OnlineRepo, order: OrderRow, parts: readonly PartRow[]): Promise<void>;
  releaseOrder(r: OnlineRepo, orderId: string, parts: readonly PartRow[]): Promise<void>;
  /** Restore saat refund `settled` (D-22-11); nilai = refund.points_value Odoo, diverifikasi ±Rp1. */
  restoreOnSettled(r: OnlineRepo, part: PartRow, refund: RefundRow, priorSettled: readonly RefundRow[]): Promise<void>;
  earnOnPickup(r: OnlineRepo, order: OrderRow, part: PartRow, eligibleAmount: number): Promise<void>;
  clawbackOnRefund(r: OnlineRepo, part: PartRow, key: string, eligibleRefunded: number): Promise<void>;
  /** Saldo tertahan (reservasi aktif) member — `GET /v1/me` (C.2). */
  reservedPoints(memberHash: string): Promise<number>;
}
export const ONLINE_POINTS = Symbol('ONLINE_POINTS');

function sqlOf(r: OnlineRepo): Queryable {
  const q = r.sql?.() ?? null;
  if (!q) throw new Error('operasi poin online wajib di dalam transaksi PostgreSQL');
  return q;
}

/**
 * Poin online (docs/04 §14.C, W-12; L-77, L-79). Redeem = porsi pembayaran "Poin EFN" per bagian; ledger BFF satu-satunya
 * saldo. L-79: redeem HANYA bila kebijakan PT + nilai tukar global Odoo dipublikasikan (`source = policy`) — Odoo
 * menolak poin tanpa kebijakan (fail-closed), BFF tidak menawarkan/menerima redeem dengan parameter env.
 */
@Injectable()
export class PgOnlinePoints implements OnlinePointsPort {
  private readonly logger = new Logger('OnlinePoints');
  now: () => number = () => Date.now();

  constructor(
    private readonly config: AppConfig,
    @Inject(DB) private readonly db: Db,
    private readonly loyalty: LoyaltyService,
    private readonly metrics: MetricsService,
    @Optional() private readonly policies: LoyaltyPolicyService | null = null,
    /** G31 §20.3/§20.4: engine MGM (sumber akrual & retur). */
    @Optional() private readonly mgm: MgmEngineService | null = null,
  ) {}

  private async params(company: CompanyCode): Promise<EffectiveLoyaltyParams> {
    return this.policies ? this.policies.effective(company) : envLoyaltyParams(this.config, company);
  }

  /** L-79: kebijakan PT Odoo + nilai tukar global harus dari kebijakan (bukan env). */
  private redeemable(p: EffectiveLoyaltyParams): boolean {
    return p.source === 'policy' && p.pointValueVersion !== null;
  }

  static maxPoints(amountPayable: number, pct: number, pv: number, available: number): number {
    const byPct = Math.floor(Math.floor((amountPayable * pct) / 100) / pv);
    const byCash = Math.floor((amountPayable - 1) / pv); // porsi tunai ≥ Rp1
    return Math.max(0, Math.min(available, byPct, byCash));
  }

  async available(q: Queryable, memberHash: string): Promise<number> {
    const balance = await this.loyalty.balanceByHash(memberHash, q);
    const held = await q.query<{ held: string | number | null }>(
      `SELECT COALESCE(SUM(points), 0)::bigint AS held FROM platform.loyalty_reservation
        WHERE member_hash = $1 AND status = 'reserved' AND hold_until > $2`,
      [memberHash, new Date(this.now()).toISOString()],
    );
    return balance - Number(held.rows[0]?.held ?? 0);
  }

  async reservedPoints(memberHash: string): Promise<number> {
    const r = await this.db.query<{ held: string | number | null }>(
      `SELECT COALESCE(SUM(points), 0)::bigint AS held FROM platform.loyalty_reservation
        WHERE member_hash = $1 AND status = 'reserved' AND hold_until > $2`,
      [memberHash, new Date(this.now()).toISOString()],
    );
    return Number(r.rows[0]?.held ?? 0);
  }

  async quoteBlock(
    _memberId: string,
    memberHash: string | null,
    groups: { channel_code: string; company_code: string; amount_payable: number; redeem: boolean }[],
  ): Promise<QuotePoints> {
    const available = memberHash ? Math.max(0, await this.available(this.db, memberHash)) : 0;
    let pv = this.config.loyaltyPointValueIdr;
    const out: QuotePoints['groups'] = [];
    for (const g of groups) {
      if (!isCompanyCode(g.company_code)) continue;
      const p = await this.params(g.company_code);
      pv = p.pointValueIdr;
      const allowed = this.config.onlineLoyaltyRedeemEnabled && g.redeem && this.redeemable(p) && p.redeemMaxPct > 0;
      const max = allowed ? PgOnlinePoints.maxPoints(g.amount_payable, p.redeemMaxPct, p.pointValueIdr, available) : 0;
      out.push({
        channel_code: g.channel_code,
        redeem_allowed: allowed && max >= p.redeemMinPoints,
        min_points: p.redeemMinPoints,
        max_points: max,
        max_value_idr: max * p.pointValueIdr,
      });
    }
    return { balance_available: available, point_value_idr: pv, groups: out };
  }

  async plan(
    groups: { channel_code: string; company_code: string; amount_payable: number; redeem: boolean }[],
    req: { channel_code: string; points: number }[],
  ): Promise<RedeemPlan[]> {
    if (req.length === 0) return [];
    if (!this.config.onlineLoyaltyRedeemEnabled) throw OnlineErrors.pointsUnavailable();
    const out: RedeemPlan[] = [];
    for (const x of req) {
      const g = groups.find((y) => y.channel_code === x.channel_code);
      if (!g || !isCompanyCode(g.company_code)) throw OnlineErrors.invalidInput();
      const p = await this.params(g.company_code);
      if (!g.redeem || !this.redeemable(p) || p.redeemMaxPct <= 0) throw OnlineErrors.pointsUnavailable();
      if (x.points < p.redeemMinPoints) throw OnlineErrors.belowMinimum();
      const value = x.points * p.pointValueIdr;
      if (value > g.amount_payable - 1) throw OnlineErrors.pointsCoverFullAmount();
      // nilai poin ≤ floor(amount_payable × pct / 100) — termasuk pajak (K-18-2)
      if (value > Math.floor((g.amount_payable * p.redeemMaxPct) / 100)) throw OnlineErrors.exceedsMaxPct();
      out.push({ channel_code: g.channel_code, company_code: g.company_code, points: x.points, value_idr: value });
    }
    return out;
  }

  async reserve(r: OnlineRepo, memberId: string, partId: string, plan: RedeemPlan, amountPayable: number, holdUntil: Date): Promise<string> {
    const q = sqlOf(r);
    const m = await this.loyalty.ledgerEncForMember(q, memberId);
    if (!m) throw OnlineErrors.accountDeleting();
    await this.loyalty.lockMember(q, m.hash); // urutan kunci: member online → member ledger (sama dengan jalur lain)
    if ((await this.available(q, m.hash)) < plan.points) throw OnlineErrors.insufficientPoints();
    const id = randomUUID();
    const now = new Date(this.now()).toISOString();
    await q.query(
      `INSERT INTO platform.loyalty_reservation
         (id, member_hash, company_code, order_ref, token_jti, points, value_idr, amount_total, status, expires_at, hold_until,
          created_at, updated_at, channel, part_id)
       VALUES ($1, $2, $3, $4, NULL, $5, $6, $7, 'reserved', $8, $8, $9, $9, 'online', $10)`,
      [id, m.hash, plan.company_code, `online:${partId}`, plan.points, plan.value_idr, amountPayable, holdUntil.toISOString(), now, partId],
    );
    this.metrics.recordLoyaltyPosPoints('reserved', plan.company_code, plan.points);
    return id;
  }

  async commitOrder(r: OnlineRepo, order: OrderRow, parts: readonly PartRow[]): Promise<void> {
    const withPoints = parts.filter((p) => (p.points_redeemed ?? 0) > 0 && p.loyalty_reservation_id);
    if (withPoints.length === 0) return;
    const q = sqlOf(r);
    const now = new Date(this.now());
    for (const part of withPoints) {
      const res = await q.query<{ id: string; member_hash: string; company_code: string; points: number; value_idr: string | number; status: string }>(
        `SELECT id::text AS id, member_hash, company_code, points, value_idr, status FROM platform.loyalty_reservation WHERE id = $1 FOR UPDATE`,
        [part.loyalty_reservation_id],
      );
      const row = res.rows[0];
      if (!row) continue;
      if (row.status === 'committed') continue; // idempoten
      if (row.status !== 'reserved' || !isCompanyCode(row.company_code)) {
        reportLoyaltyAnomaly(this.logger, this.metrics, 'commit_payment_mismatch', { order_ref: part.id, company: part.company_code, stage: `reservation_${row.status}` });
        continue;
      }
      await this.loyalty.lockMember(q, row.member_hash);
      const enc = await this.encFor(q, order.member_id, row.member_hash);
      const key = `${ONLINE_REASON.REDEEM}:${part.id}`;
      // §13.1 no. 5: FIFO per lot; penerbit lot dicatat (lintas PT bila ≠ PT penebus)
      const allocs = allocateDebit(await this.loyalty.buckets(q, row.member_hash, now), Number(row.points), row.company_code);
      await this.write(q, allocs, key, (a) => ({
        memberHash: row.member_hash,
        memberRefEnc: enc,
        delta: -a.points,
        reason: ONLINE_REASON.REDEEM,
        source: ONLINE_SOURCE,
        sourceRef: part.id,
        companyCode: row.company_code as CompanyCode,
        expiresAt: a.expiresAt,
        issuerCompanyCode: a.issuer ?? (row.company_code as CompanyCode),
      }));
      await q.query(
        `UPDATE platform.loyalty_reservation SET status = 'committed', committed_at = $2, commit_idempotency_key = $3, updated_at = $2
          WHERE id = $1 AND status = 'reserved'`,
        [row.id, now.toISOString(), key],
      );
      await touchActivity(q, row.member_hash, 'online_redeem', now);
      this.metrics.recordLoyaltyPosPoints('redeemed', row.company_code, Number(row.points));
    }
  }

  async releaseOrder(r: OnlineRepo, _orderId: string, parts: readonly PartRow[]): Promise<void> {
    const ids = parts.map((p) => p.loyalty_reservation_id).filter((x): x is string => typeof x === 'string');
    if (ids.length === 0) return;
    await sqlOf(r).query(
      `UPDATE platform.loyalty_reservation SET status = 'released', updated_at = now()
        WHERE id = ANY($1::uuid[]) AND channel = 'online' AND status IN ('reserved', 'expired')`,
      [ids],
    );
  }

  async restoreOnSettled(r: OnlineRepo, part: PartRow, refund: RefundRow, priorSettled: readonly RefundRow[]): Promise<void> {
    const V = part.points_value_idr ?? 0;
    const value = refund.points_value ?? 0;
    if (V <= 0 || value <= 0) return;
    const q = sqlOf(r);
    const restoredBefore = part.points_restored_value ?? 0;
    // pola SEC-128: round(min(ΣR, T) × V ÷ T) − Σ restore sebelumnya, toleransi ±Rp1 (T = amount_payable bagian)
    const T = part.amount_payable;
    const cumulative = Math.min(T, [...priorSettled, refund].reduce((s, x) => s + (x.amount ?? 0), 0));
    const expected = Math.max(0, Math.round((cumulative * V) / T) - restoredBefore);
    if (Math.abs(value - expected) > 1) {
      reportLoyaltyAnomaly(this.logger, this.metrics, 'restore_value_mismatch', { order_ref: part.id, points_restored_value: value, expected_value: expected });
      return; // tidak dipulihkan otomatis — CS meninjau (anomali critical)
    }
    const capped = Math.min(value, V - restoredBefore);
    const res = await q.query<{ points: number; value_idr: string | number; member_hash: string; company_code: string }>(
      `SELECT points, value_idr, member_hash, company_code FROM platform.loyalty_reservation WHERE part_id = $1 AND status = 'committed'`,
      [part.id],
    );
    const row = res.rows[0];
    if (!row || Number(row.points) <= 0) return;
    const pv = Number(row.value_idr) / Number(row.points);
    const points = Math.floor(capped / pv + EPS); // L-77 no. 3: dibulatkan ke BAWAH ke poin utuh
    if (points <= 0) return;
    await this.loyalty.lockMember(q, row.member_hash);
    const redeemRows = await q.query<{ delta_points: number; expires_at: Date | string | null; issuer: string }>(
      `SELECT delta_points, expires_at, COALESCE(issuer_company_code, company_code) AS issuer FROM platform.loyalty_ledger
        WHERE source = 'online' AND reason = 'online_redeem' AND source_ref = $1 ORDER BY id`,
      [part.id],
    );
    const now = new Date(this.now());
    const allocs: Allocation[] = [];
    let left = points;
    for (const x of redeemRows.rows) {
      if (left <= 0) break;
      const take = Math.min(left, -Number(x.delta_points));
      if (take <= 0 || !isCompanyCode(x.issuer)) continue;
      const orig = x.expires_at === null ? null : new Date(x.expires_at);
      allocs.push({ expiresAt: orig === null || orig.getTime() > now.getTime() ? orig : expiryFor(now.getTime(), this.config.loyaltyPointMaxAgeDays), points: take, issuer: x.issuer });
      left -= take;
    }
    if (left > 0) allocs.push({ expiresAt: expiryFor(now.getTime(), this.config.loyaltyPointMaxAgeDays), points: left, issuer: row.company_code as CompanyCode });
    const enc = await this.encFor(q, null, row.member_hash);
    await this.write(q, allocs, `${ONLINE_REASON.RESTORE}:${refund.id}`, (a) => ({
      memberHash: row.member_hash,
      memberRefEnc: enc,
      delta: a.points,
      reason: ONLINE_REASON.RESTORE,
      source: ONLINE_SOURCE,
      sourceRef: part.id,
      companyCode: row.company_code as CompanyCode,
      expiresAt: a.expiresAt,
      issuerCompanyCode: a.issuer ?? (row.company_code as CompanyCode),
    }));
    await r.updatePart(part.id, { points_restored_value: restoredBefore + capped });
    part.points_restored_value = restoredBefore + capped;
    if (isCompanyCode(row.company_code)) this.metrics.recordLoyaltyPosPoints('restored', row.company_code, points);
  }

  async earnOnPickup(r: OnlineRepo, order: OrderRow, part: PartRow, eligibleAmount: number): Promise<void> {
    if ((part.earn_status ?? 'none') !== 'none' && part.earn_status !== 'pending') return;
    if (!this.config.onlineLoyaltyEarnEnabled || !order.member_id || !isCompanyCode(part.company_code)) {
      await r.updatePart(part.id, { earn_status: 'skipped', eligible_amount: eligibleAmount });
      return;
    }
    const q = sqlOf(r);
    const m = await this.loyalty.ledgerEncForMember(q, order.member_id);
    if (!m) {
      await r.updatePart(part.id, { earn_status: 'skipped', eligible_amount: eligibleAmount });
      return;
    }
    const p = await this.params(part.company_code);
    const points = Math.floor(eligibleAmount / p.earnIdrPerPoint + EPS);
    await this.loyalty.lockMember(q, m.hash);
    const now = new Date(this.now());
    if (points > 0) {
      let limited = points > p.maxPointsPerOrder;
      if (!limited && p.dailyMaxPointsMember > 0) {
        const wibStart = new Date(Math.floor((now.getTime() + 7 * 3600_000) / 86_400_000) * 86_400_000 - 7 * 3600_000);
        const today = await q.query<{ earned: string | number | null }>(
          `SELECT COALESCE(SUM(delta_points), 0)::bigint AS earned FROM platform.loyalty_ledger
            WHERE member_ref_hash = $1 AND source = 'online' AND reason = 'online_earn' AND created_at >= $2`,
          [m.hash, wibStart.toISOString()],
        );
        limited = Number(today.rows[0]?.earned ?? 0) + points > p.dailyMaxPointsMember;
      }
      if (limited) {
        reportLoyaltyAnomaly(this.logger, this.metrics, 'online_earn_limited', { order_ref: part.id, company: part.company_code, points });
        await r.updatePart(part.id, { earn_status: 'limited', eligible_amount: eligibleAmount });
        return;
      }
      const allocs = splitCredit(await this.loyalty.buckets(q, m.hash, now), points, expiryFor(now.getTime(), this.config.loyaltyPointMaxAgeDays), part.company_code);
      await this.write(q, allocs, `${ONLINE_REASON.EARN}:${part.id}`, (a) => ({
        memberHash: m.hash,
        memberRefEnc: m.enc,
        delta: a.points,
        reason: ONLINE_REASON.EARN,
        source: ONLINE_SOURCE,
        sourceRef: part.id,
        companyCode: part.company_code as CompanyCode,
        expiresAt: a.expiresAt,
        issuerCompanyCode: a.issuer ?? (part.company_code as CompanyCode),
      }));
      this.metrics.recordLoyaltyPosPoints('earned', part.company_code, points);
    }
    await touchActivity(q, m.hash, 'online_earn', now);
    // G31 §20.3: sumber akrual MGM (basis = eligible saat diambil; no-op bila MGM mati / tanpa referrer)
    if (this.mgm) {
      await this.mgm.recordSource(q, { kind: 'online', ref: part.id, company: part.company_code as CompanyCode, buyerHash: m.hash, basisIdr: eligibleAmount, occurredAt: now });
    }
    await r.updatePart(part.id, { earn_status: 'earned', points_earned: points, eligible_amount: eligibleAmount });
    part.earn_status = 'earned';
    part.points_earned = points;
    part.eligible_amount = eligibleAmount;
  }

  async clawbackOnRefund(r: OnlineRepo, part: PartRow, key: string, eligibleRefunded: number): Promise<void> {
    // refund sebelum diambil → tidak ada clawback (eligible saat picked_up sudah neto)
    if (part.earn_status !== 'earned' || !part.eligible_amount || eligibleRefunded <= 0) return;
    const q = sqlOf(r);
    // G31 §20.4: retur sumber MGM (juga bila earn 0 poin; tidak bergantung MGM_ENABLED)
    if (this.mgm) {
      await this.mgm.recordReversal(q, { kind: 'online', ref: part.id, reverseKey: key, ratio: Math.min(1, eligibleRefunded / part.eligible_amount) });
    }
    if ((part.points_earned ?? 0) <= 0) return;
    const rows = await q.query<{ member_ref_hash: string; delta_points: number; reason: string; expires_at: Date | string | null; issuer: string; idempotency_key: string }>(
      `SELECT member_ref_hash, delta_points, reason, expires_at, COALESCE(issuer_company_code, company_code) AS issuer, idempotency_key
         FROM platform.loyalty_ledger WHERE source = 'online' AND source_ref = $1 AND reason IN ('online_earn', 'online_reverse') ORDER BY id`,
      [part.id],
    );
    if (rows.rows.some((x) => x.idempotency_key === key || x.idempotency_key.startsWith(`${key}#`))) return; // replay
    const earned = rows.rows.filter((x) => x.reason === ONLINE_REASON.EARN).reduce((s, x) => s + Number(x.delta_points), 0);
    const prior = -rows.rows.filter((x) => x.reason === ONLINE_REASON.REVERSE).reduce((s, x) => s + Number(x.delta_points), 0);
    const hash = rows.rows[0]?.member_ref_hash;
    if (!hash || earned <= 0) return;
    // rasio ≤ 1, pembulatan ke atas (memihak PT, pola §9.4), dibatasi sisa earn
    const ratio = Math.min(1, eligibleRefunded / part.eligible_amount);
    const claw = Math.max(0, Math.min(earned - prior, Math.ceil(earned * ratio - EPS)));
    if (claw <= 0) return;
    await this.loyalty.lockMember(q, hash);
    const now = new Date(this.now());
    const buckets: LedgerBucket[] = await this.loyalty.buckets(q, hash, now);
    const earnLot = rows.rows.find((x) => x.reason === ONLINE_REASON.EARN);
    const issuer = earnLot && isCompanyCode(earnLot.issuer) ? earnLot.issuer : (part.company_code as CompanyCode);
    const target = earnLot?.expires_at ? new Date(earnLot.expires_at) : null;
    const remaining = buckets.find((b) => (b.expiresAt?.getTime() ?? null) === (target?.getTime() ?? null) && b.issuer === issuer)?.remaining ?? 0;
    const fromLot = target === null ? claw : Math.min(claw, Math.max(0, remaining));
    const allocs: Allocation[] = [];
    if (fromLot > 0) allocs.push({ expiresAt: target, points: fromLot, issuer });
    if (claw - fromLot > 0) allocs.push({ expiresAt: null, points: claw - fromLot, issuer }); // kekurangan → utang
    const enc = await this.encFor(q, null, hash);
    await this.write(q, allocs, key, (a) => ({
      memberHash: hash,
      memberRefEnc: enc,
      delta: -a.points,
      reason: ONLINE_REASON.REVERSE,
      source: ONLINE_SOURCE,
      sourceRef: part.id,
      companyCode: part.company_code as CompanyCode,
      expiresAt: a.expiresAt,
      issuerCompanyCode: a.issuer ?? issuer,
    }));
    if (isCompanyCode(part.company_code)) this.metrics.recordLoyaltyPosPoints('clawed_back', part.company_code, claw);
  }

  /** member_ref_enc (AAD ledger): dari member aktif, atau salinan ciphertext entri ledger terbaru subjek. */
  private async encFor(q: Queryable, memberId: string | null, hash: string): Promise<Buffer> {
    if (memberId) {
      const m = await this.loyalty.ledgerEncForMember(q, memberId);
      if (m && m.hash === hash) return m.enc;
    }
    const r = await q.query<{ member_ref_enc: Buffer }>(
      `SELECT member_ref_enc FROM platform.loyalty_ledger WHERE member_ref_hash = $1 AND member_ref_enc IS NOT NULL ORDER BY id DESC LIMIT 1`,
      [hash],
    );
    const enc = r.rows[0]?.member_ref_enc;
    if (!enc) throw new Error('member_ref_enc subjek tidak tersedia');
    return enc;
  }

  private async write(q: Queryable, allocs: Allocation[], key: string, build: (a: Allocation) => Omit<LedgerEntryInput, 'idempotencyKey'>): Promise<void> {
    let i = 0;
    for (const a of allocs) {
      if (a.points <= 0) continue;
      const entry: LedgerEntryInput = { ...build(a), idempotencyKey: i === 0 ? key : `${key}#${i + 1}` };
      i += 1;
      const inserted = await this.loyalty.insertEntry(entry, q);
      if (!inserted && i === 1) return; // replay: entri pertama sudah ada → idempoten
    }
  }
}
