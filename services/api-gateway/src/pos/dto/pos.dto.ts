import { Errors } from '../../common/errors/api-error';
import { normalizeMsisdn } from '../../common/pii/pii';
import { asRecord, requireInt, requireNumber, requireString, requireUuid } from '../../common/validation';
import { CompanyCode, isCompanyCode } from '../../loyalty/loyalty.service';
import { PosOtpPurpose, isPosOtpPurpose } from '../../otp/otp.service';
import { DISCOUNT_TOKEN_PURPOSE } from '../discount-token.service';

/** DTO kontrak §3.1 / §3.4. Validasi manual → 400 `invalid_request` generik (`invalid_phone` untuk nomor). */

/**
 * `order_ref` (kontrak §1): string opaque ≤ 64 karakter (Odoo 19 mengirim `pos.order.uuid`).
 * Hanya panjang & charset (ASCII tercetak) yang divalidasi; formatnya TIDAK di-parse.
 */
export const ORDER_REF_MAX = 64;
const ORDER_REF_RE = /^[\x20-\x7e]{1,64}$/;

export function requireOrderRef(o: Record<string, unknown>): string {
  const v = requireString(o, 'order_ref', ORDER_REF_MAX);
  if (!ORDER_REF_RE.test(v)) throw Errors.invalidRequest();
  return v;
}

/**
 * `member_ref` (SEC-07 v2): diambil server Odoo dari nomor HP `partner_id` order. Hanya nomor Indonesia
 * `^62[0-9]{8,13}$` → selain itu 400 `invalid_phone` (SEC-06).
 */
export function requireMemberRef(value: unknown): string {
  const memberRef = normalizeMsisdn(value);
  if (!memberRef) throw Errors.invalidPhone();
  return memberRef;
}

export interface PosOtpRequestDto {
  /** docs/04 §9.3: `pos_promo` (default) | `pos_redeem` | `pos_enroll` (§9.9 K-18-8). */
  purpose: PosOtpPurpose;
  /** K-18-8: wajib `true` untuk `pos_enroll` (centang kasir atas pernyataan usia pelanggan). */
  ageConfirmed?: boolean;
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
  const purpose = o.purpose === undefined || o.purpose === null ? DISCOUNT_TOKEN_PURPOSE : o.purpose;
  if (!isPosOtpPurpose(purpose)) throw Errors.invalidRequest();
  return {
    purpose,
    ageConfirmed: optionalBool(o, 'age_confirmed'),
    memberRef,
    orderRef: requireOrderRef(o),
    amount: requireNumber(o, 'amount', 0, 1_000_000_000_000),
    companyCode: o.company_code,
    posConfigId: requireInt(o, 'pos_config_id', 1, 2_147_483_647),
  };
}

/** `age_confirmed` opsional: tidak ada/null = false; selain boolean → 400 `invalid_request`. */
function optionalBool(o: Record<string, unknown>, key: string): boolean {
  const v = o[key];
  if (v === undefined || v === null) return false;
  if (typeof v !== 'boolean') throw Errors.invalidRequest();
  return v;
}

export function parsePosOtpVerify(body: unknown): PosOtpVerifyDto {
  const o = asRecord(body);
  const code = o.code;
  if (typeof code !== 'string' || code.length > 16) throw Errors.invalidRequest();
  // K-18-8: Odoo boleh meneruskan age_confirmed ke verify; divalidasi bentuknya saja (pernyataan diikat saat request)
  optionalBool(o, 'age_confirmed');
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

export interface LoyaltyBalanceDto {
  memberRef: string;
  /** K-8: kunci rate limit per kasir. Opsional demi kompatibilitas; Odoo WAJIB mengirim (tanpa → bucket bersama). */
  posConfigId: number | null;
  companyCode: CompanyCode | null;
}

/**
 * R2 (§8.13) + K-8 (§9.6): `POST /v1/pos/loyalty/balance` body `{ member_ref, pos_config_id?, company_code? }`.
 */
export function parseLoyaltyBalance(body: unknown): LoyaltyBalanceDto {
  const o = asRecord(body);
  const memberRef = requireMemberRef(o.member_ref);
  const posConfigId = o.pos_config_id === undefined || o.pos_config_id === null ? null : requireInt(o, 'pos_config_id', 1, 2_147_483_647);
  let companyCode: CompanyCode | null = null;
  if (o.company_code !== undefined && o.company_code !== null) {
    if (!isCompanyCode(o.company_code)) throw Errors.invalidRequest();
    companyCode = o.company_code;
  }
  return { memberRef, posConfigId, companyCode };
}
