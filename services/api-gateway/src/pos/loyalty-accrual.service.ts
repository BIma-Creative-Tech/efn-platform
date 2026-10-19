import { Inject, Injectable, Logger, Optional } from '@nestjs/common';
import { AppConfig } from '../config/app-config';
import { DB, Db } from '../common/db/db';
import { Errors } from '../common/errors/api-error';
import { PolicyErrors } from '../loyalty/loyalty-policy';
import { LoyaltyPolicyService } from '../loyalty/loyalty-policy.service';
import { COMPANY_CODES, CompanyCode, isCompanyCode } from '../loyalty/loyalty.service';
import { POS_SOURCES, REVERSE_REASON } from './pos-loyalty.service';

const DAY_MS = 86_400_000;
const WIB_OFFSET_MS = 7 * 3600 * 1000;
/** Tanggal paling awal yang dilayani (sebelum platform berjalan tidak ada ledger). */
const MIN_DATE_MS = Date.UTC(2026, 0, 1);

/** G22 §14.C.5: alasan entri ledger poin online (source `online`) — klasifikasi akrual sama dengan padanan POS. */
export const ONLINE_REASONS = {
  EARN: 'online_earn',
  REDEEM: 'online_redeem',
  REVERSE: 'online_reverse',
  RESTORE: 'online_restore',
} as const;
/** G22 §14.D.3 no. 3: entri eksplisit hangus karena tidak aktif (source `loyalty_expiry`). */
export const INACTIVITY_EXPIRE_REASON = 'inactivity_expire';

/**
 * Respons `GET /v1/pos/loyalty/accruals` (docs/04 §13.1 no. 6). Semua nilai **integer Rupiah** = poin × nilai tukar
 * global (kebijakan EFN aktif di akhir tanggal, L-62; fallback env). Field kontrak + field ADITIF (ditandai) untuk
 * rekonsiliasi §13.1 no. 7.
 */
export interface LoyaltyAccrualResponse {
  company_code: CompanyCode;
  date: string;
  /** Kredit earn lot terbitan PT ini (termasuk pelunasan utang lot PT ini & saldo pembuka migrasi). */
  earned_value_idr: number;
  /** Clawback (refund) atas lot terbitan PT ini. */
  earn_reversed_value_idr: number;
  /** Penukaran DI PT ini per PT penerbit lot (kunci = PT ini → penukaran sama PT, tidak dijurnal di akrual). */
  redeemed_value_idr_by_issuer: Record<string, number>;
  /** Pengembalian poin redeem (refund) yang terjadi DI PT ini (semua penerbit). */
  restored_value_idr: number;
  /** Sisa lot terbitan PT ini yang kedaluwarsa pada tanggal ini (breakage, W-17 G22). */
  expired_value_idr: number;
  /** Saldo liabilitas lot terbitan PT ini di akhir tanggal (23:59:59.999 WIB). Bisa < 0 bila ada utang member. */
  closing_liability_idr: number;
  generated_at: string;
  // ── aditif (bukan kontrak minimum) ──
  /** Nilai tukar yang dipakai konversi. */
  point_value_idr: number;
  /** Versi kebijakan global EFN sumber nilai tukar (null = env). */
  point_value_version: number | null;
  /** Rincian `restored_value_idr` per penerbit (kebalikan `redeemed_value_idr_by_issuer`). */
  restored_value_idr_by_issuer: Record<string, number>;
  /** Penukaran lot terbitan PT ini di PT MANA PUN (sisi penerbit, untuk rekonsiliasi liabilitas). */
  redeemed_value_idr_as_issuer: number;
  /** Pengembalian ke lot terbitan PT ini di PT mana pun. */
  restored_value_idr_as_issuer: number;
  /**
   * L-66 (§13.7, mengikat): lot terbitan PT ini yang DITUKAR di PT LAIN, per PT penebus (kunci PT sendiri tidak ada;
   * `{}` bila tidak ada). Σ + `redeemed_value_idr_by_issuer[PT ini]` = `redeemed_value_idr_as_issuer`.
   */
  redeemed_value_idr_as_issuer_by_redeemer: Record<string, number>;
  /** L-66: idem untuk pengembalian; Σ + `restored_value_idr_by_issuer[PT ini]` = `restored_value_idr_as_issuer`. */
  restored_value_idr_as_issuer_by_redeemer: Record<string, number>;
  /** Pengurangan lain atas lot PT ini (forfeit erasure DSAR, penyesuaian). */
  forfeited_value_idr: number;
}

