import { Errors } from '../../common/errors/api-error';
import { asRecord, requireInt, requireString } from '../../common/validation';
import { CompanyCode, isCompanyCode } from '../../loyalty/loyalty.service';
import { PAYMENT_STATUSES, PaymentStatus } from '../payment-gateway';

/** `POST /v1/pos/payments` (docs/04 §8.3). */
export interface CreatePaymentDto {
  orderRef: string;
  amount: number;
  currency: 'IDR';
  companyCode: CompanyCode;
  posConfigId: number;
  method: 'qris';
  idempotencyKey: string;
}

const ORDER_REF_RE = /^[\x20-\x7e]{1,64}$/;
const IDEMPOTENCY_RE = /^[\x21-\x7e]{8,128}$/;
const AMOUNT_MAX = 999_999_999_999.99;

export function parseCreatePayment(body: unknown): CreatePaymentDto {
  const o = asRecord(body);
  const orderRef = requireString(o, 'order_ref', 64);
  if (!ORDER_REF_RE.test(orderRef)) throw Errors.invalidRequest();
  const amount = o.amount;
  // numeric(14,2): positif, maks 2 desimal
  if (typeof amount !== 'number' || !Number.isFinite(amount) || amount <= 0 || amount > AMOUNT_MAX) throw Errors.invalidRequest();
  if (Math.abs(Math.round(amount * 100) - amount * 100) > 1e-6) throw Errors.invalidRequest();
  if (o.currency !== 'IDR') throw Errors.invalidRequest('Mata uang harus IDR.');
  if (o.method !== 'qris') throw Errors.invalidRequest('Metode harus qris.');
  if (!isCompanyCode(o.company_code)) throw Errors.invalidRequest();
  const idempotencyKey = requireString(o, 'idempotency_key', 128);
  if (!IDEMPOTENCY_RE.test(idempotencyKey)) throw Errors.invalidRequest();
  return {
    orderRef,
    amount,
    currency: 'IDR',
    companyCode: o.company_code,
    posConfigId: requireInt(o, 'pos_config_id', 1, 2_147_483_647),
    method: 'qris',
    idempotencyKey,
  };
}

/** Dev-only `POST /v1/pos/payments/:id/simulate` `{ status }`. */
export function parseSimulate(body: unknown): { status: Exclude<PaymentStatus, 'pending'> } {
  const o = asRecord(body);
  const s = o.status;
  if (typeof s !== 'string' || s === 'pending' || !(PAYMENT_STATUSES as readonly string[]).includes(s)) throw Errors.invalidRequest();
  return { status: s as Exclude<PaymentStatus, 'pending'> };
}
