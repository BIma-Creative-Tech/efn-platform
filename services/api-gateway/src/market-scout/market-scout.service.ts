import { Inject, Injectable, Logger } from '@nestjs/common';
import { randomUUID } from 'node:crypto';
import { AppConfig } from '../config/app-config';
import { QUEUES } from '../common/amqp/queues';
import { DB, Db } from '../common/db/db';
import { ApiError, Errors } from '../common/errors/api-error';
import { MemberService } from '../common/member/member.service';
import { OutboxService } from '../common/outbox/outbox.service';
import { PII_AAD, PiiCipher } from '../common/pii/crypto';
import { maskMsisdn } from '../common/pii/pii';
import { RateLimiter, WIB_OFFSET_SECONDS } from '../common/rate-limit/rate-limiter';
import { AuthUser } from '../common/security/access-token.service';
import { isUuid } from '../common/validation';
import { ConsentService } from '../privacy/consent.service';
import { RawReportForm, parseReportFields } from './dto/report.dto';
import { ImageProcessor, NormalizedImage } from './image/image-processor';
import { detectImageKind } from './image/magic-bytes';
import { OBJECT_STORAGE, ObjectStorage } from './storage/object-storage';

export type ReportStatus = 'queued' | 'processing' | 'valid' | 'rejected' | 'flagged';

export interface SubmitReportResponse {
  report_id: string;
  status: 'queued';
}

export interface ReportStatusResponse {
  report_id: string;
  status: ReportStatus;
  reason: string | null;
  points_awarded: number;
}

/** Payload `ocr.requested` v2 (kontrak §4): `user_hash`, tanpa nomor HP mentah. */
export interface OcrRequestedPayload extends Record<string, unknown> {
  schema_version: 2;
  report_id: string;
  user_hash: string;
  image_bucket: string;
  image_object: string;
  product_name: string;
  reported_price: number;
  competitor_name: string | null;
  geo: { lat: number; lng: number } | null;
  submitted_at: string;
}

/**
 * Price Hunter intake (kontrak §3.3, alur docs/02 §3.2, D-06, R-S06, R-B05).
 *
 * Urutan: validasi field → magic bytes → cap harian (Redis, reset 00:00 WIB) → re-encode sharp + dHash →
 * PUT MinIO → transaksi { INSERT price_hunter_report (queued) + INSERT outbox ocr.requested } → 202.
 *
 * Konsistensi DB↔broker memakai **transactional outbox** (bukan publish-setelah-commit):
 * publish-setelah-commit bisa kehilangan event bila proses mati/broker down di antara COMMIT dan publish,
 * meninggalkan report `queued` selamanya. Dengan outbox, event pasti terkirim (at-least-once) begitu
 * baris di-commit; latensi ditekan dengan memicu relay segera setelah commit. Biaya: satu tabel + relay,
 * dan duplikasi mungkin → worker wajib idempotent (cek status `queued` → `processing`).
 * Objek MinIO ditulis SEBELUM transaksi; bila transaksi gagal, objek dihapus (best effort) — objek yatim
 * yang tersisa tidak berbahaya dan bisa dibersihkan lifecycle rule bucket.
 *
 * PII (docs/04 §7.2): tabel menyimpan `user_hash` (member_hash) + `user_enc` (AES-256-GCM, AAD
 * `efn:price_hunter_report.user_enc:v1`); nomor HP plaintext tidak disimpan dan tidak ada di payload event / outbox.
 * Koordinat dibulatkan 3 desimal di server sebelum disimpan & diteruskan (SEC-17), dan DIBUANG bila consent
 * `location` member tidak aktif (docs/04 §8.4).
 * v3 (§8.2): identitas dari access token = member.id → `user_hash` = member.member_hash, `user_enc` dibuat dari
 * `member.member_ref_enc` (dekripsi → enkripsi ulang dengan AAD laporan). Kepemilikan GET via member_hash.
 * Retensi (§8.4): `user_enc` di-null-kan > 90 hari, baris dihapus > 2 tahun (RetentionService).
 */
@Injectable()
export class MarketScoutService {
  private readonly logger = new Logger(MarketScoutService.name);
  now: () => Date = () => new Date();

  constructor(
    private readonly config: AppConfig,
    @Inject(DB) private readonly db: Db,
    private readonly outbox: OutboxService,
    private readonly limiter: RateLimiter,
    private readonly images: ImageProcessor,
    @Inject(OBJECT_STORAGE) private readonly storage: ObjectStorage,
    private readonly cipher: PiiCipher,
    private readonly members: MemberService,
    private readonly consents: ConsentService,
  ) {}

