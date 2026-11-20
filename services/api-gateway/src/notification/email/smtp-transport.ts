import { createTransport } from 'nodemailer';
import type { Transporter } from 'nodemailer';
import type SMTPPool from 'nodemailer/lib/smtp-pool';
import type { SmtpSettings } from './email.config';

/**
 * Adapter SMTP (ADR-048 §3.2). Abstraksi sempit agar test tidak menyentuh jaringan.
 *
 * TLS wajib, tanpa fallback plaintext:
 * - `implicit` → `secure: true` (TLS sejak awal, port 465);
 * - `starttls` → `secure: false` + `requireTLS: true` (STARTTLS WAJIB; server tanpa STARTTLS = gagal kirim, bukan
 *   lanjut plaintext) + `ignoreTLS: false`;
 * - selalu `tls.rejectUnauthorized: true`, `minVersion: TLSv1.2`, `servername` = host (bila bukan IP), CA tambahan
 *   opsional (`EMAIL_SMTP_CA_FILE`, mis. CA internal untuk Mailpit dev/relai internal).
 */
export const SMTP_TRANSPORT = Symbol('SMTP_TRANSPORT');

export interface OutgoingEmail {
  from: string;
  to: string;
  subject: string;
  text: string;
  html: string;
  headers: Record<string, string>;
}

export interface SmtpTransport {
  send(mail: OutgoingEmail): Promise<void>;
  close(): void;
}

/** Galat SMTP yang dipetakan: `permanentRecipient` = 5xx pada RCPT TO (alamat tidak valid → berhenti kirim). */
export interface SmtpFailure {
  permanentRecipient: boolean;
  responseCode: number | undefined;
}

export function classifySmtpError(err: unknown): SmtpFailure {
  const e = (err ?? {}) as { responseCode?: unknown; command?: unknown };
  const code = typeof e.responseCode === 'number' ? e.responseCode : undefined;
  const command = typeof e.command === 'string' ? e.command.toUpperCase() : '';
  return { permanentRecipient: code !== undefined && code >= 500 && code < 600 && command.startsWith('RCPT'), responseCode: code };
}

/** Opsi nodemailer (diekspor untuk test: memastikan tidak ada jalur plaintext). */
export function smtpTransportOptions(s: SmtpSettings): SMTPPool.Options {
  return {
    host: s.host,
    port: s.port,
    secure: s.tlsMode === 'implicit',
    requireTLS: s.tlsMode === 'starttls',
    ignoreTLS: false,
    ...(s.user ? { auth: { user: s.user, pass: s.password ?? '' } } : {}),
    tls: {
      rejectUnauthorized: true,
      minVersion: 'TLSv1.2',
      ...(s.servername ? { servername: s.servername } : {}),
      ...(s.ca ? { ca: s.ca } : {}),
    },
    pool: true,
    maxConnections: 2,
    connectionTimeout: 15_000,
    greetingTimeout: 15_000,
    socketTimeout: 30_000,
    // tidak ada logger/debug: log SMTP memuat alamat penerima
    logger: false,
    debug: false,
  };
}

export class NodemailerSmtpTransport implements SmtpTransport {
  private readonly transporter: Transporter;

  constructor(settings: SmtpSettings) {
    this.transporter = createTransport(smtpTransportOptions(settings));
  }

  async send(mail: OutgoingEmail): Promise<void> {
    await this.transporter.sendMail({
      from: mail.from,
      to: mail.to,
      subject: mail.subject,
      text: mail.text,
      html: mail.html,
      headers: mail.headers,
    });
  }

  close(): void {
    this.transporter.close();
  }
}

/** Dipakai saat `EMAIL_ENABLED=false`: setiap kirim gagal (consumer juga tidak didaftarkan). */
export class DisabledSmtpTransport implements SmtpTransport {
  async send(): Promise<void> {
    throw new Error('kanal email nonaktif');
  }
  close(): void {
    /* tidak ada koneksi */
  }
}
