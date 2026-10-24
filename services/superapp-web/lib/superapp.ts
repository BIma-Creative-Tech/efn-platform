/**
 * Data & helper MURNI landing Super-App (`app.`, brand holding `efn`) — docs/04 §15.7, docs/design/superapp-concept.md §6.
 * Pola Eraspace (satu aplikasi, banyak brand): deretan brand → panel per brand (foto produk asli) → mode pesan → poin.
 * Copy = DRAF (menunggu Marketing). Angka/foto TIDAK ditulis di sini: dari katalog BFF saat render.
 */
import { BRANDS, type Brand, type BrandId } from './brands';
import type { CatalogChannel } from './catalog';

/** Brand yang ditampilkan di Super-App (urutan tampil). Holding tidak termasuk. */
export const SUPERAPP_BRANDS: readonly Exclude<BrandId, 'efn'>[] = ['parisbaguette', 'bacha', 'grandlucky', 'wetzels'];

/** Copy panel brand (DRAF). `kind` hanya untuk label kategori, bukan klaim layanan. */
export interface BrandPanelCopy {
  kind: string;
  line: string;
}

export const BRAND_PANEL_COPY: Record<Exclude<BrandId, 'efn'>, BrandPanelCopy> = {
  parisbaguette: { kind: 'Bakery café', line: 'Roti, pastry, dan kue untuk pagi hingga perayaan.' },
  bacha: { kind: 'Kopi & butik', line: 'Kopi arabika pilihan, dari butik hingga kafe.' },
  grandlucky: { kind: 'Supermarket', line: 'Belanja segar harian, lokal hingga impor.' },
  wetzels: { kind: 'Pretzel', line: 'Pretzel hangat, bitz, dan lemonade; paket katering untuk acara.' },
};

export interface BrandGroup {
  brand: Brand;
  channels: CatalogChannel[];
}

/** Kelompokkan kanal katalog per brand (kanal milik PT brand). Brand tanpa kanal tetap ikut (panel tanpa foto). */
export function groupChannelsByBrand(channels: readonly CatalogChannel[]): BrandGroup[] {
  return SUPERAPP_BRANDS.map((id) => {
    const brand = BRANDS[id];
    const companies = brand.companies as readonly string[];
    return { brand, channels: channels.filter((c) => companies.includes(c.company_code)) };
  });
}

/**
 * URL situs brand dari host Super-App saat ini: `app.<domain>` → `<subdomain>.<domain>` (https). Host tanpa prefix
 * `app.` (mis. `localhost`) → null (tautan situs brand tidak ditampilkan; panel tetap menaut ke katalog Super-App).
 */
export function brandSiteUrl(host: string | null | undefined, brand: Pick<Brand, 'subdomain'>): string | null {
  if (!host || !brand.subdomain) return null;
  const h = host.split(',')[0]!.trim().toLowerCase().replace(/:\d+$/, '').replace(/\.$/, '');
  if (!/^app\.[a-z0-9.-]+$/.test(h) || !/^[a-z0-9-]+$/.test(brand.subdomain)) return null;
  return `https://${brand.subdomain}.${h.slice('app.'.length)}/`;
}

/** Tautan katalog Super-App untuk kanal pertama brand (`/belanja?channel=`), atau `/belanja`. */
export function brandShopHref(group: BrandGroup): string {
  const ch = group.channels[0];
  return ch ? `/belanja?${new URLSearchParams({ channel: ch.channel_code })}` : '/belanja';
}

/** Jumlah kartu produk di hero 3D (= jumlah slot panggung di components/landing/Hero3D.tsx). */
export const HERO_MAX_ITEMS = 7;

/**
 * Ambil bergiliran (round-robin) dari beberapa daftar: hero 3D menampilkan campuran brand, bukan satu brand saja.
 * Duplikat `key` dibuang; berhenti di `max`.
 */
export function interleave<T>(lists: readonly (readonly T[])[], max: number, key: (v: T) => string | number): T[] {
  const out: T[] = [];
  const seen = new Set<string | number>();
  const longest = Math.max(0, ...lists.map((l) => l.length));
  for (let i = 0; i < longest && out.length < max; i++) {
    for (const l of lists) {
      const v = l[i];
      if (v === undefined) continue;
      const k = key(v);
      if (seen.has(k)) continue;
      seen.add(k);
      out.push(v);
      if (out.length >= max) break;
    }
  }
  return out;
}
