import { PermanentMessageError } from '../common/amqp/amqp.service';
import { Queryable } from '../common/db/db';
import { PII_AAD, PiiCipher, PiiDecryptError } from '../common/pii/crypto';
import { memberHash } from '../common/pii/pii';

/**
 * Penerima `notify.push` `source=loyalty_expiry` (docs/04 §14.D.4): `source_ref` = `loyalty_expiry_notice.id` →
 * `member_hash` notice wajib = `member_hash` pesan → nomor dari `member.member_ref_enc` (AAD member) atau, bila subjek
 * hanya ada di ledger POS, entri ledger terbaru (AAD ledger) → hash hasil dekripsi diverifikasi.
 * Notice tidak `queued` lagi (superseded/expired/cancelled) atau subjek terhapus → `null` (dilewati, ack).
 */
export async function resolveLoyaltyExpirySubject(
  q: Queryable,
  cipher: PiiCipher,
  memberHashPepper: string,
  noticeId: string,
  expectedMemberHash: string,
): Promise<string | null> {
  const n = await q.query<{ member_hash: string; status: string }>(
    `SELECT member_hash, status FROM platform.loyalty_expiry_notice WHERE id = $1`,
    [noticeId],
  );
  const row = n.rows[0];
  if (!row) return null; // dihapus erasure/retensi
  if (row.member_hash !== expectedMemberHash) throw new PermanentMessageError('member_hash tidak cocok dengan notice');
  if (row.status !== 'queued') return null; // aktivitas baru → superseded (minimisasi pesan, D.3 no. 5)
  const m = await q.query<{ member_ref_enc: Buffer }>(
    `SELECT member_ref_enc FROM platform.member WHERE member_hash = $1 AND deleted_at IS NULL`,
    [expectedMemberHash],
  );
  let enc: Buffer | null = m.rows[0]?.member_ref_enc ?? null;
  let aad: string = PII_AAD.MEMBER_REF;
  if (!enc) {
    const l = await q.query<{ member_ref_enc: Buffer }>(
      `SELECT member_ref_enc FROM platform.loyalty_ledger WHERE member_ref_hash = $1 AND member_ref_enc IS NOT NULL ORDER BY id DESC LIMIT 1`,
      [expectedMemberHash],
    );
    enc = l.rows[0]?.member_ref_enc ?? null;
    aad = PII_AAD.LOYALTY_LEDGER_MEMBER_REF;
  }
  if (!enc) return null;
  let ref: string;
  try {
    ref = cipher.decrypt(enc, aad);
  } catch (err) {
    if (err instanceof PiiDecryptError) throw new PermanentMessageError('member_ref_enc tidak dapat didekripsi');
    throw err;
  }
  if (memberHash(memberHashPepper, ref) !== expectedMemberHash) throw new PermanentMessageError('member_ref_enc tidak konsisten');
  return ref;
}

/**
 * L-76: bukti pemberitahuan TANPA identitas (tanggal kirim, kategori, id notice opak, tanggal hangus) — tidak ikut
 * terhapus anonimisasi/erasure. Idempoten per notice.
 */
export async function recordExpiryNoticeProof(q: Queryable, noticeId: string, expireDate: string | null, sentAt: Date): Promise<void> {
  if (!expireDate || !/^\d{4}-\d{2}-\d{2}$/.test(expireDate)) return;
  await q.query(
    `INSERT INTO platform.loyalty_expiry_notice_proof (id, notice_id, category, channel, expire_date, sent_at)
     VALUES (gen_random_uuid(), $1, 'loyalty_inactivity_notice', 'whatsapp', $2::date, $3)
     ON CONFLICT (notice_id) DO NOTHING`,
    [noticeId, expireDate, sentAt.toISOString()],
  );
}
