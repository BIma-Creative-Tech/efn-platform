import { Inject, Injectable } from '@nestjs/common';
import { createHmac, timingSafeEqual } from 'node:crypto';
import { AppConfig } from '../config/app-config';
import { DB, Db } from '../common/db/db';
import { Errors } from '../common/errors/api-error';
import { redisKeyHash } from '../common/pii/pii';
import { RateLimiter } from '../common/rate-limit/rate-limiter';
import { isUuid } from '../common/validation';
import { toIso } from './consent.service';
import type { DsarRequestItem } from './dsar.service';

export const DSAR_RECEIPT_HEADER = 'x-dsar-receipt';
export const DSAR_RECEIPT_PURPOSE = 'dsar_receipt';
const RECEIPT_RE = /^v1\.([0-9]{1,12})\.([A-Za-z0-9_-]{43})$/;
const RECEIPT_RL_WINDOW_SECONDS = 900;

/**
 * R6 (docs/04 §8.13): bukti permintaan hapus akun yang tetap bisa dipakai setelah akun hilang.
 *
 * `receipt_token` = `v1.<exp_epoch_s>.<base64url(HMAC-SHA256(k, "dsar_receipt\n<request_id>\n<exp>"))>`,
 * k = `AppConfig.dsarReceiptKey` (HKDF dari jwt_secret, info `efn:dsar_receipt:v1`). Opak bagi klien, TANPA PII,
 * terikat `request_id` (HMAC), berlaku `DSAR_RECEIPT_TTL_DAYS` (default 30). Tidak disimpan di server (stateless) —
 * tidak ada tabel yang menautkan receipt ke subjek.
 *
 * `GET /v1/dsar/requests/:id` + header `X-DSAR-Receipt` → `{ id, type, status, requested_at, scheduled_at, completed_at }`.
 * Token hilang/salah/kedaluwarsa/milik request lain, id bukan UUID, atau request tidak ada → `404 not_found` generik
 * (tidak membocorkan keberadaan request). Rate limit per IP (hash HMAC, SEC-24) `DSAR_RECEIPT_RL_LIMIT` / 15 menit.
 */
@Injectable()
export class DsarReceiptService {
  now: () => number = () => Date.now();

  constructor(
    private readonly config: AppConfig,
    @Inject(DB) private readonly db: Db,
    private readonly limiter: RateLimiter,
  ) {}

  issue(requestId: string): string {
    const exp = Math.floor(this.now() / 1000) + this.config.dsarReceiptTtlDays * 86_400;
    return `v1.${exp}.${this.mac(requestId.toLowerCase(), exp).toString('base64url')}`;
  }

  /** true bila token sah untuk `requestId` dan belum kedaluwarsa. Perbandingan waktu-konstan. */
  verify(requestId: string, token: string | undefined): boolean {
    if (typeof token !== 'string' || token.length > 128 || !isUuid(requestId)) return false;
    const m = RECEIPT_RE.exec(token);
    if (!m) return false;
    const exp = Number(m[1]);
    if (!Number.isSafeInteger(exp) || exp <= Math.floor(this.now() / 1000)) return false;
    const given = Buffer.from(m[2], 'base64url');
    const expected = this.mac(requestId.toLowerCase(), exp);
    return given.length === expected.length && timingSafeEqual(given, expected);
  }

  async status(requestId: string, token: string | undefined, ip: string | null): Promise<DsarRequestItem> {
    await this.limiter.enforce(
      `dsar:receipt-ip:${redisKeyHash(this.config.redisKeyPepper, ip ?? 'unknown')}`,
      this.config.dsarReceiptRateLimit,
      RECEIPT_RL_WINDOW_SECONDS,
    );
    if (!this.verify(requestId, token)) throw Errors.notFound();
    const res = await this.db.query<{
      id: string;
      type: string;
      status: string;
      requested_at: Date | string;
      scheduled_at: Date | string | null;
      completed_at: Date | string | null;
    }>(
      `SELECT id::text AS id, type, status, requested_at, scheduled_at, completed_at
         FROM platform.dsar_request WHERE id = $1 AND type = 'delete'`,
      [requestId.toLowerCase()],
    );
    const r = res.rows[0];
    if (!r) throw Errors.notFound();
    return {
      id: r.id,
      type: r.type,
      status: r.status,
      requested_at: toIso(r.requested_at),
      scheduled_at: toIso(r.scheduled_at),
      completed_at: toIso(r.completed_at),
    };
  }

  private mac(requestId: string, exp: number): Buffer {
    return createHmac('sha256', this.config.dsarReceiptKey).update(`${DSAR_RECEIPT_PURPOSE}\n${requestId}\n${exp}`, 'utf8').digest();
  }
}
