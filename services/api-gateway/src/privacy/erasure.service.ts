import { Inject, Injectable, Logger, OnModuleDestroy, OnModuleInit } from '@nestjs/common';
import { randomUUID } from 'node:crypto';
import { AppConfig } from '../config/app-config';
import { DB, Db, Queryable } from '../common/db/db';
import { erasureSuppressionTag } from '../common/pii/erasure-suppression';
import { randomTombstone } from '../common/pii/pii';
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
 * Penghapusan akun lintas-store (docs/04 §8.4, §8.13; PDP_DSAR_DESIGN §5), tanpa nomor HP mentah di luar BFF.
 *
 * Job periodik (`DSAR_JOB_INTERVAL_MS`, satu replika per siklus via `pg_try_advisory_xact_lock`):
 * untuk setiap `dsar_request` `delete` berstatus `scheduled` yang `scheduled_at` ≤ now (masa tenggang
 * `DSAR_DELETE_GRACE_DAYS` habis) — dalam SATU transaksi:
 *   1. status → `processing`, `stores_pending = {platform, odoo, clickhouse}`, `member.deleted_at = now()`
 *      (token yang beredar langsung ditolak JwtAuthGuard);
 *   2. erasure platform SEGERA dengan tombstone ACAK 32 byte (SEC-31, `randomTombstone`) — tidak diturunkan dari
 *      `request_id`/`member_hash` dan tidak disimpan di tabel/log mana pun selain baris yang di-tombstone:
 *      - ledger: entri `forfeit` per PT (−saldo belum kedaluwarsa, source `dsar`, `source_ref` NULL, idempotency_key
 *        UUID acak — tidak memuat request_id) lalu SEMUA baris member `member_ref_hash` → tombstone,
 *        `member_ref_enc` NULL, `erased_at` now (catatan finansial 10 th dipertahankan, total liabilitas per PT seimbang);
 *      - `price_hunter_report`: `user_hash` → tombstone, `user_enc`/`geo_*` NULL, `updated_at` now (ETL ikut menerima
 *        versi teranonim lewat watermark updated_at);
 *      - `loyalty_reservation` dan `loyalty_pos_earn` (POS, §9.5/§9.6): baris subjek DIHAPUS (transien, pseudonim);
 *      - `otp_audit`: `subject` → tombstone, `ip_enc` NULL;
 *      - `consent`: `ip_enc` NULL (baris dipertahankan sebagai bukti tanpa PII);
 *      - `dsar_request` LAIN milik member (export, delete yang dulu dibatalkan): `member_hash` NULL;
 *      - `member`: baris DIHAPUS;
 *      - `stores_pending` −= platform.
 *   3. SETELAH commit (SEC-40 race hapus-foto vs batal): foto MinIO laporan subjek dihapus (best effort; sisa
 *      dibersihkan lifecycle 90 hari). Daftar objek diambil di dalam transaksi sebelum tombstone.
 * `dsar_request.member_hash` hanya dipertahankan selama `processing` (Odoo/ETL masih membutuhkannya) dan di-NULL-kan
 * saat `completed` (§8.13). Store lain menyelesaikan lewat `completeStore` (Odoo via HTTP pull/complete, ClickHouse via
 * event `privacy.erasure.completed`). `stores_pending` kosong → `completed`, `completed_at`, `member_hash` NULL.
 * Gagal di tengah → transaksi rollback, status tetap `scheduled`, dicoba siklus berikutnya (tombstone baru).
 *
 * SEC-65 (Gelombang 13c): saat request masuk `processing`, tag HMAC subjek (`erasureSuppressionTag`, domain terpisah
 * dari member_hash) disimpan di `platform.erasure_suppression` selama `ERASURE_SUPPRESSION_DAYS` (default 90; 0 = tidak
 * disimpan) agar impor migrasi K-7 menolak subjek ini setelah `member_hash` request di-NULL-kan. Baris kedaluwarsa
 * dihapus di awal setiap siklus job.
 *
 * Sisa risiko (didokumentasikan, README): korelasi waktu (`erased_at`/`created_at` forfeit ≈ `scheduled_at` request)
 * dapat menautkan baris ber-tombstone ke `request_id`, tetapi setelah `completed` request tidak lagi memuat
 * `member_hash` sehingga rantai ke nomor HP terputus.
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
      await this.purgeSuppression();
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
    const result = await this.db.tx(async (q) => {
      const lock = await q.query<{ ok: boolean }>('SELECT pg_try_advisory_xact_lock($1, hashtext($2)) AS ok', [
        ERASURE_LOCK_KEY,
        requestId,
      ]);
      if (!lock.rows[0]?.ok) return null;
      const started = await q.query<{ member_id: string; member_hash: string }>(
        `UPDATE platform.dsar_request
            SET status = 'processing', stores_pending = ARRAY['platform', 'odoo', 'clickhouse']::text[]
          WHERE id = $1 AND type = 'delete' AND status = 'scheduled' AND scheduled_at <= now()
          RETURNING member_id::text AS member_id, member_hash`,
        [requestId],
      );
      const r = started.rows[0];
      if (!r) return null;
      await q.query(`UPDATE platform.member SET deleted_at = now() WHERE id = $1 AND deleted_at IS NULL`, [r.member_id]);
      await this.suppress(q, r.member_hash);
      const objects = await q.query<{ image_object: string }>(
        `SELECT image_object FROM platform.price_hunter_report WHERE user_hash = $1`,
        [r.member_hash],
      );
      await this.erasePlatform(q, requestId, r.member_id, r.member_hash);
      await this.completeStore('platform', requestId, q);
      return objects.rows.map((o) => o.image_object);
    });
    if (result === null) return false;
    await this.removePhotos(result);
    return true;
  }

  /** Erasure data platform untuk satu subjek (di dalam transaksi pemanggil). */
  async erasePlatform(q: Queryable, requestId: string, memberId: string, hash: string): Promise<void> {
    const tomb = randomTombstone();
    // 1. Forfeit saldo per PT (liabilitas turun tercatat; perlakuan akuntansi: Finance, ADR-020).
    //    source_ref NULL & idempotency_key acak: entri tidak memuat request_id (SEC-31). Idempotensi dijamin transaksi +
    //    transisi status `scheduled → processing` (retry setelah rollback memulai dari nol).
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
         VALUES ($1, NULL, now(), $2, $3, 'dsar_forfeit', 'dsar', NULL, $4)`,
        [tomb, b.company_code, -Number(b.balance), `dsar:forfeit:${randomUUID()}`],
      );
    }
    // 2. Tombstone ledger
    await q.query(
      `UPDATE platform.loyalty_ledger
          SET member_ref_hash = $2, member_ref_enc = NULL, erased_at = COALESCE(erased_at, now())
        WHERE member_ref_hash = $1`,
      [hash, tomb],
    );
    // 2b. Reservasi redeem     // 2b. Reservasi redeem POS (§9.5): data transien berisi member_ref_enc → dihapus (catatan finansial ada di ledger) catatan earn per order POS (§9.5, §9.6 K-6): transien/pseudonim → dihapus (ledger tetap)
    await q.query(`DELETE FROM platform.loyalty_reservation WHERE member_hash = $1`, [hash]);
    await q.query(`DELETE FROM platform.loyalty_pos_earn WHERE member_hash = $1`, [hash]);
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
    // 6. Request DSAR lain milik subjek (export, delete yang pernah dibatalkan) tidak lagi memuat member_hash
    await q.query(
      `UPDATE platform.dsar_request SET member_hash = NULL
        WHERE member_id = $1 AND id <> $2 AND member_hash IS NOT NULL`,
      [memberId, requestId],
    );
    // 7. Member
    await q.query(`DELETE FROM platform.member WHERE id = $1`, [memberId]);
  }

  /**
   * Tandai satu store selesai. Idempotent: store yang sudah dihapus dari `stores_pending` → ok dengan status saat ini.
   * Hanya request `processing`/`completed` yang diterima. Saat `stores_pending` kosong → `completed` dan
   * `member_hash` di-NULL-kan (SEC-31, §8.13).
   */
  async completeStore(store: ErasureStore, requestId: string, q: Queryable = this.db): Promise<CompleteStoreResult> {
    const res = await q.query<{ status: string }>(
      `UPDATE platform.dsar_request
          SET stores_pending = array_remove(stores_pending, $2::text),
              status = CASE WHEN status = 'processing' AND cardinality(array_remove(stores_pending, $2::text)) = 0
                            THEN 'completed' ELSE status END,
              completed_at = CASE WHEN status = 'processing' AND cardinality(array_remove(stores_pending, $2::text)) = 0
                                  THEN now() ELSE completed_at END,
              member_hash = CASE WHEN cardinality(array_remove(stores_pending, $2::text)) = 0
                                 THEN NULL ELSE member_hash END
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
        WHERE type = 'delete' AND status = 'processing' AND $1::text = ANY(stores_pending) AND member_hash IS NOT NULL
        ORDER BY requested_at
        LIMIT 500`,
      [store],
    );
    return res.rows.map((r) => ({ request_id: r.request_id, member_hash: r.member_hash }));
  }

  /** SEC-65: tag daftar tolak migrasi (perpanjang bila sudah ada, tidak pernah memendekkan). */
  private async suppress(q: Queryable, hash: string): Promise<void> {
    const days = this.config.erasureSuppressionDays;
    if (days <= 0) return;
    await q.query(
      `INSERT INTO platform.erasure_suppression (tag, expires_at)
       VALUES ($1, now() + make_interval(days => $2::int))
       ON CONFLICT (tag) DO UPDATE SET expires_at = GREATEST(platform.erasure_suppression.expires_at, EXCLUDED.expires_at)`,
      [erasureSuppressionTag(this.config.erasureSuppressionKey, hash), days],
    );
  }

  /** Retensi daftar tolak (gagal → dicatat, siklus erasure tetap jalan). */
  private async purgeSuppression(): Promise<void> {
    try {
      const res = await this.db.query(`DELETE FROM platform.erasure_suppression WHERE expires_at <= now()`);
      if ((res.rowCount ?? 0) > 0) this.logger.log(`Daftar tolak erasure: ${res.rowCount} tag kedaluwarsa dihapus`);
    } catch (err) {
      this.logger.warn(`Retensi daftar tolak erasure gagal: ${err instanceof Error ? err.message : String(err)}`);
    }
  }

  private async removePhotos(objects: readonly string[]): Promise<void> {
    const bucket = this.config.marketScoutBucket;
    let failed = 0;
    for (const o of objects) {
      try {
        await this.storage.remove(bucket, o);
      } catch {
        failed += 1;
      }
    }
    if (failed > 0) this.logger.warn(`Erasure: ${failed} foto gagal dihapus (akan dihapus lifecycle MinIO)`);
  }
}