export interface FlowRow {
  issuer: string;
  company_code: string;
  reason: string;
  credit: boolean | string;
  flow_points: string | number;
}

/** `YYYY-MM-DD` → [awal, akhir) hari kalender Asia/Jakarta (UTC+7, tanpa DST). */
export function wibDayRange(date: string): { start: Date; end: Date } {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(date);
  if (!m) throw Errors.invalidRequest();
  const [y, mo, d] = [Number(m[1]), Number(m[2]), Number(m[3])];
  const utc = Date.UTC(y, mo - 1, d);
  const back = new Date(utc);
  // tolak tanggal tidak ada (2026-02-30) — round-trip kalender
  if (back.getUTCFullYear() !== y || back.getUTCMonth() !== mo - 1 || back.getUTCDate() !== d || utc < MIN_DATE_MS) {
    throw Errors.invalidRequest();
  }
  const start = new Date(utc - WIB_OFFSET_MS);
  return { start, end: new Date(start.getTime() + DAY_MS) };
}

/**
 * Akrual harian liabilitas poin per PT (docs/04 §13.1 no. 6–7, L-61: Odoo MENARIK dari BFF; ADR-020 revisi).
 *
 * Definisi (semua dari `platform.loyalty_ledger`, penerbit = `COALESCE(issuer_company_code, company_code)`):
 * - arus tanggal D = entri dengan `created_at` ∈ [awal D, akhir D) WIB, KECUALI entri "lahir kedaluwarsa"
 *   (`expires_at ≤ created_at`: clawback ke lot yang sudah kedaluwarsa — poinnya sudah dihitung breakage, tidak
 *   menggeser liabilitas lagi);
 * - kedaluwarsa D = Σ entri lot penerbit dengan `expires_at` ∈ D (dan `created_at < expires_at`) = sisa lot saat hangus
 *   (model lot: kedaluwarsa tidak menulis entri);
 * - saldo penutup = Σ entri lot penerbit `created_at < akhir D` dengan `expires_at` NULL atau ≥ akhir D (lot yang hangus
 *   tepat 00:00 WIB D+1 masih dihitung di penutup D dan menjadi `expired` D+1 → identitas
 *   penutup(D) = penutup(D−1) + earned − reversed − redeemed_as_issuer + restored_as_issuer − expired − forfeited).
 * Klasifikasi arus: kredit `pos_refund_restore` → restored; kredit lain → earned; debit `pos_redeem` → redeemed;
 * debit `pos_refund_clawback` → earn_reversed; debit lain (`dsar_forfeit`, penyesuaian) → forfeited.
 *
 * Deterministik & idempoten untuk tanggal LAMPAU: `created_at` = `now()` DB saat insert (tidak bisa mundur), entri
 * bermasa berlaku selalu `expires_at > created_at`, kebijakan berlaku `GREATEST(effective_from, received_at)`. Tanggal
 * hari ini/masa depan, atau < `LOYALTY_ACCRUAL_CLOSE_GRACE_SECONDS` setelah tengah malam → `422 date_not_closed`.
 * Hanya agregat integer — tidak ada member_hash/order_ref di respons maupun log.
 */
@Injectable()
export class LoyaltyAccrualService {
  private readonly logger = new Logger(LoyaltyAccrualService.name);
  now: () => number = () => Date.now();

  constructor(
    private readonly config: AppConfig,
    @Inject(DB) private readonly db: Db,
    @Optional() private readonly policies: LoyaltyPolicyService | null = null,
  ) {}

