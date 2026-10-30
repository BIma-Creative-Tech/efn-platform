import { Inject, Injectable, Logger, Optional } from '@nestjs/common';
import { DB, Db } from '../common/db/db';
import { touchActivity } from '../loyalty/loyalty-activity';
import { LoyaltyInactivityJob } from '../loyalty/loyalty-inactivity.job';
import { ONLINE_POINTS, OnlinePointsPort } from '../orders/online-points';
import { AppConfig } from '../config/app-config';
import { ApiError, Errors } from '../common/errors/api-error';
import { MemberService } from '../common/member/member.service';
import { maskMsisdn, memberHash, redisKeyHash } from '../common/pii/pii';
import { PII_AAD, PiiCipher } from '../common/pii/crypto';
import { isIP } from 'node:net';
import { RateLimiter } from '../common/rate-limit/rate-limiter';
import { AccessTokenService, AuthUser } from '../common/security/access-token.service';
import { LoyaltyService } from '../loyalty/loyalty.service';
import { OtpService } from '../otp/otp.service';
import { AltchaChallenge, AltchaService, adaptiveAltchaMaxNumber, altchaPressureLevel } from './altcha.service';
import { AuthOtpRequestDto, AuthOtpVerifyDto } from './dto/auth.dto';

export interface AuthOtpRequestResponse {
  challenge_id: string;
  expires_in: number;
  /** docs/04 §3.2 v5.2: hanya mode uji dev untuk nomor `DEV_OTP_REVEAL_MEMBERS`; di luar itu field tidak ada. */
  dev_code?: string;
}

export interface AuthOtpVerifyResponse {
  access_token: string;
  token_type: 'Bearer';
  expires_in: number;
  /** G27 §18.3: hanya bila klien mengirim `marketing_consent`. */
  marketing_consent?: 'recorded' | 'stale_text' | 'unavailable';
  /** G27 §18.3a: hanya bila klien mengirim `privacy_consent`. */
  privacy_consent?: 'recorded' | 'stale_text' | 'unavailable';
}

export interface MeResponse {
  member_ref_masked: string;
  points_balance: number;
  /** G22 §14.C.2: poin tertahan reservasi aktif (checkout online/POS) — saldo tersedia = balance − reserved. */
  points_reserved: number;
  /** G22 §14.D.6: `expires_on` hanya bila ≤ 60 hari lagi (null bila saldo 0 / masih jauh / job mati). */
  points_expiry: { inactive_since: string | null; expires_on: string | null; notice_sent: boolean };
}

