import { Inject, Injectable, Logger } from '@nestjs/common';
import { createHmac, randomInt, randomUUID } from 'node:crypto';
import { isIP } from 'node:net';
import { AppConfig } from '../config/app-config';
import { DB, Db } from '../common/db/db';
import { MetricsService } from '../common/metrics/metrics.service';
import { Errors } from '../common/errors/api-error';
import { PII_AAD, PiiCipher } from '../common/pii/crypto';
import { maskMsisdn, memberHash } from '../common/pii/pii';
import { RateLimiter } from '../common/rate-limit/rate-limiter';
import { KV_STORE, KvStore } from '../common/redis/kv-store';
import { safeEqualHex } from '../common/security/safe-equal';
import { UUID_RE } from '../common/validation';
import { WHATSAPP_SENDER, WhatsAppSender } from './whatsapp/whatsapp-sender';

/**
 * `dsar` = re-OTP step-up untuk aksi DSAR (docs/04 §8.4). `pos_redeem` = OTP redeem poin di POS (docs/04 §9.3) — token
 * hasilnya tidak bisa dipakai untuk consume promo (`pos_promo`) dan sebaliknya.
 */
export type OtpPurpose = 'pos_promo' | 'pos_redeem' | 'pos_enroll' | 'login' | 'dsar';
/**
 * Purpose kanal kasir (rate limit/budget POS, challenge terikat `order_ref`). `pos_enroll` (G18 K-18-8) = kasir
 * mendaftarkan member baru; wajib pernyataan usia, TIDAK menghasilkan discount_token.
 */
export const POS_OTP_PURPOSES = ['pos_promo', 'pos_redeem', 'pos_enroll'] as const;
export type PosOtpPurpose = (typeof POS_OTP_PURPOSES)[number];

export function isPosOtpPurpose(v: unknown): v is PosOtpPurpose {
  return typeof v === 'string' && (POS_OTP_PURPOSES as readonly string[]).includes(v);
}
export type OtpFailureReason = 'invalid' | 'expired' | 'locked';
export type OtpAuditOutcome = 'sent' | 'verified' | 'failed' | 'expired' | 'locked';

export interface IssueOtpInput {
  purpose: OtpPurpose;
  memberRef: string;
  orderRef?: string | null;
  companyCode?: string | null;
  /** SEC-07: nilai order saat OTP POS diminta (diikat ke discount_token). */
  amount?: number | null;
  /** K-18-8: pos.config peminta (disimpan di challenge `pos_enroll` untuk jejak pernyataan usia). */
  posConfigId?: number | null;
  ip?: string | null;
}

export interface IssueOtpResult {
  challengeId: string;
  expiresIn: number;
  /** Mode uji dev (`AppConfig.devOtpRevealFor`): kode untuk nomor uji, HANYA purpose `login`. Selain itu tidak ada. */
  devCode?: string;
}

export interface VerifyOtpInput {
  purpose: OtpPurpose;
  /**
   * Purpose lain yang juga diterima (mis. verify POS menerima `pos_promo` dan `pos_redeem` karena body verify tidak
   * memuat purpose — purpose dibawa challenge). Hasil `ok` menyebut purpose challenge yang sebenarnya.
   */
  alsoAccept?: readonly OtpPurpose[];
  challengeId: string;
  code: string;
  /** Wajib untuk purpose POS: harus sama dengan order_ref saat request. */
  orderRef?: string | null;
  ip?: string | null;
}

export type VerifyOtpResult =
  | {
      ok: true;
      /** Purpose challenge (dari Redis, bukan dari pemanggil). */
      purpose: OtpPurpose;
      memberHash: string;
      memberRef: string | null;
      orderRef: string | null;
      companyCode: string | null;
      amount: number | null;
      /** K-18-8: pos.config dari challenge (`pos_enroll`), selain itu null. */
      posConfigId: number | null;
    }
  | { ok: false; reason: OtpFailureReason; attemptsLeft: number };

