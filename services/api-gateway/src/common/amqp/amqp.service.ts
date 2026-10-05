import { Injectable, Logger, OnModuleDestroy, OnModuleInit } from '@nestjs/common';
import { Channel, ConfirmChannel, ConsumeMessage, connect, credentials } from 'amqplib';
import { AppConfig } from '../../config/app-config';
import { ALL_QUEUES, QueueMissingError, checkQueueWithDlq } from './queues';

type AmqpConnection = Awaited<ReturnType<typeof connect>>;

/** Lempar dari handler bila pesan tidak akan pernah berhasil (payload invalid) → langsung ke DLQ. */
export class PermanentMessageError extends Error {}

export interface MessageMeta {
  messageId: string | undefined;
  /**
   * Properti AMQP `user_id`. RabbitMQ menolak publish bila nilainya ≠ user koneksi publisher, sehingga consumer
   * dapat mengautentikasi publisher (SEC-08, docs/04 §4). `undefined` bila publisher tidak menyetelnya.
   */
  userId: string | undefined;
  redelivered: boolean;
}

export type MessageHandler = (payload: unknown, meta: MessageMeta) => Promise<void>;

function errMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

/**
 * Koneksi RabbitMQ tunggal untuk BFF.
 * - Transport `amqps://` dengan verifikasi CA internal (docs/04 §7.1); user khusus `efn-bff` (§7.3).
 * - Topologi dideklarasikan broker; BFF hanya `checkQueue` (passive, §8.10) untuk semua queue + DLQ.
 * - Publish lewat **confirm channel**: `publish()` baru resolve setelah broker ack (persistent, message_id,
 *   `user_id` = username koneksi — divalidasi broker, SEC-08).
 * - Consume lewat channel terpisah dengan prefetch; ack manual.
 * - Kegagalan handler: percobaan pertama di-requeue sekali, redelivery kedua → nack tanpa requeue → DLQ.
 *   `PermanentMessageError` → langsung DLQ.
 * - Reconnect otomatis dengan backoff eksponensial (maks 30 s); consumer terdaftar dipasang ulang.
 *   Bila broker mati saat start, BFF tetap jalan (OTP/POS tidak bergantung broker); outbox menahan pesan.
 */
@Injectable()
export class AmqpService implements OnModuleInit, OnModuleDestroy {
  private readonly logger = new Logger(AmqpService.name);
  private connection: AmqpConnection | null = null;
  private publishChannel: ConfirmChannel | null = null;
  private consumeChannel: Channel | null = null;
  private readonly handlers = new Map<string, MessageHandler>();
  private reconnectTimer: NodeJS.Timeout | null = null;
  private closing = false;
  private attempt = 0;
  /** Username koneksi aktif (= properti `user_id` saat publish). */
  private connectedUser: string | null = null;

  constructor(private readonly config: AppConfig) {}

  /**
   * Broker tidak terjangkau saat start → BFF tetap jalan dan reconnect (OTP/POS tidak bergantung broker).
   * Broker terjangkau tetapi queue belum dideklarasikan (definitions) → GAGAL START dengan pesan jelas (§8.10).
   */
  async onModuleInit(): Promise<void> {
    await this.connectSafely(true);
  }

  async onModuleDestroy(): Promise<void> {
    this.closing = true;
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    const conn = this.connection;
    this.connection = null;
    this.publishChannel = null;
    this.consumeChannel = null;
    if (conn) await conn.close().catch(() => undefined);
  }

  isConnected(): boolean {
    return this.publishChannel !== null;
  }

  /** Publish JSON persistent ke queue (default exchange). Resolve setelah publisher confirm. */
  async publish(queue: string, payload: unknown, messageId: string): Promise<void> {
    const ch = this.publishChannel;
    const userId = this.connectedUser;
    if (!ch || !userId) throw new Error('RabbitMQ belum terhubung');
    const body = Buffer.from(JSON.stringify(payload), 'utf8');
    await new Promise<void>((resolve, reject) => {
      ch.sendToQueue(
        queue,
        body,
        {
          persistent: true,
          contentType: 'application/json',
          messageId,
          userId,
          timestamp: Math.floor(Date.now() / 1000),
        },
        (err: unknown) => (err ? reject(err instanceof Error ? err : new Error('Broker menolak pesan (nack)')) : resolve()),
      );
    });
  }

  /** Daftarkan consumer. Aman dipanggil sebelum koneksi siap — dipasang saat (re)connect. */
  async consume(queue: string, handler: MessageHandler): Promise<void> {
    this.handlers.set(queue, handler);
    const ch = this.consumeChannel;
    if (ch) await this.startConsumer(ch, queue, handler);
  }

