import { Logger } from '@nestjs/common';
import { MetricsService } from '../common/metrics/metrics.service';

/**
 * SEC-61 (Gelombang 13c): pelaporan anomali poin — SATU format untuk alert Loki dan Prometheus.
 *
 * Baris log: `EFN_LOYALTY_ANOMALY {json}` (satu baris, JSON tanpa spasi baris baru), pola sama dengan
 * `EFN_AUDIT_SINK {json}` Odoo sehingga rule Loki memakai `| regexp "EFN_LOYALTY_ANOMALY (?P<payload>[{].*[}])" | ...`.
 * Metrik: `efn_loyalty_anomaly_total{kind,severity}`.
 *
 * TANPA PII: tidak ada nomor HP, member_hash, IP. `order_ref` (uuid pos.order, opak — setara `ledger.source_ref`
 * yang juga dibuka ke `efn_readonly`) dicantumkan agar investigasi bisa langsung ke order Odoo. Nilai string
 * disaring ke `[A-Za-z0-9_.:-]` (maks 64) agar input pemanggil tidak bisa menyuntik baris log palsu.
 */
export const LOYALTY_ANOMALY_KINDS = {
  // ── runtime: earn ditolak pagar batas (409 earn_limit_exceeded) ──
  earn_limit_order: 'warning',
  earn_limit_member_day: 'warning',
  earn_limit_pos_config_hour: 'warning',
  earn_limit_company_hour: 'critical',
  // ── runtime: migrasi K-7 ──
  migration_large_balance: 'warning',
  migration_erased_member: 'warning',
  // ── job rekonsiliasi (LoyaltyReconService) ──
  /** Σ entri ledger pos_earn order ≠ points_earned catatan earn → tulis langsung ke ledger / bug. */
  earn_ledger_mismatch: 'critical',
  /** points_earned > floor(eligible / tarif) → aturan earn dilanggar (atau tarif diturunkan setelah earn). */
  earn_above_rule: 'critical',
  /** points_earned > LOYALTY_POS_MAX_EARN_POINTS saat ini (earn sebelum plafon diturunkan / pagar dilewati). */
  earn_above_order_cap: 'warning',
  /** Entri ledger pos_earn tanpa catatan loyalty_pos_earn (bukan karena erasure/retensi) → tulis di luar BFF. */
  earn_ledger_orphan: 'critical',
  /** Σ earn member per hari WIB > LOYALTY_EARN_DAILY_MAX_POINTS (pagar dilewati / balapan / data lama). */
  member_day_above_limit: 'warning',
  /** Clawback kumulatif > earn order asal. */
  reverse_exceeds_earn: 'critical',
  /** Restore kumulatif > redeem order asal (uang + poin kembali dua kali). */
  restore_exceeds_redeem: 'critical',
  // ── G18 kontrak §9.9 v6 (poin = alat bayar) ──
  /** K-18-3: `payment_amount` commit ≠ `value_idr` reservasi → commit ditolak (Odoo menahan order untuk review). */
  commit_payment_mismatch: 'critical',
  /** SEC-118: `points_restored_value` Odoo menyimpang dari restore yang diharapkan BFF → reverse ditahan (409). */
  restore_value_unexpected: 'critical',
  /** SEC-128: reverse tanpa data nilai (event lama) dan rasio eligible 0 (refund barang dikecualikan) → tidak bisa dicek. */
  restore_value_unverified: 'warning',
  /** SEC-128: supervisor menyetujui reverse yang ditahan (`restore_approved`) → cek nilai dilewati, dibatasi sisa redeem. */
  restore_value_override: 'warning',
  /** K-18-4: `eligible_amount` > porsi uang order (amount_total reservasi × (1+toleransi) − nilai poin) → earn ditolak. */
  earn_exceeds_money_portion: 'warning',
  /** Entri ledger dengan `source` di luar daftar penulis BFF (admin/SQL manual/server action). */
  ledger_unknown_source: 'critical',
  // ── G21 §13.1 kebijakan poin per PT ──
  /** Kebijakan dari Odoo di luar plafon `LOYALTY_POLICY_CEILING_*` (422) atau tersimpan lalu plafon diturunkan (fallback env). */
  policy_out_of_bounds: 'warning',
  /** Versi kebijakan sama dengan isi berbeda (409) — outbox Odoo/penyetuju perlu ditinjau. */
  policy_conflict: 'critical',
  // ── G22 §14.A–§14.D ──
  /** D-22-5: kunci `pos_earn:<order_uuid>` dipakai earn QR dan POS untuk member berbeda (409 final). */
  qr_pos_member_conflict: 'critical',
  /** Reverse `earn_channel: "qr"` untuk order yang tidak dikenal BFF (200 nol; perlu ditinjau). */
  qr_reverse_unknown_order: 'warning',
  /** C.3: nilai restore poin online dari Odoo menyimpang > ±Rp1 dari hitungan BFF → refund review. */
  restore_value_mismatch: 'critical',
  /** C.3: earn online saat diambil melewati pagar batas kebijakan PT → `earn_status=limited`. */
  online_earn_limited: 'warning',
  /** D.3 no. 4: kandidat hangus > LOYALTY_EXPIRY_MAX_PER_RUN → run dibatalkan (alert EfnLoyaltyExpiryAborted). */
  expiry_run_aborted: 'critical',
} as const;

export type LoyaltyAnomalyKind = keyof typeof LOYALTY_ANOMALY_KINDS;
export type LoyaltyAnomalySeverity = (typeof LOYALTY_ANOMALY_KINDS)[LoyaltyAnomalyKind];

export const LOYALTY_ANOMALY_LOG_TAG = 'EFN_LOYALTY_ANOMALY';

export type AnomalyFields = Record<string, string | number | null | undefined>;

const SAFE_STRING = /[^A-Za-z0-9_.:-]/g;

function clean(fields: AnomalyFields): Record<string, string | number | null> {
  const out: Record<string, string | number | null> = {};
  for (const [k, v] of Object.entries(fields)) {
    if (!/^[a-z_]{1,32}$/.test(k) || v === undefined) continue;
    if (typeof v === 'number') out[k] = Number.isFinite(v) ? v : null;
    else if (v === null) out[k] = null;
    else out[k] = String(v).replace(SAFE_STRING, '').slice(0, 64);
  }
  return out;
}

/** Bentuk baris log (dipisah agar mudah diuji dan dipakai ulang devsecops sebagai contoh). */
export function formatLoyaltyAnomaly(kind: LoyaltyAnomalyKind, fields: AnomalyFields = {}): string {
  const payload = { kind, severity: LOYALTY_ANOMALY_KINDS[kind], ...clean(fields) };
  return `${LOYALTY_ANOMALY_LOG_TAG} ${JSON.stringify(payload)}`;
}

/** Catat anomali: log WARN satu baris + counter. Tidak pernah melempar. */
export function reportLoyaltyAnomaly(
  logger: Pick<Logger, 'warn'>,
  metrics: MetricsService,
  kind: LoyaltyAnomalyKind,
  fields: AnomalyFields = {},
): void {
  try {
    logger.warn(formatLoyaltyAnomaly(kind, fields));
    metrics.recordLoyaltyAnomaly(kind, LOYALTY_ANOMALY_KINDS[kind]);
  } catch {
    // pelaporan tidak boleh menggagalkan transaksi
  }
}