/**
 * Login pelanggan Super-App dengan OTP WhatsApp (kontrak §3.2, v3 §8.1–§8.2).
 * - `GET /v1/auth/challenge` → challenge ALTCHA; `POST /v1/auth/otp/request` wajib `altcha` valid & belum dipakai
 *   (`400 captcha_required`). Captcha diverifikasi SEBELUM rate limit agar bot tanpa proof-of-work tidak
 *   menghabiskan kuota nomor korban.
 * - Request selalu 202 untuk nomor Indonesia berformat valid (login = daftar). Nomor non-Indonesia → 400 `invalid_phone`.
 * - W-19 (Q-P3): verify wajib `age_confirmed: true` (pernyataan usia ≥ `MEMBER_MIN_AGE`, 17) → selain itu
 *   `400 age_confirmation_required` tanpa menyentuh challenge; waktu pernyataan disimpan `member.age_confirmed_at`.
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
    /** G22 §14.D.2: login OTP sukses = aktivitas (jam tidak aktif W-17). Opsional di test lama. */
    @Optional() @Inject(DB) private readonly db: Db | null = null,
    @Optional() @Inject(ONLINE_POINTS) private readonly points: OnlinePointsPort | null = null,
    @Optional() @Inject(LoyaltyInactivityJob) private readonly expiry: LoyaltyInactivityJob | null = null,
    @Optional() @Inject(PiiCipher) private readonly cipher: PiiCipher | null = null,
  ) {}

  /** Pseudonim IP untuk nama key Redis (SEC-24). */
  ipKey(ip: string | null): string {
    return redisKeyHash(this.config.redisKeyPepper, ip ?? 'unknown');
  }

  /**
   * SEC-34: challenge terikat IP + biaya adaptif. Level dihitung dari counter Redis yang SUDAH ada (tanpa key baru):
   * challenge per IP (`altcha:ip:<ipk>`), OTP login per IP (`otp:login:ip:<ipk>`), budget OTP login global per jam
   * (`otp:budget:login`). maxnumber = ALTCHA_MAX_NUMBER × 2^level, maksimum ALTCHA_MAX_NUMBER_CAP.
   */
  async challenge(ip: string | null): Promise<AltchaChallenge> {
    const ipk = this.ipKey(ip);
    const window = this.config.otpRateWindowSeconds;
    const rl = await this.limiter.enforce(`altcha:ip:${ipk}`, this.config.altchaChallengeRateLimit, window);
    let level = 0;
    if (this.config.altchaAdaptive) {
      const [ipOtpRequests, globalOtpRequests, altchaFails, verifyAttempts, verifyFails] = await Promise.all([
        this.limiter.peek(`otp:login:ip:${ipk}`, window),
        this.limiter.peek('otp:budget:login', 3600),
        this.limiter.peek(`altcha:fail:ip:${ipk}`, window),
        this.limiter.peek(`otp:login:verify-ip:${ipk}`, window),
        this.limiter.peek(`otp:login:verify-fail-ip:${ipk}`, window),
      ]);
      level = altchaPressureLevel({
        ipChallenges: rl.count,
        ipChallengeLimit: this.config.altchaChallengeRateLimit,
        ipOtpRequests,
        ipOtpLimit: this.config.otpRateSourceLimit,
        globalOtpRequests,
        globalOtpBudget: this.config.otpGlobalHourlyBudget,
        failures: {
          altchaFails,
          // percobaan ALTCHA ≈ yang lolos (counter OTP per IP) + yang gagal
          altchaAttempts: altchaFails + ipOtpRequests,
          otpVerifyFails: verifyFails,
          otpVerifyAttempts: verifyAttempts,
          minSample: this.config.altchaFailMinSample,
        },
      });
    }
    const maxnumber = adaptiveAltchaMaxNumber(this.config.altchaMaxNumber, this.config.altchaMaxNumberCap, level);
    return this.altcha.createChallenge(ip, maxnumber, level);
  }

  async requestOtp(dto: AuthOtpRequestDto, ip: string | null): Promise<AuthOtpRequestResponse> {
    const window = this.config.otpRateWindowSeconds;
    const ipk = this.ipKey(ip);
    if (!(await this.altcha.verify(dto.altcha, ip))) {
      await this.countFailure(`altcha:fail:ip:${ipk}`, window);
      throw Errors.captchaRequired();
    }
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
    return { challenge_id: res.challengeId, expires_in: res.expiresIn, ...(res.devCode ? { dev_code: res.devCode } : {}) };
  }

  async verifyOtp(dto: AuthOtpVerifyDto, ip: string | null): Promise<AuthOtpVerifyResponse> {
    // W-19 (Q-P3): pernyataan usia minimum wajib; dicek sebelum OTP diverifikasi (challenge tidak hangus). Pesan sama
    // untuk nomor terdaftar/tidak (tidak membocorkan keanggotaan).
    if (!dto.ageConfirmed) {
      throw ApiError.of(
        400,
        'age_confirmation_required',
        `Konfirmasi bahwa Anda berusia minimal ${this.config.memberMinAge} tahun untuk melanjutkan.`,
      );
    }
    // G27 §18.3a (UU PDP): persetujuan Kebijakan Privasi wajib — dicek sebelum OTP (challenge tidak hangus).
    if (this.config.privacyConsentEnforced) {
      const active = this.config.consentActiveText('privacy');
      if (!dto.privacyConsent || (active && dto.privacyConsent.version !== active.version)) {
        throw ApiError.of(400, 'privacy_consent_required', 'Setujui Kebijakan Privasi untuk masuk atau mendaftar.');
      }
    }
    await this.limiter.enforce(
      `otp:login:verify-ip:${this.ipKey(ip)}`,
      this.config.otpRateVerifyLimit,
      this.config.otpRateWindowSeconds,
    );
    const res = await this.otp.verify({ purpose: 'login', challengeId: dto.challengeId, code: dto.code, ip });
    if (!res.ok) {
      await this.countFailure(`otp:login:verify-fail-ip:${this.ipKey(ip)}`, this.config.otpRateWindowSeconds);
      throw new ApiError(400, { ok: false, reason: res.reason, attempts_left: res.attemptsLeft });
    }
    if (!res.memberRef) {
      // Tidak seharusnya terjadi: challenge login selalu menyimpan member_ref (terenkripsi)
      this.logger.error('Challenge login tanpa member_ref yang dapat dibaca');
      throw new ApiError(400, { ok: false, reason: 'invalid', attempts_left: 0 });
    }
    const member = await this.members.upsertOnLogin(res.memberRef, { ageConfirmed: true });
    if (member.deleting) {
      // Hanya pemilik nomor (lolos OTP) yang melihat status ini → tidak membocorkan keanggotaan ke pihak lain
      throw Errors.conflict('account_deleting', 'Akun sedang dalam proses penghapusan.');
    }
    // G22 §14.D.2 (D-22-12 default L-73): login OTP sukses = aktivitas member. Best effort — login tidak gagal karenanya.
    if (this.db) {
      await touchActivity(this.db, memberHash(this.config.memberHashPepper, res.memberRef), 'login_otp', new Date()).catch(() =>
        this.logger.warn('Pembaruan aktivitas login gagal (diabaikan)'),
      );
    }
    const token = this.tokens.issue(member.id);
    this.logger.log(`Login berhasil untuk ${maskMsisdn(res.memberRef)}`);
    const pc = dto.privacyConsent ? await this.recordSignupConsent('privacy', member.id, dto.privacyConsent.version, ip) : undefined;
    const mc = dto.marketingConsent ? await this.recordSignupConsent('marketing', member.id, dto.marketingConsent.version, ip) : undefined;
    return {
      access_token: token.accessToken,
      token_type: 'Bearer',
      expires_in: token.expiresIn,
      ...(mc ? { marketing_consent: mc } : {}),
      ...(pc ? { privacy_consent: pc } : {}),
    };
  }

  /**
   * G27 §18.3/§18.3a: consent yang dicentang di layar masuk/daftar (pemasaran opsional, privasi wajib). Hanya versi teks
   * AKTIF dicatat (append-only `platform.consent`, source `signup`, IP terenkripsi). Baris baru hanya bila status
   * terakhir member untuk purpose itu bukan persetujuan atas versi yang sama (login berulang tidak menumpuk baris).
   * Login tidak pernah gagal karenanya.
   */
  private async recordSignupConsent(
    purpose: 'marketing' | 'privacy',
    memberId: string,
    version: string,
    ip: string | null,
  ): Promise<'recorded' | 'stale_text' | 'unavailable'> {
    const active = this.config.consentActiveText(purpose);
    if (!active || !this.db) return 'unavailable';
    if (active.version !== version) return 'stale_text';
    try {
      const last = await this.db.query<{ granted: boolean; version: string }>(
        `SELECT granted, version FROM platform.consent WHERE member_id = $1 AND purpose = $2 ORDER BY created_at DESC, id DESC LIMIT 1`,
        [memberId, purpose],
      );
      const prev = last.rows[0];
      if (prev && prev.granted === true && prev.version === active.version) return 'recorded';
      const ipEnc = ip && isIP(ip) ? this.cipher?.encrypt(ip, PII_AAD.CONSENT_IP) ?? null : null;
      await this.db.query(
        `INSERT INTO platform.consent (member_id, purpose, granted, version, text_sha256, source, ip_enc)
         VALUES ($1, $2, true, $3, $4, 'signup', $5)`,
        [memberId, purpose, active.version, active.textSha256, ipEnc],
      );
      return 'recorded';
    } catch {
      this.logger.warn(`Pencatatan consent ${purpose} saat daftar gagal (login tetap berhasil)`);
      return 'unavailable';
    }
  }

  /**
   * SEC-54: counter kegagalan per IP (hash, SEC-24) untuk level ALTCHA berbasis rasio. Best effort: Redis gagal tidak
   * mengubah respons (tetap 400 generik).
   */
  private async countFailure(key: string, window: number): Promise<void> {
    try {
      await this.limiter.hit(key, Number.MAX_SAFE_INTEGER, window);
    } catch {
      // diabaikan — hanya sinyal biaya adaptif
    }
  }

  async me(user: AuthUser): Promise<MeResponse> {
    const memberRef = await this.members.memberRef(user.memberId);
    if (!memberRef) throw Errors.unauthorized();
    const balance = await this.loyalty.balanceByHash(user.memberHash);
    return {
      member_ref_masked: maskMsisdn(memberRef),
      points_balance: balance,
      points_reserved: this.points ? await this.points.reservedPoints(user.memberHash) : 0,
      points_expiry: this.expiry
        ? await this.expiry.expiryInfo(user.memberHash, balance)
        : { inactive_since: null, expires_on: null, notice_sent: false },
    };
  }
}