  private async connectSafely(initial = false): Promise<void> {
    if (this.closing) return;
    try {
      await this.establish();
      this.attempt = 0;
      this.logger.log('Terhubung ke RabbitMQ');
    } catch (err) {
      this.logger.error(`Gagal terhubung ke RabbitMQ: ${errMessage(err)}`);
      if (initial && err instanceof QueueMissingError) throw err;
      this.scheduleReconnect();
    }
  }

  private async establish(): Promise<void> {
    const c = this.config.amqpConnection;
    // amqps: socket options diteruskan ke tls.connect (CA internal, rejectUnauthorized, servername, TLS ≥ 1.2)
    const conn = await connect(c.url, {
      credentials: credentials.plain(c.username, c.password),
      ...(c.tls ? { ...c.tls } : {}),
    });
    try {
      conn.on('error', (err: Error) => this.logger.error(`RabbitMQ connection error: ${err.message}`));
      conn.on('close', () => {
        if (this.connection !== conn) return;
        this.connection = null;
        this.publishChannel = null;
        this.consumeChannel = null;
        if (!this.closing) {
          this.logger.warn('Koneksi RabbitMQ tertutup; mencoba reconnect');
          this.scheduleReconnect();
        }
      });

      const pub = await conn.createConfirmChannel();
      pub.on('error', (err: Error) => this.logger.error(`Publish channel error: ${err.message}`));
      // Channel yang mati membuat state setengah rusak; tutup koneksi agar reconnect penuh
      pub.on('close', () => {
        if (!this.closing && this.connection === conn) void conn.close().catch(() => undefined);
      });
      // §8.10: passive declare. checkQueue yang gagal (404) menutup channel → pakai channel sementara khusus cek
      const probe = await conn.createChannel();
      probe.on('error', () => undefined);
      for (const q of ALL_QUEUES) await checkQueueWithDlq(probe, q);
      await probe.close().catch(() => undefined);

      const sub = await conn.createChannel();
      sub.on('error', (err: Error) => this.logger.error(`Consume channel error: ${err.message}`));
      sub.on('close', () => {
        if (!this.closing && this.connection === conn) void conn.close().catch(() => undefined);
      });
      await sub.prefetch(this.config.amqpPrefetch);

      this.connection = conn;
      this.publishChannel = pub;
      this.consumeChannel = sub;
      this.connectedUser = c.username;
      for (const [queue, handler] of this.handlers) await this.startConsumer(sub, queue, handler);
    } catch (err) {
      await conn.close().catch(() => undefined);
      throw err;
    }
  }

  private scheduleReconnect(): void {
    if (this.closing || this.reconnectTimer) return;
    const delay = Math.min(30_000, 1000 * 2 ** this.attempt);
    this.attempt += 1;
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      void this.connectSafely();
    }, delay);
    this.reconnectTimer.unref();
  }

  private async startConsumer(ch: Channel, queue: string, handler: MessageHandler): Promise<void> {
    await ch.consume(
      queue,
      (msg: ConsumeMessage | null) => {
        if (msg === null) return; // consumer dibatalkan broker
        void this.dispatch(ch, queue, msg, handler);
      },
      { noAck: false },
    );
  }

  private async dispatch(ch: Channel, queue: string, msg: ConsumeMessage, handler: MessageHandler): Promise<void> {
    let payload: unknown;
    try {
      payload = JSON.parse(msg.content.toString('utf8'));
    } catch {
      this.logger.warn(`Pesan ${queue} bukan JSON valid → DLQ`);
      this.safeNack(ch, msg, false);
      return;
    }
    try {
      const messageId = typeof msg.properties.messageId === 'string' ? msg.properties.messageId : undefined;
      const userId = typeof msg.properties.userId === 'string' ? msg.properties.userId : undefined;
      await handler(payload, { messageId, userId, redelivered: msg.fields.redelivered });
      try {
        ch.ack(msg);
      } catch (err) {
        this.logger.warn(`Ack ${queue} gagal (channel tertutup?): ${errMessage(err)}`);
      }
    } catch (err) {
      const permanent = err instanceof PermanentMessageError;
      const requeue = !permanent && !msg.fields.redelivered;
      this.logger.error(
        `Handler ${queue} gagal (${permanent ? 'permanen' : 'transien'}; ${requeue ? 'requeue' : 'DLQ'}): ${errMessage(err)}`,
      );
      this.safeNack(ch, msg, requeue);
    }
  }

  private safeNack(ch: Channel, msg: ConsumeMessage, requeue: boolean): void {
    try {
      ch.nack(msg, false, requeue);
    } catch (err) {
      this.logger.warn(`Nack gagal (channel tertutup?): ${errMessage(err)}`);
    }
  }
}
