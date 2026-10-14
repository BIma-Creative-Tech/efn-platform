import { Inject, Injectable, Logger } from '@nestjs/common';
import { randomUUID } from 'node:crypto';
import { AppConfig } from '../config/app-config';
import { DB, Db, Queryable } from '../common/db/db';
import { ApiError, Errors } from '../common/errors/api-error';
import { LoyaltyPosOp, MetricsService } from '../common/metrics/metrics.service';
import { RateLimiter } from '../common/rate-limit/rate-limiter';
import { safeEqualHex } from '../common/security/safe-equal';
import {
  Allocation,
  CompanyCode,
  LedgerBucket,
  LedgerEntryInput,
  LoyaltyService,
  allocateDebit,
  isCompanyCode,
  splitCredit,
} from '../loyalty/loyalty.service';
import { LoyaltyAnomalyKind, reportLoyaltyAnomaly } from '../loyalty/loyalty-anomaly';
import { DiscountTokenService, amountWithinBounds } from './discount-token.service';
import { PosEarnDto, PosReverseDto, RedeemCommitDto, RedeemReserveDto } from './dto/pos-loyalty.dto';

export interface PosEarnResponse {
  points_earned: number;
  points_balance: number;
}
export interface RedeemReserveResponse {
  reservation_id: string;
  points: number;
  value_idr: number;
  /** TTL UI (15 menit) — K-3: hanya informasi kasir. */
  expires_at: string;
  /** K-3: batas commit / penahanan poin (default +24 jam). Field tambahan (non-breaking). */
  hold_until: string;
}
export interface RedeemCommitResponse {
  points_redeemed: number;
  points_balance: number;
}
export interface PosReverseResponse {
  points_clawed_back: number;
  points_restored: number;
  points_balance: number;
}
export interface LoyaltyParamsResponse {
  earn_idr_per_point: number;
  point_value_idr: number;
  redeem_min_points: number;
  redeem_max_pct_of_order: number;
  point_expiry_days: number;
  reservation_ttl_seconds: number;
  reservation_commit_window_seconds: number;
  /** G18 K-18-4: Odoo menerapkan faktor porsi uang pada `eligible_amount` bila true (`LOYALTY_EARN_EXCLUDE_POINT_PAYMENT`). */
  earn_exclude_point_payment: boolean;
}

/** Sumber entri ledger Fase 1 (§9.5). */
export const POS_SOURCES = { EARN: 'pos_earn', REDEEM: 'pos_redeem', REVERSE: 'pos_reverse' } as const;
export const REVERSE_REASON = { CLAWBACK: 'pos_refund_clawback', RESTORE: 'pos_refund_restore' } as const;
/** Toleransi galat floating point pada perhitungan proporsional (mis. 0.1 × 30 = 3.0000000000000004). */
const EPS = 1e-9;

interface ReservationRow {
  id: string;
  member_hash: string;
  company_code: string;
  order_ref: string;
  points: number;
  value_idr: string | number;
  status: 'reserved' | 'committed' | 'released' | 'expired';
  expires_at: Date | string;
  hold_until: Date | string;
  commit_idempotency_key: string | null;
}

interface LedgerRow {
  member_ref_hash: string;
  company_code: string;
  delta_points: number;
  expires_at: Date | string | null;
  reason: string;
  source: string;
  source_ref: string | null;
  idempotency_key: string;
}

interface EarnRecordRow {
  order_ref: string;
  company_code?: string;
  idempotency_key: string;
  member_hash: string;
  points_earned: number;
}

/** Error kontrak loyalty POS (format umum §3: `{ error: { code, message } }`). */
const LoyaltyErrors = {
  insufficientPoints: (): ApiError => Errors.conflict('insufficient_points', 'Poin tidak mencukupi.'),
  belowMinimum: (): ApiError => Errors.conflict('below_minimum', 'Jumlah poin di bawah minimum penukaran.'),
  exceedsMaxPct: (): ApiError => Errors.conflict('exceeds_max_pct', 'Nilai poin melebihi batas maksimum per transaksi.'),
  tokenInvalid: (): ApiError => Errors.conflict('token_invalid', 'Verifikasi OTP tidak valid atau kedaluwarsa. Minta OTP baru.'),
  memberMismatch: (): ApiError => Errors.conflict('member_mismatch', 'Pelanggan tidak sesuai dengan verifikasi OTP.'),
  amountMismatch: (): ApiError => Errors.conflict('amount_mismatch', 'Total transaksi berubah terlalu jauh sejak OTP. Minta OTP baru.'),
  valueMismatch: (): ApiError => Errors.conflict('value_mismatch', 'Nilai poin tidak sesuai dengan reservasi.'),
  restoreValueMismatch: (): ApiError =>
    Errors.conflict('restore_value_mismatch', 'Nilai pengembalian poin tidak sesuai dengan penukaran asal. Perlu ditinjau.'),
  paymentMismatch: (): ApiError => Errors.conflict('payment_mismatch', 'Nilai pembayaran poin tidak sesuai dengan reservasi.'),
  reservationExists: (): ApiError => Errors.conflict('reservation_exists', 'Transaksi ini sudah memiliki penukaran poin aktif.'),
  reservationNotFound: (): ApiError => ApiError.of(404, 'reservation_not_found', 'Reservasi poin tidak ditemukan.'),
  reservationReleased: (): ApiError => Errors.conflict('reservation_released', 'Reservasi poin sudah dibatalkan.'),
  reservationExpired: (): ApiError => Errors.conflict('reservation_expired', 'Reservasi poin sudah kedaluwarsa.'),
  alreadyCommitted: (): ApiError => Errors.conflict('already_committed', 'Reservasi poin sudah dipakai.'),
  orderMismatch: (): ApiError => Errors.conflict('order_mismatch', 'Transaksi tidak sesuai dengan reservasi.'),
  idempotencyConflict: (): ApiError => Errors.conflict('idempotency_conflict', 'Kunci idempotensi sudah dipakai untuk data lain.'),
  earnPending: (): ApiError => Errors.conflict('earn_pending', 'Poin transaksi asal belum tercatat. Coba lagi nanti.'),
  earnLimitExceeded: (): ApiError => Errors.conflict('earn_limit_exceeded', 'Poin transaksi melebihi batas. Hubungi admin.'),
};

const DAY_MS = 86_400_000;
const WIB_OFFSET_MS = 7 * 3600 * 1000;

/** Awal hari kalender WIB (00:00 WIB) yang memuat `nowMs`. */
export function startOfWibDay(nowMs: number): Date {
  return new Date(Math.floor((nowMs + WIB_OFFSET_MS) / DAY_MS) * DAY_MS - WIB_OFFSET_MS);
}

