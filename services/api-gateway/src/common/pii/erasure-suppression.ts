import { createHmac } from 'node:crypto';
import { Queryable } from '../db/db';

/**
 * SEC-65 (Gelombang 13c): tag daftar tolak migrasi pasca-erasure (`platform.erasure_suppression`, 06-*.sql).
 *
 * tag = hex(HMAC-SHA256(k, "efn:erasure_suppression:v1\n" + member_hash)), k = `AppConfig.erasureSuppressionKey`
 * (HKDF otp_pepper, domain terpisah). Tag tidak sama dengan member_hash mana pun → tidak bisa di-JOIN ke tabel lain;
 * hanya pemegang otp_pepper yang dapat menghitung tag dari nomor HP. Rotasi otp_pepper memutus daftar (sama seperti
 * seluruh member_hash).
 */
export function erasureSuppressionTag(key: Buffer, memberHash: string): string {
  return createHmac('sha256', key).update(`efn:erasure_suppression:v1\n${memberHash}`, 'utf8').digest('hex');
}

export type ErasureBlock = 'erasure_active' | 'erased' | null;

/**
 * Status erasure subjek untuk penolakan impor migrasi:
 * - `erasure_active`: ada `dsar_request` delete `scheduled`/`processing` dengan member_hash ini (masa tenggang atau
 *   store lain belum selesai — member_hash masih tersimpan di request);
 * - `erased`: tag ada di daftar tolak dan belum kedaluwarsa (`completed`; member_hash request sudah NULL);
 * - null: tidak diketahui pernah dihapus (atau di luar masa `ERASURE_SUPPRESSION_DAYS`).
 */
export async function erasureBlock(q: Queryable, key: Buffer, memberHash: string, at: Date): Promise<ErasureBlock> {
  const res = await q.query<{ kind: string }>(
    `SELECT 'erasure_active' AS kind FROM platform.dsar_request
      WHERE type = 'delete' AND status IN ('scheduled', 'processing') AND member_hash = $1
     UNION ALL
     SELECT 'erased' AS kind FROM platform.erasure_suppression WHERE tag = $2 AND expires_at > $3
     LIMIT 1`,
    [memberHash, erasureSuppressionTag(key, memberHash), at.toISOString()],
  );
  const kind = res.rows[0]?.kind;
  return kind === 'erasure_active' || kind === 'erased' ? kind : null;
}
