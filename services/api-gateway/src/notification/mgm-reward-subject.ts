import { PermanentMessageError } from '../common/amqp/amqp.service';
import { Queryable } from '../common/db/db';
import { PII_AAD, PiiCipher, PiiDecryptError } from '../common/pii/crypto';
import { memberHash } from '../common/pii/pii';

/**
 * Penerima `notify.push` `source=mgm_reward` (docs/04 §20.11): `source_ref` = `mgm_reward.id` → `referrer_hash` wajib =
 * `member_hash` pesan → member aktif dengan hash itu → `member_ref_enc` (AAD member) → hash hasil dekripsi wajib sama.
 * Reward tidak ada → permanen (DLQ). Referrer bukan member aktif lagi (erasure: hash di-tombstone) → `null` (dilewati).
 */
export async function resolveMgmRewardSubject(
  q: Queryable,
  cipher: PiiCipher,
  memberHashPepper: string,
  rewardId: string,
  expectedMemberHash: string,
): Promise<string | null> {
  const res = await q.query<{ referrer_hash: string; member_ref_enc: Buffer | null }>(
    `SELECT w.referrer_hash, m.member_ref_enc
       FROM platform.mgm_reward w
       LEFT JOIN platform.member m ON m.member_hash = w.referrer_hash AND m.deleted_at IS NULL
      WHERE w.id = $1`,
    [rewardId],
  );
  const row = res.rows[0];
  if (!row) throw new PermanentMessageError('reward MGM sumber tidak ditemukan');
  if (row.referrer_hash !== expectedMemberHash) throw new PermanentMessageError('member_hash tidak cocok dengan referrer reward');
  if (row.member_ref_enc === null) return null;
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
