import { Inject, Injectable, Logger } from '@nestjs/common';
import { randomUUID } from 'node:crypto';
import { AppConfig } from '../config/app-config';
import { DB, Db } from '../common/db/db';
import { ApiError, Errors } from '../common/errors/api-error';
import { MemberService } from '../common/member/member.service';
import { maskMsisdn, redisKeyHash } from '../common/pii/pii';
import { RateLimiter } from '../common/rate-limit/rate-limiter';
import { AccessTokenService, AuthUser } from '../common/security/access-token.service';
import { safeEqualHex } from '../common/security/safe-equal';
import { LoyaltyService } from '../loyalty/loyalty.service';
import { OtpService } from '../otp/otp.service';
import { ConsentService, toIso } from './consent.service';
import { ReauthInput } from './dto/privacy.dto';

export interface DsarRequestItem {
  id: string;
  type: string;
  status: string;
  requested_at: string | null;
  scheduled_at: string | null;
  completed_at: string | null;
}

export interface DeleteAccountResponse {
  request_id: string;
  scheduled_at: string;
  points_forfeited: number;
}

/** Pemroses data (informasi untuk subjek data; finalisasi oleh DPO). */
export const DATA_PROCESSORS = [
  { name: 'PT EFN (holding) & anak perusahaan', role: 'pengendali', purpose: 'akun, loyalty, Price Hunter' },
  { name: 'Meta Platforms (WhatsApp Cloud API)', role: 'pemroses', purpose: 'pengiriman OTP & notifikasi' },
  { name: 'Penyedia pembayaran QRIS (belum ditetapkan, ADR-016)', role: 'pemroses', purpose: 'pembayaran' },
] as const;

const EXPORT_ROW_LIMIT = 1000;

/**
 * Hak subjek data (docs/04 §8.4, §8.9; PDP_DSAR_DESIGN §3).
 * - `POST /v1/me/reauth`: `{}` → OTP (purpose `dsar`) ke nomor akun sendiri (rate limit per member); `{challenge_id,code}`
 *   → token step-up `purpose: dsar` 10 menit. Challenge harus milik member yang sama (member_hash).
 * - `GET /v1/me/data` & `DELETE /v1/me`: wajib header `X-Step-Up-Token` (hilang → `403 step_up_required`, tidak
 *   valid/kedaluwarsa/milik member lain → `403 step_up_invalid`).
 * - `DELETE /v1/me` idempotent: permintaan aktif (scheduled/processing) dikembalikan apa adanya. Login tetap boleh
 *   selama masa tenggang (agar bisa membatalkan); erasure berjalan setelah `scheduled_at` (ErasureService).
 * - Export dicatat sebagai `dsar_request` type `export` status `completed` (audit). Export > batas baris dipotong
 *   (`truncated: true`); jalur 202 + tautan unduh MinIO (export besar) BELUM diimplementasi.
 */
@Injectable()
export class DsarService {
  private readonly logger = new Logger(DsarService.name);
  now: () => number = () => Date.now();

  constructor(
    private readonly config: AppConfig,
    @Inject(DB) private readonly db: Db,
    private readonly members: MemberService,
    private readonly tokens: AccessTokenService,
    private readonly otp: OtpService,
    private readonly limiter: RateLimiter,
    private readonly loyalty: LoyaltyService,
    private readonly consents: ConsentService,
  ) {}

  requireStepUp(user: AuthUser, token: string | undefined): void {
    if (!token) throw Errors.stepUpRequired();
    if (!this.tokens.verifyStepUp(token, user.memberId)) throw Errors.stepUpInvalid();
  }

