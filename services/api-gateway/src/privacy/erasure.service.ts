import { Inject, Injectable, Logger, OnModuleDestroy, OnModuleInit } from '@nestjs/common';
import { AppConfig } from '../config/app-config';
import { DB, Db, Queryable } from '../common/db/db';
import { erasureTombstone } from '../common/pii/pii';
import { OBJECT_STORAGE, ObjectStorage } from '../market-scout/storage/object-storage';

export const ERASURE_STORES = ['platform', 'odoo', 'clickhouse'] as const;
export type ErasureStore = (typeof ERASURE_STORES)[number];
export const ERASURE_LOCK_KEY = 0x45464e45; // 'EFNE'
const DUE_BATCH = 50;

export type CompleteStoreResult =
  | { ok: true; status: string }
  | { ok: false; reason: 'not_found' | 'not_processing' };

export interface ErasureRunResult {
  processed: number;
  failed: number;
  skipped: boolean;
}

/**
 * Penghapusan akun lintas-store (docs/04 §8.4, PDP_DSAR_DESIGN §5), tanpa nomor HP mentah di luar BFF.
 *
 * Job periodik (`DSAR_JOB_INTERVAL_MS`, satu replika per siklus via `pg_try_advisory_xact_lock`):
 * untuk setiap `dsar_request` `delete` berstatus `scheduled` yang `scheduled_at` ≤ now (masa tenggang
 * `DSAR_DELETE_GRACE_DAYS` habis) — dalam SATU transaksi:
 *   1. status → `processing`, `stores_pending = {platform, odoo, clickhouse}`, `member.deleted_at = now()`
 *      (token yang beredar langsung ditolak JwtAuthGuard);
 *   2. erasure platform SEGERA (permintaan lead: identitas subjek hilang dari sumber sebelum ETL siklus berikut):
 *      - ledger: entri `forfeit` per PT (−saldo belum kedaluwarsa, source `dsar`) lalu SEMUA baris member
 *        `member_ref_hash` → tombstone `sha256("erased:"||request_id)`, `member_ref_enc` NULL, `erased_at` now
 *        (catatan finansial 10 th dipertahankan, total liabilitas per PT tetap seimbang);
 *      - `price_hunter_report`: `user_hash` → tombstone, `user_enc`/`geo_*` NULL, `updated_at` now (ETL ikut
 *        menerima versi teranonim lewat watermark updated_at); foto MinIO dihapus (best effort SEBELUM transaksi;
 *        sisa dibersihkan lifecycle 90 hari);
 *      - `otp_audit`: `subject` → tombstone, `ip_enc` NULL;
 *      - `consent`: `ip_enc` NULL (baris dipertahankan sebagai bukti tanpa PII);
 *      - `member`: baris DIHAPUS;
 *      - `stores_pending` −= platform.
 *   Pilihan tombstone (bukan sekadar `erased_at`): hash lama tidak lagi muncul di sumber mana pun, sehingga ETL yang
 *   memuat ulang (watermark/backfill) tidak bisa menghidupkan kembali subjek; tombstone tidak dapat dihubungkan ke
 *   nomor HP (tanpa pepper pun). `dsar_request.member_hash` dipertahankan agar Odoo/ClickHouse menemukan subjeknya
 *   dan agar restore backup bisa menjalankan ulang daftar penghapusan.
 * Store lain menyelesaikan lewat `completeStore` (Odoo via HTTP pull/complete, ClickHouse via event
 * `privacy.erasure.completed`). `stores_pending` kosong → `completed`, `completed_at`.
 * Gagal di tengah → transaksi rollback, status tetap `scheduled`, dicoba siklus berikutnya (idempotent).
 */
@Injectable()
export class ErasureService implements OnModuleInit, OnModuleDestroy {
  private readonly logger = new Logger(ErasureService.name);
  private timer: NodeJS.Timeout | null = null;
  private running = false;

  constructor(
    private readonly config: AppConfig,
    @Inject(DB) private readonly db: Db,
    @Inject(OBJECT_STORAGE) private readonly storage: ObjectStorage,
  ) {}