/** 00:00 WIB setelah `days` hari penuh sejak `nowMs`; `days` = 0 → null (tanpa kedaluwarsa). */
export function expiryFor(nowMs: number, days: number): Date | null {
  if (days <= 0) return null;
  const startOfDayWib = Math.floor((nowMs + WIB_OFFSET_MS) / DAY_MS) * DAY_MS - WIB_OFFSET_MS;
  return new Date(startOfDayWib + (days + 1) * DAY_MS);
}

function isUniqueViolation(err: unknown): boolean {
  return typeof err === 'object' && err !== null && (err as { code?: unknown }).code === '23505';
}

function toDate(v: Date | string): Date {
  return v instanceof Date ? v : new Date(v);
}

function errorCode(err: unknown): string {
  if (err instanceof ApiError) {
    const body = err.body as { error?: { code?: string } };
    return body.error?.code ?? 'error';
  }
  return 'error';
}

/**
 * Loyalty di POS — Fase 1 (docs/04 §9, §9.6 v4.1; ADR-017, ADR-020). Dipanggil server Odoo (service key,
 * `/v1/pos/loyalty/*`).
 *
 * - **Earn** (§9.2, K-1): poin = floor(eligible_amount / LOYALTY_EARN_IDR_PER_POINT) (pembulatan ke bawah; eligible
 *   dihitung Odoo, termasuk pengecualian kategori — K-2). Masa berlaku LOYALTY_POINT_EXPIRY_DAYS (0 = tanpa
 *   kedaluwarsa); bila member berutang, bagian kredit melunasi utang dulu (`splitCredit`). Setiap earn (juga 0 poin)
 *   dicatat di `platform.loyalty_pos_earn` (order_ref PK) — dasar `earn_pending` (K-6) dan idempotensi.
 * - **Reserve** (§9.3): token `pos_redeem` (bukan `pos_promo`), DIKONSUMSI saat reserve (K-4: UNIQUE token_jti; retry
 *   identik mengembalikan reservasi yang sama), order & member sama dengan klaim token, `amount_total` dalam batas
 *   SEC-07, poin ≥ minimum, nilai ≤ max% × amount_total, poin ≤ saldo − reservasi aktif. Serialisasi per member
 *   (`pg_advisory_xact_lock`): dua reservasi paralel yang bersama-sama melebihi saldo → satu `insufficient_points`.
 * - **Commit** (K-3): body membawa member_ref & value_idr yang diverifikasi; reservasi `reserved` boleh di-commit
 *   sampai `hold_until` (default 24 jam; `expires_at` 15 menit hanya UI — commit setelahnya dicatat `outcome="late"`).
 *   Entri ledger negatif dialokasikan FIFO (`allocateDebit`), idempoten (status + `commit_idempotency_key`).
 * - **Release** (K-5): idempoten; committed → 409 `already_committed`.
 * - **G18 §9.9 v6 (poin = alat bayar)**: reserve `amount_total` integer incl pajak (K-18-2); commit wajib
 *   `payment_amount` = `value_idr` reservasi (K-18-3, beda → 409 `payment_mismatch` + anomali critical); earn dengan
 *   `LOYALTY_EARN_EXCLUDE_POINT_PAYMENT` dipagari porsi uang (K-18-4); reverse `points_restored_value` (K-18-5).
 * - **Reverse** (§9.4): catatan earn order asal belum ada → 409 `earn_pending` (K-6). Restore redeem floor(R × ratio)
 *   lalu clawback earn ceil(E × ratio) (pembulatan memihak PT, kumulatif dibatasi total asal; ratio = 1 → sisa penuh).
 *   Clawback melebihi sisa bucket → utang (bucket NULL). Restore ke bucket asal; bucket kedaluwarsa → masa berlaku baru.
 *
 * Saldo yang dikembalikan = saldo ledger (belum dikurangi reservasi aktif), sama dengan `POST /v1/pos/loyalty/balance`.
 */
@Injectable()
export class PosLoyaltyService {
  private readonly logger = new Logger(PosLoyaltyService.name);
  now: () => number = () => Date.now();

  constructor(
    private readonly config: AppConfig,
    @Inject(DB) private readonly db: Db,
    private readonly loyalty: LoyaltyService,
    private readonly tokens: DiscountTokenService,
    private readonly limiter: RateLimiter,
    private readonly metrics: MetricsService,
  ) {}

  params(): LoyaltyParamsResponse {
    return {
      earn_idr_per_point: this.config.loyaltyEarnIdrPerPoint,
      point_value_idr: this.config.loyaltyPointValueIdr,
      redeem_min_points: this.config.loyaltyRedeemMinPoints,
      redeem_max_pct_of_order: this.config.loyaltyRedeemMaxPctOfOrder,
      point_expiry_days: this.config.loyaltyPointExpiryDays,
      reservation_ttl_seconds: this.config.loyaltyReservationTtlSeconds,
      reservation_commit_window_seconds: this.config.loyaltyReservationCommitWindowSeconds,
      earn_exclude_point_payment: this.config.loyaltyEarnExcludePointPayment,
    };
  }

  /** Poin earn untuk `eligible` Rupiah — pembulatan ke bawah. */
  pointsFor(eligibleAmount: number): number {
    return Math.floor(eligibleAmount / this.config.loyaltyEarnIdrPerPoint + EPS);
  }

  // ───────────────────────── Earn (§9.2) ─────────────────────────
  async earn(dto: PosEarnDto): Promise<PosEarnResponse> {
    return this.observe('earn', async () => {
      const hash = this.loyalty.hashMember(dto.memberRef);
      await this.rateLimit('earn', hash);
      const points = this.pointsFor(dto.eligibleAmount);
      return this.db.tx(async (q) => {
        await this.loyalty.lockMember(q, hash);
        const existing = await q.query<EarnRecordRow>(
          `SELECT order_ref, idempotency_key, member_hash, points_earned
             FROM platform.loyalty_pos_earn WHERE order_ref = $1 OR idempotency_key = $2`,
          [dto.orderRef, dto.idempotencyKey],
        );
        if (existing.rows.length > 0) {
          const consistent = existing.rows.every(
            (r) => r.order_ref === dto.orderRef && r.idempotency_key === dto.idempotencyKey && r.member_hash === hash,
          );
          if (!consistent) throw LoyaltyErrors.idempotencyConflict();
          return {
            result: { points_earned: Number(existing.rows[0].points_earned), points_balance: await this.loyalty.balanceByHash(hash, q) },
            outcome: 'replay',
          };
        }
        // SEC-61: pagar batas SETELAH cek replay (retry earn yang dulu diterima tetap 200 walau batas diturunkan)
        if (points > 0) await this.enforceMoneyPortion(q, dto, points);
        if (points > 0) await this.enforceEarnLimits(q, hash, dto, points);
        await q.query(
          `INSERT INTO platform.loyalty_pos_earn
             (order_ref, idempotency_key, member_hash, company_code, eligible_amount, points_earned, created_at)
           VALUES ($1, $2, $3, $4, $5, $6, $7)`,
          [dto.orderRef, dto.idempotencyKey, hash, dto.companyCode, dto.eligibleAmount, points, new Date(this.now()).toISOString()],
        );
        if (points > 0) {
          const parts = splitCredit(await this.loyalty.buckets(q, hash, new Date(this.now())), points, this.earnExpiry());
          const enc = this.loyalty.encryptMemberRef(dto.memberRef);
          await this.writeParts(q, parts, (part, i) => ({
            idempotencyKey: i === 0 ? dto.idempotencyKey : `${dto.idempotencyKey}#${i + 1}`,
            memberHash: hash,
            memberRefEnc: enc,
            delta: part.points,
            reason: 'pos_sale',
            source: POS_SOURCES.EARN,
            sourceRef: dto.orderRef,
            companyCode: dto.companyCode,
            expiresAt: part.expiresAt,
          }));
          this.metrics.recordLoyaltyPosPoints('earned', dto.companyCode, points);
        }
        return {
          result: { points_earned: points, points_balance: await this.loyalty.balanceByHash(hash, q) },
          outcome: points > 0 ? 'ok' : 'zero',
        };
      });
    });
  }

