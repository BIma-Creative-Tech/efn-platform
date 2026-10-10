import { PermanentMessageError } from '../common/amqp/amqp.service';
import { Queryable } from '../common/db/db';
import { PII_AAD, PiiCipher, PiiDecryptError } from '../common/pii/crypto';
import { memberHash } from '../common/pii/pii';

/**
 * Penerima `notify.push` `source=online_order` (docs/04 §10.9): `source_ref` = `part_id` →
 * `online_order.member_id` → `member.member_ref_enc` (AAD member) → hash hasil dekripsi wajib = `member_hash` pesan.
 * `member_id` sudah dilepas (retensi/erasure) atau member dihapus → `null` (pesan dilewati, bukan error).
 */
export async function resolveOnlineOrderSubject(
  q: Queryable,
  cipher: PiiCipher,
  memberHashPepper: string,
  partId: string,
  expectedMemberHash: string,
): Promise<string | null> {
  const res = await q.query<{ member_hash: string | null; member_ref_enc: Buffer | null; part_found: boolean }>(
    `SELECT m.member_hash, m.member_ref_enc, true AS part_found
       FROM platform.online_order_part p
       JOIN platform.online_order o ON o.id = p.order_id
       LEFT JOIN platform.member m ON m.id = o.member_id AND m.deleted_at IS NULL
      WHERE p.id = $1`,
    [partId],
  );
  const row = res.rows[0];
  if (!row) throw new PermanentMessageError('bagian order sumber tidak ditemukan');
  if (row.member_hash === null || row.member_ref_enc === null) return null;
  if (row.member_hash !== expectedMemberHash) throw new PermanentMessageError('member_hash tidak cocok dengan pemilik order');
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