  onModuleInit(): void {
    const ms = this.config.dsarJobIntervalMs;
    if (ms > 0) {
      this.timer = setInterval(() => void this.runOnce(), ms);
      this.timer.unref();
    }
  }

  onModuleDestroy(): void {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
  }

  /** Tidak pernah melempar (dipanggil dari timer). */
  async runOnce(): Promise<ErasureRunResult> {
    if (this.running) return { processed: 0, failed: 0, skipped: true };
    this.running = true;
    try {
      const due = await this.db.query<{ id: string }>(
        `SELECT id::text AS id FROM platform.dsar_request
          WHERE type = 'delete' AND status = 'scheduled' AND scheduled_at <= now()
          ORDER BY scheduled_at LIMIT $1`,
        [DUE_BATCH],
      );
      let processed = 0;
      let failed = 0;
      for (const row of due.rows) {
        try {
          if (await this.processDue(row.id)) processed += 1;
        } catch (err) {
          failed += 1;
          this.logger.error(`Erasure ${row.id} gagal (dicoba ulang): ${err instanceof Error ? err.message : String(err)}`);
        }
      }
      if (processed + failed > 0) this.logger.log(`Erasure platform: ${processed} selesai, ${failed} gagal`);
      return { processed, failed, skipped: false };
    } catch (err) {
      this.logger.error(`Job erasure gagal: ${err instanceof Error ? err.message : String(err)}`);
      return { processed: 0, failed: 0, skipped: true };
    } finally {
      this.running = false;
    }
  }

  /** @returns false bila request tidak lagi `scheduled` (dibatalkan/diproses replika lain). */
  async processDue(requestId: string): Promise<boolean> {
    const req = await this.db.query<{ member_hash: string }>(
      `SELECT member_hash FROM platform.dsar_request WHERE id = $1 AND type = 'delete' AND status = 'scheduled'`,
      [requestId],
    );
    const hash = req.rows[0]?.member_hash;
    if (!hash) return false;
    await this.removePhotos(hash);

    return this.db.tx(async (q) => {
      const lock = await q.query<{ ok: boolean }>('SELECT pg_try_advisory_xact_lock($1, hashtext($2)) AS ok', [
        ERASURE_LOCK_KEY,
        requestId,
      ]);
      if (!lock.rows[0]?.ok) return false;
      const started = await q.query<{ member_id: string; member_hash: string }>(
        `UPDATE platform.dsar_request
            SET status = 'processing', stores_pending = ARRAY['platform', 'odoo', 'clickhouse']::text[]
          WHERE id = $1 AND type = 'delete' AND status = 'scheduled' AND scheduled_at <= now()
          RETURNING member_id::text AS member_id, member_hash`,
        [requestId],
      );
      const r = started.rows[0];
      if (!r) return false;
      await q.query(`UPDATE platform.member SET deleted_at = now() WHERE id = $1 AND deleted_at IS NULL`, [r.member_id]);
      await this.erasePlatform(q, requestId, r.member_id, r.member_hash);
      await this.completeStore('platform', requestId, q);
      return true;
    });
  }

