import { Inject, Injectable, Logger, OnModuleDestroy, OnModuleInit, Optional } from '@nestjs/common';
import { KV_STORE, KvStore } from '../common/redis/kv-store';
import { purgeMemberOnlineRedis } from '../cart/quote.service';
import { randomUUID } from 'node:crypto';
import { AppConfig } from '../config/app-config';
import { DB, Db, Queryable } from '../common/db/db';
import { erasureSuppressionTag } from '../common/pii/erasure-suppression';
import { randomTombstone } from '../common/pii/pii';
import { OBJECT_STORAGE, ObjectStorage } from '../market-scout/storage/object-storage';
import { MetricsService } from '../common/metrics/metrics.service';

export const ERASURE_STORES = ['platform', 'odoo', 'clickhouse'] as const;
export type ErasureStore = (typeof ERASURE_STORES)[number];
export const ERASURE_LOCK_KEY = 0x45464e45; // 'EFNE'

/** SEC-100: erasure ditunda karena member masih punya order online terbuka (rollback transaksi). */
export class ErasureDeferred extends Error {}
const DUE_BATCH = 50;
/** W-18 (Q-P18): eskalasi CS (hari sejak `requested_at`) selama erasure tertahan pesanan online terbuka. */
export const DEFER_ESCALATION_DAYS = [3, 10] as const;
const ESCALATION_BATCH = 200;
/** Bagian order online yang menahan erasure (sama dengan L-4). */
const OPEN_PART_STATUSES = `('paying', 'confirmed', 'ready', 'review')`;
const OPEN_ORDER_PREDICATE = `(o.status IN ('created', 'awaiting_payment')
                 OR EXISTS (SELECT 1 FROM platform.online_order_part p
                             WHERE p.order_id = o.id AND p.status IN ${OPEN_PART_STATUSES}))`;
/** Maksimum id bagian yang dicetak di satu baris anomali (sisanya lewat CLI `online-erasure-pending`). */
const MAX_LOGGED_PARTS = 20;

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
 *      - `loyalty_reservation` (§9.5): baris non-`committed` DIHAPUS; `committed` (nilai keuangan redeem, rekonsiliasi
 *        K-18-6, SEC-124/L-25l) DIPERTAHANKAN dengan `member_hash` → tombstone (tautan member dilepas);
 *      - `loyalty_pos_earn` (§9.6): baris subjek DIHAPUS (transien, pseudonim);
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
 * dari member_hash) disimpan di `platform.erasure_suppression` selama `ERASURE_SUPPRESSION_DAYS` (default 365, W-19; 0 = tidak
 * disimpan) agar impor migrasi K-7 menolak subjek ini setelah `member_hash` request di-NULL-kan. Baris kedaluwarsa
 * dihapus di awal setiap siklus job.
 *
 * W-18 (Q-P18, keputusan pemilik proyek 10 Okt 2026) — pesanan online terbuka:
 * - saat jatuh tempo dan member masih punya order/bagian terbuka → erasure DITUNDA (rollback, dicoba tiap siklus) selama
 *   `requested_at` + `DSAR_OPEN_ORDER_MAX_DEFER_DAYS` (14) belum lewat;
 * - eskalasi CS satu kali per tingkat (kolom `dsar_request.defer_escalation`): hari 3 → `EFN_ONLINE_ANOMALY
 *   erasure_deferred_open_orders` severity warning, hari 10 → severity critical; metrik
 *   `efn_dsar_erasure_deferral_total{event}`. Dihitung untuk semua request `scheduled` (juga selama masa tenggang);
 * - setelah batas lewat → erasure TETAP dijalankan: `online_order.member_id` NULL (tautan dilepas), baris order/bagian
 *   tetap untuk diselesaikan CS lewat CLI (`online-erasure-pending`, `online-refund`, `online-resolve`); anomali
 *   `erasure_forced_open_orders` (critical) memuat id bagian (opak, tanpa PII).
 * Log/metrik tidak memuat nomor HP, member_id, atau member_hash.
 *
 * Sisa risiko (didokumentasikan, README): korelasi waktu (`erased_at`/`created_at` forfeit ≈ `scheduled_at` request)
 * dapat menautkan baris ber-tombstone ke `request_id`, tetapi setelah `completed` request tidak lagi memuat
 * `member_hash` sehingga rantai ke nomor HP terputus.
 */
@Injectable()
export class ErasureService implements OnModuleInit, OnModuleDestroy {
  private readonly logger = new Logger(ErasureService.name);
  private readonly anomalyLog = new Logger('OnlineAnomaly');
  private timer: NodeJS.Timeout | null = null;
  private running = false;