  // ───────────────────────── Reserve (§9.3 langkah 2) ─────────────────────────
  async reserve(dto: RedeemReserveDto): Promise<RedeemReserveResponse> {
    return this.observe('reserve', async () => {
      const hash = this.loyalty.hashMember(dto.memberRef);
      await this.rateLimit('reserve', hash);

      const verified = this.tokens.verifyClaims(dto.discountToken, 'pos_redeem');
      if (!verified.ok) throw LoyaltyErrors.tokenInvalid();
      const claims = verified.claims;
      if (claims.orderRef !== dto.orderRef || !isCompanyCode(claims.companyCode)) throw LoyaltyErrors.tokenInvalid();
      if (!safeEqualHex(claims.memberHash, hash)) throw LoyaltyErrors.memberMismatch();
      if (!amountWithinBounds(claims.amount, dto.amountTotal, this.config.posOtpAmountTolerancePct)) {
        throw LoyaltyErrors.amountMismatch();
      }
      const companyCode: CompanyCode = claims.companyCode;
      if (dto.points < this.config.loyaltyRedeemMinPoints) throw LoyaltyErrors.belowMinimum();
      const valueIdr = dto.points * this.config.loyaltyPointValueIdr;
      // nilai poin ≤ pct% × amount_total (perbandingan perkalian, bukan pembagian)
      if (valueIdr * 100 > dto.amountTotal * this.config.loyaltyRedeemMaxPctOfOrder + EPS) throw LoyaltyErrors.exceedsMaxPct();

      const now = new Date(this.now());
      try {
        return await this.db.tx(async (q) => {
          await this.loyalty.lockMember(q, hash);
          // Reservasi order ini yang melewati hold_until tetapi belum disapu job → expired (agar unik per order tidak menghalangi)
          await q.query(
            `UPDATE platform.loyalty_reservation SET status = 'expired', updated_at = $2
              WHERE order_ref = $1 AND status = 'reserved' AND hold_until <= $2`,
            [dto.orderRef, now.toISOString()],
          );
          // K-4: token sekali pakai. Retry identik (respons hilang) → reservasi yang sama selama masih reserved.
          const byToken = await q.query<ReservationRow>(
            `SELECT id::text AS id, member_hash, order_ref, points, value_idr, status, expires_at, hold_until
               FROM platform.loyalty_reservation WHERE token_jti = $1`,
            [claims.jti],
          );
          const prev = byToken.rows[0];
          if (prev) {
            if (prev.status === 'reserved' && prev.order_ref === dto.orderRef && Number(prev.points) === dto.points) {
              return {
                result: this.reserveResponse(prev.id, dto.points, Number(prev.value_idr), toDate(prev.expires_at), toDate(prev.hold_until)),
                outcome: 'replay',
              };
            }
            throw LoyaltyErrors.tokenInvalid();
          }
          const active = await q.query<{ n: number }>(
            `SELECT 1 AS n FROM platform.loyalty_reservation WHERE order_ref = $1 AND status IN ('reserved', 'committed')`,
            [dto.orderRef],
          );
          if ((active.rowCount ?? 0) > 0) throw LoyaltyErrors.reservationExists();

          const available = await this.available(q, hash, now);
          if (available < dto.points) throw LoyaltyErrors.insufficientPoints();

          const id = randomUUID();
          const expiresAt = new Date(now.getTime() + this.config.loyaltyReservationTtlSeconds * 1000);
          const holdUntil = new Date(
            Math.max(expiresAt.getTime(), now.getTime() + this.config.loyaltyReservationCommitWindowSeconds * 1000),
          );
          await q.query(
            `INSERT INTO platform.loyalty_reservation
               (id, member_hash, company_code, order_ref, token_jti, points, value_idr, amount_total,
                status, expires_at, hold_until, created_at, updated_at)
             VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'reserved', $9, $10, $11, $11)`,
            [
              id,
              hash,
              companyCode,
              dto.orderRef,
              claims.jti,
              dto.points,
              valueIdr,
              dto.amountTotal,
              expiresAt.toISOString(),
              holdUntil.toISOString(),
              now.toISOString(),
            ],
          );
          this.metrics.recordLoyaltyPosPoints('reserved', companyCode, dto.points);
          return { result: this.reserveResponse(id, dto.points, valueIdr, expiresAt, holdUntil), outcome: 'ok' };
        });
      } catch (err) {
        // Balapan: token sama paralel / dua reservasi order sama dari member berbeda → indeks unik menolak satu
        if (isUniqueViolation(err)) {
          const constraint = (err as { constraint?: string }).constraint ?? '';
          throw constraint.includes('jti') ? LoyaltyErrors.tokenInvalid() : LoyaltyErrors.reservationExists();
        }
        throw err;
      }
    });
  }

