/**
 * Wishlist, notify-me (restock) dan price alert (G50; docs/04 §28.8). MODUL MURNI (server + client, diuji unit).
 *
 * Kontrak BFF (Bearer member):
 *   GET    /v1/me/wishlist?channel=<kode>                    -> { items: [{ channel_code, product_id, added_at }] }
 *   PUT    /v1/me/wishlist/items { channel_code, product_id } -> 204 (idempoten, <= 200)
 *   DELETE /v1/me/wishlist/items/:channel_code/:product_id    -> 204
 *   GET    /v1/me/restock-alerts                              -> { items: [{ id, channel_code, outlet_code, product_id, channels, created_at, expires_at }] }
 *   POST   /v1/me/restock-alerts { channel_code, outlet_code, product_id, channels[] } -> 201 { id } | 409 already_in_stock (<= 50)
 *   DELETE /v1/me/restock-alerts/:id                          -> 204
 *   GET/POST/DELETE /v1/me/price-alerts { channel_code, product_id, target_price_idr } (integer > 0 dan < harga saat ini, <= 50)
 *
 * ASUMSI (kontrak tidak merinci): bentuk GET price-alerts = `{ items: [{ channel_code, product_id, target_price_idr,
 * created_at?, expires_at? }] }`; DELETE price-alerts memakai body `{ channel_code, product_id }` pada koleksi.
 * Pesan error SELALU dari peta Indonesia di sini, bukan dari teks BFF.
 */
import { CHANNEL_RE, OUTLET_RE } from './catalog';

export const WISHLIST_MAX = 200;
export const RESTOCK_MAX = 50;
export const PRICE_ALERT_MAX = 50;
export const PRICE_MAX_IDR = 1_000_000_000;

export type NotifyChannel = 'push' | 'email' | 'wa_unofficial';
export const NOTIFY_CHANNELS: readonly NotifyChannel[] = ['push', 'email', 'wa_unofficial'] as const;

export const NOTIFY_CHANNEL_LABEL: Record<NotifyChannel, string> = {
  push: 'Notifikasi aplikasi',
  email: 'Email',
  wa_unofficial: 'WhatsApp',
};

export interface WishlistItem {
  channel_code: string;
  product_id: number;
  added_at: string | null;
}
export interface RestockAlert {
  id: string;
  channel_code: string;
  outlet_code: string;
  product_id: number;
  channels: NotifyChannel[];
  created_at: string | null;
  expires_at: string | null;
}
export interface PriceAlert {
  channel_code: string;
  product_id: number;
  target_price_idr: number;
  created_at: string | null;
  expires_at: string | null;
}

function isProductId(v: unknown): v is number {
  return typeof v === 'number' && Number.isSafeInteger(v) && v > 0;
}
const str = (v: unknown): string | null => (typeof v === 'string' && v.length <= 64 ? v : null);

function listOf(data: unknown): unknown[] {
  const items = data && typeof data === 'object' ? (data as { items?: unknown }).items : null;
  return Array.isArray(items) ? items : [];
}

export function parseWishlist(data: unknown): WishlistItem[] {
  const out: WishlistItem[] = [];
  for (const it of listOf(data).slice(0, WISHLIST_MAX)) {
    if (!it || typeof it !== 'object') continue;
    const r = it as Record<string, unknown>;
    if (typeof r.channel_code !== 'string' || !CHANNEL_RE.test(r.channel_code) || !isProductId(r.product_id)) continue;
    out.push({ channel_code: r.channel_code, product_id: r.product_id, added_at: str(r.added_at) });
  }
  return out;
}

export function isNotifyChannel(v: unknown): v is NotifyChannel {
  return v === 'push' || v === 'email' || v === 'wa_unofficial';
}

export function parseRestockAlerts(data: unknown): RestockAlert[] {
  const out: RestockAlert[] = [];
  for (const it of listOf(data).slice(0, RESTOCK_MAX * 2)) {
    if (!it || typeof it !== 'object') continue;
    const r = it as Record<string, unknown>;
    const id = typeof r.id === 'string' || typeof r.id === 'number' ? String(r.id) : null;
    if (
      !id ||
      !/^[A-Za-z0-9_-]{1,64}$/.test(id) ||
      typeof r.channel_code !== 'string' ||
      !CHANNEL_RE.test(r.channel_code) ||
      typeof r.outlet_code !== 'string' ||
      !OUTLET_RE.test(r.outlet_code) ||
      !isProductId(r.product_id)
    ) {
      continue;
    }
    const channels = Array.isArray(r.channels) ? r.channels.filter(isNotifyChannel) : [];
    out.push({
      id,
      channel_code: r.channel_code,
      outlet_code: r.outlet_code,
      product_id: r.product_id,
      channels,
      created_at: str(r.created_at),
      expires_at: str(r.expires_at),
    });
  }
  return out;
}

