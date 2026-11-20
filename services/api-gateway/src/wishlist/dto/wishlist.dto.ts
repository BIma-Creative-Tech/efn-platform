import { ApiError, Errors } from '../../common/errors/api-error';
import { asRecord, isUuid } from '../../common/validation';
import { ONLINE_CHANNEL_RE } from '../../config/app-config';

/** Validasi body/param `src/wishlist` (docs/04 §28.8). Allowlist ketat; gagal → 400 generik. */

export const ALERT_CHANNELS = ['push', 'email', 'wa_unofficial'] as const;
export type AlertChannel = (typeof ALERT_CHANNELS)[number];
export const OUTLET_CODE_RE = /^[A-Za-z0-9_-]{1,32}$/;
const MAX_PRODUCT_ID = 2_147_483_647;

export interface WishlistItemInput {
  channel_code: string;
  product_id: number;
}
export interface RestockAlertInput extends WishlistItemInput {
  outlet_code: string;
  channels: AlertChannel[];
}
export interface PriceAlertInput extends WishlistItemInput {
  target_price_idr: number;
  /** Usulan kontrak G50-A (opsional, default `["push"]`). */
  channels: AlertChannel[];
}

function onlyKeys(o: Record<string, unknown>, keys: readonly string[]): void {
  if (!Object.keys(o).every((k) => keys.includes(k))) throw Errors.invalidRequest();
}

export function parseChannelCode(v: unknown): string {
  if (typeof v !== 'string' || !ONLINE_CHANNEL_RE.test(v)) throw Errors.invalidRequest();
  return v;
}

export function parseProductId(v: unknown): number {
  const n = typeof v === 'string' && /^\d{1,10}$/.test(v) ? Number(v) : v;
  if (typeof n !== 'number' || !Number.isSafeInteger(n) || n < 1 || n > MAX_PRODUCT_ID) throw Errors.invalidRequest();
  return n;
}

export function parseAlertId(v: unknown): string {
  if (!isUuid(v)) throw Errors.notFound();
  return v.toLowerCase();
}

export function parseWishlistItem(body: unknown): WishlistItemInput {
  const o = asRecord(body);
  onlyKeys(o, ['channel_code', 'product_id']);
  return { channel_code: parseChannelCode(o.channel_code), product_id: parseProductId(o.product_id) };
}

export function parseRestockAlert(body: unknown): RestockAlertInput {
  const o = asRecord(body);
  onlyKeys(o, ['channel_code', 'outlet_code', 'product_id', 'channels']);
  if (typeof o.outlet_code !== 'string' || !OUTLET_CODE_RE.test(o.outlet_code)) throw Errors.invalidRequest();
  return {
    channel_code: parseChannelCode(o.channel_code),
    outlet_code: o.outlet_code,
    product_id: parseProductId(o.product_id),
    channels: parseAlertChannels(o.channels),
  };
}

export function parseAlertChannels(v: unknown): AlertChannel[] {
  if (!Array.isArray(v) || v.length === 0 || v.length > ALERT_CHANNELS.length) throw Errors.invalidRequest();
  const channels = [...new Set(v)];
  if (channels.length !== v.length || channels.some((c) => !(ALERT_CHANNELS as readonly unknown[]).includes(c))) {
    throw Errors.invalidRequest();
  }
  return channels as AlertChannel[];
}

export function parsePriceAlert(body: unknown): PriceAlertInput {
  const o = asRecord(body);
  onlyKeys(o, ['channel_code', 'product_id', 'target_price_idr', 'channels']);
  const t = o.target_price_idr;
  if (typeof t !== 'number' || !Number.isSafeInteger(t) || t < 1 || t > 1_000_000_000_000) throw Errors.invalidRequest();
  return {
    channel_code: parseChannelCode(o.channel_code),
    product_id: parseProductId(o.product_id),
    target_price_idr: t,
    channels: o.channels === undefined ? ['push'] : parseAlertChannels(o.channels),
  };
}

export const WishlistErrors = {
  full: (): ApiError => Errors.conflict('wishlist_full', 'Wishlist sudah penuh (maksimal 200 produk).'),
  alertLimit: (): ApiError => Errors.conflict('alert_limit_reached', 'Batas pengingat aktif sudah tercapai (maksimal 50).'),
  alreadyInStock: (): ApiError => Errors.conflict('already_in_stock', 'Produk ini sedang tersedia di outlet tersebut.'),
  channelUnavailable: (): ApiError => ApiError.of(400, 'channel_unavailable', 'Kanal pemberitahuan yang dipilih belum tersedia.'),
  targetNotBelowPrice: (): ApiError => ApiError.of(400, 'invalid_target_price', 'Harga target harus lebih rendah dari harga saat ini.'),
};
