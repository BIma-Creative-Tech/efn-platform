import { Inject, Injectable, Logger, OnApplicationBootstrap } from '@nestjs/common';
import { AmqpService, MessageMeta, PermanentMessageError } from '../../common/amqp/amqp.service';
import { DB, Db } from '../../common/db/db';
import { claimMessage } from '../../common/db/processed-message';
import { KV_STORE, KvStore } from '../../common/redis/kv-store';
import { AppConfig } from '../../config/app-config';
import { withdrawMarketing } from '../email/marketing-consent';
import { WA_CHANNEL_DOWN_KEY } from './wa-unofficial.notifier';
import { WaUnofficialConfig } from './wa-unofficial.config';
import {
  NotifyWaResultMessage,
  WA_RESULT_CONSUMER,
  WA_RESULT_PUBLISHERS,
  WA_RESULT_QUEUE,
  WA_RESULT_STATUSES,
  WaResultStatus,
} from './wa-unofficial.contract';

const HASH_RE = /^[0-9a-f]{64}$/;
const REASON_RE = /^[a-z0-9_]{1,40}$/;
/** Lama penanda kanal tidak tersedia di Redis. */
const DOWN_TTL_SECONDS: Readonly<Record<'banned' | 'disconnected', number>> = { banned: 3600, disconnected: 600 };

export type WaResultOutcome = 'updated' | 'opted_out' | 'opt_out_unknown' | 'duplicate' | 'ignored';

export function parseWaResultMessage(payload: unknown): NotifyWaResultMessage {
  if (typeof payload !== 'object' || payload === null || Array.isArray(payload)) throw new PermanentMessageError('payload bukan object');
  const o = payload as Record<string, unknown>;
  if (o.schema_version !== 1) throw new PermanentMessageError('schema_version tidak didukung');
  if ('to_e164' in o) throw new PermanentMessageError('hasil WA tidak boleh memuat nomor');
  if (o.kind !== 'result' && o.kind !== 'opt_out') throw new PermanentMessageError('kind tidak dikenal');
  if (typeof o.to_hash !== 'string' || !HASH_RE.test(o.to_hash)) throw new PermanentMessageError('to_hash tidak valid');
  if (typeof o.at !== 'string' || Number.isNaN(Date.parse(o.at))) throw new PermanentMessageError('at tidak valid');
  let ref: string | null = null;
  if (o.ref_idempotency_key !== null && o.ref_idempotency_key !== undefined) {
    if (typeof o.ref_idempotency_key !== 'string' || !o.ref_idempotency_key.startsWith('wa:') || o.ref_idempotency_key.length > 300) {
      throw new PermanentMessageError('ref_idempotency_key tidak valid');
    }
    ref = o.ref_idempotency_key;
  }
  let status: WaResultStatus | undefined;
  if (o.status !== undefined && o.status !== null) {
    if (typeof o.status !== 'string' || !(WA_RESULT_STATUSES as readonly string[]).includes(o.status)) throw new PermanentMessageError('status tidak dikenal');
    status = o.status as WaResultStatus;
  }
  if (o.kind === 'result' && (!ref || !status)) throw new PermanentMessageError('result wajib ref_idempotency_key + status');
  const reason = typeof o.reason === 'string' && REASON_RE.test(o.reason) ? o.reason : undefined;
  return { schema_version: 1, ref_idempotency_key: ref, kind: o.kind, status, reason, to_hash: o.to_hash, at: o.at };
}

/**
 * Consumer `notify.wa_unofficial.result` (docs/04 §28.7). Publisher wajib `efn-wa-unofficial`.
 * Hanya didaftarkan bila kanal aktif dan `AMQP_CONSUMERS_ENABLED`.
 *
 * - `kind=result` → status `platform.wa_unofficial_outbound` (hanya dari `queued`/`disconnected`; hasil akhir tidak
 *   ditimpa). `message_id` service = `warslt:<key>:<status>` (pesan DLQ `disconnected` yang diputar ulang lalu `sent`).
 *   `banned`/`disconnected` → penanda Redis `waun:down` (notifier melewati kanal sementara; TIDAK dialihkan ke Cloud API).
 * - `kind=opt_out` ("STOP") → cari member lewat `wa_unofficial_recipient.to_hash` → cabut consent `marketing`
 *   (source `wa_stop`). to_hash tak dikenal → dicatat, di-ack.
 * Idempoten via `processed_message` (kunci = `message_id` AMQP, fallback `warslt:`/`waoptout:` dari payload).
 * Log tanpa nomor (hanya 8 hex awal to_hash).
 */
