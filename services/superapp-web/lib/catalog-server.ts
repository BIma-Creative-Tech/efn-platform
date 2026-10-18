/**
 * Data katalog untuk Server Components (`app/belanja/**`, `app/keranjang`). SERVER-SIDE SAJA.
 * Memanggil BFF `/v1/catalog/*` (docs/04 §10.3.2) dan meneruskan `x-forwarded-for` permintaan asli supaya rate limit
 * per IP di BFF berlaku per pengunjung, bukan per server Next.js.
 */
import 'server-only';
import { headers } from 'next/headers';
import { bffFetch } from './bff';
import type { Brand } from './brands';
import {
  buildProductQuery,
  channelsForBrand,
  channelsPath,
  isChannelCode,
  parseCategories,
  parseChannels,
  parseProductDetail,
  parseProductPage,
  type CatalogCategory,
  type CatalogChannel,
  type ProductDetail,
  type ProductPage,
  type ProductQuery,
} from './catalog';

export type Loaded<T> = { ok: true; data: T } | { ok: false; unavailable: boolean; notFound: boolean };

async function load<T>(path: string, parse: (v: unknown) => T | null): Promise<Loaded<T>> {
  const incoming = await headers();
  const r = await bffFetch<unknown>(path, { incoming });
  if (!r.ok) {
    return { ok: false, unavailable: r.status === 503 && r.code === 'online_unavailable', notFound: r.status === 404 };
  }
  const data = parse(r.data);
  return data === null ? { ok: false, unavailable: false, notFound: false } : { ok: true, data };
}

export async function loadChannels(brand: Brand): Promise<Loaded<CatalogChannel[]>> {
  const r = await load(channelsPath(brand.brandCode), parseChannels);
  if (!r.ok) return r;
  return { ok: true, data: channelsForBrand(r.data, brand.companies) };
}

/** Kanal aktif: `?channel=` bila valid & tersedia di brand ini, selain itu kanal pertama. */
export function pickChannel(channels: CatalogChannel[], requested: unknown): CatalogChannel | null {
  if (isChannelCode(requested)) {
    const found = channels.find((c) => c.channel_code === requested);
    if (found) return found;
  }
  return channels[0] ?? null;
}

export function loadCategories(channel: string): Promise<Loaded<CatalogCategory[]>> {
  return load(`/v1/catalog/categories?${new URLSearchParams({ channel })}`, parseCategories);
}

export function loadProducts(query: ProductQuery): Promise<Loaded<ProductPage>> {
  const qs = buildProductQuery(query);
  if (!qs) return Promise.resolve({ ok: false, unavailable: false, notFound: true });
  return load(`/v1/catalog/products?${qs}`, parseProductPage);
}

export function loadProduct(productId: number, channel: string): Promise<Loaded<ProductDetail>> {
  return load(`/v1/catalog/products/${productId}?${new URLSearchParams({ channel })}`, parseProductDetail);
}
