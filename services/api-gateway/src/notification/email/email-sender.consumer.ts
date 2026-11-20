import { Inject, Injectable, Logger, OnApplicationBootstrap, OnModuleDestroy } from '@nestjs/common';
import { AmqpService, MessageMeta, PermanentMessageError } from '../../common/amqp/amqp.service';
import { DB, Db, Queryable } from '../../common/db/db';
import { claimMessage } from '../../common/db/processed-message';
import { PiiCipher } from '../../common/pii/crypto';
import { isMemberHash } from '../../common/pii/pii';
import { RateLimiter } from '../../common/rate-limit/rate-limiter';
import { AppConfig } from '../../config/app-config';
import {
  NOTIFY_EMAIL_CONSUMER,
  decryptMemberEmail,
  NOTIFY_EMAIL_PUBLISHERS,
  NOTIFY_EMAIL_QUEUE,
  SOURCE_REF_RE,
  isPlausibleEmail,
  maskEmail,
} from './email.constants';
import { EmailConfig } from './email.config';
import { EMAIL_SOURCES, EmailSource, EmailTemplateError, isMarketingSource, normalizeEmailParams, renderEmail } from './email-templates';
import { isMarketingGranted } from './marketing-consent';
import { NotifyEmailMessage } from './email.notifier';
import { SMTP_TRANSPORT, SmtpTransport, classifySmtpError } from './smtp-transport';
import { signUnsubscribeToken } from './unsubscribe-token';

export type EmailSendOutcome = 'sent' | 'duplicate' | 'skipped' | 'invalid_address';

/** Kunci Redis kuota kirim global (lintas replika). */
const RATE_KEY = 'email:send';
/** Batas tunggu kuota sebelum pesan dikembalikan (transien). */
const MAX_RATE_WAIT_MS = 5 * 60_000;

export function parseNotifyEmailMessage(payload: unknown): NotifyEmailMessage {
  if (typeof payload !== 'object' || payload === null || Array.isArray(payload)) throw new PermanentMessageError('payload bukan object');
  const o = payload as Record<string, unknown>;
  if (o.schema_version !== 1) throw new PermanentMessageError('schema_version tidak didukung');
  for (const forbidden of ['email', 'to', 'address', 'member_ref', 'user_id']) {
    if (forbidden in o) throw new PermanentMessageError('payload notify.email tidak boleh memuat alamat/identitas mentah');
  }
  if (typeof o.idempotency_key !== 'string' || o.idempotency_key.length === 0 || o.idempotency_key.length > 300) {
    throw new PermanentMessageError('idempotency_key tidak valid');
  }
  if (!isMemberHash(o.member_hash)) throw new PermanentMessageError('member_hash tidak valid');
  if (typeof o.source !== 'string' || !(EMAIL_SOURCES as readonly string[]).includes(o.source)) throw new PermanentMessageError('source tidak didukung');
  if (typeof o.source_ref !== 'string' || !SOURCE_REF_RE.test(o.source_ref)) throw new PermanentMessageError('source_ref tidak valid');
  const source = o.source as EmailSource;
  if (o.idempotency_key !== `email:${source}:${o.source_ref}:${o.member_hash}`) throw new PermanentMessageError('idempotency_key tidak cocok');
  const rawParams = typeof o.params === 'object' && o.params !== null && !Array.isArray(o.params) ? (o.params as Record<string, unknown>) : {};
  let params: Record<string, string>;
  try {
    params = normalizeEmailParams(String(o.template), source, rawParams);
  } catch (err) {
    throw new PermanentMessageError(err instanceof EmailTemplateError ? err.message : 'template/parameter tidak valid');
  }
  return {
    schema_version: 1,
    idempotency_key: o.idempotency_key,
    member_hash: o.member_hash,
    source,
    source_ref: o.source_ref,
    template: String(o.template),
    params,
  };
}

/**
 * Consumer `notify.email` (docs/04 §28.7, ADR-048). Hanya didaftarkan bila `EMAIL_ENABLED=true` dan
 * `AMQP_CONSUMERS_ENABLED` (queue harus sudah dideklarasikan broker; lihat README).
 *
 * - Publisher wajib `efn-bff` (properti AMQP `user_id`, SEC-08).
 * - Kuota `EMAIL_RATE_PER_MIN` global via Redis; menunggu di proses (bukan requeue — queue lama hanya requeue sekali).
 * - Idempotensi: klaim `processed_message` + kirim SMTP dalam satu transaksi (pola notify.push). Kirim gagal transien →
 *   rollback → requeue sekali lalu DLQ. Kirim sukses tetapi COMMIT gagal → bisa terkirim dua kali (diterima).
 * - Gerbang diperiksa ULANG saat kirim: member aktif, consent `marketing` (member bisa berhenti berlangganan di antara
 *   antre dan kirim), email terverifikasi & `active`.
 * - 5xx pada RCPT TO → `member_email.status = invalid` (berhenti kirim, ADR-048 §4), pesan di-ack.
 * - Alamat didekripsi hanya di memori saat kirim; log memakai `maskEmail`.
 */
@Injectable()
export class EmailSenderConsumer implements OnApplicationBootstrap, OnModuleDestroy {
  private readonly logger = new Logger(EmailSenderConsumer.name);
  /** Dapat diganti di test. */
  sleep: (ms: number) => Promise<void> = (ms) => new Promise((r) => setTimeout(r, ms).unref());
  now: () => number = () => Date.now();

