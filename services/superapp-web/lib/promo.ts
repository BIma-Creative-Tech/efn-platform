/**
 * Promo Super-App web (docs/04 §18.2, §18.4). MURNI (server + client, diuji unit).
 *
 * - `GET /api/promo` -> BFF `GET /v1/promo?surface=web` (publik). Respons di-allowlist di sini.
 * - Slide 1 hero = hero lama (tidak berubah); banner = slide berikutnya. Tanpa banner -> hero lama saja.
 * - Pop-up: satu per kunjungan beranda, menghormati `frequency` (localStorage, hanya id promo + tanggal; bukan data
 *   pribadi).
 */

export type PromoLink = { type: 'brand'; brand: string } | { type: 'product'; channel: string; product_id: number } | { type: 'url'; url: string } | null;

export interface PromoItem {
  id: string;
  title: string;
  subtitle: string;
  cta_label: string;
  link: PromoLink;
  image_sha: string | null;
  frequency?: 'once' | 'daily' | 'every_open';
}

export interface PromoFeed {
  banners: PromoItem[];
  popups: PromoItem[];
}

export const EMPTY_PROMO: PromoFeed = { banners: [], popups: [] };

const ID_RE = /^[A-Z][A-Z0-9]{1,9}-\d{1,10}$/;
const SHA_RE = /^[0-9a-f]{64}$/;
const CODE_RE = /^[A-Z][A-Z0-9]{1,9}$/;
const URL_RE = /^https:\/\/[A-Za-z0-9.-]+(?::\d{1,5})?(?:\/[A-Za-z0-9._~/%-]*)?(?:\?[A-Za-z0-9._~=&%-]*)?$/;

const obj = (v: unknown): Record<string, unknown> | null => (v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : null);
const text = (v: unknown, max: number): string => (typeof v === 'string' ? v.trim().slice(0, max) : '');

export function parsePromoLink(v: unknown): PromoLink {
  const o = obj(v);
  if (!o) return null;
  if (o.type === 'brand' && typeof o.brand === 'string' && CODE_RE.test(o.brand)) return { type: 'brand', brand: o.brand };
  if (o.type === 'product' && typeof o.channel === 'string' && CODE_RE.test(o.channel) && typeof o.product_id === 'number' && Number.isSafeInteger(o.product_id) && o.product_id > 0) {
    return { type: 'product', channel: o.channel, product_id: o.product_id };
  }
  if (o.type === 'url' && typeof o.url === 'string' && o.url.length <= 300 && URL_RE.test(o.url)) return { type: 'url', url: o.url };
  return null;
}

function parseItem(v: unknown, popup: boolean): PromoItem | null {
  const o = obj(v);
  if (!o || typeof o.id !== 'string' || !ID_RE.test(o.id)) return null;
  const title = text(o.title, 80);
  if (!title) return null;
  const item: PromoItem = {
    id: o.id,
    title,
    subtitle: text(o.subtitle, 160),
    cta_label: text(o.cta_label, 24),
    link: parsePromoLink(o.link),
    image_sha: typeof o.image_sha === 'string' && SHA_RE.test(o.image_sha) ? o.image_sha : null,
  };
  if (popup) item.frequency = o.frequency === 'daily' || o.frequency === 'every_open' ? o.frequency : 'once';
  return item;
}

export function parsePromoFeed(v: unknown): PromoFeed | null {
  const o = obj(v);
  if (!o || !Array.isArray(o.banners) || !Array.isArray(o.popups)) return null;
  return {
    banners: o.banners.slice(0, 10).map((x) => parseItem(x, false)).filter((x): x is PromoItem => x !== null),
    popups: o.popups.slice(0, 5).map((x) => parseItem(x, true)).filter((x): x is PromoItem => x !== null),
  };
}

/** Gambar promo lewat origin sendiri (CSP `img-src 'self'`). */
export function promoImageUrl(sha: string | null, kind: 'banner' | 'popup'): string | null {
  return sha && SHA_RE.test(sha) ? `/api/promo/images/${sha}?w=${kind === 'banner' ? 1600 : 1024}` : null;
}

/** Tujuan tautan CTA di web. Brand -> katalog; produk -> detail; url https -> tab baru. */
export function promoHref(link: PromoLink, channelOfBrand: (brand: string) => string | null): { href: string; external: boolean } | null {
  if (!link) return null;
  if (link.type === 'url') return { href: link.url, external: true };
  if (link.type === 'product') return { href: `/belanja/${link.product_id}?${new URLSearchParams({ channel: link.channel })}`, external: false };
  const ch = channelOfBrand(link.brand);
  return { href: ch ? `/belanja?${new URLSearchParams({ channel: ch })}` : '/belanja', external: false };
}

/**
 * Peta kode brand/PT/kanal -> kode kanal online (tautan banner `brand` menyebut kode brand seperti PB, GL, BACHA, atau
 * kode PT seperti BCR). Kanal pertama menang.
 */
export function brandChannelMap(channels: { channel_code: string; company_code: string; brand_code: string | null }[]): Record<string, string> {
  const out: Record<string, string> = {};
  for (const c of channels) {
    for (const k of [c.brand_code, c.company_code, c.channel_code]) if (k && !(k in out)) out[k] = c.channel_code;
  }
  return out;
}

export const POPUP_SEEN_KEY = 'efn.promo.seen.v1';

/** Tanggal lokal `YYYY-MM-DD` (WIB untuk pelanggan Indonesia; cukup tanggal perangkat). */
export function localDay(now: Date): string {
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
}

/** Riwayat tampil pop-up `{ id: tanggal terakhir }` dari localStorage (rusak = kosong). */
export function parseSeen(raw: string | null): Record<string, string> {
  if (!raw) return {};
  try {
    const v = JSON.parse(raw) as unknown;
    const o = obj(v);
    if (!o) return {};
    const out: Record<string, string> = {};
    for (const [k, d] of Object.entries(o).slice(0, 50)) if (ID_RE.test(k) && typeof d === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(d)) out[k] = d;
    return out;
  } catch {
    return {};
  }
}

/** Pop-up pertama (urutan feed) yang boleh tampil hari ini menurut `frequency`. */
export function pickPopup(popups: PromoItem[], seen: Record<string, string>, today: string): PromoItem | null {
  for (const p of popups) {
    const last = seen[p.id];
    if (p.frequency === 'every_open') return p;
    if (p.frequency === 'daily' && last !== today) return p;
    if ((p.frequency ?? 'once') === 'once' && !last) return p;
  }
  return null;
}
