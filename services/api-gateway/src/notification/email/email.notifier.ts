import { Inject, Injectable, Logger } from '@nestjs/common';
import { DB, Db } from '../../common/db/db';
import { OutboxService } from '../../common/outbox/outbox.service';
import { isUuid } from '../../common/validation';
import { NOTIFY_EMAIL_QUEUE, SOURCE_REF_RE } from './email.constants';
import { EmailConfig } from './email.config';
import { EMAIL_SOURCES, EmailSource, EmailTemplateError, isMarketingSource, normalizeEmailParams } from './email-templates';
import { isMarketingGranted } from './marketing-consent';

export interface EmailEnqueueInput {
  memberId: string;
  source: EmailSource;
  sourceRef: string;
  template: string;
  params: Record<string, string | number>;
}

export type EmailEnqueueResult = 'queued' | 'skipped_disabled' | 'skipped_no_consent' | 'skipped_no_email';

/** Payload `notify.email` (docs/04 §28.7) — TANPA alamat email (ADR-048 §3.6). */
export interface NotifyEmailMessage {
  schema_version: 1;
  idempotency_key: string;
  member_hash: string;
  source: EmailSource;
  source_ref: string;
  template: string;
  params: Record<string, string>;
}

export function emailIdempotencyKey(source: EmailSource, sourceRef: string, memberHash: string): string {
  return `email:${source}:${sourceRef}:${memberHash}`;
}

/**
 * Antarmuka untuk dispatcher BFF-A (campaign/alert): memutuskan apakah email boleh diantre, lalu menulis
 * `platform.email_outbound` + baris outbox `notify.email` dalam SATU transaksi (outbox §5 → publish confirm channel).
 *
 * Urutan gerbang: flag `EMAIL_ENABLED` → member aktif → consent `marketing` (kecuali `transactional`; Q-SM25 default)
 * → email terverifikasi & berstatus `active`. Template/parameter yang salah = kesalahan pemanggil → `EmailTemplateError`.
 *
 * Idempoten: `idempotency_key = email:<source>:<source_ref>:<member_hash>`; panggilan ulang → `queued` tanpa baris
 * outbox kedua. Alamat email tidak dibaca/didekripsi di sini.
 */
@Injectable()
export class EmailNotifier {
  private readonly logger = new Logger(EmailNotifier.name);

  constructor(
    private readonly config: EmailConfig,
    @Inject(DB) private readonly db: Db,
    private readonly outbox: OutboxService,
  ) {}

  async enqueue(input: EmailEnqueueInput): Promise<EmailEnqueueResult> {
    if (!this.config.enabled) return 'skipped_disabled';
    if (!EMAIL_SOURCES.includes(input.source)) throw new EmailTemplateError('source email tidak dikenal');
    if (typeof input.sourceRef !== 'string' || !SOURCE_REF_RE.test(input.sourceRef)) throw new EmailTemplateError('sourceRef tidak valid');
    const params = normalizeEmailParams(input.template, input.source, input.params ?? {});
    if (!isUuid(input.memberId)) return 'skipped_no_email';
    const memberId = input.memberId.toLowerCase();

    return this.db.tx(async (q): Promise<EmailEnqueueResult> => {
      const res = await q.query<{ member_hash: string; verified_at: Date | string | null; status: string | null }>(
        `SELECT m.member_hash, e.verified_at, e.status
           FROM platform.member m
           LEFT JOIN platform.member_email e ON e.member_id = m.id
          WHERE m.id = $1 AND m.deleted_at IS NULL`,
        [memberId],
      );
      const row = res.rows[0];
      if (!row) return 'skipped_no_email';
      if (isMarketingSource(input.source) && !(await isMarketingGranted(q, memberId))) return 'skipped_no_consent';
      if (!row.verified_at || row.status !== 'active') return 'skipped_no_email';

      const key = emailIdempotencyKey(input.source, input.sourceRef, row.member_hash);
      const ins = await q.query<{ idempotency_key: string }>(
        `INSERT INTO platform.email_outbound (idempotency_key, member_id, source, source_ref, template)
         VALUES ($1, $2, $3, $4, $5)
         ON CONFLICT (idempotency_key) DO NOTHING
         RETURNING idempotency_key`,
        [key, memberId, input.source, input.sourceRef, input.template],
      );
      if ((ins.rowCount ?? 0) === 0) return 'queued'; // sudah diantre sebelumnya
      const msg: NotifyEmailMessage = {
        schema_version: 1,
        idempotency_key: key,
        member_hash: row.member_hash,
        source: input.source,
        source_ref: input.sourceRef,
        template: input.template,
        params,
      };
      await this.outbox.enqueue(q, NOTIFY_EMAIL_QUEUE, { ...msg }, key);
      this.logger.debug(`notify.email diantre (${input.source}/${input.template})`);
      return 'queued';
    });
  }
}