  async reauth(
    user: AuthUser,
    input: ReauthInput,
    ip: string | null,
  ): Promise<{ status: 202; body: { challenge_id: string; expires_in: number } } | { status: 200; body: { step_up_token: string; expires_in: number } }> {
    if (input.kind === 'request') {
      await this.limiter.enforce(`otp:dsar:member:${user.memberHash}`, this.config.dsarReauthLimit, this.config.otpRateWindowSeconds);
      const memberRef = await this.members.memberRef(user.memberId);
      if (!memberRef) throw Errors.unauthorized();
      const res = await this.otp.issue({ purpose: 'dsar', memberRef, ip });
      return { status: 202, body: { challenge_id: res.challengeId, expires_in: res.expiresIn } };
    }
    await this.limiter.enforce(
      `otp:dsar:verify-ip:${redisKeyHash(this.config.redisKeyPepper, ip ?? 'unknown')}`,
      this.config.otpRateVerifyLimit,
      this.config.otpRateWindowSeconds,
    );
    const res = await this.otp.verify({ purpose: 'dsar', challengeId: input.challengeId, code: input.code, ip });
    if (!res.ok) throw new ApiError(400, { ok: false, reason: res.reason, attempts_left: res.attemptsLeft });
    // Challenge milik member lain (challenge_id bocor) → tidak menerbitkan step-up
    if (!safeEqualHex(res.memberHash, user.memberHash)) throw new ApiError(400, { ok: false, reason: 'invalid', attempts_left: 0 });
    const t = this.tokens.issueStepUp(user.memberId);
    return { status: 200, body: { step_up_token: t.token, expires_in: t.expiresIn } };
  }

  async exportData(user: AuthUser): Promise<Record<string, unknown>> {
    await this.limiter.enforce(`dsar:export:${user.memberHash}`, this.config.dsarExportHourlyLimit, 3600);
    const memberRef = await this.members.memberRef(user.memberId);
    if (!memberRef) throw Errors.unauthorized();
    const member = await this.db.query<{ created_at: Date | string }>(`SELECT created_at FROM platform.member WHERE id = $1`, [user.memberId]);
    const ledger = await this.db.query<{
      created_at: Date | string;
      delta_points: number;
      reason: string;
      source: string;
      company_code: string;
      expires_at: Date | string | null;
    }>(
      `SELECT created_at, delta_points, reason, source, company_code, expires_at
         FROM platform.loyalty_ledger WHERE member_ref_hash = $1
        ORDER BY created_at DESC LIMIT $2`,
      [user.memberHash, EXPORT_ROW_LIMIT + 1],
    );
    const reports = await this.db.query<{
      id: string;
      product_name: string;
      reported_price: string | number;
      competitor_name: string | null;
      status: string;
      reason: string | null;
      geo_lat: number | null;
      geo_lng: number | null;
      created_at: Date | string;
    }>(
      `SELECT id::text AS id, product_name, reported_price, competitor_name, status, reason, geo_lat, geo_lng, created_at
         FROM platform.price_hunter_report WHERE user_hash = $1
        ORDER BY created_at DESC LIMIT $2`,
      [user.memberHash, EXPORT_ROW_LIMIT + 1],
    );
    const consentHistory = await this.db.query<{
      purpose: string;
      granted: boolean;
      version: string;
      text_sha256: string;
      source: string;
      created_at: Date | string;
    }>(
      `SELECT purpose, granted, version, text_sha256, source, created_at
         FROM platform.consent WHERE member_id = $1 ORDER BY created_at DESC, id DESC LIMIT $2`,
      [user.memberId, EXPORT_ROW_LIMIT],
    );
    const requests = await this.listRequests(user);
    const truncated = ledger.rows.length > EXPORT_ROW_LIMIT || reports.rows.length > EXPORT_ROW_LIMIT;

    await this.db.query(
      `INSERT INTO platform.dsar_request (id, member_id, member_hash, type, status, requested_at, completed_at)
       VALUES ($1, $2, $3, 'export', 'completed', now(), now())`,
      [randomUUID(), user.memberId, user.memberHash],
    );
    this.logger.log(`Export DSAR untuk ${maskMsisdn(memberRef)}`);

    return {
      generated_at: new Date(this.now()).toISOString(),
      profile: {
        member_id: user.memberId,
        member_ref: memberRef,
        member_ref_masked: maskMsisdn(memberRef),
        created_at: toIso(member.rows[0]?.created_at),
      },
      points: {
        balance: await this.loyalty.balanceByHash(user.memberHash),
        entries: ledger.rows.slice(0, EXPORT_ROW_LIMIT).map((r) => ({
          created_at: toIso(r.created_at),
          delta_points: Number(r.delta_points),
          reason: r.reason,
          source: r.source,
          company_code: r.company_code,
          expires_at: toIso(r.expires_at),
        })),
      },
      price_hunter_reports: reports.rows.slice(0, EXPORT_ROW_LIMIT).map((r) => ({
        report_id: r.id,
        product_name: r.product_name,
        reported_price: Number(r.reported_price),
        competitor_name: r.competitor_name,
        status: r.status,
        reason: r.reason,
        location: r.geo_lat !== null && r.geo_lng !== null ? { lat: r.geo_lat, lng: r.geo_lng } : null,
        created_at: toIso(r.created_at),
      })),
      consents: {
        current: await this.consents.list(user.memberId),
        history: consentHistory.rows.map((r) => ({
          purpose: r.purpose,
          granted: r.granted,
          version: r.version,
          text_sha256: r.text_sha256,
          source: r.source,
          created_at: toIso(r.created_at),
        })),
      },
      requests: requests.items,
      processors: DATA_PROCESSORS,
      truncated,
    };
  }