/** Challenge disimpan lebih lama dari TTL OTP agar verify setelah kedaluwarsa menjawab `expired`, bukan `invalid`. */
export const OTP_TOMBSTONE_GRACE_SECONDS = 600;
const CODE_RE = /^\d{6}$/;

/**
 * Inti OTP bersama untuk POS (`pos_promo`, `pos_redeem`) dan login Super-App (`login`) — kontrak §3.1/§3.2, R-S04.
 *
 * - Kode 6 digit dari `crypto.randomInt`; yang disimpan hanya HMAC-SHA256(otp_pepper, challenge_id:code).
 * - Redis hash `otp:<challenge_id>`: h=hash, p=purpose, s=member_hash, o=order_ref, c=company_code, t=amount (POS,
 *   SEC-07), a=attempts, l=locked, e=expires_at (unix), m=member_ref TERENKRIPSI (HANYA login; AES-256-GCM AAD
 *   `efn:redis_otp.m:v1`, SEC-24 — nomor HP tidak pernah plaintext di Redis/AOF; dibutuhkan untuk upsert member).
 * - Maks `OTP_MAX_ATTEMPTS` (3) percobaan → locked. Attempt dinaikkan atomik (HINCRBY) SEBELUM membandingkan,
 *   sehingga percobaan paralel tidak bisa melewati batas.
 * - Sukses = DEL key; hanya pemanggil yang berhasil DEL (=1) yang dianggap menang (sekali pakai).
 * - Setiap hasil dicatat di `platform.otp_audit` dengan subject = member_hash (bukan nomor HP); IP klien disimpan
 *   terenkripsi di `ip_enc` (AES-256-GCM, docs/04 §7.2) — forensik hanya lewat dekripsi oleh admin.
 * - Rate limit per nomor/IP dilakukan pemanggil (PosService/AuthService) karena kuncinya berbeda per kanal.
 * - SEC-06: budget global pengiriman WA per jam DI SINI (berlaku untuk semua pemanggil), terpisah per purpose:
 *   `login` (kanal publik) = `OTP_GLOBAL_HOURLY_BUDGET`, `pos_promo`/`pos_redeem` = `OTP_POS_HOURLY_BUDGET`, sehingga pumping
 *   lewat login publik tidak bisa menghabiskan budget kasir.
 */
@Injectable()
export class OtpService {
  private readonly logger = new Logger(OtpService.name);
  now: () => number = () => Date.now();

  constructor(
    private readonly config: AppConfig,
    @Inject(KV_STORE) private readonly kv: KvStore,
    @Inject(DB) private readonly db: Db,
    @Inject(WHATSAPP_SENDER) private readonly sender: WhatsAppSender,
    private readonly metrics: MetricsService,
    private readonly limiter: RateLimiter,
    private readonly cipher: PiiCipher,
  ) {}

  /** Dipanggil PosService/AuthService saat rate limit menolak permintaan OTP. */
  recordRateLimited(purpose: OtpPurpose): void {
    this.metrics.recordOtpRequest(purpose, 'rate_limited');
  }