export function parsePriceAlerts(data: unknown): PriceAlert[] {
  const out: PriceAlert[] = [];
  for (const it of listOf(data).slice(0, PRICE_ALERT_MAX * 2)) {
    if (!it || typeof it !== 'object') continue;
    const r = it as Record<string, unknown>;
    if (
      typeof r.channel_code !== 'string' ||
      !CHANNEL_RE.test(r.channel_code) ||
      !isProductId(r.product_id) ||
      typeof r.target_price_idr !== 'number' ||
      !Number.isSafeInteger(r.target_price_idr) ||
      r.target_price_idr <= 0
    ) {
      continue;
    }
    out.push({
      channel_code: r.channel_code,
      product_id: r.product_id,
      target_price_idr: r.target_price_idr,
      created_at: str(r.created_at),
      expires_at: str(r.expires_at),
    });
  }
  return out;
}

// ---------- validasi input (UX; BFF tetap sumber validasi) ----------

export type Validation<T> = { ok: true; value: T } | { ok: false; code: string };

export function validateWishlistRef(body: unknown): Validation<{ channel_code: string; product_id: number }> {
  const b = body && typeof body === 'object' ? (body as Record<string, unknown>) : null;
  if (!b || typeof b.channel_code !== 'string' || !CHANNEL_RE.test(b.channel_code) || !isProductId(b.product_id)) {
    return { ok: false, code: 'invalid_request' };
  }
  return { ok: true, value: { channel_code: b.channel_code, product_id: b.product_id } };
}

export function validateRestockCreate(
  body: unknown,
): Validation<{ channel_code: string; outlet_code: string; product_id: number; channels: NotifyChannel[] }> {
  const b = body && typeof body === 'object' ? (body as Record<string, unknown>) : null;
  if (
    !b ||
    typeof b.channel_code !== 'string' ||
    !CHANNEL_RE.test(b.channel_code) ||
    typeof b.outlet_code !== 'string' ||
    !OUTLET_RE.test(b.outlet_code) ||
    !isProductId(b.product_id) ||
    !Array.isArray(b.channels) ||
    b.channels.length < 1 ||
    b.channels.length > 3 ||
    !b.channels.every(isNotifyChannel)
  ) {
    return { ok: false, code: 'invalid_request' };
  }
  const channels = [...new Set(b.channels as NotifyChannel[])];
  return { ok: true, value: { channel_code: b.channel_code, outlet_code: b.outlet_code, product_id: b.product_id, channels } };
}

export function validatePriceAlertCreate(body: unknown): Validation<{ channel_code: string; product_id: number; target_price_idr: number }> {
  const b = body && typeof body === 'object' ? (body as Record<string, unknown>) : null;
  if (
    !b ||
    typeof b.channel_code !== 'string' ||
    !CHANNEL_RE.test(b.channel_code) ||
    !isProductId(b.product_id) ||
    typeof b.target_price_idr !== 'number' ||
    !Number.isSafeInteger(b.target_price_idr) ||
    b.target_price_idr <= 0 ||
    b.target_price_idr > PRICE_MAX_IDR
  ) {
    return { ok: false, code: 'invalid_request' };
  }
  return { ok: true, value: { channel_code: b.channel_code, product_id: b.product_id, target_price_idr: b.target_price_idr } };
}

/** Input harga target dari form (teks): hanya digit (pemisah ribuan/spasi dibuang). Kosong/invalid -> null. */
export function parseTargetPrice(raw: string): number | null {
  const digits = raw.replace(/[.\s]/g, '');
  if (!/^[0-9]{1,10}$/.test(digits)) return null;
  const n = Number(digits);
  return Number.isSafeInteger(n) && n > 0 ? n : null;
}

