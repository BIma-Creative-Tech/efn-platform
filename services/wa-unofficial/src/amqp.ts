import { Channel, ConfirmChannel, ConsumeMessage, connect, credentials } from 'amqplib';
import type { RunConfig } from './config';
import { WA_QUEUE, WA_RESULT_QUEUE, WaResult } from './contract';
import { Log, errMsg } from './log';
import type { Delivery, Disposition, ResultSink } from './processor';

type Conn = Awaited<ReturnType<typeof connect>>;

/**
 * Koneksi RabbitMQ service (user `efn-wa-unofficial`): HANYA consume `notify.wa_unofficial` dan publish
 * `notify.wa_unofficial.result` (ADR-044 §3.2). amqps + CA internal (docs/04 §7.1). Topologi dideklarasikan broker;
 * service hanya passive `checkQueue` (queue + DLQ) → user tidak butuh hak `configure`.
 * Prefetch 1 (pengiriman serial, sesuai jitter & kuota). Publish hasil lewat confirm channel.
 * Handler: `ack` → ack; `dlq` → nack tanpa requeue (DLQ `notify.wa_unofficial.dlq`); error tak terduga → nack tanpa
 * requeue (tidak ada loop poison message). Reconnect otomatis dengan backoff (maks 30 dtk).
 */
export class AmqpLink implements ResultSink {
  private conn: Conn | null = null;
  private pub: ConfirmChannel | null = null;
  private sub: Channel | null = null;
  private closing = false;
  private attempt = 0;
  private timer: NodeJS.Timeout | null = null;
  connectFn: typeof connect = connect;

  constructor(
    private readonly cfg: RunConfig['amqp'],
    private readonly log: Log,
    private readonly handler: (d: Delivery) => Promise<Disposition>,
  ) {}

  isConnected(): boolean {
    return this.pub !== null;
  }

  async start(): Promise<void> {
    await this.connectSafely();
  }

  async stop(): Promise<void> {
    this.closing = true;
    if (this.timer) clearTimeout(this.timer);
    const c = this.conn;
    this.conn = null;
    this.pub = null;
    this.sub = null;
    if (c) await c.close().catch(() => undefined);
  }

  async publish(result: WaResult, messageId: string): Promise<void> {
    const ch = this.pub;
    if (!ch) throw new Error('RabbitMQ belum terhubung');
    await new Promise<void>((resolve, reject) => {
      ch.sendToQueue(
        WA_RESULT_QUEUE,
        Buffer.from(JSON.stringify(result), 'utf8'),
        { persistent: true, contentType: 'application/json', messageId, userId: this.cfg.username, timestamp: Math.floor(Date.now() / 1000) },
        (err: unknown) => (err ? reject(err instanceof Error ? err : new Error('broker menolak pesan')) : resolve()),
      );
    });
  }

  private async connectSafely(): Promise<void> {
    if (this.closing) return;
    try {
      await this.establish();
      this.attempt = 0;
      this.log.info('terhubung ke RabbitMQ');
    } catch (err) {
      this.log.error('gagal terhubung ke RabbitMQ', { err: errMsg(err) });
      this.schedule();
    }
  }

  private schedule(): void {
    if (this.closing || this.timer) return;
    const delay = Math.min(30_000, 1000 * 2 ** this.attempt);
    this.attempt += 1;
    this.timer = setTimeout(() => {
      this.timer = null;
      void this.connectSafely();
    }, delay);
    this.timer.unref();
  }

  private async establish(): Promise<void> {
    const conn = await this.connectFn(this.cfg.url, {
      credentials: credentials.plain(this.cfg.username, this.cfg.password),
      ...(this.cfg.tls ? { ...this.cfg.tls } : {}),
    });
    try {
      conn.on('error', (err: Error) => this.log.error('RabbitMQ error', { err: err.message }));
      conn.on('close', () => {
        if (this.conn !== conn) return;
        this.conn = null;
        this.pub = null;
        this.sub = null;
        if (!this.closing) {
          this.log.warn('koneksi RabbitMQ tertutup; reconnect');
          this.schedule();
        }
      });
      const probe = await conn.createChannel();
      probe.on('error', () => undefined);
      for (const q of [`${WA_QUEUE}.dlq`, WA_QUEUE, `${WA_RESULT_QUEUE}.dlq`, WA_RESULT_QUEUE]) {
        try {
          await probe.checkQueue(q);
        } catch {
          throw new Error(`queue '${q}' belum dideklarasikan broker atau tidak dapat diakses (docs/04 §8.10, §28.7)`);
        }
      }
      await probe.close().catch(() => undefined);
      const pub = await conn.createConfirmChannel();
      pub.on('error', (err: Error) => this.log.error('publish channel error', { err: err.message }));
      const sub = await conn.createChannel();
      sub.on('error', (err: Error) => this.log.error('consume channel error', { err: err.message }));
      await sub.prefetch(this.cfg.prefetch);
      this.conn = conn;
      this.pub = pub;
      this.sub = sub;
      await sub.consume(
        WA_QUEUE,
        (msg: ConsumeMessage | null) => {
          if (msg) void this.dispatch(sub, msg);
        },
        { noAck: false },
      );
    } catch (err) {
      await conn.close().catch(() => undefined);
      throw err;
    }
  }

  private async dispatch(ch: Channel, msg: ConsumeMessage): Promise<void> {
    let disposition: Disposition = 'dlq';
    try {
      let payload: unknown = null;
      try {
        payload = JSON.parse(msg.content.toString('utf8'));
      } catch {
        payload = null;
      }
      const userId = typeof msg.properties.userId === 'string' ? msg.properties.userId : undefined;
      disposition = await this.handler({ payload, userId });
    } catch (err) {
      this.log.error('pemrosesan pesan gagal tak terduga → DLQ', { err: errMsg(err) });
      disposition = 'dlq';
    }
    try {
      if (disposition === 'ack') ch.ack(msg);
      else ch.nack(msg, false, false);
    } catch (err) {
      this.log.warn('ack/nack gagal (channel tertutup?)', { err: errMsg(err) });
    }
  }
}
