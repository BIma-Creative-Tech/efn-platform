import { Inject, Injectable, Logger, OnModuleDestroy, OnModuleInit } from '@nestjs/common';
import { DB, Db } from '../../common/db/db';
import { WaUnofficialConfig } from './wa-unofficial.config';

/**
 * Retensi tabel kanal WA unofficial (usulan; final = DPO Q-SM18):
 * - `platform.wa_unofficial_outbound` (status kirim per member, tanpa nomor/isi): 90 hari;
 * - `platform.wa_unofficial_recipient` (to_hash → member untuk STOP): 180 hari sejak antre terakhir.
 * Keduanya ikut terhapus saat member dihapus (FK CASCADE). Interval `CHANNEL_RETENTION_INTERVAL_MS`; tidak melempar.
 */
@Injectable()
export class WaUnofficialRetentionJob implements OnModuleInit, OnModuleDestroy {
  private readonly logger = new Logger(WaUnofficialRetentionJob.name);
  private timer: NodeJS.Timeout | null = null;

  constructor(
    private readonly config: WaUnofficialConfig,
    @Inject(DB) private readonly db: Db,
  ) {}

  onModuleInit(): void {
    const ms = this.config.retentionIntervalMs;
    if (ms > 0) {
      this.timer = setInterval(() => void this.runOnce(), ms);
      this.timer.unref();
    }
  }

  onModuleDestroy(): void {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
  }

  async runOnce(): Promise<void> {
    try {
      await this.db.query(`DELETE FROM platform.wa_unofficial_outbound WHERE queued_at < now() - interval '90 days'`);
      await this.db.query(`DELETE FROM platform.wa_unofficial_recipient WHERE last_queued_at < now() - interval '180 days'`);
    } catch (err) {
      this.logger.warn(`purge retensi WA unofficial gagal: ${err instanceof Error ? err.message : String(err)}`);
    }
  }
}