  // ───────────────────────── Commit (§9.3 langkah 3, K-3) ─────────────────────────
  async commit(dto: RedeemCommitDto): Promise<RedeemCommitResponse> {
    return this.observe('commit', async () => {
      const hash = this.loyalty.hashMember(dto.memberRef);
      try {
        return await this.db.tx(async (q) => {
          // Urutan kunci sama dengan reserve (member → baris) agar tidak deadlock
          await this.loyalty.lockMember(q, hash);
          const res = await q.query<ReservationRow>(
            `SELECT id::text AS id, member_hash, company_code, order_ref, points, value_idr, status, expires_at, hold_until,
                    commit_idempotency_key
               FROM platform.loyalty_reservation WHERE id = $1 FOR UPDATE`,
            [dto.reservationId],
          );
          const r = res.rows[0];
          if (!r) throw LoyaltyErrors.reservationNotFound();
          if (r.order_ref !== dto.orderRef) throw LoyaltyErrors.orderMismatch();
          if (!safeEqualHex(r.member_hash, hash)) throw LoyaltyErrors.memberMismatch();
          if (Number(r.value_idr) !== dto.valueIdr) throw LoyaltyErrors.valueMismatch();
          // K-18-3: baris pembayaran Poin EFN harus PERSIS nilai reservasi (toleransi 0); beda → tolak + anomali (Odoo hold)
          if (dto.paymentAmount !== Number(r.value_idr)) {
            reportLoyaltyAnomaly(this.logger, this.metrics, 'commit_payment_mismatch', {
              order_ref: r.order_ref,
              company: r.company_code,
              payment_amount: dto.paymentAmount,
              value_idr: Number(r.value_idr),
            });
            throw LoyaltyErrors.paymentMismatch();
          }
          // SEC-128: total order final harus ≥ nilai poin yang dibayarkan (penyebut alokasi restore)
          if (dto.orderGoodsIncl !== null && dto.orderGoodsIncl < dto.paymentAmount) {
            reportLoyaltyAnomaly(this.logger, this.metrics, 'commit_payment_mismatch', {
              order_ref: r.order_ref,
              company: r.company_code,
              payment_amount: dto.paymentAmount,
              order_goods_incl: dto.orderGoodsIncl,
            });
            throw LoyaltyErrors.paymentMismatch();
          }
          const points = Number(r.points);

          if (r.status === 'committed') {
            if (r.commit_idempotency_key !== dto.idempotencyKey) throw LoyaltyErrors.idempotencyConflict();
            return {
              result: { points_redeemed: points, points_balance: await this.loyalty.balanceByHash(hash, q) },
              outcome: 'replay',
            };
          }
          if (r.status === 'released') throw LoyaltyErrors.reservationReleased();
          const now = new Date(this.now());
          if (r.status === 'expired' || toDate(r.hold_until).getTime() <= now.getTime()) throw LoyaltyErrors.reservationExpired();
          const late = toDate(r.expires_at).getTime() <= now.getTime();

          if ((await this.ledgerByKeys(q, [dto.idempotencyKey])).length > 0) throw LoyaltyErrors.idempotencyConflict();
          if (!isCompanyCode(r.company_code)) throw new Error('company_code reservasi tidak dikenal');
          const company: CompanyCode = r.company_code;
          const enc = this.loyalty.encryptMemberRef(dto.memberRef);
          const parts = allocateDebit(await this.loyalty.buckets(q, hash, now), points);
          await this.writeParts(q, parts, (part, i) => ({
            idempotencyKey: i === 0 ? dto.idempotencyKey : `${dto.idempotencyKey}#${i + 1}`,
            memberHash: hash,
            memberRefEnc: enc,
            delta: -part.points,
            reason: 'pos_redeem',
            source: POS_SOURCES.REDEEM,
            sourceRef: r.order_ref,
            companyCode: company,
            expiresAt: part.expiresAt,
          }));
          await q.query(
            `UPDATE platform.loyalty_reservation
                SET status = 'committed', committed_at = $2, commit_idempotency_key = $3, updated_at = $2,
                    order_goods_incl = $4
              WHERE id = $1`,
            [r.id, now.toISOString(), dto.idempotencyKey, dto.orderGoodsIncl],
          );
          const balance = await this.loyalty.balanceByHash(hash, q);
          if (balance < 0) this.logger.warn(`commit redeem order ${r.order_ref}: saldo member negatif (${balance})`);
          this.metrics.recordLoyaltyPosPoints('redeemed', company, points);
          return { result: { points_redeemed: points, points_balance: balance }, outcome: late ? 'late' : 'ok' };
        });
      } catch (err) {
        if (isUniqueViolation(err)) throw LoyaltyErrors.idempotencyConflict();
        throw err;
      }
    });
  }

  // ───────────────────────── Release (§9.3 langkah 4, K-5) ─────────────────────────
  async release(reservationId: string): Promise<{ ok: true }> {
    return this.observe('release', async () => {
      const now = new Date(this.now()).toISOString();
      const upd = await this.db.query<{ id: string }>(
        `UPDATE platform.loyalty_reservation SET status = 'released', updated_at = $2
          WHERE id = $1 AND status IN ('reserved', 'expired')
          RETURNING id::text AS id`,
        [reservationId, now],
      );
      if ((upd.rowCount ?? 0) > 0) return { result: { ok: true as const }, outcome: 'ok' };
      const cur = await this.db.query<{ status: string }>(`SELECT status FROM platform.loyalty_reservation WHERE id = $1`, [
        reservationId,
      ]);
      const status = cur.rows[0]?.status;
      if (status === undefined) throw LoyaltyErrors.reservationNotFound();
      if (status === 'committed') throw LoyaltyErrors.alreadyCommitted();
      return { result: { ok: true as const }, outcome: 'replay' }; // sudah released
    });
  }

