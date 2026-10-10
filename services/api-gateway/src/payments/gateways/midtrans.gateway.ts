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
  parseJsonObject,
} from '../payment-gateway';

/**
 * ⚠️ KERANGKA — BELUM DIUJI KE SANDBOX MIDTRANS. Nonaktif sampai ADR-016 diputuskan; konstruktor melempar bila
 * `PAYMENT_PROVIDER_EXPERIMENTAL` ≠ true.
 *
 * Disusun dari dokumentasi publik Midtrans Core API (QRIS) — WAJIB diverifikasi ulang sebelum dipakai:
 * - `POST {base}/v2/charge` Basic auth (`<server key>:`), body `{ payment_type: "qris", transaction_details:
 *   { order_id, gross_amount }, qris: { acquirer }, custom_expiry: { expiry_duration, unit: "minute" } }` →
 *   `{ transaction_id, order_id, transaction_status: "pending", qr_string, expiry_time, actions[] }`.
 *   `order_id` = payment_id BFF (dipakai juga sebagai `provider_ref`, karena status/cancel memakai order_id).
 * - Status: `GET {base}/v2/{order_id}/status`; cancel: `POST {base}/v2/{order_id}/cancel`.
 * - Notifikasi HTTP: verifikasi `signature_key = SHA512(order_id + status_code + gross_amount + server_key)`
 *   (waktu-konstan). Tidak ada timestamp bertanda tangan → replay = idempotensi per
 *   `transaction_id:transaction_status` + transisi status monoton. Midtrans menyarankan konfirmasi via GET status;
 *   PaymentsService hanya menerima status final dari notifikasi terverifikasi ini (sesuai kontrak).
 * - Mapping: settlement/capture → paid; pending → pending; expire → expired; cancel → cancelled; deny/failure → failed.
 * - Base default produksi `https://api.midtrans.com`; sandbox `https://api.sandbox.midtrans.com` lewat
 *   `PAYMENT_PROVIDER_BASE_URL`.
 */
export interface MidtransOptions {
  serverKey: string;
  baseUrl?: string;
  acquirer: string;
  timeoutMs: number;
  experimental: boolean;
}

const DEFAULT_BASE = 'https://api.midtrans.com';

export function mapMidtransStatus(s: unknown): PaymentStatus {
  switch (s) {
    case 'settlement':
    case 'capture':
      return 'paid';
    case 'pending':
      return 'pending';
    case 'expire':
      return 'expired';
    case 'cancel':
      return 'cancelled';
    default:
      return 'failed';
  }
}

function amountString(v: unknown): string | null {
  if (typeof v === 'string' && /^\d+(\.\d{1,2})?$/.test(v)) return v;
  if (typeof v === 'number' && Number.isFinite(v)) return v.toFixed(2);
  return null;
}

export class MidtransGateway implements PaymentGateway {
  readonly name = 'midtrans' as const;
  private readonly base: string;

  constructor(
    private readonly opts: MidtransOptions,
    private readonly http: PublicHttp,
  ) {
    if (!opts.experimental) throw new ConfigError('MidtransGateway belum diuji ke sandbox: wajib PAYMENT_PROVIDER_EXPERIMENTAL=true');
    this.base = (opts.baseUrl ?? DEFAULT_BASE).replace(/\/+$/, '');
  }

  private headers(): Record<string, string> {
    return {
      Authorization: `Basic ${Buffer.from(`${this.opts.serverKey}:`).toString('base64')}`,
      'Content-Type': 'application/json',
      Accept: 'application/json',
    };
  }

  async createQris(input: CreateQrisInput): Promise<CreateQrisResult> {
    if (!Number.isInteger(input.amount)) throw new PaymentProviderError('Midtrans QRIS IDR membutuhkan gross_amount bulat');
    const minutes = Math.max(1, Math.round((input.expiresAt.getTime() - Date.now()) / 60_000));
    const res = await this.http(`${this.base}/v2/charge`, {
      method: 'POST',
      headers: this.headers(),
      body: JSON.stringify({
        payment_type: 'qris',
        transaction_details: { order_id: input.paymentId, gross_amount: input.amount },
        qris: { acquirer: this.opts.acquirer },
        custom_expiry: { expiry_duration: minutes, unit: 'minute' },
      }),
      timeoutMs: this.opts.timeoutMs,
    });
    if (!res.ok) throw new PaymentProviderError(`Midtrans charge HTTP ${res.status}`);
    const j = (await res.json()) as Record<string, unknown>;
    if (typeof j.qr_string !== 'string' || j.order_id !== input.paymentId) throw new PaymentProviderError('respons Midtrans tidak dikenal');
    return { providerRef: input.paymentId, qrString: j.qr_string, expiresAt: input.expiresAt };
  }

