import { PermanentMessageError } from '../common/amqp/amqp.service';
import { Queryable } from '../common/db/db';
import { PII_AAD, PiiCipher, PiiDecryptError } from '../common/pii/crypto';
import { memberHash } from '../common/pii/pii';

/**
 * Penerima `notify.push` `source=table_booking` (docs/04 §15.5): `source_ref` = `table_booking.id` →
 * `table_booking.member_id` → `member.member_ref_enc` (AAD member) → hash hasil dekripsi wajib = `member_hash` pesan.
 * Tautan member sudah dilepas (BOOKING_MEMBER_LINK_DAYS/erasure) atau member dihapus → `null` (dilewati).
 */
export async function resolveTableBookingSubject(
  q: Queryable,
  cipher: PiiCipher,
  memberHashPepper: string,
  bookingId: string,
  expectedMemberHash: string,
): Promise<string | null> {
  const res = await q.query<{ member_hash: string | null; member_ref_enc: Buffer | null }>(
    `SELECT m.member_hash, m.member_ref_enc
       FROM platform.table_booking b
       LEFT JOIN platform.member m ON m.id = b.member_id AND m.deleted_at IS NULL
      WHERE b.id = $1`,
    [bookingId],
  );
  const row = res.rows[0];
  if (!row) throw new PermanentMessageError('reservasi sumber tidak ditemukan');
  if (row.member_hash === null || row.member_ref_enc === null) return null;
  if (row.member_hash !== expectedMemberHash) throw new PermanentMessageError('member_hash tidak cocok dengan pemilik reservasi');
  let ref: string;
  try {
    ref = cipher.decrypt(row.member_ref_enc, PII_AAD.MEMBER_REF);
  } catch (err) {
    if (err instanceof PiiDecryptError) throw new PermanentMessageError('member_ref_enc tidak dapat didekripsi');
    throw err;
  }
  if (memberHash(memberHashPepper, ref) !== expectedMemberHash) throw new PermanentMessageError('member_ref_enc tidak konsisten');
  return ref;
}
