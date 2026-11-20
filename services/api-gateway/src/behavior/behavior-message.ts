import { PermanentMessageError } from '../common/amqp/amqp.service';
import { isCompanyCode } from '../loyalty/loyalty.service';
import { CLIENT_EVENT_TYPES, ClientEventType, MAX_EVENTS_PER_BATCH, isUuidV4 } from './dto/behavior.dto';

/** docs/04 §28.7: queue `behavior.event` (DLQ `behavior.event.dlq`), `message_id` = `behavior:<batch_id>`. */
export const BEHAVIOR_EVENT_QUEUE = 'behavior.event';
/**
 * Publisher yang diterima consumer (properti AMQP `user_id`, SEC-08). Kontrak menyebut consumer `efn-behavior`; BFF saat
 * ini memakai SATU koneksi (`efn-bff`) untuk publish & consume — lihat README (usulan kontrak).
 */
export const BEHAVIOR_PUBLISHERS: readonly string[] = ['efn-bff'];

export interface BehaviorQueueEvent {
  event_id: string;
  type: ClientEventType;
  client_ts: string;
  channel_code: string;
  /** PT pemilik kanal (`channel-company.ts`); wajib — kolom `event_raw.company_code` (cursor/partisi mart ETL). */
  company_code: string;
  outlet_code: string | null;
  product_id: number | null;
  duration_s: number | null;
  qty: number | null;
  query: string | null;
  result_count: number | null;
  stock_status: 'in_stock' | 'low' | 'out' | null;
  price_idr: number | null;
}

export interface BehaviorEventMessage {
  schema_version: 1;
  batch_id: string;
  received_at: string;
  subject_key: string;
  subject_kind: 'device' | 'member';
  channel: 'web' | 'app';
  events: BehaviorQueueEvent[];
}

export function behaviorIdempotencyKey(batchId: string): string {
  return `behavior:${batchId}`;
}

const HEX64 = /^[0-9a-f]{64}$/;
const ISO_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?Z$/;
const STOCK = ['in_stock', 'low', 'out'];

const nullableInt = (v: unknown, min: number, max: number): boolean =>
  v === null || (typeof v === 'number' && Number.isSafeInteger(v) && v >= min && v <= max);
const nullableStr = (v: unknown, re: RegExp): boolean => v === null || (typeof v === 'string' && re.test(v));

/** Validasi ulang pesan di consumer (pertahanan berlapis; publisher = BFF sendiri). Tidak valid → DLQ. */
export function parseBehaviorMessage(payload: unknown): BehaviorEventMessage {
  if (typeof payload !== 'object' || payload === null || Array.isArray(payload)) throw new PermanentMessageError('payload bukan object');
  const o = payload as Record<string, unknown>;
  if (o.schema_version !== 1) throw new PermanentMessageError('schema_version tidak didukung');
  if (!isUuidV4(o.batch_id)) throw new PermanentMessageError('batch_id tidak valid');
  if (typeof o.received_at !== 'string' || !ISO_RE.test(o.received_at) || !Number.isFinite(Date.parse(o.received_at))) {
    throw new PermanentMessageError('received_at tidak valid');
  }
  if (typeof o.subject_key !== 'string' || !HEX64.test(o.subject_key)) throw new PermanentMessageError('subject_key tidak valid');
  if (o.subject_kind !== 'device' && o.subject_kind !== 'member') throw new PermanentMessageError('subject_kind tidak valid');
  if (o.channel !== 'web' && o.channel !== 'app') throw new PermanentMessageError('channel tidak valid');
  if (!Array.isArray(o.events) || o.events.length === 0 || o.events.length > MAX_EVENTS_PER_BATCH) {
    throw new PermanentMessageError('events tidak valid');
  }
  const events: BehaviorQueueEvent[] = o.events.map((raw) => {
    if (typeof raw !== 'object' || raw === null) throw new PermanentMessageError('event tidak valid');
    const e = raw as Record<string, unknown>;
    const ok =
      isUuidV4(e.event_id) &&
      typeof e.type === 'string' &&
      (CLIENT_EVENT_TYPES as readonly string[]).includes(e.type) &&
      typeof e.client_ts === 'string' &&
      Number.isFinite(Date.parse(e.client_ts)) &&
      typeof e.channel_code === 'string' &&
      /^[A-Z0-9-]{1,8}$/.test(e.channel_code) &&
      isCompanyCode(e.company_code) &&
      nullableStr(e.outlet_code, /^[A-Za-z0-9_-]{1,16}$/) &&
      nullableInt(e.product_id, 1, 2_147_483_647) &&
      nullableInt(e.duration_s, 0, 600) &&
      nullableInt(e.qty, 0, 9999) &&
      (e.query === null || (typeof e.query === 'string' && e.query.length <= 64)) &&
      nullableInt(e.result_count, 0, 1_000_000) &&
      (e.stock_status === null || (typeof e.stock_status === 'string' && STOCK.includes(e.stock_status))) &&
      nullableInt(e.price_idr, 0, Number.MAX_SAFE_INTEGER);
    if (!ok) throw new PermanentMessageError('event tidak valid');
    return {
      event_id: String(e.event_id).toLowerCase(),
      type: e.type as ClientEventType,
      client_ts: new Date(String(e.client_ts)).toISOString(),
      channel_code: String(e.channel_code),
      company_code: String(e.company_code),
      outlet_code: (e.outlet_code as string | null) ?? null,
      product_id: (e.product_id as number | null) ?? null,
      duration_s: (e.duration_s as number | null) ?? null,
      qty: (e.qty as number | null) ?? null,
      query: (e.query as string | null) ?? null,
      result_count: (e.result_count as number | null) ?? null,
      stock_status: (e.stock_status as BehaviorQueueEvent['stock_status']) ?? null,
      price_idr: (e.price_idr as number | null) ?? null,
    };
  });
  return {
    schema_version: 1,
    batch_id: o.batch_id.toLowerCase(),
    received_at: o.received_at,
    subject_key: o.subject_key,
    subject_kind: o.subject_kind,
    channel: o.channel,
    events,
  };
}
