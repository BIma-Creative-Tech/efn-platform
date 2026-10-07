import { Errors } from '../../common/errors/api-error';
import { asRecord, requireInt, requireNumber, requireString, requireUuid } from '../../common/validation';
import { CompanyCode, isCompanyCode } from '../../loyalty/loyalty.service';
import { requireMemberRef, requireOrderRef } from './pos.dto';

/**
 * DTO loyalty POS (docs/04 §9.2–9.4). Validasi manual → 400 `invalid_request` generik (`invalid_phone` untuk nomor).
 * `member_ref` hanya di BODY (R2: tidak pernah di URL/query).
 */

/** Rupiah maksimum yang diterima (Rp10 miliar) — batas sanity, bukan aturan bisnis. */
export const MAX_AMOUNT_IDR = 10_000_000_000;
const MAX_POINTS = 1_000_000;

/** `<prefix>:<order_uuid>` — sisa key = ASCII tercetak tanpa spasi, ≤ 64 karakter (order_ref opak, §1). */
function requireIdempotencyKey(o: Record<string, unknown>, prefix: string): string {
  const v = requireString(o, 'idempotency_key', prefix.length + 1 + 64);
  if (!v.startsWith(`${prefix}:`) || !/^[\x21-\x7e]{1,64}$/.test(v.slice(prefix.length + 1))) {
    throw Errors.invalidRequest();
  }
  return v;
}

function requireRef(o: Record<string, unknown>, key: string): string {
  return requireOrderRef({ order_ref: o[key] });
}

export interface PosEarnDto {
  idempotencyKey: string;
  orderRef: string;
  memberRef: string;
  companyCode: CompanyCode;
  eligibleAmount: number;
  /**
   * SEC-61 (Gelombang 13c, opsional, non-breaking): pos.config asal order — kunci batas poin per kasir per jam
   * (`LOYALTY_EARN_POS_CONFIG_HOURLY_MAX_POINTS`). Tidak ada → hanya batas per PT per jam yang berlaku.
   */
  posConfigId?: number | null;
}

/**
 * §9.2 `{ idempotency_key: "pos_earn:<order_uuid>", order_ref, member_ref, company_code, eligible_amount,
 * pos_config_id? }`.
 */
export function parsePosEarn(body: unknown): PosEarnDto {
  const o = asRecord(body);
  const memberRef = requireMemberRef(o.member_ref);
  if (!isCompanyCode(o.company_code)) throw Errors.invalidRequest();
  return {
    idempotencyKey: requireIdempotencyKey(o, 'pos_earn'),
    orderRef: requireOrderRef(o),
    memberRef,
    companyCode: o.company_code,
    eligibleAmount: requireNumber(o, 'eligible_amount', 0, MAX_AMOUNT_IDR),
    posConfigId: o.pos_config_id === undefined || o.pos_config_id === null ? null : requireInt(o, 'pos_config_id', 1, 2_147_483_647),
  };
}

export interface RedeemReserveDto {
  discountToken: string;
  orderRef: string;
  memberRef: string;
  points: number;
  amountTotal: number;
}

/** §9.3 `{ discount_token, order_ref, member_ref, points, amount_total }`. */
export function parseRedeemReserve(body: unknown): RedeemReserveDto {
  const o = asRecord(body);
  const discountToken = requireString(o, 'discount_token', 4096);
  const orderRef = requireOrderRef(o);
  const memberRef = requireMemberRef(o.member_ref);
  return {
    discountToken,
    orderRef,
    memberRef,
    points: requireInt(o, 'points', 1, MAX_POINTS),
    amountTotal: requireNumber(o, 'amount_total', 0, MAX_AMOUNT_IDR),
  };
}

export interface RedeemCommitDto {
  reservationId: string;
  orderRef: string;
  /** K-3: diverifikasi terhadap member reservasi (member_mismatch). */
  memberRef: string;
  /** K-3: harus sama dengan value_idr reservasi (value_mismatch). */
  valueIdr: number;
  idempotencyKey: string;
}

/** §9.3 + §9.6 K-3 `{ reservation_id, order_ref, member_ref, value_idr, idempotency_key: "pos_redeem:<order_uuid>" }`. */
export function parseRedeemCommit(body: unknown): RedeemCommitDto {
  const o = asRecord(body);
  const memberRef = requireMemberRef(o.member_ref);
  return {
    reservationId: requireUuid(o, 'reservation_id'),
    orderRef: requireOrderRef(o),
    memberRef,
    valueIdr: requireInt(o, 'value_idr', 0, MAX_AMOUNT_IDR),
    idempotencyKey: requireIdempotencyKey(o, 'pos_redeem'),
  };
}

/** §9.3 `{ reservation_id }`. */
export function parseRedeemRelease(body: unknown): string {
  return requireUuid(asRecord(body), 'reservation_id');
}

export interface PosReverseDto {
  idempotencyKey: string;
  originalOrderRef: string;
  refundOrderRef: string;
  memberRef: string;
  refundedEligibleAmount: number;
  /** 0 < r ≤ 1. */
  refundRatio: number;
}

/**
 * §9.4 `{ idempotency_key: "pos_reverse:<refund_order_uuid>", original_order_ref, refund_order_ref, member_ref,
 * refunded_eligible_amount, refund_ratio }`.
 */
export function parsePosReverse(body: unknown): PosReverseDto {
  const o = asRecord(body);
  const memberRef = requireMemberRef(o.member_ref);
  const refundRatio = requireNumber(o, 'refund_ratio', 0, 1);
  if (refundRatio <= 0) throw Errors.invalidRequest();
  const originalOrderRef = requireRef(o, 'original_order_ref');
  const refundOrderRef = requireRef(o, 'refund_order_ref');
  if (originalOrderRef === refundOrderRef) throw Errors.invalidRequest();
  return {
    idempotencyKey: requireIdempotencyKey(o, 'pos_reverse'),
    originalOrderRef,
    refundOrderRef,
    memberRef,
    refundedEligibleAmount: requireNumber(o, 'refunded_eligible_amount', 0, MAX_AMOUNT_IDR),
    refundRatio,
  };
}
