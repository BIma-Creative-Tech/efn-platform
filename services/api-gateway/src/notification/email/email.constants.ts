import { PiiCipher, PiiDecryptError, piiAad } from '../../common/pii/crypto';

/** Queue kontrak docs/04 §28.7 (didefinisikan broker; BFF tidak men-declare). DLQ `notify.email.dlq`. */
export const NOTIFY_EMAIL_QUEUE = 'notify.email';
/** Nama consumer di `platform.processed_message`. */
export const NOTIFY_EMAIL_CONSUMER = 'bff.notify-email';
/** Publisher yang sah untuk `notify.email` (properti AMQP `user_id`). */
export const NOTIFY_EMAIL_PUBLISHERS: readonly string[] = ['efn-bff'];

/**
 * Kolom `platform.member_email.email_enc` (AES-256-GCM key ring PII, docs/04 §7.2; terdaftar di
 * `common/pii/pii-columns.ts`). SEC-G50-11 (pola SEC-102): AAD v2 TERIKAT BARIS
 * `efn:member_email.email_enc:v2:<member_id lowercase>` → ciphertext yang ditukar antar-member (hak UPDATE DB) gagal
 * didekripsi. `MEMBER_EMAIL_AAD` (v1, AAD kolom) hanya untuk membaca data lama selama migrasi / registry re-encrypt.
 * Penulis WAJIB memakai `encryptMemberEmail` (v2).
 */
export const MEMBER_EMAIL_AAD = piiAad('member_email', 'email_enc');

export function memberEmailAad(memberId: string): string {
  return `efn:member_email.email_enc:v2:${memberId.toLowerCase()}`;
}

export function encryptMemberEmail(cipher: PiiCipher, email: string, memberId: string): Buffer {
  return cipher.encrypt(email, memberEmailAad(memberId));
}

/** v2 (terikat `member_id`) dulu; gagal → AAD v1 lama (`legacy: true`). Keduanya gagal → `PiiDecryptError`. */
export function decryptMemberEmail(cipher: PiiCipher, enc: Buffer | Uint8Array, memberId: string): { email: string; legacy: boolean } {
  try {
    return { email: cipher.decrypt(enc, memberEmailAad(memberId)), legacy: false };
  } catch (err) {
    if (!(err instanceof PiiDecryptError)) throw err;
  }
  return { email: cipher.decrypt(enc, MEMBER_EMAIL_AAD), legacy: true };
}

const EMAIL_RE = /^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$/;

export function isPlausibleEmail(v: unknown): v is string {
  return typeof v === 'string' && v.length <= 254 && EMAIL_RE.test(v);
}

/** Log: `a***@d***.com` (ADR-048 §3.7). Tidak pernah mengembalikan alamat utuh. */
export function maskEmail(email: string): string {
  const at = typeof email === 'string' ? email.lastIndexOf('@') : -1;
  if (at <= 0) return '***';
  const local = email.slice(0, at);
  const domain = email.slice(at + 1);
  const dot = domain.lastIndexOf('.');
  const tld = dot > 0 ? domain.slice(dot) : '';
  return `${local[0]}***@${domain[0] ?? ''}***${tld}`;
}

/** `source_ref` (id kampanye/alert dari pemanggil): pengenal non-PII pendek. */
export const SOURCE_REF_RE = /^[A-Za-z0-9:_.-]{1,128}$/;