  /** Erasure data platform untuk satu subjek (di dalam transaksi pemanggil). */
  async erasePlatform(q: Queryable, requestId: string, memberId: string, hash: string): Promise<void> {
    const tomb = erasureTombstone(requestId);
    // 1. Forfeit saldo per PT (liabilitas turun tercatat; perlakuan akuntansi: Finance, ADR-020)
    const balances = await q.query<{ company_code: string; balance: string | number }>(
      `SELECT company_code, SUM(delta_points)::bigint AS balance
         FROM platform.loyalty_ledger
        WHERE member_ref_hash = $1 AND (expires_at IS NULL OR expires_at > now())
        GROUP BY company_code
       HAVING SUM(delta_points) > 0`,
      [hash],
    );
    for (const b of balances.rows) {
      await q.query(
        `INSERT INTO platform.loyalty_ledger
           (member_ref_hash, member_ref_enc, erased_at, company_code, delta_points, reason, source, source_ref, idempotency_key)
         VALUES ($1, NULL, now(), $2, $3, 'dsar_forfeit', 'dsar', $4, $5)
         ON CONFLICT (idempotency_key) DO NOTHING`,
        [tomb, b.company_code, -Number(b.balance), requestId, `dsar:forfeit:${requestId}:${b.company_code}`],
      );
    }
    // 2. Tombstone ledger
    await q.query(
      `UPDATE platform.loyalty_ledger
          SET member_ref_hash = $2, member_ref_enc = NULL, erased_at = COALESCE(erased_at, now())
        WHERE member_ref_hash = $1`,
      [hash, tomb],
    );
    // 3. Laporan Market Scout
    await q.query(
      `UPDATE platform.price_hunter_report
          SET user_hash = $2, user_enc = NULL, geo_lat = NULL, geo_lng = NULL, updated_at = now()
        WHERE user_hash = $1`,
      [hash, tomb],
    );
    // 4. Audit OTP
    await q.query(`UPDATE platform.otp_audit SET subject = $2, ip_enc = NULL WHERE subject = $1`, [hash, tomb]);
    // 5. Consent: bukti dipertahankan tanpa PII
    await q.query(`UPDATE platform.consent SET ip_enc = NULL WHERE member_id = $1 AND ip_enc IS NOT NULL`, [memberId]);
    // 6. Member
    await q.query(`DELETE FROM platform.member WHERE id = $1`, [memberId]);
  }

  /**
   * Tandai satu store selesai. Idempotent: store yang sudah dihapus dari `stores_pending` → ok dengan status saat ini.
   * Hanya request `processing`/`completed` yang diterima.
   */
  async completeStore(store: ErasureStore, requestId: string, q: Queryable = this.db): Promise<CompleteStoreResult> {
    const res = await q.query<{ status: string }>(
      `UPDATE platform.dsar_request
          SET stores_pending = array_remove(stores_pending, $2::text),
              status = CASE WHEN status = 'processing' AND cardinality(array_remove(stores_pending, $2::text)) = 0
                            THEN 'completed' ELSE status END,
              completed_at = CASE WHEN status = 'processing' AND cardinality(array_remove(stores_pending, $2::text)) = 0
                                  THEN now() ELSE completed_at END
        WHERE id = $1 AND type = 'delete' AND status IN ('processing', 'completed')
        RETURNING status`,
      [requestId, store],
    );
    if (res.rows[0]) return { ok: true, status: res.rows[0].status };
    const exists = await q.query<{ status: string }>(`SELECT status FROM platform.dsar_request WHERE id = $1 AND type = 'delete'`, [
      requestId,
    ]);
    return { ok: false, reason: exists.rows[0] ? 'not_processing' : 'not_found' };
  }

  /** Antrean pull Odoo: `[{ request_id, member_hash }]` untuk request `processing` yang masih menunggu store tsb. */
  async pendingFor(store: 'odoo'): Promise<{ request_id: string; member_hash: string }[]> {
    const res = await this.db.query<{ request_id: string; member_hash: string }>(
      `SELECT id::text AS request_id, member_hash
         FROM platform.dsar_request
        WHERE type = 'delete' AND status = 'processing' AND $1::text = ANY(stores_pending)
        ORDER BY requested_at
        LIMIT 500`,
      [store],
    );
    return res.rows.map((r) => ({ request_id: r.request_id, member_hash: r.member_hash }));
  }

  private async removePhotos(hash: string): Promise<void> {
    const objs = await this.db.query<{ image_object: string }>(
      `SELECT image_object FROM platform.price_hunter_report WHERE user_hash = $1`,
      [hash],
    );
    const bucket = this.config.marketScoutBucket;
    let failed = 0;
    for (const o of objs.rows) {
      try {
        await this.storage.remove(bucket, o.image_object);
      } catch {
        failed += 1;
      }
    }
    if (failed > 0) this.logger.warn(`Erasure: ${failed} foto gagal dihapus (akan dihapus lifecycle MinIO)`);
  }
}
