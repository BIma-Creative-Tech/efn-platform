/**
 * Katalog online (docs/04_SERVICE_CONTRACTS.md §10.2, §10.3.2) — tipe kontrak + validator MURNI
 * (tanpa `server-only`, dipakai Route Handler, Server Component, client, dan test).
 *
 * ADR terkait (Proposed): ADR-034 (kanal online lewat BFF), ADR-019 (bagian per kanal/PT), ADR-027 (pajak dari Odoo).
 * Prinsip: harga di katalog hanya INDIKATIF (`price_incl` + label pajak dari BFF/Odoo); tagihan selalu dari quote.
 * Frontend tidak pernah menghitung pajak.
 */

import { parseAttributes, type ProductAttribute } from './attributes';
import { COMPANIES } from './brands';
import { parsePaymentModes, type PaymentMethod } from './payment';

export const CHANNEL_RE = /^[A-Z][A-Z0-9-]{1,15}$/;
export const OUTLET_RE = /^[A-Z0-9][A-Z0-9-]{1,23}$/;
/** ASUMSI kontrak: `image_sha` = hex huruf kecil, SHA-1 (40, checksum lampiran Odoo) atau SHA-256 (64). Kontrak hanya menyebut "alamat berbasis hash". */
export const IMAGE_SHA_RE = /^(?:[a-f0-9]{40}|[a-f0-9]{64})$/;
/** Cursor opak dari BFF: dibatasi karakter aman URL agar tidak bisa menyisipkan parameter lain. */
export const CURSOR_RE = /^[A-Za-z0-9_\-.=~]{1,256}$/;
export const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export const MAX_QUERY_LEN = 64;
export const MAX_PAGE_LIMIT = 50;
export const MAX_STOCK_IDS = 50;
export const IMAGE_SIZES = [512, 1024] as const;
export type ImageSize = (typeof IMAGE_SIZES)[number];

export type StockStatus = 'in_stock' | 'low' | 'out';

export interface CatalogOutlet {
  outlet_code: string;
  public_name: string;
  pickup_enabled: boolean;
  prep_minutes: number;
}

export interface CatalogChannel {
  channel_code: string;
  company_code: string;
  name: string;
  /** Kode brand non-rahasia (§11.3 no. 2); null bila kanal tidak berbrand. */
  brand_code: string | null;
  /** §12.2: metode bayar yang diizinkan kanal. null = BFF belum mengirim field (tidak diketahui; BFF yang memutuskan). */
  payment_modes: PaymentMethod[] | null;
  /** G22 (§14.B B-2): jenis usaha kanal. null = BFF belum mengirim -> dipetakan dari `company_code` (`channelBusinessType`). */
  business_type: 'retail' | 'fnb' | null;
  /** G22 (§14.B B-2 / §14.C): kanal boleh earn/redeem poin. null = BFF belum mengirim (tidak diketahui; BFF yang memutuskan). */
  loyalty: { earn: boolean; redeem: boolean } | null;
  fulfilment: { pickup: boolean; delivery: boolean };
  outlets: CatalogOutlet[];
}

export interface CatalogCategory {
  category_id: number;
  name: string;
  parent_id: number | null;
  sequence: number;
  image_sha: string | null;
}

export interface ProductSummary {
  product_id: number;
  name: string;
  uom: string;
  category_id: number | null;
  image_sha: string | null;
  price_incl: number;
  price_excl: number;
  tax_labels: string[];
  max_qty: number;
  /** §12.3: berat per satuan (kg); null = belum diisi. Informasi saja — batas berat dihitung server di quote. */
  weight_kg: number | null;
  /** G22 (§14.B B-2): atribut `no_variant` (modifier) F&B; `[]` untuk ritel. */
  attributes: ProductAttribute[];
}

export interface ProductDetail extends ProductSummary {
  description: string;
}