  constructor(
    private readonly config: AppConfig,
    @Inject(DB) private readonly db: Db,
    @Inject(OBJECT_STORAGE) private readonly storage: ObjectStorage,
    /** §10.8.3: cart/quote online di Redis milik subjek dihapus setelah commit (best effort; TTL sebagai cadangan). */
    @Optional() @Inject(KV_STORE) private readonly kv: KvStore | null = null,
    /** W-18: metrik penundaan/eskalasi (token eksplisit — pelajaran QA G17 soal tipe union di design:paramtypes). */
    @Optional() @Inject(MetricsService) private readonly metrics: MetricsService | null = null,
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
      await this.escalateDeferred();
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
    try {
      return await this.processDueTx(requestId);
    } catch (err) {
      if (!(err instanceof ErasureDeferred)) throw err;
      // W-18: tidak di-alert tiap siklus (60 s) — alert CS lewat eskalasi hari 3/10 (escalateDeferred)
      this.logger.debug(`Erasure ${requestId} ditunda: pesanan online terbuka (batas ${this.config.dsarOpenOrderMaxDeferDays} hari)`);
      return false;
    }
  }

  /**
   * W-18 (Q-P18): eskalasi CS untuk request hapus `scheduled` yang masih tertahan order online terbuka, sekali per tingkat
   * (UPDATE bersyarat `defer_escalation < level` → aman multi-replika). Gagal → dicatat, siklus erasure tetap jalan.
   */
  async escalateDeferred(): Promise<number> {
    try {
      const rows = await this.db.query<{ id: string; age_days: number | string; defer_escalation: number | string; open_n: number | string }>(
        `SELECT d.id::text AS id,
                floor(extract(epoch FROM (now() - d.requested_at)) / 86400)::int AS age_days,
                d.defer_escalation,
                (SELECT count(*) FROM platform.online_order o WHERE o.member_id = d.member_id AND ${OPEN_ORDER_PREDICATE}) AS open_n
           FROM platform.dsar_request d
          WHERE d.type = 'delete' AND d.status = 'scheduled'
            AND d.defer_escalation < $1
            AND d.requested_at <= now() - make_interval(days => $2::int)
          ORDER BY d.requested_at
          LIMIT $3`,
        [DEFER_ESCALATION_DAYS.length, DEFER_ESCALATION_DAYS[0], ESCALATION_BATCH],
      );
      let n = 0;
      for (const r of rows.rows) {
        if (Number(r.open_n) <= 0) continue;
        const age = Number(r.age_days);
        const level = DEFER_ESCALATION_DAYS.filter((d) => age >= d).length;
        if (level <= Number(r.defer_escalation)) continue;
        const upd = await this.db.query(
          `UPDATE platform.dsar_request SET defer_escalation = $2
            WHERE id = $1 AND status = 'scheduled' AND defer_escalation < $2`,
          [r.id, level],
        );
        if ((upd.rowCount ?? 0) === 0) continue;
        const day = DEFER_ESCALATION_DAYS[level - 1];
        this.reportDeferral(level >= DEFER_ESCALATION_DAYS.length ? 'critical' : 'warning', 'erasure_deferred_open_orders', {
          request_id: r.id,
          day,
          open_orders: Number(r.open_n),
          max_defer_days: this.config.dsarOpenOrderMaxDeferDays,
        });
        this.metrics?.recordDsarErasureDeferral(day === 3 ? 'escalation_d3' : 'escalation_d10');
        n += 1;
      }
      return n;
    } catch (err) {
      this.logger.warn(`Eskalasi penundaan erasure gagal: ${err instanceof Error ? err.message : String(err)}`);
      return 0;
    }
  }

  /** Baris berprefiks EFN_ONLINE_ANOMALY dengan logger OnlineAnomaly → alert Loki (selector promtail tahap 6). */
  private reportDeferral(severity: 'warning' | 'critical', kind: string, extra: Record<string, unknown>): void {
    this.anomalyLog.warn(`EFN_ONLINE_ANOMALY ${JSON.stringify({ kind, severity, ...extra })}`);
  }