  async submit(user: AuthUser, form: RawReportForm): Promise<SubmitReportResponse> {
    const parsed = parseReportFields(form.fields);
    const photo = form.photo;
    if (!photo || photo.length === 0) throw Errors.invalidRequest('Foto wajib diunggah.');
    if (photo.length > this.config.marketScoutMaxBytes) throw Errors.payloadTooLarge();
    if (!detectImageKind(photo)) throw Errors.unsupportedMediaType();

    const subject = user.memberHash;
    await this.limiter.enforce(`ms:daily:${subject}`, this.config.marketScoutDailyCap, 86_400, WIB_OFFSET_SECONDS);

    // docs/04 §8.4: koordinat DIBUANG di server bila consent `location` tidak aktif (default tidak setuju)
    const hasCoords = parsed.lat !== null && parsed.lng !== null;
    const locationOk = hasCoords && (await this.consents.isGranted(user.memberId, 'location'));
    const fields = locationOk ? parsed : { ...parsed, lat: null, lng: null };

    const memberRef = await this.members.memberRef(user.memberId);
    if (!memberRef) throw Errors.unauthorized();

    let image: NormalizedImage;
    try {
      image = await this.images.normalize(photo);
    } catch (err) {
      if (err instanceof ApiError) throw err; // SEC-19: antrean decode penuh → 429
      // Lolos magic bytes tetapi gagal di-decode (rusak / polyglot / bomb) → tolak sebagai format tidak didukung
      throw Errors.unsupportedMediaType();
    }

    const reportId = randomUUID();
    const now = this.now();
    const yyyy = String(now.getUTCFullYear());
    const mm = String(now.getUTCMonth() + 1).padStart(2, '0');
    const objectKey = `reports/${yyyy}/${mm}/${reportId}.jpg`;
    const bucket = this.config.marketScoutBucket;

    try {
      await this.storage.put(bucket, objectKey, image.jpeg, 'image/jpeg');
    } catch (err) {
      this.logger.error(`Gagal menyimpan foto ke MinIO: ${err instanceof Error ? err.message : String(err)}`);
      throw Errors.serviceUnavailable();
    }

    const payload: OcrRequestedPayload = {
      schema_version: 2,
      report_id: reportId,
      user_hash: subject,
      image_bucket: bucket,
      image_object: objectKey,
      product_name: fields.productName,
      reported_price: fields.price,
      competitor_name: fields.competitorName,
      geo: fields.lat !== null && fields.lng !== null ? { lat: fields.lat, lng: fields.lng } : null,
      submitted_at: now.toISOString(),
    };

    try {
      await this.db.tx(async (q) => {
        // `image_phash` TIDAK diisi BFF: pemilik tunggal kolom ini adalah ai-ocr-worker (pHash imagehash),
        // yang membandingkan pHash laporan baru dengan nilai di kolom ini. Mengisi dHash di sini membuat
        // perbandingan pHash↔dHash tidak bermakna untuk laporan yang belum/tidak difinalisasi worker.
        await q.query(
          `INSERT INTO platform.price_hunter_report
             (id, user_hash, user_enc, product_name, reported_price, competitor_name, geo_lat, geo_lng, image_object, status)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, 'queued')`,
          [
            reportId,
            subject,
            this.cipher.encrypt(memberRef, PII_AAD.PRICE_HUNTER_REPORT_USER),
            fields.productName,
            fields.price,
            fields.competitorName,
            fields.lat,
            fields.lng,
            objectKey,
          ],
        );
        await this.outbox.enqueue(q, QUEUES.OCR_REQUESTED, payload, `ocr.requested:${reportId}`);
      });
    } catch (err) {
      await this.storage.remove(bucket, objectKey).catch(() => undefined);
      this.logger.error(`Gagal menyimpan report: ${err instanceof Error ? err.message : String(err)}`);
      throw Errors.serviceUnavailable();
    }

    void this.outbox.relayOnce();
    this.logger.log(`Report ${reportId} diterima dari ${maskMsisdn(memberRef)}`);
    return { report_id: reportId, status: 'queued' };
  }

  /**
   * Hanya pemilik yang bisa melihat: `user_hash` = member_hash dari `sub` access token.
   * Report milik orang lain → 404 (tidak membedakan "ada tapi bukan milikmu").
   */
  async get(user: AuthUser, reportId: string): Promise<ReportStatusResponse> {
    if (!isUuid(reportId)) throw Errors.notFound();
    const userHash = user.memberHash;
    const res = await this.db.query<{ id: string; status: ReportStatus; reason: string | null; points_awarded: string | number | null }>(
      `SELECT r.id, r.status, r.reason,
              COALESCE((SELECT SUM(l.delta_points)
                          FROM platform.loyalty_ledger l
                         WHERE l.idempotency_key = 'price_hunter:' || r.id::text), 0) AS points_awarded
         FROM platform.price_hunter_report r
        WHERE r.id = $1 AND r.user_hash = $2`,
      [reportId.toLowerCase(), userHash],
    );
    const row = res.rows[0];
    if (!row) throw Errors.notFound();
    return {
      report_id: row.id,
      status: row.status,
      reason: row.reason ?? null,
      points_awarded: Number(row.points_awarded ?? 0),
    };
  }
}
