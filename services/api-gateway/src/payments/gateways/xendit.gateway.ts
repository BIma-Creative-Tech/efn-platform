import { createHash, timingSafeEqual } from 'node:crypto';
import { ConfigError } from '../../config/app-config';
import { PublicHttp } from '../../common/net/public-https';
import {
  CreateQrisInput,
  CreateQrisResult,
  IntentRef,
  PaymentGateway,
  PaymentOperationUnsupportedError,
  PaymentProviderError,
  PaymentStatus,
  ProviderStatus,
  RefundOutcome,
  WebhookEvent,
  WebhookRequest,
  WebhookVerifyResult,
  headerValue,
  parseJsonObject,
} from '../payment-gateway';

/**
 * ⚠️ KERANGKA — BELUM DIUJI KE SANDBOX XENDIT. Nonaktif sampai ADR-016 diputuskan; konstruktor melempar bila
 * `PAYMENT_PROVIDER_EXPERIMENTAL` ≠ true.
 *
 * Disusun dari dokumentasi publik Xendit "QR Codes API" (header `api-version: 2022-07-31`) — WAJIB diverifikasi ulang
 * terhadap dokumentasi & sandbox terbaru sebelum dipakai:
 * - `POST {base}/qr_codes` Basic auth (`<secret API key>:`), body `{ reference_id, type: "DYNAMIC", currency: "IDR",
 *   amount, expires_at }` → `{ id, reference_id, qr_string, expires_at, status }`.
 * - Status: `GET {base}/qr_codes/{id}/payments` → `{ data: [{ id, status: "SUCCEEDED", amount, created }] }`.
 * - Cancel: QR dinamis tidak punya endpoint batal yang terdokumentasi → `PaymentOperationUnsupportedError`
 *   (BFF menandai `cancelled` lokal; pembayaran terlambat tetap tercatat `paid` + log peringatan).
 * - Webhook `qr.payment`: header `x-callback-token` = token verifikasi dashboard Xendit (dibandingkan waktu-konstan
 *   dengan secret `webhook_payment_secret`). Xendit TIDAK menandatangani timestamp → proteksi replay = idempotensi
 *   per `data.id` + transisi status monoton. Body `{ event: "qr.payment", data: { id, qr_id, status, amount, created } }`.
 */
export interface XenditOptions {
  apiKey: string;
  callbackToken: string;
  baseUrl?: string;
  timeoutMs: number;
  experimental: boolean;
}

const DEFAULT_BASE = 'https://api.xendit.co';
const API_VERSION = '2022-07-31';

export class XenditGateway implements PaymentGateway {
  readonly name = 'xendit' as const;
  private readonly base: string;
  now: () => number = () => Date.now();

  constructor(
    private readonly opts: XenditOptions,
    private readonly http: PublicHttp,
  ) {
    if (!opts.experimental) throw new ConfigError('XenditGateway belum diuji ke sandbox: wajib PAYMENT_PROVIDER_EXPERIMENTAL=true');
    this.base = (opts.baseUrl ?? DEFAULT_BASE).replace(/\/+$/, '');
  }

  private headers(): Record<string, string> {
    return {
      Authorization: `Basic ${Buffer.from(`${this.opts.apiKey}:`).toString('base64')}`,
      'api-version': API_VERSION,
      'Content-Type': 'application/json',
      Accept: 'application/json',
    };
  }

  async createQris(input: CreateQrisInput): Promise<CreateQrisResult> {
    if (!Number.isInteger(input.amount)) throw new PaymentProviderError('Xendit QRIS IDR membutuhkan amount bulat');
    const res = await this.http(`${this.base}/qr_codes`, {
      method: 'POST',
      headers: { ...this.headers(), 'idempotency-key': input.paymentId },
      body: JSON.stringify({
        reference_id: input.paymentId,
        type: 'DYNAMIC',
        currency: input.currency,
        amount: input.amount,
        expires_at: input.expiresAt.toISOString(),
      }),
      timeoutMs: this.opts.timeoutMs,
    });
    if (!res.ok) throw new PaymentProviderError(`Xendit create QR HTTP ${res.status}`);
    const j = (await res.json()) as Record<string, unknown>;
    if (typeof j.id !== 'string' || typeof j.qr_string !== 'string') throw new PaymentProviderError('respons Xendit tidak dikenal');
    const exp = typeof j.expires_at === 'string' && !Number.isNaN(Date.parse(j.expires_at)) ? new Date(j.expires_at) : input.expiresAt;
    return { providerRef: j.id, qrString: j.qr_string, expiresAt: exp };
  }

  async getStatus(intent: IntentRef): Promise<ProviderStatus> {
    if (!intent.providerRef) return { status: 'pending', paidAt: null, amount: null };
    const res = await this.http(`${this.base}/qr_codes/${encodeURIComponent(intent.providerRef)}/payments`, {
      method: 'GET',
      headers: this.headers(),
      timeoutMs: this.opts.timeoutMs,
    });
    if (!res.ok) throw new PaymentProviderError(`Xendit status HTTP ${res.status}`);
    const j = (await res.json()) as { data?: unknown };
    const data = Array.isArray(j.data) ? (j.data as Record<string, unknown>[]) : [];
    const paid = data.find((p) => p.status === 'SUCCEEDED');
    if (paid) {
      const at = typeof paid.created === 'string' && !Number.isNaN(Date.parse(paid.created)) ? new Date(paid.created) : null;
      return { status: 'paid', paidAt: at, amount: typeof paid.amount === 'number' ? paid.amount : null };
    }
    const expired = intent.expiresAt !== null && this.now() > intent.expiresAt.getTime();
    return { status: expired ? 'expired' : 'pending', paidAt: null, amount: null };
  }

  async cancel(): Promise<{ status: PaymentStatus }> {
    throw new PaymentOperationUnsupportedError('Xendit QR dinamis tidak mendukung pembatalan');
  }

  /** §10.7: refund belum didukung kerangka adapter (menunggu ADR-016 + uji sandbox) → BFF menandai manual_required. */
  async refund(): Promise<RefundOutcome> {
    throw new PaymentOperationUnsupportedError('Xendit: refund belum didukung adapter (ADR-016)');
  }

  verifyWebhook(req: WebhookRequest): WebhookVerifyResult {
    const token = headerValue(req.headers, 'x-callback-token');
    if (!token) return { ok: false, reason: 'missing_signature' };
    if (!req.rawBody) return { ok: false, reason: 'missing_body' };
    const a = createHash('sha256').update(token, 'utf8').digest();
    const b = createHash('sha256').update(this.opts.callbackToken, 'utf8').digest();
    return timingSafeEqual(a, b) ? { ok: true } : { ok: false, reason: 'signature_mismatch' };
  }

  parseWebhook(rawBody: Buffer): WebhookEvent {
    const o = parseJsonObject(rawBody);
    const data = o && typeof o.data === 'object' && o.data !== null ? (o.data as Record<string, unknown>) : null;
    if (!o || o.event !== 'qr.payment' || !data) throw new Error('event Xendit tidak didukung');
    if (typeof data.id !== 'string' || typeof data.qr_id !== 'string') throw new Error('payload Xendit tidak lengkap');
    const status: PaymentStatus = data.status === 'SUCCEEDED' ? 'paid' : 'failed';
    const paidAt = typeof data.created === 'string' && !Number.isNaN(Date.parse(data.created)) ? new Date(data.created) : null;
    return {
      eventId: data.id,
      providerRef: data.qr_id,
      status,
      paidAt: status === 'paid' ? paidAt : null,
      amount: typeof data.amount === 'number' ? data.amount : null,
    };
  }
}
