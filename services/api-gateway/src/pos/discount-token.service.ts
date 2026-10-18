import { Inject, Injectable } from '@nestjs/common';
import { randomUUID } from 'node:crypto';
import { TokenExpiredError, sign, verify } from 'jsonwebtoken';
import { AppConfig } from '../config/app-config';
import { KV_STORE, KvStore } from '../common/redis/kv-store';
import { safeEqualHex } from '../common/security/safe-equal';
/** Purpose token diskon/redeem (subset purpose OTP POS; `pos_enroll` TIDAK pernah menghasilkan token). */
export type DiscountTokenPurpose = 'pos_promo' | 'pos_redeem';

export type ConsumeFailureReason =
  | 'already_used'
  | 'order_mismatch'
  | 'member_mismatch'
  | 'amount_mismatch'
  | 'expired'
  | 'invalid';

/** SEC-07: batas bawah total akhir relatif terhadap `amount` saat OTP (50%). */
export const AMOUNT_MIN_RATIO = 0.5;

/**
 * SEC-07 sisa: total akhir order (`amount_total` saat consume) harus berada di
 * [50% × amount_otp, amount_otp × (1 + POS_OTP_AMOUNT_TOLERANCE_PCT/100)].
 * Alasan: OTP dimintakan untuk order bernilai X; kasir tidak boleh memakai token itu untuk order yang jauh lebih
 * besar (menumpuk barang setelah OTP) atau jauh lebih kecil (memindah promo ke transaksi lain). Toleransi atas
 * menampung tambahan item kecil setelah OTP.
 */
export function amountWithinBounds(otpAmount: number, amountTotal: number, tolerancePct: number): boolean {
  const lower = otpAmount * AMOUNT_MIN_RATIO;
  const upper = otpAmount * (1 + tolerancePct / 100);
  return amountTotal >= lower && amountTotal <= upper;
}
export type ConsumeResult = { ok: true } | { ok: false; reason: ConsumeFailureReason };

export interface IssueDiscountTokenInput {
  /** Purpose challenge OTP asal (docs/04 §9.3). Default `pos_promo`. */
  purpose?: DiscountTokenPurpose;
  orderRef: string;
  memberHash: string;
  companyCode: string;
  /** SEC-07: `amount` dari request OTP (Rupiah). */
  amount: number;
}

/** Klaim token yang sudah diverifikasi tanda tangan, exp, bentuk, dan purpose-nya. */
export interface DiscountTokenClaims {
  jti: string;
  purpose: DiscountTokenPurpose;
  orderRef: string;
  memberHash: string;
  companyCode: string;
  amount: number;
  exp: number;
}

export type VerifyTokenResult = { ok: true; claims: DiscountTokenClaims } | { ok: false; reason: 'expired' | 'invalid' };

export const DISCOUNT_TOKEN_PURPOSE = 'pos_promo';
const MAX_TOKEN_LENGTH = 4096;
/** Penanda `used` disimpan sedikit lebih lama dari sisa umur token. */
const USED_MARKER_GRACE_SECONDS = 60;

/**
 * `discount_token` (kontrak §3.1, §9.3): JWT HS256 (`discount_token_secret`) dengan klaim
 * `{ jti, purpose: "pos_promo" | "pos_redeem", order_ref, member_hash, company_code, amount, iat, exp }`, exp 600 s
 * (`amount` = v3 SEC-07, lihat `amountWithinBounds`). `purpose` = purpose challenge OTP asal: token `pos_redeem`
 * hanya diterima reserve redeem poin (`PosLoyaltyService`), token `pos_promo` hanya diterima consume promo.
 *
 * Sekali pakai (promo): saat consume, `SET dtok:used:<jti> <order_ref> NX EX` — hanya pemanggil pertama yang menang.
 * Idempotent untuk pasangan jti + order_ref yang sama (retry Odoo bila commit gagal setelah consume sukses):
 * panggilan ulang dengan order yang sama → ok; dengan order lain → already_used.
 * Urutan cek: tanda tangan & exp → purpose → member_hash (SEC-07, `member_mismatch`) → amount_total
 * (`amount_mismatch`, tidak membakar token) → marker `used` (replay)
 * → order_ref (mismatch tidak membakar token) → SET NX. Mismatch member juga tidak membakar token.
 * Catatan: retry setelah token kedaluwarsa menjawab `expired` (verifikasi exp didahulukan).
 */
@Injectable()
export class DiscountTokenService {
  now: () => number = () => Date.now();

  constructor(
    private readonly config: AppConfig,
    @Inject(KV_STORE) private readonly kv: KvStore,
  ) {}

