import { Module } from '@nestjs/common';
import { EmailConfig } from './email.config';
import { EmailRetentionJob } from './email-retention.job';
import { EmailSenderConsumer } from './email-sender.consumer';
import { EmailUnsubscribeController } from './email-unsubscribe.controller';
import { EmailUnsubscribeService } from './email-unsubscribe.service';
import { EmailNotifier } from './email.notifier';
import { DisabledSmtpTransport, NodemailerSmtpTransport, SMTP_TRANSPORT, SmtpTransport } from './smtp-transport';

/**
 * Kanal email (ADR-048 Proposed, docs/04 §28.7/§28.9). Diregistrasi di `AppModule` oleh BFF-A.
 * Ekspor: `EmailNotifier` (antarmuka untuk dispatcher campaign/alert).
 * Infrastruktur (DB, outbox, AMQP, PiiCipher, RateLimiter, AppConfig) dari `CommonModule` global.
 *
 * `EMAIL_ENABLED=true` dengan konfigurasi SMTP/TLS tidak lengkap → BFF GAGAL START (ADR-048 §3.2, tanpa fallback).
 */
@Module({
  controllers: [EmailUnsubscribeController],
  providers: [
    {
      provide: EmailConfig,
      useFactory: (): EmailConfig => {
        const c = EmailConfig.fromProcessEnv();
        c.assertValid();
        return c;
      },
    },
    {
      provide: SMTP_TRANSPORT,
      inject: [EmailConfig],
      useFactory: (c: EmailConfig): SmtpTransport => (c.enabled ? new NodemailerSmtpTransport(c.smtp) : new DisabledSmtpTransport()),
    },
    EmailNotifier,
    EmailSenderConsumer,
    EmailUnsubscribeService,
    EmailRetentionJob,
  ],
  exports: [EmailNotifier],
})
export class EmailModule {}
