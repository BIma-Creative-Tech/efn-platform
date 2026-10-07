import { Inject, Injectable, Logger, OnModuleDestroy, OnModuleInit } from '@nestjs/common';
import { AppConfig } from '../config/app-config';
import { DB, Db } from '../common/db/db';
import { MetricsService } from '../common/metrics/metrics.service';

/** Advisory lock job (bigint, berbeda dari RETENTION_LOCK_KEY 'EFNR'). */
export const RESERVATION_SWEEP_LOCK_KEY = 0x45464e4c; // 'EFNL'
const BATCH_SIZE = 1000;
const MAX_BATCHES = 50;

export interface SweepResult {
  expired: number;
  purged: number;
  skipped: boolean;
}

/**
 * Pelepas reservasi redeem kedaluwarsa (docs/04 §9.3: "Reservasi kedaluwarsa dilepas otomatis") + retensi
 * `platform.loyalty_reservation`.
 *
 * - `reserved` dengan `hold_until <= now` (K-3: default 24 jam, bukan TTL UI 15 menit) → `expired` (bertahap per batch).
 *   Saldo tersedia sudah mengabaikan reservasi lewat hold_until; job ini merapikan status, mengosongkan indeks unik per order, dan mencatat metrik
 *   `efn_loyalty_reservation_expired_total`.
 * - Baris berstatus akhir (`committed|released|expired`) dengan `updated_at` > `LOYALTY_RESERVATION_RETENTION_DAYS`
 *   dihapus (data transien; catatan finansial ada di ledger). `loyalty_pos_earn` dihapus setelah `LOYALTY_POS_EARN_RETENTION_DAYS`.
 * - Multi-replika: satu replika per siklus (`pg_try_advisory_xact_lock`), pola sama dengan `RetentionService`.
 */
@Injectable()
export class LoyaltyReservationSweeper implements OnModuleInit, OnModuleDestroy {
  private readonly logger = new Logger(LoyaltyReservationSweeper.name);
  private timer: NodeJS.Timeout | null = null;
  private running = false;
  now: () => number = () => Date.now();

  constructor(
    @Inject(DB) private readonly db: Db,
    private readonly config: AppConfig,
    private readonly metrics: MetricsService,
  ) {}

  onModuleInit(): void {
    const ms = this.config.loyaltyReservationSweepIntervalMs;
    if (ms > 0) {
      this.timer = setInterval(() => void this.sweepOnce(), ms);
      this.timer.unref();
    }
  }

  onModuleDestroy(): void {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
  }

  /** Tidak pernah melempar (dipanggil dari timer). */
  async sweepOnce(): Promise<SweepResult> {
    const skipped: SweepResult = { expired: 0, purged: 0, skipped: true };
    if (this.running) return skipped;
    this.running = true;
    try {
      const now = new Date(this.now());
      const res = await this.db.tx(async (q) => {
        const lock = await q.query<{ ok: boolean }>('SELECT pg_try_advisory_xact_lock($1) AS ok', [RESERVATION_SWEEP_LOCK_KEY]);
        if (!lock.rows[0]?.ok) return skipped;
        let expired = 0;
        for (let i = 0; i < MAX_BATCHES; i += 1) {
          const r = await q.query(
            `UPDATE platform.loyalty_reservation SET status = 'expired', updated_at = $1
              WHERE id IN (SELECT id FROM platform.loyalty_reservation
                            WHERE status = 'reserved' AND hold_until <= $1
                            LIMIT $2
                            FOR UPDATE SKIP LOCKED)`,
            [now.toISOString(), BATCH_SIZE],
          );
          const n = r.rowCount ?? 0;
          expired += n;
          if (n < BATCH_SIZE) break;
        }
        const cutoff = new Date(now.getTime() - this.config.loyaltyReservationRetentionDays * 86_400_000);
        let purged = 0;
        for (let i = 0; i < MAX_BATCHES; i += 1) {
          const r = await q.query(
            `DELETE FROM platform.loyalty_reservation
              WHERE id IN (SELECT id FROM platform.loyalty_reservation
                            WHERE status <> 'reserved' AND updated_at < $1
                            LIMIT $2
                            FOR UPDATE SKIP LOCKED)`,
            [cutoff.toISOString(), BATCH_SIZE],
          );
          const n = r.rowCount ?? 0;
          purged += n;
          if (n < BATCH_SIZE) break;
        }
        // K-6: catatan earn per order (loyalty_pos_earn) setelah masa retur
        const earnCutoff = new Date(now.getTime() - this.config.loyaltyPosEarnRetentionDays * 86_400_000);
        for (let i = 0; i < MAX_BATCHES; i += 1) {
          const r = await q.query(
            `DELETE FROM platform.loyalty_pos_earn
              WHERE order_ref IN (SELECT order_ref FROM platform.loyalty_pos_earn WHERE created_at < $1 LIMIT $2)`,
            [earnCutoff.toISOString(), BATCH_SIZE],
          );
          const n = r.rowCount ?? 0;
          purged += n;
          if (n < BATCH_SIZE) break;
        }
        return { expired, purged, skipped: false };
      });
      this.metrics.recordLoyaltyReservationExpired(res.expired);
      if (res.expired + res.purged > 0) {
        this.logger.log(`Reservasi poin: ${res.expired} kedaluwarsa dilepas, ${res.purged} baris lama dihapus`);
      }
      return res;
    } catch (err) {
      this.logger.error(`Job reservasi poin gagal: ${err instanceof Error ? err.message : String(err)}`);
      return skipped;
    } finally {
      this.running = false;
    }
  }
}