export interface ProductPage {
  items: ProductSummary[];
  next_cursor: string | null;
  catalog_synced_at: string | null;
}

export interface StockItem {
  product_id: number;
  status: StockStatus;
}

// ---------- validator input ----------

/** `efn.online.channel.brand_code` (docs/04 §11.3 no. 2). */
export const BRAND_CODE_RE = /^[A-Z][A-Z0-9]{1,9}$/;

export function isBrandCode(v: unknown): v is string {
  return typeof v === 'string' && BRAND_CODE_RE.test(v);
}

/** Path BFF daftar kanal; `?brand=` hanya bila kode valid (null/salah format -> semua kanal). */
export function channelsPath(brandCode: string | null | undefined): string {
  return isBrandCode(brandCode) ? `/v1/catalog/channels?${new URLSearchParams({ brand: brandCode })}` : '/v1/catalog/channels';
}

export function isChannelCode(v: unknown): v is string {
  return typeof v === 'string' && CHANNEL_RE.test(v);
}

export function isOutletCode(v: unknown): v is string {
  return typeof v === 'string' && OUTLET_RE.test(v);
}

export function isImageSha(v: unknown): v is string {
  return typeof v === 'string' && IMAGE_SHA_RE.test(v);
}

export function isUuid(v: unknown): v is string {
  return typeof v === 'string' && UUID_RE.test(v);
}

/** Integer positif aman (id Odoo `product.product.id`, `category_id`). */
export function parsePositiveInt(v: unknown): number | null {
  if (typeof v === 'number') return Number.isSafeInteger(v) && v > 0 ? v : null;
  if (typeof v !== 'string' || !/^[1-9][0-9]{0,15}$/.test(v)) return null;
  const n = Number(v);
  return Number.isSafeInteger(n) ? n : null;
}

/** Teks pencarian: trim, karakter kontrol dibuang, ≤ 64 karakter. Kosong -> null. */
export function sanitizeQuery(v: unknown): string | null {
  if (typeof v !== 'string') return null;
  const cleaned = v.replace(/[\u0000-\u001f\u007f]/g, ' ').replace(/\s+/g, ' ').trim();
  if (!cleaned) return null;
  return Array.from(cleaned).slice(0, MAX_QUERY_LEN).join('');
}

export function parseImageSize(v: unknown): ImageSize {
  return v === '1024' || v === 1024 ? 1024 : 512;
}

export function parseLimit(v: unknown, max = MAX_PAGE_LIMIT, fallback = 24): number {
  const n = parsePositiveInt(v);
  if (n === null) return fallback;
  return Math.min(n, max);
}

/** `ids=1,2,3` -> daftar unik id valid (≤ 50). Satu id tidak valid -> null (tolak seluruh permintaan). */
export function parseIdList(v: unknown, max = MAX_STOCK_IDS): number[] | null {
  if (typeof v !== 'string' || !v) return null;
  const parts = v.split(',');
  if (parts.length > max) return null;
  const out: number[] = [];
  for (const p of parts) {
    const n = parsePositiveInt(p.trim());
    if (n === null) return null;
    if (!out.includes(n)) out.push(n);
  }
  return out;
}

export interface ProductQuery {
  channel: string;
  category?: number | string | null;
  q?: string | null;
  cursor?: string | null;
  limit?: number | string | null;
}

/**
 * Bangun query string BFF `/v1/catalog/products` dari input yang sudah divalidasi. Parameter tidak valid
 * dibuang (bukan diteruskan mentah). `channel` tidak valid -> null.
 */
export function buildProductQuery(input: ProductQuery): string | null {
  if (!isChannelCode(input.channel)) return null;
  const p = new URLSearchParams({ channel: input.channel });
  const category = input.category == null ? null : parsePositiveInt(input.category);
  if (category !== null) p.set('category', String(category));
  const q = sanitizeQuery(input.q);
  if (q) p.set('q', q);
  if (typeof input.cursor === 'string' && CURSOR_RE.test(input.cursor)) p.set('cursor', input.cursor);
  p.set('limit', String(parseLimit(input.limit)));
  return p.toString();
}

