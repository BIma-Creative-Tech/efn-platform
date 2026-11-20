import { piiAad } from '../../common/pii/crypto';

/** Queue kontrak docs/04 §28.7 (didefinisikan broker; BFF tidak men-declare). DLQ `notify.email.dlq`. */
export const NOTIFY_EMAIL_QUEUE = 'notify.email';
/** Nama consumer di `platform.processed_message`. */
export const NOTIFY_EMAIL_CONSUMER = 'bff.notify-email';
/** Publisher yang sah untuk `notify.email` (properti AMQP `user_id`). */
export const NOTIFY_EMAIL_PUBLISHERS: readonly string[] = ['efn-bff'];

/**
 * Kolom `platform.member_email.email_enc` (AES-256-GCM key ring PII, docs/04 §7.2). WAJIB didaftarkan di
 * `common/pii/pii-columns.ts` (rotasi kunci) — usulan G50-B ke efn-lead (file milik bersama).
 */
export const MEMBER_EMAIL_AAD = piiAad('member_email', 'email_enc');

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