  async accrual(companyRaw: unknown, dateRaw: unknown): Promise<LoyaltyAccrualResponse> {
    if (!isCompanyCode(companyRaw) || typeof dateRaw !== 'string') throw Errors.invalidRequest();
    const company: CompanyCode = companyRaw;
    const { start, end } = wibDayRange(dateRaw);
    if (this.now() < end.getTime() + this.config.loyaltyAccrualCloseGraceSeconds * 1000) throw PolicyErrors.dateNotClosed();

    const result = await this.db.tx(async (q) => {
      // Satu snapshot konsisten untuk ketiga agregat (REPEATABLE READ harus pernyataan pertama transaksi)
      await q.query('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY');
      const flows = await q.query<FlowRow>(
        `SELECT COALESCE(issuer_company_code, company_code) AS issuer, company_code, reason,
                (delta_points > 0) AS credit, SUM(delta_points)::bigint AS flow_points
           FROM platform.loyalty_ledger
          WHERE created_at >= $1 AND created_at < $2
            AND (expires_at IS NULL OR expires_at > created_at)
            AND (company_code = $3 OR COALESCE(issuer_company_code, company_code) = $3)
          GROUP BY 1, 2, 3, 4`,
        [start.toISOString(), end.toISOString(), company],
      );
      const expired = await q.query<{ expired_points: string | number | null }>(
        `SELECT COALESCE(SUM(delta_points), 0)::bigint AS expired_points
           FROM platform.loyalty_ledger
          WHERE COALESCE(issuer_company_code, company_code) = $3
            AND expires_at >= $1 AND expires_at < $2 AND created_at < expires_at`,
        [start.toISOString(), end.toISOString(), company],
      );
      // G22 §14.D.5: + Σ(−delta) entri `inactivity_expire` yang DIBUAT pada tanggal ini untuk penerbit ini (tanpa
      // filter lahir-kedaluwarsa: lot berumur yang sudah hangus implisit tidak pernah disentuh job — sisa 0)
      const inactivity = await q.query<{ inactivity_points: string | number | null }>(
        `SELECT COALESCE(-SUM(delta_points), 0)::bigint AS inactivity_points
           FROM platform.loyalty_ledger
          WHERE reason = 'inactivity_expire' AND created_at >= $1 AND created_at < $2
            AND COALESCE(issuer_company_code, company_code) = $3`,
        [start.toISOString(), end.toISOString(), company],
      );
      const closing = await q.query<{ closing_points: string | number | null }>(
        `SELECT COALESCE(SUM(delta_points), 0)::bigint AS closing_points
           FROM platform.loyalty_ledger
          WHERE COALESCE(issuer_company_code, company_code) = $1 AND created_at < $2
            AND (expires_at IS NULL OR expires_at >= $2)`,
        [company, end.toISOString()],
      );
      // nilai tukar berlaku di akhir tanggal (kebijakan yang diterima sesudahnya tidak mengubah tanggal lampau)
      const pv = this.policies
        ? await this.policies.pointValueIdrAt(new Date(end.getTime() - 1), q)
        : { value: this.config.loyaltyPointValueIdr, version: null };
      return {
        flows: flows.rows,
        expired: Number(expired.rows[0]?.expired_points ?? 0) + Number(inactivity.rows[0]?.inactivity_points ?? 0),
        closing: Number(closing.rows[0]?.closing_points ?? 0), pv };
    });

    const out = classifyFlows(company, result.flows);
    // Odoo `parse_accrual_response` menolak nilai arus < 0. Lot bermasa berlaku normalnya tidak pernah bersaldo negatif
    // (utang selalu di lot NULL); bila terjadi (data manual), dilaporkan 0 + log agar rekonsiliasi menyorotnya.
    if (result.expired < 0) {
      this.logger.warn(`EFN_LOYALTY_ACCRUAL ${JSON.stringify({ company, date: dateRaw, anomaly: 'negative_expired', points: result.expired })}`);
      result.expired = 0;
    }
    const v = (points: number): number => points * result.pv.value;
    const byIssuer = (m: Map<string, number>): Record<string, number> =>
      Object.fromEntries(COMPANY_CODES.filter((c) => (m.get(c) ?? 0) !== 0).map((c) => [c, v(m.get(c) ?? 0)]));
    const response: LoyaltyAccrualResponse = {
      company_code: company,
      date: dateRaw,
      earned_value_idr: v(out.earned),
      earn_reversed_value_idr: v(out.earnReversed),
      redeemed_value_idr_by_issuer: byIssuer(out.redeemedByIssuer),
      restored_value_idr: v(out.restored),
      expired_value_idr: v(result.expired),
      closing_liability_idr: v(result.closing),
      generated_at: new Date(this.now()).toISOString(),
      point_value_idr: result.pv.value,
      point_value_version: result.pv.version,
      restored_value_idr_by_issuer: byIssuer(out.restoredByIssuer),
      redeemed_value_idr_as_issuer: v(out.redeemedAsIssuer),
      restored_value_idr_as_issuer: v(out.restoredAsIssuer),
      redeemed_value_idr_as_issuer_by_redeemer: byIssuer(out.redeemedAsIssuerByRedeemer),
      restored_value_idr_as_issuer_by_redeemer: byIssuer(out.restoredAsIssuerByRedeemer),
      forfeited_value_idr: v(out.forfeited),
    };
    for (const [k, n] of Object.entries(response)) {
      if (typeof n === 'number' && !Number.isSafeInteger(n)) throw new Error(`akrual ${k} di luar batas integer aman`);
    }
    // L-66: Σ map (PT lain) + bagian PT sendiri = total sebagai penerbit (konversi per bagian poin × nilai tukar → eksak)
    const sum = (m: Record<string, number>): number => Object.values(m).reduce((s, n) => s + n, 0);
    const own = (m: Record<string, number>): number => m[company] ?? 0;
    if (
      sum(response.redeemed_value_idr_as_issuer_by_redeemer) + own(response.redeemed_value_idr_by_issuer) !== response.redeemed_value_idr_as_issuer ||
      sum(response.restored_value_idr_as_issuer_by_redeemer) + own(response.restored_value_idr_by_issuer) !== response.restored_value_idr_as_issuer
    ) {
      throw new Error('akrual: rincian per PT penebus tidak konsisten dengan total sebagai penerbit');
    }
    this.logger.log(
      `EFN_LOYALTY_ACCRUAL ${JSON.stringify({ company, date: dateRaw, earned: response.earned_value_idr, closing: response.closing_liability_idr, pv: result.pv.value })}`,
    );
    return response;
  }
}

