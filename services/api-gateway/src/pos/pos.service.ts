import { Inject, Injectable, Optional } from '@nestjs/common';
import { Errors } from '../common/errors/api-error';
import { MemberService } from '../common/member/member.service';
import { AppConfig } from '../config/app-config';
import { ApiError } from '../common/errors/api-error';
import { memberHash } from '../common/pii/pii';
import { RateLimiter } from '../common/rate-limit/rate-limiter';
import { LoyaltyService } from '../loyalty/loyalty.service';
import { OtpService } from '../otp/otp.service';
import { DiscountTokenService } from './discount-token.service';
import { DiscountTokenConsumeDto, LoyaltyBalanceDto, PosOtpRequestDto, PosOtpVerifyDto } from './dto/pos.dto';

export interface PosOtpRequestResponse {
  challenge_id: string;
  expires_in: number;
}

export interface PosOtpVerifyResponse {
  ok: true;
  discount_token: string;
  expires_in: number;
}

/** K-18-8: verify challenge `pos_enroll` — member terdaftar/terverifikasi; tidak ada discount_token. */
export interface PosEnrollVerifyResponse {
  ok: true;
  enrolled: true;
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
    /** K-18-8: upsert member saat `pos_enroll` (token eksplisit; opsional agar test lama tetap jalan). */
    @Optional() @Inject(MemberService) private readonly members: MemberService | null = null,
  ) {}

  async requestOtp(dto: PosOtpRequestDto, ip: string | null): Promise<PosOtpRequestResponse> {
    // K-18-8: pendaftaran member oleh kasir wajib pernyataan usia minimum (kode galat sama dengan §3.2)
    if (dto.purpose === 'pos_enroll' && !dto.ageConfirmed) {
      throw ApiError.of(
        400,
        'age_confirmation_required',
        `Konfirmasi bahwa pelanggan berusia minimal ${this.config.memberMinAge} tahun untuk melanjutkan.`,
      );
    }
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
      if (err instanceof ApiError && err.getStatus() === 429) this.otp.recordRateLimited(dto.purpose);
      throw err;
    }
    // Rate limit per member & per pos_config dibagi bersama pos_promo dan pos_redeem (satu kanal kasir).
    const res = await this.otp.issue({
      purpose: dto.purpose,
      memberRef: dto.memberRef,
      orderRef: dto.orderRef,
      companyCode: dto.companyCode,
      amount: dto.amount,
      posConfigId: dto.posConfigId,
      ip,
    });
    return { challenge_id: res.challengeId, expires_in: res.expiresIn };
  }

  async verifyOtp(dto: PosOtpVerifyDto, ip: string | null): Promise<PosOtpVerifyResponse | PosEnrollVerifyResponse> {
    // Body verify tidak memuat purpose: purpose dibawa challenge (pos_promo | pos_redeem | pos_enroll).
    const res = await this.otp.verify({
      purpose: 'pos_promo',
      alsoAccept: ['pos_redeem', 'pos_enroll'],
      challengeId: dto.challengeId,
      code: dto.code,
      orderRef: dto.orderRef,
      ip,
    });
    if (!res.ok) {
      throw new ApiError(400, { ok: false, reason: res.reason, attempts_left: res.attemptsLeft });
    }
    if (res.purpose === 'pos_enroll') return this.enroll(res.memberRef, res.posConfigId);
    const token = this.tokens.issue({
      purpose: res.purpose === 'pos_redeem' ? 'pos_redeem' : 'pos_promo',
      orderRef: res.orderRef ?? dto.orderRef,
      memberHash: res.memberHash,
      companyCode: res.companyCode ?? '',
      // SEC-07: amount saat OTP diikat ke token; challenge lama tanpa amount → 0 (consume apa pun > 0 ditolak)
      amount: res.amount ?? 0,
    });
    return { ok: true, discount_token: token.token, expires_in: token.expiresIn };
  }

  /**
   * K-18-8: OTP `pos_enroll` terverifikasi (pemilik nomor hadir) → upsert `platform.member` dengan
   * `age_confirmed_at`, `age_confirmed_source = 'pos'`, `age_confirmed_pos_config_id`. Akun sedang dihapus → 409
   * `account_deleting` (hanya terlihat setelah OTP pemilik nomor). Odoo tidak menyimpan usia.
   */
  private async enroll(memberRef: string | null, posConfigId: number | null): Promise<PosEnrollVerifyResponse> {
    if (!this.members || !memberRef) throw Errors.serviceUnavailable();
    const m = await this.members.upsertOnLogin(memberRef, { ageConfirmed: true, ageSource: 'pos', posConfigId });
    if (m.deleting) throw Errors.conflict('account_deleting', 'Akun sedang dalam proses penghapusan.');
    return { ok: true, enrolled: true };
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

  /**
   * Nomor tidak dikenal → saldo 0 (tidak membocorkan status keanggotaan). K-8 (§9.6): tanpa OTP; rate limit per member
   * (`LOYALTY_BALANCE_RL_MEMBER_LIMIT`) dan per pos_config (`LOYALTY_BALANCE_RL_SOURCE_LIMIT`) per
   * `LOYALTY_RL_WINDOW_SECONDS` — membatasi enumerasi saldo oleh kasir. Respons hanya saldo.
   */
  async balance(dto: LoyaltyBalanceDto): Promise<{ points_balance: number }> {
    const window = this.config.loyaltyRateWindowSeconds;
    const cfg = dto.posConfigId === null ? 'na' : `${dto.companyCode ?? 'na'}:${dto.posConfigId}`;
    await this.limiter.enforce(`loy:bal:cfg:${cfg}`, this.config.loyaltyBalanceRateSourceLimit, window);
    await this.limiter.enforce(
      `loy:bal:member:${memberHash(this.config.memberHashPepper, dto.memberRef)}`,
      this.config.loyaltyBalanceRateMemberLimit,
      window,
    );
    return { points_balance: await this.loyalty.balance(dto.memberRef) };
  }
}
