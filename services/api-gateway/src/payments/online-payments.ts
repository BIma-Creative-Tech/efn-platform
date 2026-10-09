import { Queryable } from '../common/db/db';
import { GatewayName, PaymentStatus, RefundOutcome, RefundRequest } from './payment-gateway';

/**
 * Port payment untuk kanal online (docs/04 §10.7). Diimplementasikan `PaymentsService`; modul orders hanya bergantung
 * pada interface ini (test memakai fake). Tidak ada dependensi payments → orders: hook transisi didaftarkan orders.
 */
export interface OnlineIntentPortionInput {
  partId: string;
  channelCode: string;
  companyCode: string;
  amount: number;
}

export interface OnlineIntentInput {
  orderId: string;
  amount: number;
  portions: OnlineIntentPortionInput[];
  expiresAt: Date;
  /** `online:intent:<order_id>` (§10.6.4). */
  idempotencyKey: string;
}

export interface OnlineIntent {
  paymentId: string;
  provider: GatewayName;
  status: PaymentStatus;
  qrString: string | null;
  expiresAt: Date | null;
  paidAt: Date | null;
  amount: number;
}

/** Info transisi intent `purpose=online` (dipanggil DI DALAM transaksi webhook/getStatus/cancel). */
export interface OnlineIntentTransition {
  paymentId: string;
  onlineOrderId: string;
  from: PaymentStatus;
  to: PaymentStatus;
  /** true bila nominal webhook ≠ intent (status TIDAK diubah, §10.6.5). */
  amountMismatch: boolean;
}

export type OnlineIntentHook = (q: Queryable, info: OnlineIntentTransition) => Promise<void>;

export interface OnlinePaymentsPort {
  createOnlineIntent(input: OnlineIntentInput): Promise<OnlineIntent>;
  getIntent(paymentId: string): Promise<OnlineIntent | null>;
  /** Batalkan di gateway (tidak didukung → lokal) lalu status `cancelled` bila masih `pending`. Mengembalikan status terkini. */
  cancelIntent(paymentId: string): Promise<PaymentStatus>;
  /** Refund ke gateway; `PaymentOperationUnsupportedError` diteruskan (→ `manual_required`). */
  refund(paymentId: string, req: RefundRequest): Promise<RefundOutcome>;
  /** Dev/staging saja: webhook mock bertanda tangan (404 di production / provider ≠ mock). */
  simulate(paymentId: string, status: Exclude<PaymentStatus, 'pending'>): Promise<unknown>;
}

export const ONLINE_PAYMENTS = Symbol('ONLINE_PAYMENTS');