  // ───────────────────────── Reverse (§9.4, K-6) ─────────────────────────
  async reverse(dto: PosReverseDto): Promise<PosReverseResponse> {
    return this.observe('reverse', async () => {
      const hash = this.loyalty.hashMember(dto.memberRef);
      await this.rateLimit('reverse', hash);
      const clawKey = (i: number): string => `${dto.idempotencyKey}:claw${i === 0 ? '' : `#${i + 1}`}`;
      const restoreKey = (i: number): string => `${dto.idempotencyKey}:restore#${i + 1}`;

      return this.db.tx(async (q) => {
        await this.loyalty.lockMember(q, hash);
        // Replay: entri reverse ini (kunci `<key>:claw…` / `<key>:restore#n`), dicari lewat indeks (source, source_ref)
        // + kunci pertama tiap jenis lewat indeks unik (retry dengan order asal lain → terdeteksi konflik). Kunci
        // deterministik + UNIQUE menjadi pagar terakhir (writeParts → idempotency_conflict).
        const existing = (
          await q.query<LedgerRow>(
            `SELECT member_ref_hash, company_code, delta_points, expires_at, reason, source, source_ref, idempotency_key
               FROM platform.loyalty_ledger
              WHERE (source = 'pos_reverse' AND source_ref = $2 AND starts_with(idempotency_key, $1))
                 OR idempotency_key = ANY($3::text[])`,
            [`${dto.idempotencyKey}:`, dto.originalOrderRef, [clawKey(0), restoreKey(0)]],
          )
        ).rows;
        if (existing.length > 0) {
          const consistent = existing.every(
            (r) => r.member_ref_hash === hash && r.source === POS_SOURCES.REVERSE && r.source_ref === dto.originalOrderRef,
          );
          if (!consistent) throw LoyaltyErrors.idempotencyConflict();
          // K-18-6: retry dengan points_restored_value berbeda dari yang tercatat → konflik (bukan replay diam-diam)
          if (dto.pointsRestoredValue !== null) {
            const prev = await q.query<{ points_restored_value: string | number }>(
              `SELECT original_order_ref, points_restored_value, idempotency_key FROM platform.loyalty_pos_reverse
                WHERE refund_order_ref = $1 OR idempotency_key = $2`,
              [dto.refundOrderRef, dto.idempotencyKey],
            );
            if (prev.rows.some((r) => Number(r.points_restored_value) !== dto.pointsRestoredValue)) throw LoyaltyErrors.idempotencyConflict();
          }
          return {
            result: {
              points_clawed_back: -this.sumBy(existing, REVERSE_REASON.CLAWBACK),
              points_restored: this.sumBy(existing, REVERSE_REASON.RESTORE),
              points_balance: await this.loyalty.balanceByHash(hash, q),
            },
            outcome: 'replay',
          };
        }

        // K-6: earn order asal harus sudah tercatat (termasuk earn 0 poin) — selain itu Odoo retry via outbox
        const earnRec = await q.query<EarnRecordRow>(
          `SELECT order_ref, idempotency_key, member_hash, company_code, points_earned FROM platform.loyalty_pos_earn WHERE order_ref = $1`,
          [dto.originalOrderRef],
        );
        const rec = earnRec.rows[0];
        if (!rec) throw LoyaltyErrors.earnPending();
        if (!safeEqualHex(rec.member_hash, hash)) throw LoyaltyErrors.memberMismatch();

        const rows = await q.query<LedgerRow>(
          `SELECT member_ref_hash, company_code, delta_points, expires_at, reason, source, source_ref, idempotency_key
             FROM platform.loyalty_ledger
            WHERE source IN ('pos_earn', 'pos_redeem', 'pos_reverse') AND source_ref = $1
            ORDER BY id`,
          [dto.originalOrderRef],
        );
        const earnRows = rows.rows.filter((r) => r.source === POS_SOURCES.EARN);
        const redeemRows = rows.rows.filter((r) => r.source === POS_SOURCES.REDEEM);
        const priorRows = rows.rows.filter((r) => r.source === POS_SOURCES.REVERSE);
        if (![...earnRows, ...redeemRows].every((r) => r.member_ref_hash === hash)) throw LoyaltyErrors.memberMismatch();

        const earned = earnRows.reduce((s, r) => s + Number(r.delta_points), 0);
        const redeemed = -redeemRows.reduce((s, r) => s + Number(r.delta_points), 0);
        const priorClaw = -this.sumBy(priorRows, REVERSE_REASON.CLAWBACK);
        const priorRestore = this.sumBy(priorRows, REVERSE_REASON.RESTORE);
        const full = dto.refundRatio >= 1;
        if (dto.pointsRestoredValue !== null) await this.assertExpectedRestore(q, dto, redeemed);
        const restore = this.restorePoints(dto, redeemed, priorRestore, full);
        const claw = Math.max(0, Math.min(earned - priorClaw, full ? earned : Math.ceil(earned * dto.refundRatio - EPS)));

        const now = new Date(this.now());
        const enc = this.loyalty.encryptMemberRef(dto.memberRef);
        // 1. Restore dulu (clawback berikutnya bisa memakai poin yang baru dikembalikan di bucket yang sama)
        if (restore > 0) {
          const parts = this.restoreAllocation(redeemRows, restore, now);
          const company = this.companyOf(redeemRows);
          await this.writeParts(q, parts, (part, i) => ({
            idempotencyKey: restoreKey(i),
            memberHash: hash,
            memberRefEnc: enc,
            delta: part.points,
            reason: REVERSE_REASON.RESTORE,
            source: POS_SOURCES.REVERSE,
            sourceRef: dto.originalOrderRef,
            companyCode: company,
            expiresAt: part.expiresAt,
          }));
          this.metrics.recordLoyaltyPosPoints('restored', company, restore);
        }
        // 2. Clawback earn ke bucket earn asal; kekurangan → utang (bucket NULL, saldo negatif dicatat)
        if (claw > 0) {
          const parts = this.clawAllocation(earnRows, await this.loyalty.buckets(q, hash, now), claw, now);
          const company = this.companyOf(earnRows);
          await this.writeParts(q, parts, (part, i) => ({
            idempotencyKey: clawKey(i),
            memberHash: hash,
            memberRefEnc: enc,
            delta: -part.points,
            reason: REVERSE_REASON.CLAWBACK,
            source: POS_SOURCES.REVERSE,
            sourceRef: dto.originalOrderRef,
            companyCode: company,
            expiresAt: part.expiresAt,
          }));
          this.metrics.recordLoyaltyPosPoints('clawed_back', company, claw);
        }
        // K-18-5/K-18-6: nilai yang dilaporkan Odoo disimpan persisten (rekonsiliasi harian ETL), transaksi yang sama
        if (dto.pointsRestoredValue !== null) {
          const company = redeemRows[0]?.company_code ?? earnRows[0]?.company_code ?? rec.company_code;
          if (!isCompanyCode(company)) throw new Error('company_code order asal tidak dikenal');
          await this.recordReverse(q, dto, company, restore);
        }
        const balance = await this.loyalty.balanceByHash(hash, q);
        if (balance < 0) {
          this.logger.warn(`reverse refund ${dto.refundOrderRef}: saldo member negatif (${balance}) — ditagih di earn berikutnya`);
        }
        return { result: { points_clawed_back: claw, points_restored: restore, points_balance: balance }, outcome: 'ok' };
      });
    });
  }

  // ───────────────────────── helper ─────────────────────────

