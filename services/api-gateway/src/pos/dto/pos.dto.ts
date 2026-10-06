import { Errors } from '../../common/errors/api-error';
import { normalizeMsisdn } from '../../common/pii/pii';
import { asRecord, requireInt, requireNumber, requireString, requireUuid } from '../../common/validation';
import { CompanyCode, isCompanyCode } from '../../loyalty/loyalty.service';

/** DTO kontrak §3.1 / §3.4. Validasi manual → 400 `invalid_request` generik (`invalid_phone` untuk nomor). */

/**
 * `order_ref` (kontrak §1): string opaque ≤ 64 karakter (Odoo 19 mengirim `pos.order.uuid`).
 * Hanya panjang & charset (ASCII tercetak) yang divalidasi; formatnya TIDAK di-parse.
 */
export const ORDER_REF_MAX = 64;
const ORDER_REF_RE = /^[\x20-\x7e]{1,64}$/;

function requireOrderRef(o: Record<string, unknown>): string {
  const v = requireString(o, 'order_ref', ORDER_REF_MAX);
  if (!ORDER_REF_RE.test(v)) throw Errors.invalidRequest();
  return v;
}

/**
 * `member_ref` (SEC-07 v2): diambil server Odoo dari nomor HP `partner_id` order. Hanya nomor Indonesia
 * `^62[0-9]{8,13}$` → selain itu 400 `invalid_phone` (SEC-06).
 */
function requireMemberRef(value: unknown): string {
  const memberRef = normalizeMsisdn(value);
  if (!memberRef) throw Errors.invalidPhone();
  return memberRef;
}

export interface PosOtpRequestDto {
  memberRef: string;
  orderRef: string;
  amount: number;
  companyCode: CompanyCode;
  posConfigId: number;
}

export interface PosOtpVerifyDto {
  challengeId: string;
  orderRef: string;
  code: string;
}

export interface DiscountTokenConsumeDto {
  discountToken: string;
  orderRef: string;
  memberRef: string;
  /** SEC-07 (v3): total akhir order (Rupiah), WAJIB. */
  amountTotal: number;
}

export function parsePosOtpRequest(body: unknown): PosOtpRequestDto {
  const o = asRecord(body);
  const memberRef = requireMemberRef(o.member_ref);
  if (!isCompanyCode(o.company_code)) throw Errors.invalidRequest();
  return {
    memberRef,
    orderRef: requireOrderRef(o),
    amount: requireNumber(o, 'amount', 0, 1_000_000_000_000),
    companyCode: o.company_code,
    posConfigId: requireInt(o, 'pos_config_id', 1, 2_147_483_647),
  };
}

export function parsePosOtpVerify(body: unknown): PosOtpVerifyDto {
  const o = asRecord(body);
  const code = o.code;
  if (typeof code !== 'string' || code.length > 16) throw Errors.invalidRequest();
  return {
    challengeId: requireUuid(o, 'challenge_id'),
    orderRef: requireOrderRef(o),
    code: code.trim(),
  };
}

/**
 * Kontrak §3.1 v2 + SEC-07 sisa (v3, menunggu pencatatan lead):
 * `{ discount_token, order_ref, member_ref, amount_total }` — member_ref & amount_total wajib.
 */
export function parseDiscountTokenConsume(body: unknown): DiscountTokenConsumeDto {
  const o = asRecord(body);
  const discountToken = requireString(o, 'discount_token', 4096);
  const orderRef = requireOrderRef(o);
  const memberRef = requireMemberRef(o.member_ref);
  return { discountToken, orderRef, memberRef, amountTotal: requireNumber(o, 'amount_total', 0, 1_000_000_000_000) };
}

/** R2 (§8.13): `POST /v1/pos/loyalty/balance` body `{ member_ref }`. */
export function parseLoyaltyBalance(body: unknown): string {
  return requireMemberRef(asRecord(body).member_ref);
}
