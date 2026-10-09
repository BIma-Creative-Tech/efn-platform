/**
 * Abstraksi payment gateway-agnostic (docs/04 §8.3, ADR-016 Proposed — provider belum ditentukan).
 * Hanya QRIS (tanpa data kartu, PCI SAQ A). Adapter: `mock` (dev/staging), `xendit`, `midtrans` (kerangka).
 */
export type PaymentStatus = 'pending' | 'paid' | 'expired' | 'failed' | 'cancelled';
export const PAYMENT_STATUSES: readonly PaymentStatus[] = ['pending', 'paid', 'expired', 'failed', 'cancelled'];
export type GatewayName = 'mock' | 'xendit' | 'midtrans';

export type PaymentPurpose = 'pos' | 'online';

/**
 * Porsi per bagian (docs/04 §10.7, ADR-019): `mock` mengabaikannya; kelak adapter split (ADR-019 opsi C) memakainya
 * tanpa perubahan kontrak publik.
 */
export interface PaymentPortion {
  reference: string;
  companyCode: string;
  amount: number;
}

export interface CreateQrisInput {
  paymentId: string;
  orderRef: string;
  amount: number;
  currency: 'IDR';
  companyCode: string;
  expiresAt: Date;
  /** §10.7: default `pos` (perilaku POS tidak berubah). */
  purpose?: PaymentPurpose;
  portions?: PaymentPortion[];
}

export interface CreateQrisResult {
  providerRef: string;
  qrString: string;
  expiresAt: Date;
}

export interface IntentRef {
  paymentId: string;
  providerRef: string | null;
  amount: number;
  expiresAt: Date | null;
}

export interface ProviderStatus {
  status: PaymentStatus;
  paidAt: Date | null;
  amount: number | null;
}

export interface WebhookRequest {
  headers: Record<string, string | string[] | undefined>;
  rawBody: Buffer | undefined;
}

export type WebhookVerifyResult = { ok: true } | { ok: false; reason: string };

/** Event webhook ternormalisasi. `eventId` = idempotency key (unik per event provider). */
export interface WebhookEvent {
  eventId: string;
  providerRef: string;
  status: PaymentStatus;
  paidAt: Date | null;
  amount: number | null;
}

/** §10.7: refund ke gateway (selalu terikat satu refund platform; Σ refund ≤ amount intent). */
export interface RefundRequest {
  refundId: string;
  amount: number;
  reason: string;
}
export interface RefundOutcome {
  status: 'succeeded' | 'pending' | 'failed';
  providerRefundRef: string | null;
}

/** Operasi tidak didukung provider (mis. cancel QR dinamis, refund pada adapter kerangka). */
export class PaymentOperationUnsupportedError extends Error {}
/** Respons provider tidak sesuai harapan / HTTP gagal. Pesan tanpa secret. */
export class PaymentProviderError extends Error {}

export interface PaymentGateway {
  readonly name: GatewayName;
  createQris(input: CreateQrisInput): Promise<CreateQrisResult>;
  getStatus(intent: IntentRef): Promise<ProviderStatus>;
  cancel(intent: IntentRef): Promise<{ status: PaymentStatus }>;
  /**
   * §10.7: refund (idempoten per `refundId`). `mock` mendukung (sinkron, Σ refund ≤ amount); adapter yang belum
   * mendukung melempar `PaymentOperationUnsupportedError` → BFF menandai `manual_required`.
   */
  refund(intent: IntentRef, req: RefundRequest): Promise<RefundOutcome>;
  /** Verifikasi keaslian webhook (signature/token + replay window bila provider menyediakan timestamp). */
  verifyWebhook(req: WebhookRequest, nowSeconds: number): WebhookVerifyResult;
  /** Parse body yang SUDAH terverifikasi. Melempar bila bentuk tidak dikenal. */
  parseWebhook(rawBody: Buffer): WebhookEvent;
}

export function headerValue(headers: WebhookRequest['headers'], name: string): string | undefined {
  const v = headers[name.toLowerCase()];
  return typeof v === 'string' ? v : undefined;
}

export function parseJsonObject(raw: Buffer | undefined): Record<string, unknown> | null {
  if (!raw || raw.length === 0 || raw.length > 64 * 1024) return null;
  try {
    const o: unknown = JSON.parse(raw.toString('utf8'));
    return typeof o === 'object' && o !== null && !Array.isArray(o) ? (o as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}