  private async processDueTx(requestId: string): Promise<boolean> {
    const result = await this.db.tx(async (q) => {
      let forced: { openOrders: number; partIds: string[] } | null = null;
      const lock = await q.query<{ ok: boolean }>('SELECT pg_try_advisory_xact_lock($1, hashtext($2)) AS ok', [
        ERASURE_LOCK_KEY,
        requestId,
      ]);
      if (!lock.rows[0]?.ok) return null;
      const started = await q.query<{ member_id: string; member_hash: string; defer_expired: boolean | null }>(
        `UPDATE platform.dsar_request
            SET status = 'processing', stores_pending = ARRAY['platform', 'odoo', 'clickhouse']::text[]
          WHERE id = $1 AND type = 'delete' AND status = 'scheduled' AND scheduled_at <= now()
          RETURNING member_id::text AS member_id, member_hash,
                    (requested_at <= now() - make_interval(days => $2::int)) AS defer_expired`,
        [requestId, this.config.dsarOpenOrderMaxDeferDays],
      );
      const r = started.rows[0];
      if (!r) return null;
      // SEC-100 (L-16): kunci member yang sama dengan checkout lalu cek ulang order online terbuka; ada → tunda
      await q.query(`SELECT pg_advisory_xact_lock(hashtext('efn:online:member:' || $1::text))`, [r.member_id]);
      const open = await q.query<{ n: string | number }>(
        `SELECT count(*) AS n FROM platform.online_order o
          WHERE o.member_id = $1
            AND ${OPEN_ORDER_PREDICATE}`,
        [r.member_id],
      );
      const openOrders = Number(open.rows[0]?.n ?? 0);
      if (openOrders > 0) {
        // W-18 (Q-P18): tunda maksimal N hari sejak permintaan; setelah itu erasure tetap jalan
        if (r.defer_expired !== true) throw new ErasureDeferred('order online terbuka');
        const parts = await q.query<{ part_id: string }>(
          `SELECT p.id::text AS part_id FROM platform.online_order_part p JOIN platform.online_order o ON o.id = p.order_id
            WHERE o.member_id = $1 AND p.status IN ${OPEN_PART_STATUSES}
            ORDER BY p.id`,
          [r.member_id],
        );
        forced = { openOrders, partIds: parts.rows.map((p) => p.part_id) };
      }
      await q.query(`UPDATE platform.member SET deleted_at = now() WHERE id = $1 AND deleted_at IS NULL`, [r.member_id]);
      await this.suppress(q, r.member_hash);
      const objects = await q.query<{ image_object: string }>(
        `SELECT image_object FROM platform.price_hunter_report WHERE user_hash = $1`,
        [r.member_hash],
      );
      await this.erasePlatform(q, requestId, r.member_id, r.member_hash);
      await this.completeStore('platform', requestId, q);
      return { photos: objects.rows.map((o) => o.image_object), memberId: r.member_id, forced };
    });
    if (result === null) return false;
    if (result.forced) {
      // W-18: setelah commit — tautan member di order sudah dilepas; CS menyelesaikan bagian ini tanpa tautan akun
      this.reportDeferral('critical', 'erasure_forced_open_orders', {
        request_id: requestId,
        open_orders: result.forced.openOrders,
        max_defer_days: this.config.dsarOpenOrderMaxDeferDays,
        part_ids: result.forced.partIds.slice(0, MAX_LOGGED_PARTS),
        parts_total: result.forced.partIds.length,
      });
      this.metrics?.recordDsarErasureDeferral('forced');
    }
    await this.removePhotos(result.photos);
    await this.purgeOnlineRedis(result.memberId);
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
    // 2b. Reservasi redeem POS (§9.5) dan catatan earn per order (§9.6 K-6): data transien/pseudonim tanpa HP → dihapus (catatan finansial tetap di ledger)
    // SEC-124: nilai keuangan redeem committed tetap (rekonsiliasi K-18-6, 10 th), hanya tautan member yang dilepas
    await q.query(`UPDATE platform.loyalty_reservation SET member_hash = $2 WHERE member_hash = $1 AND status = 'committed'`, [hash, tomb]);
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
    // 6b. Fase 3 (§10.8.3): order online tetap (baris keuangan 10 th) tanpa tautan ke member
    await q.query(
      `UPDATE platform.online_order SET member_id = NULL, member_unlinked_at = now(), updated_at = now() WHERE member_id = $1`,
      [memberId],
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

  private async purgeOnlineRedis(memberId: string): Promise<void> {
    if (!this.kv) return;
    try {
      const n = await purgeMemberOnlineRedis(this.kv, this.config.redisKeyPepper, memberId);
      if (n > 0) this.logger.log(`Erasure: ${n} key Redis online (cart/quote) dihapus`);
    } catch (err) {
      this.logger.warn(`Erasure: hapus cart/quote Redis gagal (TTL membersihkan): ${err instanceof Error ? err.message : 'redis'}`);
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
