import { Errors } from '../../common/errors/api-error';
import { asRecord } from '../../common/validation';

/**
 * Validasi body `POST /v1/events` dan `POST /v1/events/consent` (docs/04 §28.6, ADR-047 §3.2). Tanpa class-validator:
 * allowlist eksplisit (`additionalProperties: false`). Kesalahan bentuk body → 400 generik; kesalahan per event →
 * `rejected[{index, code}]` (event lain tetap diterima).
 */

export const CLIENT_EVENT_TYPES = [
  'product_view',
  'wishlist_add',
  'wishlist_remove',
  'cart_add',
  'cart_remove',
  'checkout_start',
  'search',
  'notify_me_request',
  'price_alert_set',
] as const;
export type ClientEventType = (typeof CLIENT_EVENT_TYPES)[number];
/** `cart_abandon` hanya diturunkan server (ADR-047 §3.2), klien yang mengirimnya ditolak `invalid`. */
export type EventType = ClientEventType | 'cart_abandon';

/** Jenis yang wajib membawa `product_id`. */
export const PRODUCT_EVENT_TYPES: ReadonlySet<string> = new Set([
  'product_view',
  'wishlist_add',
  'wishlist_remove',
  'cart_add',
  'cart_remove',
  'notify_me_request',
  'price_alert_set',
]);

export const MAX_EVENTS_PER_BATCH = 50;
export const MAX_BODY_BYTES = 32 * 1024;
export const CLIENT_TS_SKEW_MS = 24 * 3600 * 1000;
export const MAX_DURATION_S = 600;

const UUID_V4_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const RFC3339_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$/;
const CHANNEL_CODE_RE = /^[A-Z0-9-]{1,8}$/;
const OUTLET_CODE_RE = /^[A-Za-z0-9_-]{1,16}$/;
const VERSION_RE = /^[A-Za-z0-9._@-]{1,32}$/;
const TOP_KEYS = new Set(['schema_version', 'device_id', 'channel', 'events']);
const EVENT_KEYS = new Set(['event_id', 'type', 'client_ts', 'channel_code', 'outlet_code', 'product_id', 'duration_s', 'qty', 'query', 'result_count']);
const CONSENT_KEYS = new Set(['device_id', 'granted', 'version']);

/** `unknown_channel` (revisi G50-A): `channel_code` tidak terpetakan ke PT → event tidak disimpan tanpa PT. */
export type RejectCode = 'invalid' | 'stale' | 'unknown_channel' | 'unknown_product';

export interface ParsedEvent {
  event_id: string;
  type: EventType;
  client_ts: string;
  channel_code: string;
  outlet_code: string | null;
  product_id: number | null;
  duration_s: number | null;
  qty: number | null;
  query: string | null;
  result_count: number | null;
}

export interface ParsedBatch {
  deviceId: string;
  channel: 'web' | 'app';
  /** Event valid beserta indeks aslinya (untuk pelaporan `rejected`). */
  events: { index: number; event: ParsedEvent }[];
  rejected: { index: number; code: RejectCode }[];
  /** Duplikat `event_id` di dalam batch yang sama. */
  duplicates: number;
}

export function isUuidV4(v: unknown): v is string {
  return typeof v === 'string' && UUID_V4_RE.test(v);
}

/**
 * Redaksi istilah pencarian (§28.6): NFKC + lowercase; pola email dan rangkaian ≥ 6 digit (boleh dipisah spasi/titik/
 * strip, mis. nomor HP `0812-3456-7890`) diganti `[redacted]`; karakter kontrol dibuang; ≤ 64 karakter.
 */
export function redactQuery(raw: string): string {
  let s = raw.normalize('NFKC').toLowerCase();
  // eslint-disable-next-line no-control-regex
  s = s.replace(/[\u0000-\u001f\u007f-\u009f]/g, ' ');
  s = s.replace(/[^\s@]+@[^\s@]+/g, '[redacted]');
  s = s.replace(/\d(?:[\s.-]?\d){5,}/g, '[redacted]');
  return s.replace(/\s+/g, ' ').trim().slice(0, 64);
}

function onlyKeys(o: Record<string, unknown>, allowed: ReadonlySet<string>): boolean {
  return Object.keys(o).every((k) => allowed.has(k));
}

function optInt(v: unknown, min: number, max: number): number | null | 'bad' {
  if (v === undefined || v === null) return null;
  if (typeof v !== 'number' || !Number.isSafeInteger(v) || v < min || v > max) return 'bad';
  return v;
}