  async issue(input: IssueOtpInput): Promise<IssueOtpResult> {
    await this.enforceGlobalBudget(input.purpose);
    const ttl = this.config.otpTtlSeconds;
    const challengeId = randomUUID();
    const code = randomInt(0, 1_000_000).toString().padStart(6, '0');
    const subject = memberHash(this.config.memberHashPepper, input.memberRef);
    const fields: Record<string, string> = {
      h: this.hashCode(challengeId, code),
      p: input.purpose,
      s: subject,
      o: input.orderRef ?? '',
      c: input.companyCode ?? '',
      a: '0',
      l: '0',
      e: String(this.nowSec() + ttl),
    };
    if (typeof input.amount === 'number' && Number.isFinite(input.amount)) fields.t = String(input.amount);
    // login & pos_enroll butuh nomor untuk upsert member setelah verify (terenkripsi, SEC-24)
    if (input.purpose === 'login' || input.purpose === 'pos_enroll') {
      fields.m = this.cipher.encrypt(input.memberRef, PII_AAD.REDIS_OTP_MEMBER).toString('base64');
    }
    if (input.purpose === 'pos_enroll' && typeof input.posConfigId === 'number') fields.g = String(input.posConfigId);
    const key = this.key(challengeId);
    await this.kv.hsetEx(key, fields, ttl + OTP_TOMBSTONE_GRACE_SECONDS);

    try {
      await this.sender.sendOtp(input.memberRef, code);
    } catch (err) {
      await this.kv.del(key).catch(() => 0);
      this.logger.error(`Gagal mengirim OTP ke ${maskMsisdn(input.memberRef)}: ${err instanceof Error ? err.message : String(err)}`);
      await this.audit(input.purpose, subject, input.orderRef ?? null, 'failed', input.ip ?? null);
      this.metrics.recordOtpRequest(input.purpose, 'delivery_failed');
      throw Errors.serviceUnavailable('otp_delivery_failed');
    }

    await this.audit(input.purpose, subject, input.orderRef ?? null, 'sent', input.ip ?? null);
    this.metrics.recordOtpRequest(input.purpose, 'sent');
    const reveal = input.purpose === 'login' && this.config.devOtpRevealFor(input.memberRef);
    return { challengeId, expiresIn: ttl, ...(reveal ? { devCode: code } : {}) };
  }

  async verify(input: VerifyOtpInput): Promise<VerifyOtpResult> {
    const { res, purpose } = await this.verifyInner(input);
    this.metrics.recordOtpVerify(purpose, res.ok ? 'verified' : res.reason);
    return res;
  }

  private async verifyInner(input: VerifyOtpInput): Promise<{ res: VerifyOtpResult; purpose: OtpPurpose }> {
    const ip = input.ip ?? null;
    const orderRefInput = input.orderRef ?? null;
    if (!UUID_RE.test(input.challengeId)) return { res: this.fail('expired', 0), purpose: input.purpose };

    const key = this.key(input.challengeId);
    const st = await this.kv.hgetall(key);
    const accepted: readonly OtpPurpose[] = [input.purpose, ...(input.alsoAccept ?? [])];
    if (!st.h || !(accepted as readonly string[]).includes(st.p ?? '')) {
      // Challenge tidak dikenal / sudah dipakai / beda tujuan → perlakukan sebagai kedaluwarsa
      await this.audit(input.purpose, 'unknown', orderRefInput, 'expired', ip);
      return { res: this.fail('expired', 0), purpose: input.purpose };
    }
    const purpose = st.p as OtpPurpose;
    return { res: await this.verifyChallenge(input, key, st, purpose), purpose };
  }

  private async verifyChallenge(
    input: VerifyOtpInput,
    key: string,
    st: Record<string, string>,
    purpose: OtpPurpose,
  ): Promise<VerifyOtpResult> {
    const ip = input.ip ?? null;
    const orderRefInput = input.orderRef ?? null;
    const subject = st.s ?? 'unknown';
    const storedOrderRef = st.o ? st.o : null;

    if (st.l === '1') {
      await this.audit(purpose, subject, storedOrderRef, 'locked', ip);
      return this.fail('locked', 0);
    }
    if (this.nowSec() > Number(st.e)) {
      await this.audit(purpose, subject, storedOrderRef, 'expired', ip);
      return this.fail('expired', 0);
    }

    const max = this.config.otpMaxAttempts;
    const attempts = await this.kv.hincrby(key, 'a', 1);
    if (attempts > max) {
      await this.kv.hset(key, 'l', '1');
      await this.audit(purpose, subject, storedOrderRef, 'locked', ip);
      return this.fail('locked', 0);
    }

    const orderMatches = !isPosOtpPurpose(purpose) || (orderRefInput ?? '') === (st.o ?? '');
    const codeMatches =
      typeof input.code === 'string' &&
      CODE_RE.test(input.code) &&
      safeEqualHex(this.hashCode(input.challengeId, input.code), st.h);

    if (orderMatches && codeMatches) {
      const won = (await this.kv.del(key)) === 1;
      if (!won) return this.fail('expired', 0); // verify paralel lain sudah memakai challenge ini
      await this.audit(purpose, subject, storedOrderRef, 'verified', ip);
      return {
        ok: true,
        purpose,
        memberHash: subject,
        memberRef: st.m ? this.decryptMemberRef(st.m) : null,
        orderRef: storedOrderRef,
        companyCode: st.c ? st.c : null,
        amount: st.t !== undefined && st.t !== '' && Number.isFinite(Number(st.t)) ? Number(st.t) : null,
        posConfigId: st.g !== undefined && /^[0-9]{1,10}$/.test(st.g) ? Number(st.g) : null,
      };
    }

    const left = max - attempts;
    if (left <= 0) {
      await this.kv.hset(key, 'l', '1');
      await this.audit(purpose, subject, storedOrderRef, 'locked', ip);
      return this.fail('locked', 0);
    }
    await this.audit(purpose, subject, storedOrderRef, 'failed', ip);
    return this.fail('invalid', left);
  }