  async getStatus(intent: IntentRef): Promise<ProviderStatus> {
    const res = await this.http(`${this.base}/v2/${encodeURIComponent(intent.providerRef ?? intent.paymentId)}/status`, {
      method: 'GET',
      headers: this.headers(),
      timeoutMs: this.opts.timeoutMs,
    });
    if (!res.ok) throw new PaymentProviderError(`Midtrans status HTTP ${res.status}`);
    const j = (await res.json()) as Record<string, unknown>;
    const status = mapMidtransStatus(j.transaction_status);
    const at = typeof j.settlement_time === 'string' && !Number.isNaN(Date.parse(j.settlement_time)) ? new Date(j.settlement_time) : null;
    const amt = amountString(j.gross_amount);
    return { status, paidAt: status === 'paid' ? at : null, amount: amt === null ? null : Number(amt) };
  }

  async cancel(intent: IntentRef): Promise<{ status: PaymentStatus }> {
    const res = await this.http(`${this.base}/v2/${encodeURIComponent(intent.providerRef ?? intent.paymentId)}/cancel`, {
      method: 'POST',
      headers: this.headers(),
      timeoutMs: this.opts.timeoutMs,
    });
    if (!res.ok) throw new PaymentProviderError(`Midtrans cancel HTTP ${res.status}`);
    const j = (await res.json()) as Record<string, unknown>;
    return { status: mapMidtransStatus(j.transaction_status) };
  }

  /** §10.7: refund belum didukung kerangka adapter (menunggu ADR-016 + uji sandbox) → BFF menandai manual_required. */
  async refund(): Promise<RefundOutcome> {
    throw new PaymentOperationUnsupportedError('Midtrans: refund belum didukung adapter (ADR-016)');
  }

  verifyWebhook(req: WebhookRequest): WebhookVerifyResult {
    const o = parseJsonObject(req.rawBody);
    if (!o) return { ok: false, reason: 'missing_body' };
    const gross = amountString(o.gross_amount);
    if (typeof o.order_id !== 'string' || typeof o.status_code !== 'string' || gross === null || typeof o.signature_key !== 'string') {
      return { ok: false, reason: 'malformed_signature' };
    }
    if (!/^[0-9a-f]{128}$/i.test(o.signature_key)) return { ok: false, reason: 'malformed_signature' };
    // Midtrans menandatangani gross_amount persis seperti string di body (mis. "10000.00")
    const rawGross = typeof o.gross_amount === 'string' ? o.gross_amount : gross;
    const expected = createHash('sha512').update(`${o.order_id}${o.status_code}${rawGross}${this.opts.serverKey}`, 'utf8').digest();
    const given = Buffer.from(o.signature_key.toLowerCase(), 'hex');
    return given.length === expected.length && timingSafeEqual(given, expected) ? { ok: true } : { ok: false, reason: 'signature_mismatch' };
  }

  parseWebhook(rawBody: Buffer): WebhookEvent {
    const o = parseJsonObject(rawBody);
    if (!o || typeof o.order_id !== 'string' || typeof o.transaction_id !== 'string' || typeof o.transaction_status !== 'string') {
      throw new Error('notifikasi Midtrans tidak lengkap');
    }
    const status = mapMidtransStatus(o.transaction_status);
    const at = typeof o.settlement_time === 'string' && !Number.isNaN(Date.parse(o.settlement_time)) ? new Date(o.settlement_time) : null;
    const amt = amountString(o.gross_amount);
    return {
      eventId: `${o.transaction_id}:${o.transaction_status}`,
      providerRef: o.order_id,
      status,
      paidAt: status === 'paid' ? at : null,
      amount: amt === null ? null : Number(amt),
    };
  }
}
