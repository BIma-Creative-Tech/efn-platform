import { Inject, Injectable, Logger, OnModuleDestroy, OnModuleInit } from '@nestjs/common';
import { AppConfig } from '../../config/app-config';
import { DB, Db, Queryable } from '../db/db';

/**
 * Retensi data minimal UU PDP (bagian teknis SEC-11). Job periodik menghapus:
 *
 * | Tabel | Kriteria | Default |
 * |---|---|---|
 * | `platform.otp_audit` | `created_at` > `OTP_AUDIT_RETENTION_DAYS` | 90 hari |
 * | `platform.processed_message` | `processed_at` > `PROCESSED_MESSAGE_RETENTION_DAYS` | 14 hari |
 * | `platform.outbox` (sudah terkirim) | `published_at` > `OUTBOX_RETENTION_DAYS` | 14 hari |
 * | `platform.price_hunter_report.user_enc` → NULL | `created_at` > `PRICE_HUNTER_USER_ENC_RETENTION_DAYS` | 90 hari |
 * | `platform.price_hunter_report.geo_lat/geo_lng` → NULL (+ `updated_at`) | `created_at` > `PRICE_HUNTER_GEO_RETENTION_DAYS` | 90 hari (W-19, Q-P8) |
 * | `platform.price_hunter_report` (baris) | `created_at` > `PRICE_HUNTER_REPORT_RETENTION_DAYS` | 730 hari |
 *
 * (docs/04 §8.4 — usulan, menunggu DPO.) Foto MinIO: lifecycle 90 hari (DevSecOps). Ledger 10 th & consent
 * "akun + 5 th" tidak di-purge BFF (ledger = catatan finansial; consent append-only tanpa hak DELETE).
 *
 * - Hapus bertahap (batch `ctid ... LIMIT`) agar tidak mengunci tabel lama; maksimal `MAX_BATCHES` per siklus.
 * - Multi-replika: hanya satu replika yang menjalankan per siklus (`pg_try_advisory_xact_lock`).
 * - `outbox` yang BELUM terkirim tidak pernah dihapus (akan dikirim relay).
 * - Purge `processed_message` > 14 hari: redelivery/replay DLQ yang lebih tua dari itu masih aman untuk poin karena
 *   UNIQUE `loyalty_ledger.idempotency_key`; notifikasi bisa terkirim ulang (diterima).
 */
export const RETENTION_LOCK_KEY = 0x45464e52; // 'EFNR'
const BATCH_SIZE = 5000;
const MAX_BATCHES = 100;

export interface PurgeResult {
  otpAudit: number;
  processedMessage: number;
  outbox: number;
  reportUserEncNulled: number;
  reportGeoNulled: number;
  reportDeleted: number;
  skipped: boolean;
}

@Injectable()
export class RetentionService implements OnModuleInit, OnModuleDestroy {
  private readonly logger = new Logger(RetentionService.name);
  private timer: NodeJS.Timeout | null = null;
  private running = false;

  constructor(
    @Inject(DB) private readonly db: Db,
    private readonly config: AppConfig,
  ) {}

  onModuleInit(): void {
    const ms = this.config.retentionPurgeIntervalMs;
    if (ms > 0) {
      this.timer = setInterval(() => void this.purgeOnce(), ms);
      this.timer.unref();
    }
  }

  onModuleDestroy(): void {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
  }

  /** Tidak pernah melempar (dipanggil dari timer). */
  async purgeOnce(): Promise<PurgeResult> {
    const empty: PurgeResult = {
      otpAudit: 0,
      processedMessage: 0,
      outbox: 0,
      reportUserEncNulled: 0,
      reportGeoNulled: 0,
      reportDeleted: 0,
      skipped: true,
    };
    if (this.running) return empty;
    this.running = true;
    try {
      const res = await this.db.tx(async (q) => {
        const lock = await q.query<{ ok: boolean }>('SELECT pg_try_advisory_xact_lock($1) AS ok', [RETENTION_LOCK_KEY]);
        if (!lock.rows[0]?.ok) return empty;
        const otpAudit = await this.purge(
          q,
          `DELETE FROM platform.otp_audit WHERE ctid IN (
             SELECT ctid FROM platform.otp_audit
              WHERE created_at < now() - make_interval(days => $1) LIMIT $2)`,
          this.config.otpAuditRetentionDays,
        );
        const processedMessage = await this.purge(
          q,
          `DELETE FROM platform.processed_message WHERE ctid IN (
             SELECT ctid FROM platform.processed_message
              WHERE processed_at < now() - make_interval(days => $1) LIMIT $2)`,
          this.config.processedMessageRetentionDays,
        );
        const outbox = await this.purge(
          q,
          `DELETE FROM platform.outbox WHERE ctid IN (
             SELECT ctid FROM platform.outbox
              WHERE published_at IS NOT NULL AND published_at < now() - make_interval(days => $1) LIMIT $2)`,
          this.config.outboxRetentionDays,
        );
        // user_enc tidak mengubah updated_at (ETL tidak membaca kolom *_enc)
        const reportUserEncNulled = await this.purge(
          q,
          `UPDATE platform.price_hunter_report SET user_enc = NULL WHERE ctid IN (
             SELECT ctid FROM platform.price_hunter_report
              WHERE user_enc IS NOT NULL AND created_at < now() - make_interval(days => $1) LIMIT $2)`,
          this.config.priceHunterUserEncRetentionDays,
        );
        // W-19 (Q-P8): lokasi dibuang setelah 90 h; updated_at diperbarui agar ETL (watermark updated_at) membawa
        // versi tanpa lokasi ke ClickHouse (pola sama dengan erasure)
        const reportGeoNulled = await this.purge(
          q,
          `UPDATE platform.price_hunter_report SET geo_lat = NULL, geo_lng = NULL, updated_at = now() WHERE ctid IN (
             SELECT ctid FROM platform.price_hunter_report
              WHERE (geo_lat IS NOT NULL OR geo_lng IS NOT NULL) AND created_at < now() - make_interval(days => $1) LIMIT $2)`,
          this.config.priceHunterGeoRetentionDays,
        );
        const reportDeleted = await this.purge(
          q,
          `DELETE FROM platform.price_hunter_report WHERE ctid IN (
             SELECT ctid FROM platform.price_hunter_report
              WHERE created_at < now() - make_interval(days => $1) LIMIT $2)`,
          this.config.priceHunterReportRetentionDays,
        );
        return { otpAudit, processedMessage, outbox, reportUserEncNulled, reportGeoNulled, reportDeleted, skipped: false };
      });
      if (!res.skipped && res.otpAudit + res.processedMessage + res.outbox + res.reportUserEncNulled + res.reportGeoNulled + res.reportDeleted > 0) {
        this.logger.log(
          `Retensi: otp_audit=${res.otpAudit}, processed_message=${res.processedMessage}, outbox=${res.outbox} dihapus; ` +
            `price_hunter_report user_enc→NULL=${res.reportUserEncNulled}, geo→NULL=${res.reportGeoNulled}, baris dihapus=${res.reportDeleted}`,
        );
      }
      return res;
    } catch (err) {
      this.logger.error(`Job retensi gagal: ${err instanceof Error ? err.message : String(err)}`);
      return empty;
    } finally {
      this.running = false;
    }
  }

  private async purge(q: Queryable, sql: string, days: number): Promise<number> {
    let total = 0;
    for (let i = 0; i < MAX_BATCHES; i += 1) {
      const res = await q.query(sql, [days, BATCH_SIZE]);
      const n = res.rowCount ?? 0;
      total += n;
      if (n < BATCH_SIZE) break;
    }
    return total;
  }
}
