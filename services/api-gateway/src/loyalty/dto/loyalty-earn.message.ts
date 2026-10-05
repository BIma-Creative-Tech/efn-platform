import { PermanentMessageError } from '../../common/amqp/amqp.service';
import { isMemberHash } from '../../common/pii/pii';
import { isUuid } from '../../common/validation';
import { CompanyCode, DEFAULT_LIABILITY_COMPANY, isCompanyCode } from '../loyalty.service';

/**
 * Payload `loyalty.earn.requested` v2 (kontrak §4, 5 Okt 2026): TANPA nomor HP mentah.
 * `{ schema_version: 2, idempotency_key: "price_hunter:<report_id>", member_hash, points, reason,
 *    source: "price_hunter", source_ref: "<report_id>" }`
 * `company_code` opsional (forward-compatible MGM), default `EFN` (ADR-020).
 */
export interface LoyaltyEarnMessage {
  schema_version: 2;
  idempotency_key: string;
  member_hash: string;
  points: number;
  reason: string;
  source: LoyaltyEarnSource;
  source_ref: string;
  company_code: CompanyCode;
}

/**
 * Allowlist sumber earn dan publisher RabbitMQ (properti AMQP `user_id`) yang berhak mengirimnya (SEC-08).
 * Sumber baru (mgm, …) ditambahkan bersama validasi semantiknya sendiri.
 */
export const EARN_SOURCE_PUBLISHERS = {
  price_hunter: 'efn-ocr',
} as const;
export type LoyaltyEarnSource = keyof typeof EARN_SOURCE_PUBLISHERS;

function isEarnSource(v: unknown): v is LoyaltyEarnSource {
  return typeof v === 'string' && Object.prototype.hasOwnProperty.call(EARN_SOURCE_PUBLISHERS, v);
}

function str(o: Record<string, unknown>, key: string, max: number, required: boolean): string | null {
  const v = o[key];
  if (v === undefined || v === null) {
    if (required) throw new PermanentMessageError(`field ${key} wajib`);
    return null;
  }
  if (typeof v !== 'string' || v.length > max || (required && v.length === 0)) {
    throw new PermanentMessageError(`field ${key} tidak valid`);
  }
  return v;
}

/**
 * Validasi ketat; payload rusak → PermanentMessageError (langsung DLQ, tidak di-retry).
 * @param maxPoints `LOYALTY_MAX_POINTS_PER_EVENT` (SEC-08).
 */
export function parseLoyaltyEarnMessage(payload: unknown, maxPoints: number): LoyaltyEarnMessage {
  if (typeof payload !== 'object' || payload === null || Array.isArray(payload)) {
    throw new PermanentMessageError('payload bukan object');
  }
  const o = payload as Record<string, unknown>;
  if (o.schema_version !== 2) throw new PermanentMessageError('schema_version tidak didukung');
  if ('member_ref' in o || 'user_id' in o) {
    // v2 melarang nomor HP mentah di payload event
    throw new PermanentMessageError('payload v2 tidak boleh memuat member_ref/user_id');
  }
  if (!isMemberHash(o.member_hash)) throw new PermanentMessageError('member_hash tidak valid');
  const points = o.points;
  if (typeof points !== 'number' || !Number.isSafeInteger(points) || points <= 0 || points > maxPoints) {
    throw new PermanentMessageError('points tidak valid / melebihi LOYALTY_MAX_POINTS_PER_EVENT');
  }
  if (!isEarnSource(o.source)) throw new PermanentMessageError('source tidak diizinkan');
  const sourceRef = str(o, 'source_ref', 200, true) as string;
  const idempotencyKey = str(o, 'idempotency_key', 200, true) as string;
  if (o.source === 'price_hunter') {
    if (!isUuid(sourceRef)) throw new PermanentMessageError('source_ref price_hunter harus UUID report');
    if (idempotencyKey !== `price_hunter:${sourceRef}`) {
      throw new PermanentMessageError('idempotency_key price_hunter harus price_hunter:<source_ref>');
    }
  }
  let companyCode: CompanyCode = DEFAULT_LIABILITY_COMPANY;
  if (o.company_code !== undefined && o.company_code !== null) {
    if (!isCompanyCode(o.company_code)) throw new PermanentMessageError('company_code tidak dikenal');
    companyCode = o.company_code;
  }
  return {
    schema_version: 2,
    idempotency_key: idempotencyKey,
    member_hash: o.member_hash,
    points,
    reason: str(o, 'reason', 200, false) ?? '',
    source: o.source,
    source_ref: sourceRef.toLowerCase(),
    company_code: companyCode,
  };
}