/** Satu event; `'invalid' | 'stale'` bila ditolak. */
export function parseEvent(raw: unknown, nowMs: number): ParsedEvent | 'invalid' | 'stale' {
  if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) return 'invalid';
  const o = raw as Record<string, unknown>;
  if (!onlyKeys(o, EVENT_KEYS)) return 'invalid';
  if (!isUuidV4(o.event_id)) return 'invalid';
  if (typeof o.type !== 'string' || !(CLIENT_EVENT_TYPES as readonly string[]).includes(o.type)) return 'invalid';
  const type = o.type as ClientEventType;
  if (typeof o.client_ts !== 'string' || !RFC3339_RE.test(o.client_ts)) return 'invalid';
  const ts = Date.parse(o.client_ts);
  if (!Number.isFinite(ts)) return 'invalid';
  if (typeof o.channel_code !== 'string' || !CHANNEL_CODE_RE.test(o.channel_code)) return 'invalid';
  let outlet: string | null = null;
  if (o.outlet_code !== undefined && o.outlet_code !== null) {
    if (typeof o.outlet_code !== 'string' || !OUTLET_CODE_RE.test(o.outlet_code)) return 'invalid';
    outlet = o.outlet_code;
  }
  const productId = optInt(o.product_id, 1, 2_147_483_647);
  if (productId === 'bad') return 'invalid';
  if (PRODUCT_EVENT_TYPES.has(type) && productId === null) return 'invalid';
  let duration: number | null = null;
  if (o.duration_s !== undefined && o.duration_s !== null) {
    if (typeof o.duration_s !== 'number' || !Number.isFinite(o.duration_s) || o.duration_s < 0) return 'invalid';
    duration = Math.min(MAX_DURATION_S, Math.floor(o.duration_s)); // §28.6: 0..600 (dipotong)
  }
  const qty = optInt(o.qty, 0, 9999);
  if (qty === 'bad') return 'invalid';
  let query: string | null = null;
  let resultCount: number | null = null;
  if (type === 'search') {
    if (typeof o.query !== 'string' || o.query.length === 0 || o.query.length > 64) return 'invalid';
    query = redactQuery(o.query);
    if (query.length === 0) return 'invalid';
    const rc = optInt(o.result_count, 0, 1_000_000);
    if (rc === 'bad') return 'invalid';
    resultCount = rc;
  } else if (o.query !== undefined || o.result_count !== undefined) {
    return 'invalid'; // §28.6: `query` hanya untuk type=search
  }
  if (Math.abs(nowMs - ts) > CLIENT_TS_SKEW_MS) return 'stale';
  return {
    event_id: o.event_id.toLowerCase(),
    type,
    client_ts: new Date(ts).toISOString(),
    channel_code: o.channel_code,
    outlet_code: outlet,
    product_id: productId,
    duration_s: duration,
    qty,
    query,
    result_count: resultCount,
  };
}

/** Body `POST /v1/events`. Bentuk tidak valid → 400. */
export function parseEventBatch(body: unknown, nowMs: number): ParsedBatch {
  const o = asRecord(body);
  if (!onlyKeys(o, TOP_KEYS) || o.schema_version !== 1) throw Errors.invalidRequest();
  if (!isUuidV4(o.device_id)) throw Errors.invalidRequest();
  if (o.channel !== 'web' && o.channel !== 'app') throw Errors.invalidRequest();
  if (!Array.isArray(o.events) || o.events.length > MAX_EVENTS_PER_BATCH) throw Errors.invalidRequest();
  const out: ParsedBatch = { deviceId: o.device_id.toLowerCase(), channel: o.channel, events: [], rejected: [], duplicates: 0 };
  const seen = new Set<string>();
  o.events.forEach((raw, index) => {
    const ev = parseEvent(raw, nowMs);
    if (ev === 'invalid' || ev === 'stale') {
      out.rejected.push({ index, code: ev });
      return;
    }
    if (seen.has(ev.event_id)) {
      out.duplicates += 1;
      return;
    }
    seen.add(ev.event_id);
    out.events.push({ index, event: ev });
  });
  return out;
}

export interface ParsedDeviceConsent {
  deviceId: string;
  granted: boolean;
  version: string;
}

/** Body `POST /v1/events/consent`. */
export function parseDeviceConsent(body: unknown): ParsedDeviceConsent {
  const o = asRecord(body);
  if (!onlyKeys(o, CONSENT_KEYS)) throw Errors.invalidRequest();
  if (!isUuidV4(o.device_id) || typeof o.granted !== 'boolean') throw Errors.invalidRequest();
  if (typeof o.version !== 'string' || !VERSION_RE.test(o.version)) throw Errors.invalidRequest();
  return { deviceId: o.device_id.toLowerCase(), granted: o.granted, version: o.version };
}