  /**
   * SEC-118 (L-25f): restore yang diharapkan dihitung dari data BFF sendiri — nilai redeem order asal V (Σ `value_idr`
   * reservasi committed; tidak ada → poin redeem ledger × nilai poin) × rasio refund kumulatif (Σ `refund_ratio` reverse
   * sebelumnya + rasio ini, ≤ 1) − Σ `points_restored_value` reverse sebelumnya. Selisih dengan nilai Odoo >
   * max(nilai poin, `LOYALTY_RESTORE_TOLERANCE_PCT` % × V) → anomali `restore_value_unexpected` (critical) + 409
   * `restore_value_mismatch` (Odoo menahan outbox untuk review; tidak ada entri yang ditulis).
   */
  private async assertExpectedRestore(q: Queryable, dto: PosReverseDto, redeemedPoints: number): Promise<void> {
    const res = await q.query<{ v: string | number | null; t: string | number | null; t_missing: string | number | null; amt: string | number | null }>(
      `SELECT COALESCE(SUM(value_idr), 0)::bigint AS v, COALESCE(SUM(order_goods_incl), 0)::bigint AS t,
              COUNT(*) FILTER (WHERE order_goods_incl IS NULL) AS t_missing, COALESCE(SUM(amount_total), 0) AS amt
         FROM platform.loyalty_reservation WHERE order_ref = $1 AND status = 'committed'`,
      [dto.originalOrderRef],
    );
    const pv = this.config.loyaltyPointValueIdr;
    const row = res.rows[0];
    const fromReservation = Number(row?.v ?? 0);
    const value = fromReservation > 0 ? fromReservation : redeemedPoints * pv;
    const prior = await q.query<{
      ratio: string | number | null;
      restored: string | number | null;
      goods: string | number | null;
      goods_missing: string | number | null;
    }>(
      `SELECT COALESCE(SUM(refund_ratio), 0) AS ratio, COALESCE(SUM(points_restored_value), 0)::bigint AS restored,
              COALESCE(SUM(refund_goods_incl), 0)::bigint AS goods, COUNT(*) FILTER (WHERE refund_goods_incl IS NULL) AS goods_missing
         FROM platform.loyalty_pos_reverse WHERE original_order_ref = $1 AND refund_order_ref <> $2`,
      [dto.originalOrderRef, dto.refundOrderRef],
    );
    const p = prior.rows[0];
    const priorValue = Number(p?.restored ?? 0);
    const reported = dto.pointsRestoredValue ?? 0;

    let expected: number;
    let tolerance: number;
    let basis: string;
    if (dto.refundGoodsIncl !== null && Number(p?.goods_missing ?? 0) === 0) {
      // SEC-128: rasio NILAI incl pajak (tidak bergantung kategori earn) — rumus sama dengan alokasi Odoo Q-F18-4:
      // round(min(Σ refund incl, T) × V ÷ T) − restore sebelumnya. Penyebut T = total order final saat commit
      // (`order_goods_incl`); belum ada (commit lama) → `amount_total` reservasi (saat reserve, bisa bergeser).
      const exactT = Number(row?.t_missing ?? 1) === 0 && Number(row?.t ?? 0) > 0;
      const total = exactT ? Number(row?.t) : Number(row?.amt ?? 0);
      if (total <= 0) {
        expected = 0;
        tolerance = 1;
      } else {
        const cumulative = Math.min(Number(p?.goods ?? 0) + dto.refundGoodsIncl, total);
        expected = Math.max(0, Math.round((cumulative * value) / total) - priorValue);
        tolerance = exactT
          ? 1 // pembulatan Rupiah (sama dengan toleransi validasi server Odoo)
          : Math.max(pv, Math.ceil((value * this.config.posOtpAmountTolerancePct) / 100));
      }
      basis = exactT ? 'goods_incl' : 'goods_incl_reserve_total';
    } else if (dto.refundRatio > 0) {
      // Event lama (tanpa refund_goods_incl): rasio eligible Odoo, toleransi longgar
      const cumulative = Math.min(1, Number(p?.ratio ?? 0) + dto.refundRatio);
      expected = Math.max(0, Math.round(value * cumulative) - priorValue);
      tolerance = Math.max(pv, Math.ceil((value * this.config.loyaltyRestoreTolerancePct) / 100));
      basis = 'eligible_ratio';
    } else {
      // Event lama, refund hanya barang dikecualikan dari earn (rasio eligible 0): tidak bisa dicek tanpa nilai incl pajak.
      // Tidak ditolak (SEC-128: refund sah); restore tetap dibatasi sisa redeem; dicatat untuk ditinjau.
      if (reported > 0) {
        reportLoyaltyAnomaly(this.logger, this.metrics, 'restore_value_unverified', {
          order_ref: dto.originalOrderRef,
          refund_order_ref: dto.refundOrderRef,
          points_restored_value: reported,
        });
      }
      return;
    }
    if (Math.abs(reported - expected) <= tolerance) return;
    const fields = {
      order_ref: dto.originalOrderRef,
      refund_order_ref: dto.refundOrderRef,
      points_restored_value: reported,
      expected_value: expected,
      tolerance,
      basis,
    };
    if (dto.restoreApproved) {
      // SEC-128: hold/review — persetujuan supervisor Odoo; dibatasi sisa redeem (restorePoints) dan selalu dicatat
      reportLoyaltyAnomaly(this.logger, this.metrics, 'restore_value_override', fields);
      return;
    }
    reportLoyaltyAnomaly(this.logger, this.metrics, 'restore_value_unexpected', fields);
    throw LoyaltyErrors.restoreValueMismatch();
  }

  /**
   * K-18-6: `platform.loyalty_pos_reverse` (append-only, retensi 10 th, tanpa PII). Idempoten per `refund_order_ref` /
   * `idempotency_key`: konflik dengan data sama → no-op; data berbeda → 409 `idempotency_conflict` (rollback).
   */
  private async recordReverse(q: Queryable, dto: PosReverseDto, company: CompanyCode, restored: number): Promise<void> {
    const value = dto.pointsRestoredValue ?? 0;
    const ins = await q.query(
      `INSERT INTO platform.loyalty_pos_reverse
         (refund_order_ref, original_order_ref, company_code, points_restored_value, points_restored, idempotency_key, created_at,
          refund_ratio, refund_goods_incl, restore_approved)
       VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
       ON CONFLICT DO NOTHING`,
      [
        dto.refundOrderRef,
        dto.originalOrderRef,
        company,
        value,
        restored,
        dto.idempotencyKey,
        new Date(this.now()).toISOString(),
        dto.refundRatio,
        dto.refundGoodsIncl,
        dto.restoreApproved,
      ],
    );
    if ((ins.rowCount ?? 0) > 0) return;
    const cur = await q.query<{ original_order_ref: string; points_restored_value: string | number; idempotency_key: string }>(
      `SELECT original_order_ref, points_restored_value, idempotency_key FROM platform.loyalty_pos_reverse
        WHERE refund_order_ref = $1 OR idempotency_key = $2`,
      [dto.refundOrderRef, dto.idempotencyKey],
    );
    const same = cur.rows.every(
      (r) => r.original_order_ref === dto.originalOrderRef && Number(r.points_restored_value) === value && r.idempotency_key === dto.idempotencyKey,
    );
    if (!same) throw LoyaltyErrors.idempotencyConflict();
  }

