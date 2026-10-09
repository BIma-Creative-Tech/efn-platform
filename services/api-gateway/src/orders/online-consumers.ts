import { Injectable, Logger, OnApplicationBootstrap, OnModuleInit } from '@nestjs/common';
import { AppConfig } from '../config/app-config';
import { AmqpService, MessageMeta, PermanentMessageError } from '../common/amqp/amqp.service';
import { QUEUES } from '../common/amqp/queues';
import { isUuid } from '../common/validation';
import { PaymentsService } from '../payments/payments.service';
import { CompensateMessage, OrderPaidMessage, RefundRequestedMessage, UnpaidClosedMessage } from './online.model';
import { PgOnlineRepo } from './online.repository';
import { OnlineSagaService } from './online-saga.service';

/** Publisher yang diizinkan untuk queue saga (SEC-08: properti AMQP `user_id` = user koneksi). */
export const SAGA_PUBLISHERS: readonly string[] = ['efn-bff'];

function obj(p: unknown): Record<string, unknown> {
  if (typeof p !== 'object' || p === null || Array.isArray(p)) throw new PermanentMessageError('payload bukan objek');
  const o = p as Record<string, unknown>;
  if (o.schema_version !== 1) throw new PermanentMessageError('schema_version tidak didukung');
  if (typeof o.idempotency_key !== 'string' || o.idempotency_key.length === 0 || o.idempotency_key.length > 200) {
    throw new PermanentMessageError('idempotency_key tidak valid');
  }
  return o;
}
function uuid(o: Record<string, unknown>, k: string): string {
  const v = o[k];
  if (!isUuid(v)) throw new PermanentMessageError(`${k} tidak valid`);
  return v.toLowerCase();
}

export function parseOrderPaid(p: unknown): OrderPaidMessage {
  const o = obj(p);
  return { schema_version: 1, idempotency_key: String(o.idempotency_key), order_id: uuid(o, 'order_id'), payment_id: uuid(o, 'payment_id') };
}
export function parseUnpaidClosed(p: unknown): UnpaidClosedMessage {
  const o = obj(p);
  const reason = o.reason;
  if (reason !== 'payment_expired' && reason !== 'customer' && reason !== 'place_failed') throw new PermanentMessageError('reason tidak valid');
  return { schema_version: 1, idempotency_key: String(o.idempotency_key), order_id: uuid(o, 'order_id'), reason };
}
export function parseCompensate(p: unknown): CompensateMessage {
  const o = obj(p);
  const reason = o.reason;
  if (reason !== 'stock_short' && reason !== 'late_payment' && reason !== 'review') throw new PermanentMessageError('reason tidak valid');
  return { schema_version: 1, idempotency_key: String(o.idempotency_key), part_id: uuid(o, 'part_id'), reason };
}
export function parseRefundRequested(p: unknown): RefundRequestedMessage {
  const o = obj(p);
  return { schema_version: 1, idempotency_key: String(o.idempotency_key), refund_id: uuid(o, 'refund_id') };
}

/**
 * Consumer saga online (docs/04 §10.9): `order.paid`, `order.unpaid_closed`, `order.compensate`,
 * `order.refund.requested` — semua idempoten, publisher wajib `efn-bff`. Error transien → nack (AmqpService:
 * requeue sekali lalu DLQ — rekonsiliasi menerbitkan ulang pekerjaan yang tertinggal, lihat README). Juga memasang
 * hook transisi intent `purpose=online` ke PaymentsService (dalam transaksi webhook).
 */
@Injectable()
export class OnlineConsumers implements OnApplicationBootstrap, OnModuleInit {
  private readonly logger = new Logger('OnlineConsumers');

  constructor(
    private readonly amqp: AmqpService,
    private readonly config: AppConfig,
    private readonly saga: OnlineSagaService,
    private readonly payments: PaymentsService,
  ) {}

  onModuleInit(): void {
    this.payments.setOnlineHook((q, info) => this.saga.onIntentTransition(new PgOnlineRepo(q), info));
  }

  async onApplicationBootstrap(): Promise<void> {
    if (!this.config.amqpConsumersEnabled) return;
    await this.amqp.consume(QUEUES.ORDER_PAID, (p, m) => this.run('order.paid', m, () => this.saga.handleOrderPaid(parseOrderPaid(p))));
    await this.amqp.consume(QUEUES.ORDER_UNPAID_CLOSED, (p, m) =>
      this.run('order.unpaid_closed', m, () => this.saga.handleUnpaidClosed(parseUnpaidClosed(p))),
    );
    await this.amqp.consume(QUEUES.ORDER_COMPENSATE, (p, m) => this.run('order.compensate', m, () => this.saga.handleCompensate(parseCompensate(p))));
    await this.amqp.consume(QUEUES.ORDER_REFUND_REQUESTED, (p, m) =>
      this.run('order.refund.requested', m, () => this.saga.handleRefundRequested(parseRefundRequested(p))),
    );
  }

  async run(queue: string, meta: Pick<MessageMeta, 'userId'>, fn: () => Promise<string>): Promise<void> {
    if (meta.userId === undefined || !SAGA_PUBLISHERS.includes(meta.userId)) {
      this.logger.warn(`${queue} ditolak: publisher tidak diizinkan`);
      throw new PermanentMessageError('publisher tidak diizinkan');
    }
    const outcome = await fn();
    this.logger.log(`${queue}: ${outcome}`);
  }
}
