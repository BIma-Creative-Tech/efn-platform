import { Inject, Injectable, Logger, OnApplicationBootstrap } from '@nestjs/common';
import { AppConfig } from '../config/app-config';
import { AmqpService, MessageMeta, PermanentMessageError } from '../common/amqp/amqp.service';
import { QUEUES } from '../common/amqp/queues';
import { DB, Db } from '../common/db/db';
import { claimMessage } from '../common/db/processed-message';
import { isUuid } from '../common/validation';
import { ErasureService } from './erasure.service';

export const ERASURE_COMPLETED_CONSUMER = 'bff.privacy-erasure-completed';

/** Store yang boleh dilaporkan lewat event + publisher RabbitMQ (`user_id`) yang berhak (SEC-08 pola sama). */
export const ERASURE_STORE_PUBLISHERS: Readonly<Record<string, string>> = {
  clickhouse: 'efn-etl',
};

export interface ErasureCompletedMessage {
  schema_version: 2;
  idempotency_key: string;
  request_id: string;
  store: 'clickhouse';
}

export function parseErasureCompleted(payload: unknown): ErasureCompletedMessage {
  if (typeof payload !== 'object' || payload === null || Array.isArray(payload)) throw new PermanentMessageError('payload bukan object');
  const o = payload as Record<string, unknown>;
  if (o.schema_version !== 2) throw new PermanentMessageError('schema_version tidak didukung');
  const rid = o.request_id;
  if (!isUuid(rid)) throw new PermanentMessageError('request_id tidak valid');
  if (o.store !== 'clickhouse') throw new PermanentMessageError('store tidak didukung');
  const requestId = rid.toLowerCase();
  // Kontrak: idempotency_key = "erasure:<request_id>:<store>" — diikat agar tidak bisa dipakai untuk request lain
  const key = `erasure:${requestId}:clickhouse`;
  if (typeof o.idempotency_key !== 'string' || o.idempotency_key.toLowerCase() !== key) {
    throw new PermanentMessageError('idempotency_key tidak cocok');
  }
  return { schema_version: 2, idempotency_key: key, request_id: requestId, store: 'clickhouse' };
}

export type ErasureCompletedOutcome = 'applied' | 'duplicate';

/**
 * Consumer `privacy.erasure.completed` (docs/04 §8.4). Publisher ETL (`user_id = efn-etl`) mengirim ulang pesan yang
 * sama setiap siklus selama store masih pending → dedup via `processed_message` + `array_remove` idempotent, dalam
 * satu transaksi. Request tidak dikenal / tidak `processing|completed` → DLQ (permanen).
 */
@Injectable()
export class ErasureCompletedConsumer implements OnApplicationBootstrap {
  private readonly logger = new Logger(ErasureCompletedConsumer.name);

  constructor(
    private readonly amqp: AmqpService,
    @Inject(DB) private readonly db: Db,
    private readonly erasure: ErasureService,
    private readonly config: AppConfig,
  ) {}

  async onApplicationBootstrap(): Promise<void> {
    if (!this.config.amqpConsumersEnabled) return;
    await this.amqp.consume(QUEUES.PRIVACY_ERASURE_COMPLETED, async (payload, meta) => {
      await this.handle(payload, meta);
    });
  }

  async handle(payload: unknown, meta: Pick<MessageMeta, 'userId'>): Promise<ErasureCompletedOutcome> {
    const msg = parseErasureCompleted(payload);
    if (meta.userId !== ERASURE_STORE_PUBLISHERS[msg.store]) {
      this.logger.warn(`privacy.erasure.completed ditolak: publisher ${meta.userId ?? '(tanpa user_id)'} tidak berhak untuk ${msg.store}`);
      throw new PermanentMessageError('publisher tidak diizinkan');
    }
    const outcome = await this.db.tx(async (q): Promise<ErasureCompletedOutcome> => {
      if (!(await claimMessage(q, ERASURE_COMPLETED_CONSUMER, msg.idempotency_key))) return 'duplicate';
      const res = await this.erasure.completeStore(msg.store, msg.request_id, q);
      if (!res.ok) throw new PermanentMessageError(`request erasure ${res.reason}`);
      return 'applied';
    });
    this.logger.log(`privacy.erasure.completed ${msg.request_id} store=${msg.store}: ${outcome}`);
    return outcome;
  }
}
