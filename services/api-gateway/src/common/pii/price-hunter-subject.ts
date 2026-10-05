import { PermanentMessageError } from '../amqp/amqp.service';
import { Queryable } from '../db/db';
import { PII_AAD, PiiCipher, PiiDecryptError } from './crypto';
import { memberHash } from './pii';

export interface PriceHunterSubject {
  reportId: string;
  status: string;
  /** member_ref hasil dekripsi `user_enc` — hanya di memori, jangan di-log (pakai maskMsisdn). */
  memberRef: string;
}

/**
 * Event v2 (docs/04 §4) tidak membawa nomor HP. BFF menyelesaikan identitas dari baris sumber:
 * `platform.price_hunter_report (id = source_ref)` → `user_hash` harus = `member_hash` pesan, lalu `user_enc`
 * didekripsi (AAD `efn:price_hunter_report.user_enc:v1`) dan hash hasil dekripsi diverifikasi ulang.
 *
 * - Report tidak ada / hash beda / dekripsi gagal → `PermanentMessageError` (DLQ; tidak ada efek).
 * - Mengembalikan status report apa adanya; pemanggil memutuskan status yang diterima.
 */
export async function resolvePriceHunterSubject(
  q: Queryable,
  cipher: PiiCipher,
  memberHashPepper: string,
  reportId: string,
  expectedMemberHash: string,
): Promise<PriceHunterSubject> {
  const res = await q.query<{ id: string; status: string; user_hash: string; user_enc: Buffer | null }>(
    `SELECT id::text AS id, status, user_hash, user_enc FROM platform.price_hunter_report WHERE id = $1`,
    [reportId],
  );
  const row = res.rows[0];
  if (!row) throw new PermanentMessageError('report sumber tidak ditemukan');
  if (row.user_hash !== expectedMemberHash) throw new PermanentMessageError('member_hash tidak cocok dengan report sumber');
  // v3: user_enc NULL = sudah melewati retensi 90 hari atau subjek dihapus (DSAR)
  if (row.user_enc === null) throw new PermanentMessageError('identitas pelapor sudah dihapus (retensi/erasure)');
  let memberRef: string;
  try {
    memberRef = cipher.decrypt(row.user_enc, PII_AAD.PRICE_HUNTER_REPORT_USER);
  } catch (err) {
    if (err instanceof PiiDecryptError) throw new PermanentMessageError('user_enc report tidak dapat didekripsi');
    throw err;
  }
  if (memberHash(memberHashPepper, memberRef) !== expectedMemberHash) {
    throw new PermanentMessageError('user_enc tidak konsisten dengan user_hash');
  }
  return { reportId: row.id, status: row.status, memberRef };
}