/** Klasifikasi arus poin (dipisah agar mudah diuji). Poin positif. */
export function classifyFlows(
  company: CompanyCode,
  rows: readonly FlowRow[],
): {
  earned: number;
  earnReversed: number;
  redeemedByIssuer: Map<string, number>;
  restored: number;
  restoredByIssuer: Map<string, number>;
  redeemedAsIssuer: number;
  restoredAsIssuer: number;
  /** L-66: lot PT ini yang ditukar di PT LAIN, per PT penebus (kunci PT sendiri tidak ada). */
  redeemedAsIssuerByRedeemer: Map<string, number>;
  restoredAsIssuerByRedeemer: Map<string, number>;
  forfeited: number;
} {
  const out = {
    earned: 0,
    earnReversed: 0,
    redeemedByIssuer: new Map<string, number>(),
    restored: 0,
    restoredByIssuer: new Map<string, number>(),
    redeemedAsIssuer: 0,
    restoredAsIssuer: 0,
    redeemedAsIssuerByRedeemer: new Map<string, number>(),
    restoredAsIssuerByRedeemer: new Map<string, number>(),
    forfeited: 0,
  };
  const add = (m: Map<string, number>, k: string, n: number): void => void m.set(k, (m.get(k) ?? 0) + n);
  for (const r of rows) {
    const pts = Math.abs(Number(r.flow_points));
    if (pts === 0) continue;
    const credit = r.credit === true || r.credit === 't' || r.credit === 'true';
    const atCompany = r.company_code === company;
    const asIssuer = r.issuer === company;
    // G22 §14.D.5: hangus tidak aktif dihitung terpisah (expired_value_idr), bukan forfeit
    if (!credit && r.reason === INACTIVITY_EXPIRE_REASON) continue;
    if (credit) {
      if (r.reason === REVERSE_REASON.RESTORE || r.reason === ONLINE_REASONS.RESTORE) {
        if (atCompany) {
          out.restored += pts;
          add(out.restoredByIssuer, r.issuer, pts);
        }
        if (asIssuer) out.restoredAsIssuer += pts;
        if (asIssuer && !atCompany) add(out.restoredAsIssuerByRedeemer, r.company_code, pts);
      } else if (asIssuer) {
        out.earned += pts;
      }
    } else if (r.reason === POS_SOURCES.REDEEM || r.reason === ONLINE_REASONS.REDEEM) {
      if (atCompany) add(out.redeemedByIssuer, r.issuer, pts);
      if (asIssuer) out.redeemedAsIssuer += pts;
      if (asIssuer && !atCompany) add(out.redeemedAsIssuerByRedeemer, r.company_code, pts);
    } else if (r.reason === REVERSE_REASON.CLAWBACK || r.reason === ONLINE_REASONS.REVERSE) {
      if (asIssuer) out.earnReversed += pts;
    } else if (asIssuer) {
      out.forfeited += pts;
    }
  }
  return out;
}