  issue(input: IssueDiscountTokenInput): { token: string; expiresIn: number } {
    const ttl = this.config.discountTokenTtlSeconds;
    const iat = Math.floor(this.now() / 1000);
    const token = sign(
      {
        jti: randomUUID(),
        purpose: input.purpose ?? DISCOUNT_TOKEN_PURPOSE,
        order_ref: input.orderRef,
        member_hash: input.memberHash,
        company_code: input.companyCode,
        amount: input.amount,
        iat,
        exp: iat + ttl,
      },
      this.config.discountTokenSecret,
      { algorithm: 'HS256' },
    );
    return { token, expiresIn: ttl };
  }

  /**
   * Verifikasi tanda tangan, exp, bentuk klaim, dan purpose. Purpose berbeda → `invalid` (token redeem tidak bisa
   * dipakai untuk promo dan sebaliknya). Tidak menyentuh state sekali-pakai.
   */
  verifyClaims(token: string, expectedPurpose: DiscountTokenPurpose): VerifyTokenResult {
    if (typeof token !== 'string' || token.length === 0 || token.length > MAX_TOKEN_LENGTH) {
      return { ok: false, reason: 'invalid' };
    }
    let claims: Record<string, unknown>;
    try {
      const decoded = verify(token, this.config.discountTokenSecret, {
        algorithms: ['HS256'],
        clockTimestamp: Math.floor(this.now() / 1000),
      });
      if (typeof decoded === 'string') return { ok: false, reason: 'invalid' };
      claims = decoded as Record<string, unknown>;
    } catch (err) {
      if (err instanceof TokenExpiredError) return { ok: false, reason: 'expired' };
      return { ok: false, reason: 'invalid' };
    }
    const { jti, purpose, exp } = claims;
    if (
      purpose !== expectedPurpose ||
      typeof jti !== 'string' ||
      jti.length === 0 ||
      jti.length > 64 ||
      typeof claims.order_ref !== 'string' ||
      typeof claims.member_hash !== 'string' ||
      typeof claims.amount !== 'number' ||
      !Number.isFinite(claims.amount) ||
      typeof exp !== 'number'
    ) {
      return { ok: false, reason: 'invalid' };
    }
    return {
      ok: true,
      claims: {
        jti,
        purpose: expectedPurpose,
        orderRef: claims.order_ref,
        memberHash: claims.member_hash,
        companyCode: typeof claims.company_code === 'string' ? claims.company_code : '',
        amount: claims.amount,
        exp,
      },
    };
  }

  /**
   * @param memberHashOfOrder member_hash dari `member_ref` yang dikirim Odoo saat consume (nomor HP partner order).
   * @param amountTotal total akhir order (Rupiah) yang dikirim Odoo saat consume (SEC-07).
   */
  async consume(token: string, orderRef: string, memberHashOfOrder: string, amountTotal: number): Promise<ConsumeResult> {
    const verified = this.verifyClaims(token, DISCOUNT_TOKEN_PURPOSE);
    if (!verified.ok) return verified;
    const claims = verified.claims;
    const nowSec = Math.floor(this.now() / 1000);
    // SEC-07: token hanya berlaku untuk pelanggan yang tercatat pada order (bukan nomor yang diketik kasir)
    if (!safeEqualHex(claims.memberHash, memberHashOfOrder)) return { ok: false, reason: 'member_mismatch' };
    // SEC-07 sisa: nilai order tidak boleh bergeser jauh dari nilai saat OTP (tidak membakar token)
    if (!amountWithinBounds(claims.amount, amountTotal, this.config.posOtpAmountTolerancePct)) {
      return { ok: false, reason: 'amount_mismatch' };
    }
    const usedKey = `dtok:used:${claims.jti}`;
    // Sudah pernah dipakai? Idempotent untuk order yang sama (retry Odoo), selain itu already_used.
    const usedFor = await this.kv.get(usedKey);
    if (usedFor !== null) return this.replayResult(usedFor, orderRef);

    // Belum dipakai: order harus sama dengan klaim token. Mismatch TIDAK membakar token.
    if (claims.orderRef !== orderRef) return { ok: false, reason: 'order_mismatch' };

    const ttl = Math.max(claims.exp - nowSec, 1) + USED_MARKER_GRACE_SECONDS;
    const first = await this.kv.setNxEx(usedKey, orderRef, ttl);
    if (first) return { ok: true };
    // Kalah balapan dengan consume paralel: bandingkan dengan nilai pemenang
    const winner = await this.kv.get(usedKey);
    return winner === null ? { ok: false, reason: 'already_used' } : this.replayResult(winner, orderRef);
  }

  private replayResult(usedForOrderRef: string, orderRef: string): ConsumeResult {
    return usedForOrderRef === orderRef ? { ok: true } : { ok: false, reason: 'already_used' };
  }
}