  constructor(
    private readonly amqp: AmqpService,
    @Inject(DB) private readonly db: Db,
    private readonly appConfig: AppConfig,
    private readonly config: EmailConfig,
    private readonly cipher: PiiCipher,
    private readonly limiter: RateLimiter,
    @Inject(SMTP_TRANSPORT) private readonly transport: SmtpTransport,
  ) {}

  async onApplicationBootstrap(): Promise<void> {
    if (!this.config.enabled || !this.appConfig.amqpConsumersEnabled) return;
    await this.amqp.consume(NOTIFY_EMAIL_QUEUE, async (payload, meta) => {
      await this.handle(payload, meta);
    });
  }

  onModuleDestroy(): void {
    this.transport.close();
  }

  async handle(payload: unknown, meta: Pick<MessageMeta, 'userId'>): Promise<EmailSendOutcome> {
    const msg = parseNotifyEmailMessage(payload);
    if (meta.userId === undefined || !NOTIFY_EMAIL_PUBLISHERS.includes(meta.userId)) {
      this.logger.warn(`notify.email ditolak: publisher ${meta.userId ?? '(tanpa user_id)'} tidak berhak`);
      throw new PermanentMessageError('publisher tidak diizinkan');
    }
    if (!this.config.enabled) return 'skipped'; // flag dimatikan setelah pesan diantre → buang (ack)
    await this.waitForSlot();
    let masked = '';
    const outcome = await this.db.tx(async (q): Promise<EmailSendOutcome> => {
      if (!(await claimMessage(q, NOTIFY_EMAIL_CONSUMER, msg.idempotency_key))) return 'duplicate';
      const res = await q.query<{ id: string; email_enc: Buffer | null; verified_at: Date | string | null; status: string | null }>(
        `SELECT m.id::text AS id, e.email_enc, e.verified_at, e.status
           FROM platform.member m
           LEFT JOIN platform.member_email e ON e.member_id = m.id
          WHERE m.member_hash = $1 AND m.deleted_at IS NULL`,
        [msg.member_hash],
      );
      const row = res.rows[0];
      const marketing = isMarketingSource(msg.source);
      if (!row || !row.email_enc || !row.verified_at || row.status !== 'active') {
        await this.markOutbound(q, msg.idempotency_key, 'skipped');
        return 'skipped';
      }
      if (marketing && !(await isMarketingGranted(q, row.id))) {
        await this.markOutbound(q, msg.idempotency_key, 'skipped');
        return 'skipped';
      }
      // SEC-G50-11: AAD v2 terikat member_id (v1 lama masih dibaca selama migrasi)
      const address = decryptMemberEmail(this.cipher, row.email_enc, row.id).email;
      if (!isPlausibleEmail(address)) throw new PermanentMessageError('alamat email tersimpan tidak valid');
      masked = maskEmail(address);

      let link: string | null = null;
      const headers: Record<string, string> = {};
      if (marketing) {
        const key = this.config.unsubscribeKey;
        if (!key) throw new Error('EMAIL_UNSUBSCRIBE_KEY belum dikonfigurasi'); // promo tanpa cara berhenti = tidak dikirim
        const token = signUnsubscribeToken(key, row.id, this.now(), this.config.unsubscribeTtlSeconds);
        link = `${this.config.unsubscribeUrl}#t=${token}`;
        const mailto = this.config.unsubscribeMailto;
        headers['List-Unsubscribe'] = mailto ? `<${link}>, <mailto:${mailto}?subject=unsubscribe>` : `<${link}>`;
      }
      const rendered = renderEmail(msg.template, msg.params, this.config.brandName, link);
      try {
        await this.transport.send({ from: this.config.from, to: address, subject: rendered.subject, text: rendered.text, html: rendered.html, headers });
      } catch (err) {
        const f = classifySmtpError(err);
        if (!f.permanentRecipient) throw new Error(`SMTP gagal${f.responseCode ? ` (kode ${f.responseCode})` : ''}`);
        await q.query(
          `UPDATE platform.member_email SET status = 'invalid', status_reason = 'smtp_rcpt_5xx', updated_at = now() WHERE member_id = $1`,
          [row.id],
        );
        await this.markOutbound(q, msg.idempotency_key, 'invalid_address');
        return 'invalid_address';
      }
      await this.markOutbound(q, msg.idempotency_key, 'sent');
      return 'sent';
    });
    this.logger.log(`notify.email ${msg.template}${masked ? ` → ${masked}` : ''}: ${outcome}`);
    return outcome;
  }

  private async markOutbound(q: Queryable, key: string, status: 'sent' | 'skipped' | 'invalid_address'): Promise<void> {
    await q.query(`UPDATE platform.email_outbound SET status = $2, done_at = now() WHERE idempotency_key = $1`, [key, status]);
  }

  /** Kuota global per menit; ditolak → tunggu sampai jendela berikut (maks 5 menit, lalu transien). */
  private async waitForSlot(): Promise<void> {
    const started = this.now();
    for (;;) {
      const r = await this.limiter.consume(RATE_KEY, 1, this.config.ratePerMin, 60);
      if (r.allowed) return;
      if (this.now() - started > MAX_RATE_WAIT_MS) throw new Error('kuota email per menit habis terlalu lama');
      await this.sleep(r.retryAfterSeconds * 1000);
    }
  }
}
