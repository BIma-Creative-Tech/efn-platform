import { PermanentMessageError } from '../common/amqp/amqp.service';
import { ONLINE_CHANNEL_RE } from '../config/app-config';
import { OUTLET_CODE_RE } from './dto/wishlist.dto';

/** docs/04 §28.7: queue notify-me & price alert (quorum, DLQ `<queue>.dlq`). Publisher & consumer = BFF (`efn-bff`). */
export const NOTIFY_RESTOCK_QUEUE = 'notify.restock';
export const NOTIFY_PRICE_ALERT_QUEUE = 'notify.price_alert';
export const ALERT_PUBLISHERS: readonly string[] = ['efn-bff'];

export interface RestockMessage {
  schema_version: 1;
  idempotency_key: string;
  channel_code: string;
  outlet_code: string;
  product_id: number;
  detected_at: string;
}
export interface PriceAlertMessage {
  schema_version: 1;
  idempotency_key: string;
  channel_code: string;
  product_id: number;
  new_price_idr: number;
  detected_at: string;
}

export const restockKey = (ch: string, outlet: string, pid: number, seq: number): string => `restock:${ch}:${outlet}:${pid}:${seq}`;
export const priceKey = (ch: string, pid: number, version: number): string => `price:${ch}:${pid}:${version}`;

function base(payload: unknown): Record<string, unknown> {
  if (typeof payload !== 'object' || payload === null || Array.isArray(payload)) throw new PermanentMessageError('payload bukan object');
  const o = payload as Record<string, unknown>;
  if (o.schema_version !== 1) throw new PermanentMessageError('schema_version tidak didukung');
  if (typeof o.idempotency_key !== 'string' || o.idempotency_key.length === 0 || o.idempotency_key.length > 200) {
    throw new PermanentMessageError('idempotency_key tidak valid');
  }
  if (typeof o.channel_code !== 'string' || !ONLINE_CHANNEL_RE.test(o.channel_code)) throw new PermanentMessageError('channel_code tidak valid');
  if (typeof o.product_id !== 'number' || !Number.isSafeInteger(o.product_id) || o.product_id < 1) throw new PermanentMessageError('product_id tidak valid');
  if (typeof o.detected_at !== 'string' || !Number.isFinite(Date.parse(o.detected_at))) throw new PermanentMessageError('detected_at tidak valid');
  return o;
}

export function parseRestockMessage(payload: unknown): RestockMessage {
  const o = base(payload);
  if (typeof o.outlet_code !== 'string' || !OUTLET_CODE_RE.test(o.outlet_code)) throw new PermanentMessageError('outlet_code tidak valid');
  return {
    schema_version: 1,
    idempotency_key: String(o.idempotency_key),
    channel_code: String(o.channel_code),
    outlet_code: o.outlet_code,
    product_id: o.product_id as number,
    detected_at: String(o.detected_at),
  };
}

export function parsePriceAlertMessage(payload: unknown): PriceAlertMessage {
  const o = base(payload);
  if (typeof o.new_price_idr !== 'number' || !Number.isSafeInteger(o.new_price_idr) || o.new_price_idr < 0) {
    throw new PermanentMessageError('new_price_idr tidak valid');
  }
  return {
    schema_version: 1,
    idempotency_key: String(o.idempotency_key),
    channel_code: String(o.channel_code),
    product_id: o.product_id as number,
    new_price_idr: o.new_price_idr,
    detected_at: String(o.detected_at),
  };
}
