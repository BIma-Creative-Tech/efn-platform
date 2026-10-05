import { Injectable, Logger } from '@nestjs/common';
import { AppConfig } from '../config/app-config';
import { ApiError, Errors } from '../common/errors/api-error';
import { MemberService } from '../common/member/member.service';
import { maskMsisdn, memberHash, redisKeyHash } from '../common/pii/pii';
import { RateLimiter } from '../common/rate-limit/rate-limiter';
import { AccessTokenService, AuthUser } from '../common/security/access-token.service';
import { LoyaltyService } from '../loyalty/loyalty.service';
import { OtpService } from '../otp/otp.service';
import { AltchaChallenge, AltchaService } from './altcha.service';
import { AuthOtpRequestDto, AuthOtpVerifyDto } from './dto/auth.dto';

export interface AuthOtpRequestResponse {
  challenge_id: string;
  expires_in: number;
}

export interface AuthOtpVerifyResponse {
  access_token: string;
  token_type: 'Bearer';
  expires_in: number;
}

export interface MeResponse {
  member_ref_masked: string;
  points_balance: number;
}

/**
 * Login pelanggan Super-App dengan OTP WhatsApp (kontrak §3.2, v3 §8.1–§8.2).
 * - `GET /v1/auth/challenge` → challenge ALTCHA; `POST /v1/auth/otp/request` wajib `altcha` valid & belum dipakai
 *   (`400 captcha_required`). Captcha diverifikasi SEBELUM rate limit agar bot tanpa proof-of-work tidak
 *   menghabiskan kuota nomor korban.
 * - Request selalu 202 untuk nomor Indonesia berformat valid (login = daftar). Nomor non-Indonesia → 400 `invalid_phone`.
 * - Sukses verify → upsert `platform.member` → access token JWT HS256 dengan `sub` = member.id (UUID, bukan HP).
 *
 * Rate limit & mitigasi lockout tertarget (SEC-06), nama key TANPA IP mentah (SEC-24: `ipk` = HMAC turunan pepper):
 * 1. Per IP klien: 20 request / 15 menit (`otp:login:ip:<ipk>`), verify 60 / 15 menit per IP.
 * 2. Per (nomor, IP klien): 3 / 15 menit (`otp:login:member-ip:<member_hash>:<ipk>`).
 * 3. Per nomor lintas IP: plafon 10 / jam (`otp:login:member:<member_hash>`).
 * 4. Budget global per jam (OtpService).
 * 5. Verifikasi tidak pernah dikunci per nomor: lock hanya per challenge.
 */
@Injectable()
export class AuthService {
  private readonly logger = new Logger(AuthService.name);

  constructor(
    private readonly config: AppConfig,
    private readonly otp: OtpService,
    private readonly limiter: RateLimiter,
    private readonly tokens: AccessTokenService,
    private readonly loyalty: LoyaltyService,
    private readonly altcha: AltchaService,
    private readonly members: MemberService,
  ) {}

  /** Pseudonim IP untuk nama key Redis (SEC-24). */
  ipKey(ip: string | null): string {
    return redisKeyHash(this.config.redisKeyPepper, ip ?? 'unknown');
  }

  async challenge(ip: string | null): Promise<AltchaChallenge> {
    await this.limiter.enforce(`altcha:ip:${this.ipKey(ip)}`, this.config.altchaChallengeRateLimit, this.config.otpRateWindowSeconds);
    return this.altcha.createChallenge();
  }

  async requestOtp(dto: AuthOtpRequestDto, ip: string | null): Promise<AuthOtpRequestResponse> {
    if (!(await this.altcha.verify(dto.altcha))) throw Errors.captchaRequired();
    const window = this.config.otpRateWindowSeconds;
    const ipk = this.ipKey(ip);
    const subject = memberHash(this.config.memberHashPepper, dto.phone);
    try {
      await this.limiter.enforce(`otp:login:ip:${ipk}`, this.config.otpRateSourceLimit, window);
      await this.limiter.enforce(`otp:login:member-ip:${subject}:${ipk}`, this.config.otpRateMemberLimit, window);
      await this.limiter.enforce(
        `otp:login:member:${subject}`,
        this.config.otpRateMemberGlobalLimit,
        this.config.otpRateMemberGlobalWindowSeconds,
      );
    } catch (err) {
      if (err instanceof ApiError && err.getStatus() === 429) this.otp.recordRateLimited('login');
      throw err;
    }
    const res = await this.otp.issue({ purpose: 'login', memberRef: dto.phone, ip });
    return { challenge_id: res.challengeId, expires_in: res.expiresIn };
  }

  async verifyOtp(dto: AuthOtpVerifyDto, ip: string | null): Promise<AuthOtpVerifyResponse> {
    await this.limiter.enforce(
      `otp:login:verify-ip:${this.ipKey(ip)}`,
      this.config.otpRateVerifyLimit,
      this.config.otpRateWindowSeconds,
    );
    const res = await this.otp.verify({ purpose: 'login', challengeId: dto.challengeId, code: dto.code, ip });
    if (!res.ok) {
      throw new ApiError(400, { ok: false, reason: res.reason, attempts_left: res.attemptsLeft });
    }
    if (!res.memberRef) {
      // Tidak seharusnya terjadi: challenge login selalu menyimpan member_ref (terenkripsi)
      this.logger.error('Challenge login tanpa member_ref yang dapat dibaca');
      throw new ApiError(400, { ok: false, reason: 'invalid', attempts_left: 0 });
    }
    const member = await this.members.upsertOnLogin(res.memberRef);
    if (member.deleting) {
      // Hanya pemilik nomor (lolos OTP) yang melihat status ini → tidak membocorkan keanggotaan ke pihak lain
      throw Errors.conflict('account_deleting', 'Akun sedang dalam proses penghapusan.');
    }
    const token = this.tokens.issue(member.id);
    this.logger.log(`Login berhasil untuk ${maskMsisdn(res.memberRef)}`);
    return { access_token: token.accessToken, token_type: 'Bearer', expires_in: token.expiresIn };
  }

  async me(user: AuthUser): Promise<MeResponse> {
    const memberRef = await this.members.memberRef(user.memberId);
    if (!memberRef) throw Errors.unauthorized();
    return {
      member_ref_masked: maskMsisdn(memberRef),
      points_balance: await this.loyalty.balanceByHash(user.memberHash),
    };
  }
}