/** Validasi UX harga target: bilangan bulat > 0 dan < harga saat ini. Pesan Indonesia, atau null bila valid. */
export function targetPriceError(raw: string, currentPrice: number, formatRupiah: (n: number) => string): string | null {
  const n = parseTargetPrice(raw);
  if (n === null) return 'Isi harga target dengan angka rupiah, tanpa desimal.';
  if (n >= currentPrice) return `Harga target harus lebih rendah dari harga saat ini (${formatRupiah(currentPrice)}).`;
  return null;
}

// ---------- pesan error ----------

/** Kode error yang boleh diteruskan Route Handler ke browser (selain itu -> `invalid_request`). */
export const ENGAGE_ERROR_CODES: readonly string[] = [
  'invalid_request',
  'already_in_stock',
  'limit_reached',
  'wishlist_limit',
  'restock_limit',
  'alert_limit',
  'price_not_lower',
  'channel_unavailable',
  'consent_required',
  'marketing_consent_required',
  'rate_limited',
  'not_found',
  'forbidden',
  'service_unavailable',
  'upstream_error',
  'feature_unavailable',
];

const LIMIT_CODES = ['limit_reached', 'wishlist_limit', 'restock_limit', 'alert_limit'];

export const ENGAGE_MESSAGES: Record<string, string> = {
  invalid_request: 'Permintaan tidak valid. Periksa isian Anda lalu coba lagi.',
  already_in_stock: 'Produk ini sudah tersedia di toko tersebut, jadi tidak perlu pengingat.',
  limit_reached: 'Batas jumlah tercapai. Hapus salah satu item lama lalu coba lagi.',
  wishlist_limit: `Wishlist penuh (maksimal ${WISHLIST_MAX} produk). Hapus beberapa produk lalu coba lagi.`,
  restock_limit: `Pengingat stok penuh (maksimal ${RESTOCK_MAX} aktif). Batalkan salah satu lalu coba lagi.`,
  alert_limit: `Pengingat harga penuh (maksimal ${PRICE_ALERT_MAX} aktif). Hapus salah satu lalu coba lagi.`,
  price_not_lower: 'Harga target harus lebih rendah dari harga saat ini.',
  channel_unavailable: 'Kanal notifikasi ini belum bisa dipakai untuk akun Anda.',
  consent_required: 'Kanal ini membutuhkan persetujuan Anda di Pengaturan Privasi.',
  marketing_consent_required: 'Kanal ini membutuhkan persetujuan info promo di Pengaturan Privasi.',
  rate_limited: 'Terlalu banyak permintaan. Coba lagi beberapa saat lagi.',
  not_found: 'Data tidak ditemukan.',
  forbidden: 'Permintaan ditolak.',
  service_unavailable: 'Layanan sedang tidak tersedia. Silakan coba lagi.',
  upstream_error: 'Terjadi kendala. Silakan coba lagi beberapa saat lagi.',
  feature_unavailable: 'Fitur ini sedang tidak tersedia.',
  network_error: 'Koneksi terputus. Periksa internet Anda lalu coba lagi.',
};

export function engageMessage(code: string | null | undefined): string {
  return (code && ENGAGE_MESSAGES[code]) || ENGAGE_MESSAGES.upstream_error!;
}

export function isLimitCode(code: string): boolean {
  return LIMIT_CODES.includes(code);
}

/**
 * Petakan kegagalan BFF (status + kode) ke kode klien yang aman. 409/422 dengan kode batas diteruskan; 409/422 tanpa
 * kode dikenal -> `limit_reached` (asumsi: 409 selain `already_in_stock` = batas jumlah; BELUM diverifikasi ke BFF).
 */
export function mapEngageCode(status: number, code: string): string {
  if (ENGAGE_ERROR_CODES.includes(code)) return code;
  if (status === 404) return 'feature_unavailable';
  if (status === 403) return 'forbidden';
  if (status === 429) return 'rate_limited';
  if (status === 409) return 'limit_reached';
  if (status === 400 || status === 422) return 'invalid_request';
  return status === 503 ? 'service_unavailable' : 'upstream_error';
}

/** Fitur dianggap TIDAK tersedia (flag mati di BFF) bila endpoint menjawab 404/501 `feature_unavailable`. */
export function isFeatureOff(status: number, code: string): boolean {
  return code === 'feature_unavailable' || status === 404 || status === 501;
}
