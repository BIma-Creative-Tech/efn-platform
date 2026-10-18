import { Errors } from '../../common/errors/api-error';
import { asRecord, requireInt, requireNumber, requireString, requireUuid } from '../../common/validation';
import { CompanyCode, isCompanyCode } from '../../loyalty/loyalty.service';
import { requireMemberRef, requireOrderRef } from './pos.dto';

/**
 * DTO loyalty POS (docs/04 §9.2–9.4). Validasi manual → 400 `invalid_request` generik (`invalid_phone` untuk nomor).
 * `member_ref` hanya di BODY (R2: tidak pernah di URL/query).
 */

/**
 * G18 (W-03 presisi IDR Rp1, kontrak §9.9 v6): semua nilai Rupiah poin INTEGER. Nilai yang dibandingkan persis
 * (`amount_total` reserve, `value_idr`, `payment_amount`, `points_restored_value`) wajib integer → selain itu 400;
 * dasar earn/refund (`eligible_amount`, `refunded_eligible_amount`) dibulatkan ke BAWAH ke Rupiah (memihak PT).
 */
function floorIdr(o: Record<string, unknown>, key: string): number {
  return Math.floor(requireNumber(o, key, 0, MAX_AMOUNT_IDR) + 1e-9);
}

function optionalInt(o: Record<string, unknown>, key: string, min: number): number | null {
  return o[key] === undefined || o[key] === null ? null : requireInt(o, key, min, MAX_AMOUNT_IDR);
}

function optionalBool(o: Record<string, unknown>, key: string): boolean {
  const v = o[key];
  if (v === undefined || v === null) return false;
  if (typeof v !== 'boolean') throw Errors.invalidRequest();
  return v;
}

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
    eligibleAmount: floorIdr(o, 'eligible_amount'),
    posConfigId: o.pos_config_id === undefined || o.pos_config_id === null ? null : requireInt(o, 'pos_config_id', 1, 2_147_483_647),
  };
}

export interface RedeemReserveDto {
  discountToken: string;
  orderRef: string;
  memberRef: string;
  points: number;
  /** K-18-2: total order TERMASUK pajak (dasar `LOYALTY_REDEEM_MAX_PCT_OF_ORDER`), integer Rupiah. */
  amountTotal: number;
}

/** §9.3 `{ discount_token, order_ref, member_ref, points, amount_total }` (K-18-2: `amount_total` integer incl pajak). */
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
    amountTotal: requireInt(o, 'amount_total', 0, MAX_AMOUNT_IDR),
  };
}

export interface RedeemCommitDto {
  reservationId: string;
  orderRef: string;
  /** K-3: diverifikasi terhadap member reservasi (member_mismatch). */
  memberRef: string;
  /** K-3: harus sama dengan value_idr reservasi (value_mismatch). */
  valueIdr: number;
  /** K-18-3: nilai baris pembayaran Poin EFN di order (integer Rupiah) — wajib sama dengan `value_idr` reservasi. */
  paymentAmount: number;
  /**
   * SEC-128 (opsional, usulan kontrak): total nilai barang order FINAL termasuk pajak (integer Rupiah, = Odoo
   * `abs(_efn_goods_incl_raw())`). Disimpan di reservasi sebagai penyebut alokasi restore proporsional. Tidak ada → null.
   */
  orderGoodsIncl: number | null;
  idempotencyKey: string;
}

/**
 * §9.3 + §9.6 K-3 + §9.9 K-18-3 `{ reservation_id, order_ref, member_ref, value_idr, payment_amount,
 * idempotency_key: "pos_redeem:<order_uuid>" }`.
 */
export function parseRedeemCommit(body: unknown): RedeemCommitDto {
  const o = asRecord(body);
  const memberRef = requireMemberRef(o.member_ref);
  return {
    reservationId: requireUuid(o, 'reservation_id'),
    orderRef: requireOrderRef(o),
    memberRef,
    valueIdr: requireInt(o, 'value_idr', 0, MAX_AMOUNT_IDR),
    paymentAmount: requireInt(o, 'payment_amount', 0, MAX_AMOUNT_IDR),
    orderGoodsIncl: optionalInt(o, 'order_goods_incl', 1),
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
  /** 0 < r ≤ 1. Tetap dasar clawback earn (K-18-5). */
  refundRatio: number;
  /**
   * K-18-5: nilai Rupiah (integer ≥ 0) baris pembayaran Poin EFN negatif pada refund. Ada → poin redeem dikembalikan
   * = floor(nilai ÷ `LOYALTY_POINT_VALUE_IDR`); tidak ada (event lama) → `refund_ratio` seperti v4.
   */
  pointsRestoredValue: number | null;
  /**
   * SEC-128 (opsional, usulan kontrak): nilai barang refund INI termasuk pajak (integer Rupiah, absolut, = Odoo
   * `abs(refund._efn_goods_incl_raw(goods))`). Ada → cek restore proporsional ketat (Rp1) tanpa bergantung kategori earn.
   */
  refundGoodsIncl: number | null;
  /**
   * SEC-128: hold/review — supervisor Odoo menyetujui reverse yang sebelumnya ditolak `restore_value_mismatch`. BFF
   * melewati cek nilai (restore tetap dibatasi sisa redeem) dan mencatat anomali `restore_value_override`.
   */
  restoreApproved: boolean;
}

/**
 * §9.4 `{ idempotency_key: "pos_reverse:<refund_order_uuid>", original_order_ref, refund_order_ref, member_ref,
 * refunded_eligible_amount, refund_ratio, points_restored_value? }` (K-18-5).
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
    refundedEligibleAmount: floorIdr(o, 'refunded_eligible_amount'),
    refundRatio,
    pointsRestoredValue:
      o.points_restored_value === undefined || o.points_restored_value === null ? null : requireInt(o, 'points_restored_value', 0, MAX_AMOUNT_IDR),
    refundGoodsIncl: optionalInt(o, 'refund_goods_incl', 0),
    restoreApproved: optionalBool(o, 'restore_approved'),
  };
}