  /**
   * K-18-5: poin redeem yang dikembalikan. `points_restored_value` ada → floor(nilai ÷ nilai poin) (pembulatan memihak
   * PT; sisa < 1 poin dicatat di `EFN_LOYALTY_RESTORE` untuk rekonsiliasi K-18-6), dibatasi sisa redeem order asal
   * (melebihi → dipotong + anomali `restore_exceeds_redeem`). Tidak ada (event v4) → floor(R × refund_ratio).
   */
  private restorePoints(dto: PosReverseDto, redeemed: number, priorRestore: number, full: boolean): number {
    const left = Math.max(0, redeemed - priorRestore);
    if (dto.pointsRestoredValue === null) {
      return Math.max(0, Math.min(left, full ? redeemed : Math.floor(redeemed * dto.refundRatio + EPS)));
    }
    const pv = this.config.loyaltyPointValueIdr;
    const asked = Math.floor(dto.pointsRestoredValue / pv);
    const remainder = dto.pointsRestoredValue - asked * pv;
    const restore = Math.min(left, asked);
    if (asked > left) {
      reportLoyaltyAnomaly(this.logger, this.metrics, 'restore_exceeds_redeem', {
        order_ref: dto.originalOrderRef,
        refund_order_ref: dto.refundOrderRef,
        points_restored_value: dto.pointsRestoredValue,
        points_asked: asked,
        points_left: left,
      });
    }
    if (dto.pointsRestoredValue > 0) {
      // Tanpa PII: order_ref opak, nilai integer. Dasar rekonsiliasi harian Odoo ↔ BFF (K-18-6).
      this.logger.log(
        `EFN_LOYALTY_RESTORE ${JSON.stringify({
          order_ref: dto.originalOrderRef.replace(/[^A-Za-z0-9_.:-]/g, '').slice(0, 64),
          value_idr: dto.pointsRestoredValue,
          points: restore,
          remainder_idr: remainder + (asked - restore) * pv,
        })}`,
      );
    }
    return restore;
  }

  /**
   * K-18-4 (`LOYALTY_EARN_EXCLUDE_POINT_PAYMENT`=true): order dengan redeem ter-commit — `eligible_amount` (tanpa
   * pajak, sudah dikali faktor porsi uang oleh Odoo) tidak mungkin melebihi porsi uang order
   * `amount_total × (1 + POS_OTP_AMOUNT_TOLERANCE_PCT) − value_idr`. Melebihi → 409 `earn_limit_exceeded` + anomali
   * `earn_exceeds_money_portion`. Pagar kasar (pertahanan berlapis), bukan pengganti perhitungan Odoo.
   */
  private async enforceMoneyPortion(q: Queryable, dto: PosEarnDto, points: number): Promise<void> {
    if (!this.config.loyaltyEarnExcludePointPayment) return;
    // SEC-123 (L-25k): commit redeem order ini belum masuk (mis. sedang retry) → earn ditunda (retryable), agar pagar
    // porsi uang tidak terlewat karena urutan outbox
    const pending = await q.query<{ pending: number }>(
      `SELECT 1 AS pending FROM platform.loyalty_reservation WHERE order_ref = $1 AND status = 'reserved' AND hold_until > $2`,
      [dto.orderRef, new Date(this.now()).toISOString()],
    );
    if ((pending.rowCount ?? 0) > 0) throw LoyaltyErrors.earnPending();
    const res = await q.query<{ value_idr: string | number; amount_total: string | number }>(
      `SELECT value_idr, amount_total FROM platform.loyalty_reservation WHERE order_ref = $1 AND status = 'committed'`,
      [dto.orderRef],
    );
    const r = res.rows[0];
    if (!r) return;
    const value = Number(r.value_idr);
    const total = Number(r.amount_total);
    const bound = Math.floor((total * (100 + this.config.posOtpAmountTolerancePct)) / 100) - value;
    if (dto.eligibleAmount > bound) {
      this.rejectEarn('earn_exceeds_money_portion', {
        company: dto.companyCode,
        order_ref: dto.orderRef,
        points,
        eligible_amount: dto.eligibleAmount,
        money_portion_max: bound,
      });
    }
  }

  /**
   * SEC-61 (Gelombang 13c) — defense in depth karena BFF mempercayai `eligible_amount` dari Odoo (SEC-58: pemanggil
   * service key mana pun). Semua pelanggaran → 409 `earn_limit_exceeded` + baris `EFN_LOYALTY_ANOMALY` + metrik;
   * TIDAK ada catatan earn (Odoo menandai order untuk review; reverse order itu kelak `earn_pending` → review).
   * 1. Plafon per order `LOYALTY_POS_MAX_EARN_POINTS`.
   * 2. Per member per hari WIB `LOYALTY_EARN_DAILY_MAX_POINTS` — Σ `loyalty_pos_earn.points_earned` hari ini, dihitung
   *    di bawah kunci member (pemanggil sudah `lockMember`) → tepat walau paralel.
   * 3. Per PT per jam `LOYALTY_EARN_COMPANY_HOURLY_MAX_POINTS` dan per pos_config per jam
   *    `LOYALTY_EARN_POS_CONFIG_HOURLY_MAX_POINTS` (bila dikirim) — counter Redis berbobot (`RateLimiter.consume`, gagal
   *    dikompensasi). Redis mati → error 5xx (fail closed; Odoo retry). Bila transaksi DB gagal setelah counter
   *    bertambah, counter sedikit lebih tinggi dari kenyataan (memihak keamanan).
   */
  private async enforceEarnLimits(q: Queryable, hash: string, dto: PosEarnDto, points: number): Promise<void> {
    const base = { company: dto.companyCode, order_ref: dto.orderRef, points };
    const cap = this.config.loyaltyPosMaxEarnPoints;
    if (points > cap) this.rejectEarn('earn_limit_order', { ...base, limit: cap });

    const daily = this.config.loyaltyEarnDailyMaxPoints;
    if (daily > 0) {
      const res = await q.query<{ earned: string | number | null }>(
        `SELECT COALESCE(SUM(points_earned), 0)::bigint AS earned
           FROM platform.loyalty_pos_earn WHERE member_hash = $1 AND created_at >= $2`,
        [hash, startOfWibDay(this.now()).toISOString()],
      );
      const today = Number(res.rows[0]?.earned ?? 0);
      if (today + points > daily) this.rejectEarn('earn_limit_member_day', { ...base, earned_today: today, limit: daily });
    }

    const companyLimit = this.config.loyaltyEarnCompanyHourlyMaxPoints;
    const companyKey = `loy:earnpts:co:${dto.companyCode}`;
    if (companyLimit > 0) {
      const r = await this.limiter.consume(companyKey, points, companyLimit, 3600);
      if (!r.allowed) this.rejectEarn('earn_limit_company_hour', { ...base, limit: companyLimit });
    }
    const cfgLimit = this.config.loyaltyEarnPosConfigHourlyMaxPoints;
    if (cfgLimit > 0 && dto.posConfigId !== undefined && dto.posConfigId !== null) {
      const r = await this.limiter.consume(`loy:earnpts:cfg:${dto.companyCode}:${dto.posConfigId}`, points, cfgLimit, 3600);
      if (!r.allowed) {
        // kuota PT yang sudah terpakai oleh percobaan ini dikembalikan
        if (companyLimit > 0) await this.limiter.release(companyKey, points, 3600).catch(() => undefined);
        this.rejectEarn('earn_limit_pos_config_hour', { ...base, pos_config_id: dto.posConfigId, limit: cfgLimit });
      }
    }
  }

