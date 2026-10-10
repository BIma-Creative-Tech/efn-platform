import { Queryable } from '../db/db';
import { PiiCipher, PiiDecryptError } from './crypto';
import { PiiColumn, assertPiiColumn } from './pii-columns';

/**
 * Re-encrypt kolom PII ke versi kunci aktif (rotasi kunci, docs/04 §7.2, runbook `pii-key-rotation.md` langkah 4; QA U2).
 *
 * - Batch keyset (`pk > last ORDER BY pk LIMIT n`) → baris yang gagal didekripsi tidak membuat loop tak berujung.
 * - Hanya baris dengan byte versi ≠ versi aktif yang disentuh → **idempotent**: dijalankan ulang = no-op.
 * - UPDATE optimistik `WHERE pk = $ AND col = <ciphertext lama>`: aman berjalan bersamaan dengan BFF (baris yang
 *   berubah di tengah jalan dilewati, run berikutnya mengambilnya) dan aman bila dua run paralel.
 * - Tidak mengubah `updated_at` (ETL tidak membaca kolom `*_enc`).
 * - Log hanya angka (tidak ada plaintext/ciphertext/kunci).
 */
export interface ColumnReport {
  column: string;
  /** Jumlah baris non-NULL per byte versi SEBELUM run. */
  before: Record<string, number>;
  reencrypted: number;
  skippedConcurrent: number;
  failed: number;
}

export async function countByVersion(q: Queryable, c: PiiColumn): Promise<Record<string, number>> {
  assertPiiColumn(c);
  const res = await q.query<{ v: number; n: string | number }>(
    `SELECT get_byte(${c.column}, 0) AS v, count(*) AS n
       FROM platform.${c.table}
      WHERE ${c.column} IS NOT NULL
      GROUP BY 1 ORDER BY 1`,
  );
  const out: Record<string, number> = {};
  for (const r of res.rows) out[String(r.v)] = Number(r.n);
  return out;
}

export async function reencryptColumn(
  q: Queryable,
  cipher: PiiCipher,
  c: PiiColumn,
  batchSize: number,
  dryRun = false,
): Promise<ColumnReport> {
  assertPiiColumn(c);
  const report: ColumnReport = {
    column: `${c.table}.${c.column}`,
    before: await countByVersion(q, c),
    reencrypted: 0,
    skippedConcurrent: 0,
    failed: 0,
  };
  if (dryRun) return report;
  const active = cipher.activeVersion;
  let last: string | null = null;
  for (;;) {
    const params: unknown[] = [active, batchSize];
    let cursor = '';
    if (last !== null) {
      params.push(last);
      cursor = `AND ${c.pk} > $3`;
    }
    const batch = await q.query<{ pk: string; blob: Buffer }>(
      `SELECT ${c.pk}::text AS pk, ${c.column} AS blob
         FROM platform.${c.table}
        WHERE ${c.column} IS NOT NULL AND get_byte(${c.column}, 0) <> $1 ${cursor}
        ORDER BY ${c.pk}
        LIMIT $2`,
      params,
    );
    if (batch.rows.length === 0) break;
    for (const row of batch.rows) {
      let next: Buffer;
      try {
        const target = c.rowAad ? c.rowAad(row.pk) : c.aad;
        let plain: string;
        try {
          plain = cipher.decrypt(row.blob, target);
        } catch (e) {
          if (!(e instanceof PiiDecryptError) || !c.rowAad) throw e;
          plain = cipher.decrypt(row.blob, c.aad); // SEC-102: format lama → ditulis ulang dengan AAD per baris
        }
        next = cipher.encrypt(plain, target);
      } catch (err) {
        if (!(err instanceof PiiDecryptError)) throw err;
        report.failed += 1;
        continue;
      }
      const upd = await q.query(
        `UPDATE platform.${c.table} SET ${c.column} = $1 WHERE ${c.pk} = $2 AND ${c.column} = $3`,
        [next, row.pk, row.blob],
      );
      if ((upd.rowCount ?? 0) > 0) report.reencrypted += 1;
      else report.skippedConcurrent += 1;
    }
    last = batch.rows[batch.rows.length - 1].pk;
    if (batch.rows.length < batchSize) break;
  }
  return report;
}

/** Jumlah baris yang ciphertext-nya memakai versi kunci yang TIDAK dimuat (mis. versi pensiun). */
export async function countUnreadable(q: Queryable, c: PiiColumn, loadedVersions: readonly number[]): Promise<number> {
  assertPiiColumn(c);
  const res = await q.query<{ n: string | number }>(
    `SELECT count(*) AS n FROM platform.${c.table}
      WHERE ${c.column} IS NOT NULL AND NOT (get_byte(${c.column}, 0) = ANY($1::int[]))`,
    [loadedVersions],
  );
  return Number(res.rows[0]?.n ?? 0);
}
