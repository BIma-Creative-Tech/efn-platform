import { Inject, Injectable, Logger, OnModuleDestroy, OnModuleInit } from '@nestjs/common';
import { DB, Db } from '../../common/db/db';
import { EmailConfig } from './email.config';

/**
 * Retensi tabel kanal email (inventaris PDP §2.8.12, usulan; final = DPO Q-SM19):
 * - `platform.email_outbound` (jejak kirim per member, tanpa alamat): 90 hari;
 * - `platform.email_unsubscribe_used` (jti token terpakai): dihapus setelah token kedaluwarsa (+1 hari).
 * `platform.member_email` ikut terhapus saat member dihapus (FK CASCADE, erasure §8.9).
 * Interval `CHANNEL_RETENTION_INTERVAL_MS` (default 6 jam, 0 = mati). Tidak pernah melempar.
 */
@Injectable()
export class EmailRetentionJob implements OnModuleInit, OnModuleDestroy {
  private readonly logger = new Logger(EmailRetentionJob.name);
  private timer: NodeJS.Timeout | null = null;

  constructor(
    private readonly config: EmailConfig,
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
      await this.db.query(`DELETE FROM platform.email_outbound WHERE queued_at < now() - interval '90 days'`);
      await this.db.query(`DELETE FROM platform.email_unsubscribe_used WHERE expires_at < now() - interval '1 day'`);
    } catch (err) {
      this.logger.warn(`purge retensi email gagal: ${err instanceof Error ? err.message : String(err)}`);
    }
  }
}