/** URL gambar lewat proxy origin sendiri (CSP `img-src 'self'`). Tidak pernah URL Odoo/MinIO. */
export function imageUrl(sha: string | null | undefined, size: ImageSize = 512): string | null {
  return isImageSha(sha) ? `/api/catalog/images/${sha}?size=${size}` : null;
}

// ---------- normalisasi respons (allowlist field; data rusak dibuang) ----------

function isObj(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v);
}

function str(v: unknown, max = 500): string | null {
  return typeof v === 'string' ? v.slice(0, max) : null;
}

function num(v: unknown): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? v : null;
}

function labels(v: unknown): string[] {
  return Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string').map((x) => x.slice(0, 40)).slice(0, 8) : [];
}

export function parseProductSummary(v: unknown): ProductSummary | null {
  if (!isObj(v)) return null;
  const id = parsePositiveInt(v.product_id);
  const name = str(v.name, 200);
  const priceIncl = num(v.price_incl);
  if (id === null || !name || priceIncl === null || priceIncl < 0) return null;
  const maxQty = parsePositiveInt(v.max_qty);
  return {
    product_id: id,
    name,
    uom: str(v.uom, 40) ?? '',
    category_id: parsePositiveInt(v.category_id),
    image_sha: isImageSha(v.image_sha) ? v.image_sha : null,
    price_incl: priceIncl,
    price_excl: num(v.price_excl) ?? priceIncl,
    tax_labels: labels(v.tax_labels),
    max_qty: maxQty === null ? 99 : Math.min(maxQty, 999),
    weight_kg: typeof v.weight_kg === 'number' && Number.isFinite(v.weight_kg) && v.weight_kg >= 0 ? v.weight_kg : null,
    attributes: parseAttributes(v.attributes),
  };
}

export function parseProductDetail(v: unknown): ProductDetail | null {
  const base = parseProductSummary(v);
  if (!base || !isObj(v)) return null;
  // Teks polos ≤ 2.000 karakter; dirender sebagai teks (React meng-escape), bukan HTML.
  return { ...base, description: str(v.description, 2000) ?? '' };
}

export function parseProductPage(v: unknown): ProductPage | null {
  if (!isObj(v) || !Array.isArray(v.items)) return null;
  const items = v.items.map(parseProductSummary).filter((x): x is ProductSummary => x !== null);
  const cursor = typeof v.next_cursor === 'string' && CURSOR_RE.test(v.next_cursor) ? v.next_cursor : null;
  return { items, next_cursor: cursor, catalog_synced_at: str(v.catalog_synced_at, 40) };
}

export function parseChannels(v: unknown): CatalogChannel[] | null {
  if (!isObj(v) || !Array.isArray(v.items)) return null;
  const out: CatalogChannel[] = [];
  for (const c of v.items) {
    if (!isObj(c) || !isChannelCode(c.channel_code)) continue;
    const f = isObj(c.fulfilment) ? c.fulfilment : {};
    const outlets: CatalogOutlet[] = [];
    if (Array.isArray(c.outlets)) {
      for (const o of c.outlets) {
        if (!isObj(o) || !isOutletCode(o.outlet_code)) continue;
        outlets.push({
          outlet_code: o.outlet_code,
          public_name: str(o.public_name, 120) ?? o.outlet_code,
          pickup_enabled: o.pickup_enabled === true,
          prep_minutes: Math.max(0, Math.round(num(o.prep_minutes) ?? 0)),
        });
      }
    }
    out.push({
      channel_code: c.channel_code,
      company_code: str(c.company_code, 16) ?? c.channel_code,
      name: str(c.name, 120) ?? c.channel_code,
      brand_code: isBrandCode(c.brand_code) ? c.brand_code : null,
      payment_modes: parsePaymentModes(c.payment_modes),
      business_type: c.business_type === 'retail' || c.business_type === 'fnb' ? c.business_type : null,
      loyalty: isObj(c.loyalty) ? { earn: c.loyalty.earn === true, redeem: c.loyalty.redeem === true } : null,
      fulfilment: { pickup: f.pickup === true, delivery: f.delivery === true },
      outlets,
    });
  }
  return out;
}

