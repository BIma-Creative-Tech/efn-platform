import { Injectable, Logger, OnModuleDestroy } from '@nestjs/common';
import { ConfirmChannel, connect, credentials } from 'amqplib';
import { AppConfig } from '../../config/app-config';
import { WA_QUEUE } from './wa-unofficial.contract';

type AmqpConnection = Awaited<ReturnType<typeof connect>>;

export const WA_QUEUE_PUBLISHER = Symbol('WA_QUEUE_PUBLISHER');

export interface WaQueuePublisher {
  /** Publish persistent ke `notify.wa_unofficial` dengan properti AMQP `expiration` (ms). Resolve setelah broker confirm. */
  publish(payload: object, messageId: string, expirationMs: number): Promise<void>;
}

/**
 * Publisher khusus `notify.wa_unofficial`. Alasan tidak memakai `AmqpService.publish`: kontrak §28.7 mewajibkan
 * properti AMQP `expiration` (≤ 24 jam), sedangkan `AmqpService.publish(queue, payload, messageId)` tidak menerima
 * opsi (file bersama, di luar kepemilikan G50-B). Usulan ke efn-lead: tambahkan parameter opsi ke `AmqpService.publish`
 * lalu ganti kelas ini dengan adaptor tipis (antarmuka `WaQueuePublisher` sudah disiapkan).
 *
 * Koneksi: kredensial & TLS SAMA dengan `AmqpService` (`AppConfig.amqpConnection`, user `efn-bff`, amqps + CA
 * internal, tanpa fallback plaintext kecuali `EFN_ALLOW_PLAINTEXT` non-production yang sudah ditegakkan AppConfig).
 * Dibuka malas (publish pertama) — kanal mati = tidak ada koneksi tambahan. Passive `checkQueue` queue + DLQ saat
 * terhubung (§8.10). Pesan tidak pernah dititipkan ke outbox PostgreSQL karena memuat `to_e164` (PII) dalam plaintext.
 */
@Injectable()
export class AmqpWaPublisher implements WaQueuePublisher, OnModuleDestroy {
  private readonly logger = new Logger(AmqpWaPublisher.name);
  private conn: AmqpConnection | null = null;
  private channel: ConfirmChannel | null = null;
  private connecting: Promise<ConfirmChannel> | null = null;
  private username: string | null = null;
  private closing = false;
  /** Dapat diganti di test. */
  connectFn: typeof connect = connect;

  constructor(private readonly config: AppConfig) {}

  async onModuleDestroy(): Promise<void> {
    this.closing = true;
    const c = this.conn;
    this.conn = null;
    this.channel = null;
    if (c) await c.close().catch(() => undefined);
  }

  async publish(payload: object, messageId: string, expirationMs: number): Promise<void> {
    if (!Number.isInteger(expirationMs) || expirationMs < 1 || expirationMs > 24 * 3600 * 1000) {
      throw new Error('expiration WA harus 1 ms .. 24 jam');
    }
    const ch = await this.ensureChannel();
    const userId = this.username;
    if (!userId) throw new Error('RabbitMQ belum terhubung');
    const body = Buffer.from(JSON.stringify(payload), 'utf8');
    await new Promise<void>((resolve, reject) => {
      ch.sendToQueue(
        WA_QUEUE,
        body,
        {
          persistent: true,
          contentType: 'application/json',
          messageId,
          userId,
          timestamp: Math.floor(Date.now() / 1000),
          expiration: String(expirationMs),
        },
        (err: unknown) => (err ? reject(err instanceof Error ? err : new Error('Broker menolak pesan (nack)')) : resolve()),
      );
    });
  }

  private ensureChannel(): Promise<ConfirmChannel> {
    if (this.closing) return Promise.reject(new Error('publisher WA sedang ditutup'));
    if (this.channel) return Promise.resolve(this.channel);
    if (!this.connecting) {
      this.connecting = this.open().finally(() => {
        this.connecting = null;
      });
    }
    return this.connecting;
  }

  private async open(): Promise<ConfirmChannel> {
    const c = this.config.amqpConnection;
    const conn = await this.connectFn(c.url, { credentials: credentials.plain(c.username, c.password), ...(c.tls ? { ...c.tls } : {}) });
    try {
      conn.on('error', (err: Error) => this.logger.error(`RabbitMQ (publisher WA) error: ${err.message}`));
      conn.on('close', () => {
        if (this.conn === conn) {
          this.conn = null;
          this.channel = null;
        }
      });
      const probe = await conn.createChannel();
      probe.on('error', () => undefined);
      for (const q of [`${WA_QUEUE}.dlq`, WA_QUEUE]) {
        try {
          await probe.checkQueue(q);
        } catch {
          throw new Error(`Queue RabbitMQ '${q}' belum dideklarasikan broker (definitions) — docs/04 §8.10, §28.7`);
        }
      }
      await probe.close().catch(() => undefined);
      const ch = await conn.createConfirmChannel();
      ch.on('error', (err: Error) => this.logger.error(`Publish channel WA error: ${err.message}`));
      ch.on('close', () => {
        if (this.channel === ch) this.channel = null;
      });
      this.conn = conn;
      this.channel = ch;
      this.username = c.username;
      return ch;
    } catch (err) {
      await conn.close().catch(() => undefined);
      throw err;
    }
  }
}
