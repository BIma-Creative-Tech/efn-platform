import { Inject, Injectable, Logger, OnApplicationBootstrap } from '@nestjs/common';
import { AppConfig } from '../config/app-config';
import { AmqpService, MessageMeta, PermanentMessageError } from '../common/amqp/amqp.service';
import { QUEUES } from '../common/amqp/queues';
import { DB, Db } from '../common/db/db';
import { claimMessage } from '../common/db/processed-message';
import { PiiCipher } from '../common/pii/crypto';
import { maskMsisdn } from '../common/pii/pii';
import { resolvePriceHunterSubject } from '../common/pii/price-hunter-subject';
import { EARN_SOURCE_PUBLISHERS, LoyaltyEarnMessage, parseLoyaltyEarnMessage } from './dto/loyalty-earn.message';
import { LoyaltyService } from './loyalty.service';
import { touchActivity } from './loyalty-activity';

export const LOYALTY_EARN_CONSUMER = 'bff.loyalty-earn';

export type EarnOutcome = 'applied' | 'duplicate';

/** Status report yang masih mungkin berubah (worker publish SEBELUM commit — lihat efn_ocr/repository.finalize). */
const PENDING_STATUSES = new Set(['queued', 'processing']);

/**
 * Consumer `loyalty.earn.requested` v2 (publisher: ai-ocr-worker; nanti MGM).
 *
 * Autentikasi & validasi semantik (SEC-08, docs/04 §4):
 * 1. Properti AMQP `user_id` (divalidasi RabbitMQ = user koneksi publisher) harus = publisher yang diizinkan untuk
 *    `source` (`price_hunter` → `efn-ocr`). Lainnya → DLQ.
 * 2. `points` ≤ `LOYALTY_MAX_POINTS_PER_EVENT` (default 100); `idempotency_key = price_hunter:<source_ref>`.
 * 3. `price_hunter_report (id = source_ref)` harus berstatus `valid` dan `user_hash = member_hash`.
 * 4. `member_ref_enc` ledger diisi dari `user_enc` report (dekripsi → verifikasi hash → enkripsi ulang dengan AAD ledger).
 *
 * Race: worker mem-publish sebelum COMMIT status `valid`, jadi report bisa sesaat masih `processing`. Consumer
 * menunggu singkat (`pendingRetryDelaysMs`); bila masih pending → error transien (requeue sekali → DLQ).
 *
 * Idempotensi dua lapis dalam SATU transaksi: `processed_message` + UNIQUE `loyalty_ledger.idempotency_key`.
 */
@Injectable()
export class LoyaltyEarnConsumer implements OnApplicationBootstrap {
  private readonly logger = new Logger(LoyaltyEarnConsumer.name);
  /** Jeda tunggu (ms) bila report masih queued/processing. Di test diganti []. */
  pendingRetryDelaysMs: number[] = [200, 500, 1000];
  sleep: (ms: number) => Promise<void> = (ms) => new Promise((r) => setTimeout(r, ms));

  constructor(
    private readonly amqp: AmqpService,
    @Inject(DB) private readonly db: Db,
    private readonly loyalty: LoyaltyService,
    private readonly config: AppConfig,
    private readonly cipher: PiiCipher,
  ) {}

  async onApplicationBootstrap(): Promise<void> {
    if (!this.config.amqpConsumersEnabled) return;
    await this.amqp.consume(QUEUES.LOYALTY_EARN_REQUESTED, async (payload, meta) => {
      await this.handle(payload, meta);
    });
  }

  async handle(payload: unknown, meta: Pick<MessageMeta, 'userId'>): Promise<EarnOutcome> {
    const msg = parseLoyaltyEarnMessage(payload, this.config.loyaltyMaxPointsPerEvent);
    const expectedPublisher = EARN_SOURCE_PUBLISHERS[msg.source];
    if (meta.userId !== expectedPublisher) {
      this.logger.warn(`loyalty.earn ditolak: publisher ${meta.userId ?? '(tanpa user_id)'} tidak berhak untuk ${msg.source}`);
      throw new PermanentMessageError('publisher tidak diizinkan untuk source ini');
    }

    await this.waitUntilFinal(msg);

    const outcome = await this.db.tx(async (q): Promise<EarnOutcome> => {
      const fresh = await claimMessage(q, LOYALTY_EARN_CONSUMER, msg.idempotency_key);
      if (!fresh) return 'duplicate';
      const subject = await resolvePriceHunterSubject(q, this.cipher, this.config.memberHashPepper, msg.source_ref, msg.member_hash);
      if (subject.status !== 'valid') throw new PermanentMessageError(`report berstatus ${subject.status}, bukan valid`);
      const { applied } = await this.loyalty.earn(
        {
          idempotencyKey: msg.idempotency_key,
          memberHash: msg.member_hash,
          memberRefEnc: this.loyalty.encryptMemberRef(subject.memberRef),
          points: msg.points,
          reason: msg.reason,
          source: msg.source,
          sourceRef: msg.source_ref,
          companyCode: msg.company_code,
          expiresAt: null,
        },
        q,
      );
      // G22 §14.D.2: laporan Price Hunter valid = aktivitas member
      if (applied) await touchActivity(q, msg.member_hash, 'price_hunter_valid', new Date());
      if (applied) this.logger.log(`loyalty earn applied: ${msg.points} poin untuk ${maskMsisdn(subject.memberRef)} (${msg.source})`);
      return applied ? 'applied' : 'duplicate';
    });
    if (outcome === 'duplicate') this.logger.log(`loyalty earn duplicate: ${msg.idempotency_key}`);
    return outcome;
  }

  /** Tunggu singkat selama report masih queued/processing (worker belum COMMIT). */
  private async waitUntilFinal(msg: LoyaltyEarnMessage): Promise<void> {
    for (let i = 0; ; i += 1) {
      const res = await this.db.query<{ status: string }>(
        `SELECT status FROM platform.price_hunter_report WHERE id = $1`,
        [msg.source_ref],
      );
      const status = res.rows[0]?.status;
      if (status === undefined || !PENDING_STATUSES.has(status)) return;
      if (i >= this.pendingRetryDelaysMs.length) {
        throw new Error(`report ${msg.source_ref} masih ${status}; dicoba ulang`);
      }
      await this.sleep(this.pendingRetryDelaysMs[i]);
    }
  }
}
