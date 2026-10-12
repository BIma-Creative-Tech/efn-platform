import { Inject, Injectable, Logger, Optional } from '@nestjs/common';
import { OnlinePrivacyService, openOnlineOrders } from '../orders/online-privacy.service';
import { randomUUID } from 'node:crypto';
import { AppConfig } from '../config/app-config';
import { DB, Db } from '../common/db/db';
import { ApiError, Errors } from '../common/errors/api-error';
import { MemberService } from '../common/member/member.service';
import { maskMsisdn, redisKeyHash } from '../common/pii/pii';
import { RateLimiter } from '../common/rate-limit/rate-limiter';
import { KV_STORE, KvStore } from '../common/redis/kv-store';
import { AccessTokenService, AuthUser } from '../common/security/access-token.service';
import { safeEqualHex } from '../common/security/safe-equal';
import { LoyaltyService } from '../loyalty/loyalty.service';
import { OtpService } from '../otp/otp.service';
import { ConsentService, toIso } from './consent.service';
import { ReauthInput } from './dto/privacy.dto';
import { DsarReceiptService } from './dsar-receipt.service';
import { OPEN_PARTS_COLUMN, dsarItem, erasureDeadlineIso } from './erasure-deferral';

export interface DsarRequestItem {
  id: string;
  type: string;
  status: string;
  requested_at: string | null;
  scheduled_at: string | null;
  completed_at: string | null;
  /** W-18 (Q-P18): jumlah bagian pesanan online terbuka (hanya hapus `scheduled`; lainnya 0). Tanpa detail order. */
  open_online_orders: number;
  /** W-18: batas paling lambat erasure bila `open_online_orders` > 0, selain itu `null`. */
  erasure_deadline: string | null;
}

export interface DeleteAccountResponse {
  request_id: string;
  scheduled_at: string;
  points_forfeited: number;
  /** R6 (§8.13): bukti opak untuk `GET /v1/dsar/requests/:id` (header `X-DSAR-Receipt`), 30 hari, tanpa PII. */
  receipt_token: string;
  /** W-18 (Q-P18): jumlah bagian pesanan online yang belum selesai saat ini (0 = tidak ada penundaan). */
  open_online_orders: number;
  /**
   * W-18 (Q-P18): bila ada pesanan terbuka — batas paling lambat erasure dijalankan (`requested_at` +
   * `DSAR_OPEN_ORDER_MAX_DEFER_DAYS`, tidak lebih awal dari `scheduled_at`); `null` bila tidak ada penundaan.
   */
  erasure_deadline: string | null;
}

export interface DeleteOptions {
  /** `?confirm_open_orders=true`: pelanggan menerima penundaan ≤ 14 hari & pelepasan tautan pesanan (W-18). */
  confirmOpenOrders?: boolean;
}