@Injectable()
export class WaUnofficialResultConsumer implements OnApplicationBootstrap {
  private readonly logger = new Logger(WaUnofficialResultConsumer.name);

  constructor(
    private readonly amqp: AmqpService,
    @Inject(DB) private readonly db: Db,
    private readonly appConfig: AppConfig,
    private readonly config: WaUnofficialConfig,
    @Inject(KV_STORE) private readonly kv: KvStore,
  ) {}

  async onApplicationBootstrap(): Promise<void> {
    if (!this.config.enabled || !this.appConfig.amqpConsumersEnabled) return;
    await this.amqp.consume(WA_RESULT_QUEUE, async (payload, meta) => {
      await this.handle(payload, meta);
    });
  }

  async handle(payload: unknown, meta: Pick<MessageMeta, 'userId' | 'messageId'>): Promise<WaResultOutcome> {
    const msg = parseWaResultMessage(payload);
    if (meta.userId === undefined || !WA_RESULT_PUBLISHERS.includes(meta.userId)) {
      this.logger.warn(`notify.wa_unofficial.result ditolak: publisher ${meta.userId ?? '(tanpa user_id)'} tidak berhak`);
      throw new PermanentMessageError('publisher tidak diizinkan');
    }
    const key =
      typeof meta.messageId === 'string' && meta.messageId.length > 0 && meta.messageId.length <= 300
        ? meta.messageId
        : msg.kind === 'result'
          ? `warslt:${msg.ref_idempotency_key}:${msg.status}`
          : `waoptout:${msg.to_hash}:${msg.at}`;
    const tag = msg.to_hash.slice(0, 8);

    const outcome = await this.db.tx(async (q): Promise<WaResultOutcome> => {
      if (!(await claimMessage(q, WA_RESULT_CONSUMER, key))) return 'duplicate';
      if (msg.kind === 'opt_out') {
        const r = await q.query<{ member_id: string }>(
          `SELECT member_id::text AS member_id FROM platform.wa_unofficial_recipient WHERE to_hash = $1`,
          [msg.to_hash],
        );
        const memberId = r.rows[0]?.member_id;
        if (!memberId) return 'opt_out_unknown';
        await withdrawMarketing(q, this.appConfig, memberId, 'wa_stop');
        return 'opted_out';
      }
      const upd = await q.query(
        `UPDATE platform.wa_unofficial_outbound
            SET status = $2, reason = $3, result_at = now()
          WHERE idempotency_key = $1 AND to_hash = $4 AND status IN ('queued', 'disconnected')`,
        [msg.ref_idempotency_key, msg.status, msg.reason ?? null, msg.to_hash],
      );
      return (upd.rowCount ?? 0) > 0 ? 'updated' : 'ignored';
    });

    if (msg.kind === 'result' && (msg.status === 'banned' || msg.status === 'disconnected')) {
      await this.kv.setEx(WA_CHANNEL_DOWN_KEY, msg.status, DOWN_TTL_SECONDS[msg.status]);
      this.logger.error(`WA unofficial melaporkan sesi ${msg.status}; kanal dilewati sementara (tanpa pengalihan ke Cloud API)`);
    } else if (msg.kind === 'result' && msg.status === 'sent') {
      await this.kv.del(WA_CHANNEL_DOWN_KEY);
    }
    this.logger.log(`notify.wa_unofficial.result ${msg.kind}${msg.status ? `/${msg.status}` : ''} [${tag}…]: ${outcome}`);
    return outcome;
  }
}
