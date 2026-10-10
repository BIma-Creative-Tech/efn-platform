import { computeWebhookSignature, verifyWebhookSignature, WEBHOOK_SIGNATURE_HEADER } from '../../common/security/webhook-signature';
import {
  CreateQrisInput,
  CreateQrisResult,
  IntentRef,
  PAYMENT_STATUSES,
  PaymentGateway,
  PaymentStatus,
  ProviderStatus,
  RefundOutcome,
  RefundRequest,
  WebhookEvent,
  WebhookRequest,
  WebhookVerifyResult,
  headerValue,
  parseJsonObject,
} from '../payment-gateway';

/**
 * Adapter `mock` (docs/04 §8.3): default dev/staging, DITOLAK di production (AppConfig.paymentProvider).
 * - QR dummy yang jelas palsu (bukan payload EMVCo) — tidak bisa dibayar dengan aplikasi bank.
 * - Tidak ada state eksternal: `getStatus` = `expired` setelah `expires_at`, selain itu `pending`.
 * - Status lain hanya lewat webhook terverifikasi: header `X-EFN-Signature: t=<unix>,v1=<hex>`
 *   (HMAC-SHA256 `webhook_payment_secret` atas `"<t>." + rawBody`, replay window 300 s — kontrak §2).
 *   Endpoint dev `POST /v1/pos/payments/:id/simulate` membangun webhook bertanda tangan dengan `buildWebhook` dan
 *   memprosesnya lewat jalur yang sama (verifikasi → idempotensi → transisi).
 * Body: `{ event_id, provider_ref, status, amount, paid_at }`.
 */
export class MockGateway implements PaymentGateway {
  readonly name = 'mock' as const;
  now: () => number = () => Date.now();

  constructor(private readonly webhookSecret: () => string) {}

  async createQris(input: CreateQrisInput): Promise<CreateQrisResult> {
    return {
      providerRef: `mock_${input.paymentId}`,
      qrString: `EFN-MOCK-QRIS|${input.paymentId}|${input.amount}|BUKAN-QR-SUNGGUHAN`,
      expiresAt: input.expiresAt,
    };
  }

  async getStatus(intent: IntentRef): Promise<ProviderStatus> {
    const expired = intent.expiresAt !== null && this.now() > intent.expiresAt.getTime();
    return { status: expired ? 'expired' : 'pending', paidAt: null, amount: null };
  }

  async cancel(): Promise<{ status: PaymentStatus }> {
    return { status: 'cancelled' };
  }

  /** Refund per intent (in-memory per proses): idempoten per `refundId`, Σ refund ≤ amount intent (§10.7). */
  private readonly refunds = new Map<string, Map<string, number>>();

  async refund(intent: IntentRef, req: RefundRequest): Promise<RefundOutcome> {
    const book = this.refunds.get(intent.paymentId) ?? new Map<string, number>();
    this.refunds.set(intent.paymentId, book);
    const ref = `mock_rf_${req.refundId}`;
    if (book.has(req.refundId)) return { status: 'succeeded', providerRefundRef: ref };
    const total = [...book.values()].reduce((a, b) => a + b, 0);
    if (!(req.amount > 0) || total + req.amount > intent.amount + 0.005) return { status: 'failed', providerRefundRef: null };
    book.set(req.refundId, req.amount);
    return { status: 'succeeded', providerRefundRef: ref };
  }

  verifyWebhook(req: WebhookRequest, nowSeconds: number): WebhookVerifyResult {
    const r = verifyWebhookSignature({
      header: headerValue(req.headers, WEBHOOK_SIGNATURE_HEADER),
      rawBody: req.rawBody,
      secret: this.webhookSecret(),
      nowSeconds,
    });
    return r.ok ? { ok: true } : { ok: false, reason: r.reason };
  }

  parseWebhook(rawBody: Buffer): WebhookEvent {
    const o = parseJsonObject(rawBody);
    if (!o) throw new Error('body webhook mock tidak valid');
    const status = o.status;
    if (typeof o.event_id !== 'string' || o.event_id.length === 0 || o.event_id.length > 128) throw new Error('event_id tidak valid');
    if (typeof o.provider_ref !== 'string' || o.provider_ref.length === 0) throw new Error('provider_ref tidak valid');
    if (typeof status !== 'string' || !(PAYMENT_STATUSES as readonly string[]).includes(status)) throw new Error('status tidak valid');
    const amount = typeof o.amount === 'number' && Number.isFinite(o.amount) ? o.amount : null;
    const paidAt = typeof o.paid_at === 'string' && !Number.isNaN(Date.parse(o.paid_at)) ? new Date(o.paid_at) : null;
    return { eventId: o.event_id, providerRef: o.provider_ref, status: status as PaymentStatus, paidAt, amount };
  }

  /** Bangun webhook mock bertanda tangan (dipakai endpoint simulasi dev & test). */
  static buildWebhook(
    secret: string,
    body: { event_id: string; provider_ref: string; status: PaymentStatus; amount: number | null; paid_at: string | null },
    nowSeconds: number,
  ): WebhookRequest {
    const rawBody = Buffer.from(JSON.stringify(body), 'utf8');
    const sig = computeWebhookSignature(secret, nowSeconds, rawBody);
    return { headers: { [WEBHOOK_SIGNATURE_HEADER]: `t=${nowSeconds},v1=${sig}` }, rawBody };
  }
}