/** SEC-40: penanda jti step-up terpakai. Prefix di bawah `otp:*` (sudah ada di ACL Redis `efn-bff`). */
export const STEP_UP_USED_PREFIX = 'otp:stepup-used:';

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
 * - W-18 (Q-P18): ada bagian pesanan online terbuka → `409 open_online_orders` (teks menyebut batas 14 hari) kecuali
 *   `?confirm_open_orders=true`; dengan konfirmasi permintaan dicatat (`open_orders_ack_at`) dan erasure ditunda paling
 *   lama `DSAR_OPEN_ORDER_MAX_DEFER_DAYS` sejak `requested_at` (ErasureService: eskalasi CS hari 3 & 10, lalu tetap jalan).
 *   Pra-cek 409 dilakukan SEBELUM token step-up sekali pakai ditandai (`precheckDelete`), agar pelanggan tidak perlu
 *   OTP ulang hanya untuk mengonfirmasi.
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
    private readonly receipts: DsarReceiptService,
    @Inject(KV_STORE) private readonly kv: KvStore,
    /** Fase 3 (§10.8.3, L-4): export order online + pra-hapus akun. Opsional agar modul lain/test lama tetap jalan. */
    // QA G17: token eksplisit WAJIB — tipe union `X | null` diserialisasi TypeScript sebagai `Object` di
    // design:paramtypes, sehingga @Optional() tanpa @Inject selalu menyuntik undefined (L-4 409 diam-diam mati).
    @Optional() @Inject(OnlinePrivacyService) private readonly online: OnlinePrivacyService | null = null,
  ) {}

  requireStepUp(user: AuthUser, token: string | undefined): void {
    if (!token) throw Errors.stepUpRequired();
    if (!this.tokens.verifyStepUp(token, user.memberId)) throw Errors.stepUpInvalid();
  }

  /**
   * SEC-40: seperti `requireStepUp`, lalu menandai `jti` terpakai secara atomik (`SET NX EX` sampai token kedaluwarsa).
   * Dipakai `DELETE /v1/me`: token step-up hanya bisa mengonfirmasi SATU permintaan hapus; pakai ulang → 403
   * `step_up_invalid`. Ditandai SEBELUM operasi (aman terhadap balapan); bila operasi gagal pengguna mengulang re-OTP.
   * Redis tidak tersedia → error (fail closed). Ekspor (`GET /v1/me/data`) tetap boleh memakai token yang sama selama
   * masa berlakunya (rate limit per jam membatasi).
   */
  async consumeStepUp(user: AuthUser, token: string | undefined): Promise<void> {
    if (!token) throw Errors.stepUpRequired();
    const claims = this.tokens.verifyStepUpClaims(token, user.memberId);
    if (!claims) throw Errors.stepUpInvalid();
    const ttl = Math.max(1, claims.exp - Math.floor(this.now() / 1000) + 1);
    if (!(await this.kv.setNxEx(`${STEP_UP_USED_PREFIX}${claims.jti}`, '1', ttl))) throw Errors.stepUpInvalid();
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
    const onlineOrders = this.online ? await this.online.exportOrders(user.memberId) : { items: [], truncated: false };
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
      // §10.8.3: tanpa kode ambil, QR, id internal Odoo/payment
      online_orders: onlineOrders.items,
      requests: requests.items,
      processors: DATA_PROCESSORS,
      truncated: truncated || onlineOrders.truncated,
    };
  }

  /**
   * W-18: pra-cek read-only SEBELUM `consumeStepUp` — ada bagian pesanan terbuka tanpa konfirmasi → 409 (token step-up
   * tetap bisa dipakai untuk mengulang dengan `?confirm_open_orders=true`). Permintaan aktif → lewati (idempotent).
   */
  async precheckDelete(user: AuthUser, opts: DeleteOptions = {}): Promise<void> {
    if (!this.online || opts.confirmOpenOrders) return;
    if (await this.findActiveDelete(user.memberId)) return;
    const open = await this.online.openPaidParts(user.memberId);
    if (open > 0) throw openOnlineOrders(open, this.config.dsarOpenOrderMaxDeferDays);
  }

  async requestDelete(user: AuthUser, opts: DeleteOptions = {}): Promise<DeleteAccountResponse> {
    const confirm = opts.confirmOpenOrders === true;
    const maxDeferDays = this.config.dsarOpenOrderMaxDeferDays;
    const points = await this.loyalty.balanceByHash(user.memberHash);
    const active = await this.findActiveDelete(user.memberId);
    if (active) return this.deleteResponse(active, points, await this.openPartsOf(user.memberId));
    // L-4: order belum dibayar dibatalkan otomatis; bagian paying|confirmed|ready|review → 409 open_online_orders
    // kecuali dikonfirmasi (W-18, Q-P18)
    if (this.online) await this.online.prepareDelete(user.memberId, { confirm, maxDeferDays });
    const id = randomUUID();
    const nowMs = this.now();
    const requested = new Date(nowMs).toISOString();
    const scheduled = new Date(nowMs + this.config.dsarDeleteGraceDays * 86_400_000).toISOString();
    let open = 0;
    try {
      await this.db.tx(async (q) => {
        // SEC-100: kunci member yang sama dengan checkout + cek ulang order terbuka, atomik dengan INSERT
        if (this.online) open = await this.online.lockAndAssertNoOpenOrders(q, user.memberId, { confirm, maxDeferDays });
        await q.query(
          `INSERT INTO platform.dsar_request
             (id, member_id, member_hash, type, status, stores_pending, requested_at, scheduled_at, open_orders_ack_at)
           VALUES ($1, $2, $3, 'delete', 'scheduled', '{}', $5::timestamptz, $4, CASE WHEN $6::boolean THEN $5::timestamptz END)`,
          [id, user.memberId, user.memberHash, scheduled, requested, open > 0],
        );
      });
    } catch (err) {
      if (err instanceof ApiError) throw err;
      // Balapan dua DELETE paralel → unique uq_dsar_active_delete; kembalikan yang menang
      const again = await this.findActiveDelete(user.memberId);
      if (again) return this.deleteResponse(again, points, await this.openPartsOf(user.memberId));
      throw err;
    }
    const note = open > 0 ? ` (pesanan online terbuka: ${open}, batas ${maxDeferDays} hari)` : '';
    this.logger.log(`Permintaan hapus akun ${id} dijadwalkan ${scheduled}${note}`);
    return this.deleteResponse({ id, requestedAt: requested, scheduledAt: scheduled }, points, open);
  }

  private async openPartsOf(memberId: string): Promise<number> {
    return this.online ? this.online.openPaidParts(memberId) : 0;
  }

  /** Batas erasure W-18: max(requested_at + N hari, scheduled_at). */
  erasureDeadline(requestedAt: string, scheduledAt: string): string {
    return erasureDeadlineIso(requestedAt, scheduledAt, 1, this.config.dsarOpenOrderMaxDeferDays) ?? scheduledAt;
  }

  private deleteResponse(
    req: { id: string; requestedAt: string; scheduledAt: string },
    points: number,
    openParts: number,
  ): DeleteAccountResponse {
    return {
      request_id: req.id,
      scheduled_at: req.scheduledAt,
      points_forfeited: points,
      receipt_token: this.receipts.issue(req.id),
      open_online_orders: openParts,
      erasure_deadline: openParts > 0 ? this.erasureDeadline(req.requestedAt, req.scheduledAt) : null,
    };
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
      open_n?: number | string | null;
    }>(
      `SELECT d.id::text AS id, d.type, d.status, d.requested_at, d.scheduled_at, d.completed_at,
              ${OPEN_PARTS_COLUMN}
         FROM platform.dsar_request d WHERE d.member_id = $1
        ORDER BY d.requested_at DESC LIMIT 50`,
      [user.memberId],
    );
    return { items: res.rows.map((r) => dsarItem(r, this.config.dsarOpenOrderMaxDeferDays)) };
  }

  private async findActiveDelete(memberId: string): Promise<{ id: string; requestedAt: string; scheduledAt: string } | null> {
    const res = await this.db.query<{ id: string; requested_at: Date | string; scheduled_at: Date | string }>(
      `SELECT id::text AS id, requested_at, scheduled_at FROM platform.dsar_request
        WHERE member_id = $1 AND type = 'delete' AND status IN ('scheduled', 'processing')
        LIMIT 1`,
      [memberId],
    );
    const r = res.rows[0];
    return r ? { id: r.id, requestedAt: toIso(r.requested_at) ?? '', scheduledAt: toIso(r.scheduled_at) ?? '' } : null;
  }
}