export function parseCategories(v: unknown): CatalogCategory[] | null {
  if (!isObj(v) || !Array.isArray(v.items)) return null;
  const out: CatalogCategory[] = [];
  for (const c of v.items) {
    if (!isObj(c)) continue;
    const id = parsePositiveInt(c.category_id);
    const name = str(c.name, 120);
    if (id === null || !name) continue;
    out.push({
      category_id: id,
      name,
      parent_id: parsePositiveInt(c.parent_id),
      sequence: num(c.sequence) ?? 0,
      image_sha: isImageSha(c.image_sha) ? c.image_sha : null,
    });
  }
  return out.sort((a, b) => a.sequence - b.sequence || a.name.localeCompare(b.name, 'id'));
}

export function parseStock(v: unknown): StockItem[] | null {
  if (!isObj(v) || !Array.isArray(v.items)) return null;
  const out: StockItem[] = [];
  for (const s of v.items) {
    if (!isObj(s)) continue;
    const id = parsePositiveInt(s.product_id);
    if (id === null) continue;
    if (s.status !== 'in_stock' && s.status !== 'low' && s.status !== 'out') continue;
    out.push({ product_id: id, status: s.status });
  }
  return out;
}

export const STOCK_LABEL: Record<StockStatus, string> = {
  in_stock: 'Tersedia',
  low: 'Stok terbatas',
  out: 'Habis',
};

/** Kanal yang boleh tampil di brand ini (brand membatasi PT; kanal tanpa PT dikenal tetap tampil di brand holding). */
export function channelsForBrand(channels: CatalogChannel[], companies: readonly string[]): CatalogChannel[] {
  if (companies.length === 0) return channels;
  return channels.filter((c) => companies.includes(c.company_code));
}

/** Jenis usaha kanal: dari BFF bila ada, kalau tidak dari PT (PB/BCF = F&B). */
export function channelBusinessType(channel: Pick<CatalogChannel, 'business_type' | 'company_code'>): 'retail' | 'fnb' | null {
  if (channel.business_type) return channel.business_type;
  const known = (COMPANIES as Record<string, { businessType: 'retail' | 'fnb' | null } | undefined>)[channel.company_code];
  return known ? known.businessType : null;
}

/**
 * Harga yang ditampilkan di katalog. Ritel: harga termasuk pajak. F&B (docs/04 §14.B B-5): harga SEBELUM pajak +
 * label pajak dari Odoo ("belum termasuk PB1 10%"); rincian pajak final ada di ringkasan keranjang (quote). Frontend
 * tidak menghitung pajak.
 */
export function priceDisplay(
  p: Pick<ProductSummary, 'price_incl' | 'price_excl' | 'tax_labels'>,
  businessType: 'retail' | 'fnb' | null,
): { amount: number; taxNote: string; excl: boolean } {
  const labels = p.tax_labels.join(', ');
  if (businessType === 'fnb') {
    return { amount: p.price_excl, taxNote: `belum termasuk ${labels || 'pajak'}`, excl: true };
  }
  return { amount: p.price_incl, taxNote: labels ? `termasuk ${labels}` : '', excl: false };
}

/** Nama PT untuk pengelompokan keranjang/pesanan (identitas dari lib/brands.ts; kode tak dikenal -> kode itu sendiri). */
export function companyDisplayName(code: string): string {
  const known = (COMPANIES as Record<string, { displayName: string } | undefined>)[code];
  return known ? known.displayName : code;
}
