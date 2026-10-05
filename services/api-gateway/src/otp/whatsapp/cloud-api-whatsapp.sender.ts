import { ConfigError } from '../../config/app-config';
import { createPublicHttp } from '../../common/net/public-https';
import { maskMsisdn } from '../../common/pii/pii';
import { WhatsAppSender } from './whatsapp-sender';

export interface CloudApiOptions {
  baseUrl: string;
  apiVersion: string;
  phoneNumberId: string;
  token: string;
  otpTemplate: string;
  languageCode: string;
  otpCopyCodeButton: boolean;
  timeoutMs: number;
}

export interface FetchInit {
  method: string;
  headers: Record<string, string>;
  body: string;
  signal?: AbortSignal;
}

export interface FetchResponseLike {
  ok: boolean;
  status: number;
  json(): Promise<unknown>;
}

export type FetchLike = (url: string, init: FetchInit) => Promise<FetchResponseLike>;

/**
 * SEC-23: default transport = https.Agent dengan root store publik saja (bukan `fetch` global yang ikut memercayai
 * CA internal via NODE_EXTRA_CA_CERTS). Lihat `common/net/public-https.ts`.
 */
function defaultFetch(timeoutMs: number): FetchLike {
  const http = createPublicHttp();
  return (url, init) => http(url, { method: init.method, headers: init.headers, body: init.body, timeoutMs });
}

/**
 * Adapter WhatsApp Cloud API (Meta Graph API): `POST {base}/{version}/{phone-number-id}/messages`
 * dengan template message. OTP memakai template kategori AUTHENTICATION (`WHATSAPP_OTP_TEMPLATE`,
 * default `efn_otp`) — body param {{1}} = kode, dan tombol copy-code (url button index 0) juga berisi kode.
 *
 * Error tidak memuat token, kode OTP, atau nomor utuh; hanya status HTTP + error.code Graph.
 */
export class CloudApiWhatsAppSender implements WhatsAppSender {
  private readonly endpoint: string;
  private readonly fetchImpl: FetchLike;

  constructor(
    private readonly opts: CloudApiOptions,
    fetchImpl?: FetchLike,
  ) {
    this.fetchImpl = fetchImpl ?? defaultFetch(opts.timeoutMs);
    if (!/^v\d+\.\d+$/.test(opts.apiVersion)) throw new ConfigError('WHATSAPP_API_VERSION harus berformat vNN.N');
    if (!/^\d+$/.test(opts.phoneNumberId)) throw new ConfigError('WHATSAPP_PHONE_NUMBER_ID harus numerik');
    if (!/^[a-z0-9_]{1,512}$/.test(opts.otpTemplate)) throw new ConfigError('WHATSAPP_OTP_TEMPLATE tidak valid');
    this.endpoint = `${opts.baseUrl.replace(/\/+$/, '')}/${opts.apiVersion}/${opts.phoneNumberId}/messages`;
  }

  async sendOtp(to: string, code: string): Promise<void> {
    const components: Record<string, unknown>[] = [
      { type: 'body', parameters: [{ type: 'text', text: code }] },
    ];
    if (this.opts.otpCopyCodeButton) {
      components.push({ type: 'button', sub_type: 'url', index: '0', parameters: [{ type: 'text', text: code }] });
    }
    await this.post(to, this.templateBody(to, this.opts.otpTemplate, components));
  }

  async sendTemplate(to: string, templateName: string, bodyParams: readonly string[]): Promise<void> {
    const components: Record<string, unknown>[] =
      bodyParams.length > 0
        ? [{ type: 'body', parameters: bodyParams.map((text) => ({ type: 'text', text })) }]
        : [];
    await this.post(to, this.templateBody(to, templateName, components));
  }

  private templateBody(to: string, name: string, components: Record<string, unknown>[]): Record<string, unknown> {
    return {
      messaging_product: 'whatsapp',
      recipient_type: 'individual',
      to,
      type: 'template',
      template: { name, language: { code: this.opts.languageCode }, components },
    };
  }

  private async post(to: string, body: Record<string, unknown>): Promise<void> {
    let res: FetchResponseLike;
    try {
      res = await this.fetchImpl(this.endpoint, {
        method: 'POST',
        headers: { Authorization: `Bearer ${this.opts.token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
        signal: AbortSignal.timeout(this.opts.timeoutMs),
      });
    } catch (err) {
      const name = err instanceof Error ? err.name : 'Error';
      throw new Error(`WhatsApp Cloud API tidak terjangkau (${name}) untuk ${maskMsisdn(to)}`);
    }
    if (!res.ok) {
      let graphCode = 'unknown';
      try {
        const j = (await res.json()) as { error?: { code?: unknown } } | null;
        if (j?.error?.code !== undefined) graphCode = String(j.error.code);
      } catch {
        // body bukan JSON
      }
      throw new Error(`WhatsApp Cloud API HTTP ${res.status} (error.code=${graphCode}) untuk ${maskMsisdn(to)}`);
    }
  }
}
