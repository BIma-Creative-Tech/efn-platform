import { Inject, Injectable, Logger, OnModuleDestroy, OnModuleInit } from '@nestjs/common';
import { AppConfig } from '../../config/app-config';
import { AmqpService } from '../amqp/amqp.service';
import { DB, Db, Queryable } from '../db/db';

interface OutboxRow {
  id: string;
  queue: string;
  payload: unknown;
  message_id: string;
}

/**
 * Transactional outbox (kontrak §5 `platform.outbox`).
 *
 * Penulis memanggil `enqueue(q, ...)` di dalam transaksi yang sama dengan perubahan datanya,
 * sehingga "data tersimpan" dan "event akan terkirim" atomik. Relay mengirim baris yang belum
 * terbit (FOR UPDATE SKIP LOCKED → aman untuk >1 replika BFF), lalu menandai `published_at`.
 *
 * Semantik: at-least-once. Bila commit penandaan gagal setelah broker ack, pesan bisa terkirim dua kali;
 * consumer wajib idempotent (message_id = idempotency key).
 */
@Injectable()
export class OutboxService implements OnModuleInit, OnModuleDestroy {
  private readonly logger = new Logger(OutboxService.name);
  private timer: NodeJS.Timeout | null = null;
  private running = false;

  constructor(
    @Inject(DB) private readonly db: Db,
    private readonly amqp: AmqpService,
    private readonly config: AppConfig,
  ) {}

  onModuleInit(): void {
    const ms = this.config.outboxRelayIntervalMs;
    if (ms > 0) {
      this.timer = setInterval(() => void this.relayOnce(), ms);
      this.timer.unref();
    }
  }

  onModuleDestroy(): void {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
  }

  async enqueue(q: Queryable, queue: string, payload: Record<string, unknown>, messageId: string): Promise<void> {
    await q.query(
      `INSERT INTO platform.outbox (queue, payload, message_id) VALUES ($1, $2::jsonb, $3)`,
      [queue, JSON.stringify(payload), messageId],
    );
  }

  /**
   * Kirim maksimal `batchSize` pesan tertunda. Tidak pernah melempar (dipanggil fire-and-forget).
   * @returns jumlah pesan yang terbit.
   */
  async relayOnce(batchSize = 50): Promise<number> {
    if (this.running || !this.amqp.isConnected()) return 0;
    this.running = true;
    try {
      return await this.db.tx(async (q) => {
        const { rows } = await q.query<OutboxRow>(
          `SELECT id, queue, payload, message_id
             FROM platform.outbox
            WHERE published_at IS NULL
            ORDER BY id
            LIMIT $1
            FOR UPDATE SKIP LOCKED`,
          [batchSize],
        );
        let published = 0;
        for (const row of rows) {
          try {
            await this.amqp.publish(row.queue, row.payload, row.message_id);
          } catch (err) {
            this.logger.warn(`Publish outbox ${row.id} ke ${row.queue} gagal: ${err instanceof Error ? err.message : String(err)}`);
            break; // sisanya dicoba di siklus berikut, urutan dipertahankan
          }
          await q.query(`UPDATE platform.outbox SET published_at = now() WHERE id = $1`, [row.id]);
          published += 1;
        }
        return published;
      });
    } catch (err) {
      this.logger.error(`Relay outbox gagal: ${err instanceof Error ? err.message : String(err)}`);
      return 0;
    } finally {
      this.running = false;
    }
  }
}
