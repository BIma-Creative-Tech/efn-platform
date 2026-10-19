import { Queryable } from '../common/db/db';
import { isMemberHash } from '../common/pii/pii';

/**
 * G22 §14.D.2 (D-22-12, D-22-15; default L-73): jenis aktivitas member yang MENGGESER jam tidak aktif W-17. Yang
 * TIDAK dihitung: clawback, restore, reverse, forfeit, expire, cek saldo POS (K-8), penyesuaian admin, notifikasi.
 * Definisi final = Finance + Legal (D-22-12, PLACEHOLDER default rekomendasi draf).
 */
export const ACTIVITY_KINDS = [
  'pos_earn',
  'pos_redeem',
  'pos_enroll',
  'online_earn',
  'online_redeem',
  'online_paid',
  'price_hunter_valid',
  'login_otp',
  'qr_claim',
  'migration_import',
] as const;
export type ActivityKind = (typeof ACTIVITY_KINDS)[number];

/**
 * Perbarui `platform.loyalty_member_activity` DI DALAM transaksi peristiwa (`q` = klien transaksi pemanggil):
 * `last_activity_at = GREATEST(lama, at)` — idempoten dan aman terhadap urutan (retry/event terlambat tidak memundurkan
 * jam). `member_hash` pseudonim; tidak ada data lain. Hash tidak valid → diabaikan (tidak pernah melempar karena data
 * aktivitas bukan alasan menggagalkan transaksi bisnis — kecuali error DB yang memang harus me-rollback).
 */
export async function touchActivity(q: Queryable, memberHash: string, kind: ActivityKind, at: Date): Promise<void> {
  if (!isMemberHash(memberHash)) return;
  await q.query(
    `INSERT INTO platform.loyalty_member_activity (member_hash, last_activity_at, last_kind, updated_at)
     VALUES ($1, $2, $3, now())
     ON CONFLICT (member_hash) DO UPDATE
       SET last_kind = CASE WHEN EXCLUDED.last_activity_at >= platform.loyalty_member_activity.last_activity_at
                            THEN EXCLUDED.last_kind ELSE platform.loyalty_member_activity.last_kind END,
           last_activity_at = GREATEST(platform.loyalty_member_activity.last_activity_at, EXCLUDED.last_activity_at),
           updated_at = now()`,
    [memberHash, at.toISOString(), kind],
  );
}
