import { Inject, Injectable, Logger, OnApplicationBootstrap } from '@nestjs/common';
import { AppConfig } from '../config/app-config';
import { AmqpService, MessageMeta, PermanentMessageError } from '../common/amqp/amqp.service';
import { DB, Db } from '../common/db/db';
import { claimMessage } from '../common/db/processed-message';
import { BEHAVIOR_EVENT_QUEUE, BEHAVIOR_PUBLISHERS, behaviorIdempotencyKey, parseBehaviorMessage } from './behavior-message';
import { resolveChannelCompanies } from './channel-company';

export const BEHAVIOR_WRITER_CONSUMER = 'bff.behavior-writer';

export type WriterOutcome = { status: 'written'; inserted: number; deduped: number } | { status: 'duplicate' } | { status: 'stale' };

/**
 * Consumer `behavior.event` → PostgreSQL staging `behavior.event_raw` (partisi harian, retensi 7 hari; L-153, ADR-047
 * §2.2 opsi a). Tidak menulis ClickHouse (ADR-003: ETL 15 menit yang membawa ke `fact_visitor_event`).
 *
 * Idempotensi dua lapis dalam SATU transaksi: (1) `processed_message` per `behavior:<batch_id>`; (2) dedup
 * `(subject_key, event_id)` lintas batch di `behavior.event_dedup` — hanya event yang lolos dedup yang ditulis.
 * Publisher wajib `efn-bff` (properti AMQP `user_id`). Payload tidak valid → DLQ (`PermanentMessageError`).
 * Batch yang `received_at`-nya sudah melewati retensi staging diabaikan (ack) — partisinya sudah dibuang.
 */
@Injectable()
export class BehaviorWriterConsumer implements OnApplicationBootstrap {
  private readonly logger = new Logger(BehaviorWriterConsumer.name);
  now: () => number = () => Date.now();

  constructor(
    private readonly amqp: AmqpService,
    @Inject(DB) private readonly db: Db,
    private readonly config: AppConfig,
  ) {}

  async onApplicationBootstrap(): Promise<void> {
    // Queue G50 hanya dikonsumsi bila fitur aktif (broker lama tanpa definitions G50 tetap bisa dipakai)
    if (!this.config.amqpConsumersEnabled || !this.config.behaviorTrackingEnabled) return;
    await this.amqp.consume(BEHAVIOR_EVENT_QUEUE, async (payload, meta) => {
      await this.handle(payload, meta);
    });
  }

  async handle(payload: unknown, meta: Pick<MessageMeta, 'userId'>): Promise<WriterOutcome> {
    const msg = parseBehaviorMessage(payload);
    if (meta.userId === undefined || !BEHAVIOR_PUBLISHERS.includes(meta.userId)) {
      this.logger.warn(`behavior.event ditolak: publisher ${meta.userId ?? '(tanpa user_id)'} tidak berhak`);
      throw new PermanentMessageError('publisher tidak diizinkan');
    }
    const ageMs = this.now() - Date.parse(msg.received_at);
    if (ageMs > this.config.behaviorEventRetentionDays * 86_400_000) return { status: 'stale' };
    const outcome = await this.db.tx(async (q): Promise<WriterOutcome> => {
      if (!(await claimMessage(q, BEHAVIOR_WRITER_CONSUMER, behaviorIdempotencyKey(msg.batch_id)))) return { status: 'duplicate' };
      // QA G50 (L-154): subjek yang di-erase SETELAH batch ini diterima BFF (pesan masih di antrean saat erasure) tidak
      // boleh menulis ulang event ke staging -> ETL tidak akan membawanya ke ClickHouse setelah jendela recheck.
      // Event dari perangkat yang sama sesudah erasure (received_at > erased_at, consent baru) tetap diterima.
      const erased = await q.query(`SELECT 1 FROM behavior.erased_subject WHERE subject_key = $1 AND erased_at >= $2::timestamptz`, [
        msg.subject_key,
        msg.received_at,
      ]);
      if ((erased.rowCount ?? 0) > 0) return { status: 'stale' };
      const fresh = await q.query<{ event_id: string }>(
        `INSERT INTO behavior.event_dedup (subject_key, event_id, received_at)
         SELECT $1, e, $3::timestamptz FROM unnest($2::uuid[]) AS e
         ON CONFLICT (subject_key, event_id) DO NOTHING
         RETURNING event_id::text AS event_id`,
        [msg.subject_key, msg.events.map((e) => e.event_id), msg.received_at],
      );
      const keep = new Set(fresh.rows.map((r) => String(r.event_id).toLowerCase()));
      const rows = msg.events.filter((e) => keep.has(e.event_id));
      if (rows.length > 0) {
        // PT diturunkan ulang dari pemetaan kanal → PT saat tulis (sumber sama dengan ingest); kanal yang sementara tidak
        // terpetakan memakai PT yang sudah divalidasi saat ingest (pesan ber-`company_code` ∈ COMPANY_CODES).
        const companies = await resolveChannelCompanies(q, rows.map((e) => e.channel_code));
        // ingested_at = DEFAULT clock_timestamp() (cursor ETL), tidak dikirim BFF
        await q.query(
          `INSERT INTO behavior.event_raw
             (received_at, batch_id, subject_key, subject_kind, channel, event_id, type, client_ts, channel_code, outlet_code,
              product_id, duration_s, qty, query, result_count, stock_status, price_idr, company_code)
           SELECT $1::timestamptz, $2::uuid, $3, $4, $5, t.event_id, t.type, t.client_ts, t.channel_code, t.outlet_code,
                  t.product_id, t.duration_s, t.qty, t.query, t.result_count, t.stock_status, t.price_idr, t.company_code
             FROM unnest($6::uuid[], $7::text[], $8::timestamptz[], $9::text[], $10::text[], $11::int[], $12::smallint[],
                         $13::int[], $14::text[], $15::int[], $16::text[], $17::bigint[], $18::text[])
                  AS t(event_id, type, client_ts, channel_code, outlet_code, product_id, duration_s, qty, query, result_count,
                       stock_status, price_idr, company_code)`,
          [
            msg.received_at,
            msg.batch_id,
            msg.subject_key,
            msg.subject_kind,
            msg.channel,
            rows.map((e) => e.event_id),
            rows.map((e) => e.type),
            rows.map((e) => e.client_ts),
            rows.map((e) => e.channel_code),
            rows.map((e) => e.outlet_code),
            rows.map((e) => e.product_id),
            rows.map((e) => e.duration_s),
            rows.map((e) => e.qty),
            rows.map((e) => e.query),
            rows.map((e) => e.result_count),
            rows.map((e) => e.stock_status),
            rows.map((e) => e.price_idr),
            rows.map((e) => companies.get(e.channel_code) ?? e.company_code),
          ],
        );
      }
      return { status: 'written', inserted: rows.length, deduped: msg.events.length - rows.length };
    });
    if (outcome.status === 'written') this.logger.debug(`behavior.event ${msg.batch_id}: ${outcome.inserted} ditulis, ${outcome.deduped} duplikat`);
    return outcome;
  }
}