  /**
   * Budget global per jam (fixed window Redis `rl:otp:budget:<purpose>:<jam>`). Habis → 429 generik
   * (tidak membocorkan alasan), metrik `outcome="budget_exhausted"` dan log error untuk alert.
   */
  private async enforceGlobalBudget(purpose: OtpPurpose): Promise<void> {
    // Kanal pelanggan (login, dsar) memakai budget publik; POS (pos_promo, pos_redeem) budget terpisah per purpose
    const budget = isPosOtpPurpose(purpose) ? this.config.otpPosHourlyBudget : this.config.otpGlobalHourlyBudget;
    const res = await this.limiter.hit(`otp:budget:${purpose}`, budget, 3600);
    if (res.allowed) return;
    this.metrics.recordOtpRequest(purpose, 'budget_exhausted');
    if (res.count === budget + 1) {
      this.logger.error(`Budget global OTP ${purpose} (${budget}/jam) habis — kemungkinan OTP pumping (SEC-06)`);
    }
    throw Errors.rateLimited(res.retryAfterSeconds);
  }

  /** Gagal dekripsi (mis. kunci versi lama sudah dipensiunkan saat challenge hidup) → null; pemanggil menolak login. */
  private decryptMemberRef(b64: string): string | null {
    try {
      return this.cipher.decrypt(Buffer.from(b64, 'base64'), PII_AAD.REDIS_OTP_MEMBER);
    } catch {
      this.logger.error('member_ref challenge login tidak dapat didekripsi');
      return null;
    }
  }

  private fail(reason: OtpFailureReason, attemptsLeft: number): VerifyOtpResult {
    return { ok: false, reason, attemptsLeft };
  }

  private key(challengeId: string): string {
    return `otp:${challengeId.toLowerCase()}`;
  }

  private hashCode(challengeId: string, code: string): string {
    return createHmac('sha256', this.config.otpCodeKey).update(`${challengeId.toLowerCase()}:${code}`, 'utf8').digest('hex');
  }

  private nowSec(): number {
    return Math.floor(this.now() / 1000);
  }

  /** Audit tidak boleh memutus alur OTP; kegagalan hanya di-log. */
  private async audit(
    purpose: OtpPurpose,
    subject: string,
    orderRef: string | null,
    outcome: OtpAuditOutcome,
    ip: string | null,
  ): Promise<void> {
    try {
      const ipEnc = ip && isIP(ip) ? this.cipher.encrypt(ip, PII_AAD.OTP_AUDIT_IP) : null;
      await this.db.query(
        `INSERT INTO platform.otp_audit (purpose, subject, order_ref, outcome, ip_enc) VALUES ($1, $2, $3, $4, $5)`,
        [purpose, subject, orderRef, outcome, ipEnc],
      );
    } catch (err) {
      this.logger.warn(`Gagal menulis otp_audit (${outcome}): ${err instanceof Error ? err.message : String(err)}`);
    }
  }
}