  private rejectEarn(kind: LoyaltyAnomalyKind, fields: Record<string, string | number>): never {
    reportLoyaltyAnomaly(this.logger, this.metrics, kind, fields);
    throw LoyaltyErrors.earnLimitExceeded();
  }

  /** Saldo tersedia = saldo ledger − reservasi aktif (status reserved & belum lewat hold_until). */
  private async available(q: Queryable, hash: string, at: Date): Promise<number> {
    const balance = await this.loyalty.balanceByHash(hash, q);
    const held = await q.query<{ held: string | number | null }>(
      `SELECT COALESCE(SUM(points), 0)::bigint AS held
         FROM platform.loyalty_reservation
        WHERE member_hash = $1 AND status = 'reserved' AND hold_until > $2`,
      [hash, at.toISOString()],
    );
    return balance - Number(held.rows[0]?.held ?? 0);
  }

  private async ledgerByKeys(q: Queryable, keys: string[]): Promise<LedgerRow[]> {
    const res = await q.query<LedgerRow>(
      `SELECT member_ref_hash, company_code, delta_points, expires_at, reason, source, source_ref, idempotency_key
         FROM platform.loyalty_ledger WHERE idempotency_key = ANY($1::text[])`,
      [keys],
    );
    return res.rows;
  }

  private async writeParts(q: Queryable, parts: Allocation[], build: (part: Allocation, i: number) => LedgerEntryInput): Promise<void> {
    for (const [i, part] of parts.entries()) {
      if (part.points <= 0) continue;
      const inserted = await this.loyalty.insertEntry(build(part, i), q);
      // Kunci deterministik sudah ada padahal pemeriksaan awal kosong → data tidak konsisten; batalkan transaksi
      if (!inserted) throw LoyaltyErrors.idempotencyConflict();
    }
  }

  /**
   * Restore ke bucket redeem asal, mulai dari bucket yang paling lama berlaku. Bucket asal yang sudah kedaluwarsa →
   * masa berlaku baru (`earnExpiry`) agar poin yang dikembalikan tidak langsung hangus.
   */
  private restoreAllocation(redeemRows: LedgerRow[], points: number, now: Date): Allocation[] {
    const rows = [...redeemRows].sort((a, b) => {
      if (a.expires_at === null) return b.expires_at === null ? 0 : -1;
      if (b.expires_at === null) return 1;
      return toDate(b.expires_at).getTime() - toDate(a.expires_at).getTime();
    });
    const out: Allocation[] = [];
    let left = points;
    for (const r of rows) {
      if (left <= 0) break;
      const take = Math.min(left, -Number(r.delta_points));
      if (take <= 0) continue;
      const orig = r.expires_at === null ? null : toDate(r.expires_at);
      const target = orig === null || orig.getTime() > now.getTime() ? orig : this.earnExpiry();
      const same = out.find((a) => (a.expiresAt?.getTime() ?? null) === (target?.getTime() ?? null));
      if (same) same.points += take;
      else out.push({ expiresAt: target, points: take });
      left -= take;
    }
    if (left > 0) out.push({ expiresAt: this.earnExpiry(), points: left });
    return out;
  }

  /**
   * Clawback ke bucket earn asal (bagian bermasa berlaku). Bucket asal sudah kedaluwarsa → entri ikut tanggal itu
   * (poin sudah hangus; tidak menghukum dua kali). Sisa bucket tidak cukup → kekurangan ke bucket NULL (utang).
   */
  private clawAllocation(earnRows: LedgerRow[], buckets: LedgerBucket[], points: number, now: Date): Allocation[] {
    const dated = earnRows.find((r) => r.expires_at !== null);
    const target = dated?.expires_at ? toDate(dated.expires_at) : null;
    if (target === null) return [{ expiresAt: null, points }];
    if (target.getTime() <= now.getTime()) return [{ expiresAt: target, points }];
    const remaining = buckets.find((b) => b.expiresAt?.getTime() === target.getTime())?.remaining ?? 0;
    const fromBucket = Math.min(points, Math.max(0, remaining));
    const out: Allocation[] = [];
    if (fromBucket > 0) out.push({ expiresAt: target, points: fromBucket });
    if (points - fromBucket > 0) out.push({ expiresAt: null, points: points - fromBucket });
    return out;
  }

  private companyOf(rows: LedgerRow[]): CompanyCode {
    const c = rows[0]?.company_code;
    if (!isCompanyCode(c)) throw new Error('company_code ledger asal tidak dikenal');
    return c;
  }

  private sumBy(rows: LedgerRow[], reason: string): number {
    return rows.filter((r) => r.reason === reason).reduce((s, r) => s + Number(r.delta_points), 0);
  }

  /**
   * Masa berlaku poin baru: pukul 00:00 WIB setelah `LOYALTY_POINT_EXPIRY_DAYS` hari penuh. Dibulatkan per hari agar
   * jumlah bucket (dan entri debit per redeem) tetap kecil. 0 hari = tanpa kedaluwarsa (NULL).
   */
  private earnExpiry(): Date | null {
    return expiryFor(this.now(), this.config.loyaltyPointExpiryDays);
  }

  private reserveResponse(id: string, points: number, valueIdr: number, expiresAt: Date, holdUntil: Date): RedeemReserveResponse {
    return {
      reservation_id: id,
      points,
      value_idr: valueIdr,
      expires_at: expiresAt.toISOString(),
      hold_until: holdUntil.toISOString(),
    };
  }

  private async rateLimit(op: 'earn' | 'reserve' | 'reverse', hash: string): Promise<void> {
    await this.limiter.enforce(`loy:${op}:${hash}`, this.config.loyaltyRateMemberLimit, this.config.loyaltyRateWindowSeconds);
  }

  /** Catat metrik hasil per operasi (kode error kontrak sebagai outcome). */
  private async observe<T>(op: LoyaltyPosOp, fn: () => Promise<{ result: T; outcome: string }>): Promise<T> {
    try {
      const { result, outcome } = await fn();
      this.metrics.recordLoyaltyPosOp(op, outcome);
      return result;
    } catch (err) {
      this.metrics.recordLoyaltyPosOp(op, errorCode(err));
      throw err;
    }
  }
}
