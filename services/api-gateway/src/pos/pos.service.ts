import { Injectable } from '@nestjs/common';
import { AppConfig } from '../config/app-config';
import { ApiError } from '../common/errors/api-error';
import { memberHash } from '../common/pii/pii';
import { RateLimiter } from '../common/rate-limit/rate-limiter';
import { LoyaltyService } from '../loyalty/loyalty.service';
import { OtpService } from '../otp/otp.service';
import { DiscountTokenService } from './discount-token.service';
import { DiscountTokenConsumeDto, PosOtpRequestDto, PosOtpVerifyDto } from './dto/pos.dto';

export interface PosOtpRequestResponse {
  challenge_id: string;
  expires_in: number;
}

export interface PosOtpVerifyResponse {
  ok: true;
  discount_token: string;
  expires_in: number;
}

/**
 * Gerbang OTP promo POS (kontrak §3.1, alur docs/02 §3.1). Dipanggil server Odoo (service key).
 * Rate limit: per member 3/15 menit, per pos_config 20/15 menit (pemanggil selalu server Odoo,
 * sehingga IP tidak bermakna; pos_config mewakili kasir).
 */
@Injectable()
export class PosService {
  constructor(
    private readonly config: AppConfig,
    private readonly otp: OtpService,
    private readonly tokens: DiscountTokenService,
    private readonly limiter: RateLimiter,
    private readonly loyalty: LoyaltyService,
  ) {}

  async requestOtp(dto: PosOtpRequestDto, ip: string | null): Promise<PosOtpRequestResponse> {
    const window = this.config.otpRateWindowSeconds;
    try {
      await this.limiter.enforce(
        `otp:pos:cfg:${dto.companyCode}:${dto.posConfigId}`,
        this.config.otpRateSourceLimit,
        window,
      );
      await this.limiter.enforce(
        `otp:pos:member:${memberHash(this.config.memberHashPepper, dto.memberRef)}`,
        this.config.otpRateMemberLimit,
        window,
      );
    } catch (err) {
      if (err instanceof ApiError && err.getStatus() === 429) this.otp.recordRateLimited('pos_promo');
      throw err;
    }
    const res = await this.otp.issue({
      purpose: 'pos_promo',
      memberRef: dto.memberRef,
      orderRef: dto.orderRef,
      companyCode: dto.companyCode,
      amount: dto.amount,
      ip,
    });
    return { challenge_id: res.challengeId, expires_in: res.expiresIn };
  }

  async verifyOtp(dto: PosOtpVerifyDto, ip: string | null): Promise<PosOtpVerifyResponse> {
    const res = await this.otp.verify({
      purpose: 'pos_promo',
      challengeId: dto.challengeId,
      code: dto.code,
      orderRef: dto.orderRef,
      ip,
    });
    if (!res.ok) {
      throw new ApiError(400, { ok: false, reason: res.reason, attempts_left: res.attemptsLeft });
    }
    const token = this.tokens.issue({
      orderRef: res.orderRef ?? dto.orderRef,
      memberHash: res.memberHash,
      companyCode: res.companyCode ?? '',
      // SEC-07: amount saat OTP diikat ke token; challenge lama tanpa amount → 0 (consume apa pun > 0 ditolak)
      amount: res.amount ?? 0,
    });
    return { ok: true, discount_token: token.token, expires_in: token.expiresIn };
  }

  /** SEC-07: `member_ref` dari partner order harus sama dengan member token → selain itu 409 member_mismatch. */
  async consumeToken(dto: DiscountTokenConsumeDto): Promise<{ ok: true }> {
    const res = await this.tokens.consume(
      dto.discountToken,
      dto.orderRef,
      memberHash(this.config.memberHashPepper, dto.memberRef),
      dto.amountTotal,
    );
    if (!res.ok) throw new ApiError(409, { ok: false, reason: res.reason });
    return { ok: true };
  }

  /** Nomor tidak dikenal → saldo 0 (tidak membocorkan status keanggotaan). */
  async balance(memberRef: string): Promise<{ points_balance: number }> {
    return { points_balance: await this.loyalty.balance(memberRef) };
  }
}
