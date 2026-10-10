import { Inject, Injectable, Logger, OnApplicationBootstrap } from '@nestjs/common';
import { AppConfig } from '../config/app-config';
import { AmqpService, MessageMeta, PermanentMessageError } from '../common/amqp/amqp.service';
import { QUEUES } from '../common/amqp/queues';
import { DB, Db } from '../common/db/db';
import { claimMessage } from '../common/db/processed-message';
import { PiiCipher } from '../common/pii/crypto';
import { isMemberHash, maskMsisdn } from '../common/pii/pii';
import { resolvePriceHunterSubject } from '../common/pii/price-hunter-subject';
import { resolveOnlineOrderSubject } from './online-order-subject';
import { isUuid } from '../common/validation';
import { WHATSAPP_SENDER, WhatsAppSender } from '../otp/whatsapp/whatsapp-sender';
import { NOTIFY_TEMPLATES, renderTemplateParams } from './templates';

export const NOTIFY_PUSH_CONSUMER = 'bff.notify-push';

/** Sumber yang didukung + publisher RabbitMQ (`user_id`) yang boleh mengirim notifikasi untuk sumber itu. */
export const NOTIFY_SOURCE_PUBLISHERS: Readonly<Record<string, readonly string[]>> = {
  price_hunter: ['efn-ocr', 'efn-bff'],
  // Fase 3 (docs/04 §10.9): hanya BFF sendiri (outbox saga online)
  online_order: ['efn-bff'],
};

/** Template yang boleh dipakai per sumber (template online tidak bisa dikirim dengan source price_hunter dan sebaliknya). */
export const SOURCE_TEMPLATES: Readonly<Record<string, readonly string[]>> = {
  price_hunter: ['price_hunter_valid', 'price_hunter_rejected'],
  online_order: ['online_order_ready', 'online_order_cancelled', 'online_order_refunded'],
};

/** Payload `notify.push` v2 (kontrak §4): tanpa nomor HP; penerima di-resolve dari baris sumber. */
export interface NotifyPushMessage {
  schema_version: 2;
  idempotency_key: string;
  member_hash: string;
  source: string;
  source_ref: string;
  template: string;
  params: Record<string, unknown>;
}

export function parseNotifyPushMessage(payload: unknown): NotifyPushMessage {
  if (typeof payload !== 'object' || payload === null || Array.isArray(payload)) {
    throw new PermanentMessageError('payload bukan object');
  }
  const o = payload as Record<string, unknown>;
  if (o.schema_version !== 2) throw new PermanentMessageError('schema_version tidak didukung');
  if ('user_id' in o || 'member_ref' in o) throw new PermanentMessageError('payload v2 tidak boleh memuat nomor HP');
  if (typeof o.idempotency_key !== 'string' || o.idempotency_key.length === 0 || o.idempotency_key.length > 200) {
    throw new PermanentMessageError('idempotency_key tidak valid');
  }
  if (!isMemberHash(o.member_hash)) throw new PermanentMessageError('member_hash tidak valid');
  if (typeof o.source !== 'string' || !Object.prototype.hasOwnProperty.call(NOTIFY_SOURCE_PUBLISHERS, o.source)) {
    throw new PermanentMessageError('source tidak didukung');
  }
  if (!isUuid(o.source_ref)) throw new PermanentMessageError('source_ref tidak valid');
  // hasOwnProperty: `constructor`/`toString` tidak lolos sebagai template (catatan Info security review)
  if (typeof o.template !== 'string' || !Object.prototype.hasOwnProperty.call(NOTIFY_TEMPLATES, o.template)) {
    throw new PermanentMessageError('template tidak dikenal');
  }
  if (!SOURCE_TEMPLATES[o.source].includes(o.template)) throw new PermanentMessageError('template tidak sesuai sumber');
  const params =
    typeof o.params === 'object' && o.params !== null && !Array.isArray(o.params) ? (o.params as Record<string, unknown>) : {};
  return {
    schema_version: 2,
    idempotency_key: o.idempotency_key,
    member_hash: o.member_hash,
    source: o.source,
    source_ref: o.source_ref.toLowerCase(),
    template: o.template,
    params,
  };
}

export type NotifyOutcome = 'sent' | 'duplicate' | 'skipped';

/**
 * Consumer `notify.push` v2: kirim template WhatsApp (atau log di mode console).
 *
 * Penerima: payload hanya membawa `member_hash`. Nomor diselesaikan dari baris sumber
 * (`source=price_hunter` → `platform.price_hunter_report.user_enc`, `id = source_ref`) dengan dekripsi
 * AES-256-GCM lalu diverifikasi `member_hash(nomor) = member_hash pesan` (lihat `resolvePriceHunterSubject`).
 * Publisher harus termasuk `NOTIFY_SOURCE_PUBLISHERS[source]` (properti AMQP `user_id`).
 *
 * Idempotensi: klaim `processed_message` dan pengiriman WA berada dalam satu transaksi.
 * Bila kirim gagal → rollback (klaim batal) → pesan di-requeue sekali lalu DLQ.
 * Bila kirim sukses tetapi COMMIT gagal → pesan bisa terkirim dua kali (jendela sempit, diterima untuk notifikasi).
 */
@Injectable()
export class NotifyPushConsumer implements OnApplicationBootstrap {
  private readonly logger = new Logger(NotifyPushConsumer.name);

  constructor(
    private readonly amqp: AmqpService,
    @Inject(DB) private readonly db: Db,
    @Inject(WHATSAPP_SENDER) private readonly sender: WhatsAppSender,
    private readonly config: AppConfig,
    private readonly cipher: PiiCipher,
  ) {}

  async onApplicationBootstrap(): Promise<void> {
    if (!this.config.amqpConsumersEnabled) return;
    await this.amqp.consume(QUEUES.NOTIFY_PUSH, async (payload, meta) => {
      await this.handle(payload, meta);
    });
  }

  async handle(payload: unknown, meta: Pick<MessageMeta, 'userId'>): Promise<NotifyOutcome> {
    const msg = parseNotifyPushMessage(payload);
    const allowed = NOTIFY_SOURCE_PUBLISHERS[msg.source];
    if (meta.userId === undefined || !allowed.includes(meta.userId)) {
      this.logger.warn(`notify.push ditolak: publisher ${meta.userId ?? '(tanpa user_id)'} tidak berhak untuk ${msg.source}`);
      throw new PermanentMessageError('publisher tidak diizinkan');
    }
    const tpl = NOTIFY_TEMPLATES[msg.template];
    let to = '';
    const outcome = await this.db.tx(async (q): Promise<NotifyOutcome> => {
      const fresh = await claimMessage(q, NOTIFY_PUSH_CONSUMER, msg.idempotency_key);
      if (!fresh) return 'duplicate';
      if (msg.source === 'online_order') {
        const ref = await resolveOnlineOrderSubject(q, this.cipher, this.config.memberHashPepper, msg.source_ref, msg.member_hash);
        if (!ref) return 'skipped'; // tautan member sudah dilepas (retensi/erasure)
        to = ref;
      } else {
        const subject = await resolvePriceHunterSubject(q, this.cipher, this.config.memberHashPepper, msg.source_ref, msg.member_hash);
        to = subject.memberRef;
      }
      await this.sender.sendTemplate(to, tpl.waTemplate, renderTemplateParams(tpl, msg.params));
      return 'sent';
    });
    this.logger.log(`notify.push ${msg.template}${to ? ` → ${maskMsisdn(to)}` : ''}: ${outcome}`);
    return outcome;
  }
}