  async requestDelete(user: AuthUser): Promise<DeleteAccountResponse> {
    const points = await this.loyalty.balanceByHash(user.memberHash);
    const active = await this.findActiveDelete(user.memberId);
    if (active) return { request_id: active.id, scheduled_at: active.scheduledAt, points_forfeited: points };
    const id = randomUUID();
    const scheduled = new Date(this.now() + this.config.dsarDeleteGraceDays * 86_400_000);
    try {
      await this.db.query(
        `INSERT INTO platform.dsar_request (id, member_id, member_hash, type, status, stores_pending, requested_at, scheduled_at)
         VALUES ($1, $2, $3, 'delete', 'scheduled', '{}', now(), $4)`,
        [id, user.memberId, user.memberHash, scheduled.toISOString()],
      );
    } catch (err) {
      // Balapan dua DELETE paralel → unique uq_dsar_active_delete; kembalikan yang menang
      const again = await this.findActiveDelete(user.memberId);
      if (again) return { request_id: again.id, scheduled_at: again.scheduledAt, points_forfeited: points };
      throw err;
    }
    this.logger.log(`Permintaan hapus akun ${id} dijadwalkan ${scheduled.toISOString()}`);
    return { request_id: id, scheduled_at: scheduled.toISOString(), points_forfeited: points };
  }

  async cancelDelete(user: AuthUser): Promise<{ request_id: string; status: 'cancelled' }> {
    const res = await this.db.query<{ id: string }>(
      `UPDATE platform.dsar_request SET status = 'cancelled', completed_at = now()
        WHERE member_id = $1 AND type = 'delete' AND status = 'scheduled'
        RETURNING id::text AS id`,
      [user.memberId],
    );
    const row = res.rows[0];
    if (!row) throw Errors.notFound();
    return { request_id: row.id, status: 'cancelled' };
  }

  async listRequests(user: AuthUser): Promise<{ items: DsarRequestItem[] }> {
    const res = await this.db.query<{
      id: string;
      type: string;
      status: string;
      requested_at: Date | string;
      scheduled_at: Date | string | null;
      completed_at: Date | string | null;
    }>(
      `SELECT id::text AS id, type, status, requested_at, scheduled_at, completed_at
         FROM platform.dsar_request WHERE member_id = $1
        ORDER BY requested_at DESC LIMIT 50`,
      [user.memberId],
    );
    return {
      items: res.rows.map((r) => ({
        id: r.id,
        type: r.type,
        status: r.status,
        requested_at: toIso(r.requested_at),
        scheduled_at: toIso(r.scheduled_at),
        completed_at: toIso(r.completed_at),
      })),
    };
  }

  private async findActiveDelete(memberId: string): Promise<{ id: string; scheduledAt: string } | null> {
    const res = await this.db.query<{ id: string; scheduled_at: Date | string }>(
      `SELECT id::text AS id, scheduled_at FROM platform.dsar_request
        WHERE member_id = $1 AND type = 'delete' AND status IN ('scheduled', 'processing')
        LIMIT 1`,
      [memberId],
    );
    const r = res.rows[0];
    return r ? { id: r.id, scheduledAt: toIso(r.scheduled_at) ?? '' } : null;
  }
}
