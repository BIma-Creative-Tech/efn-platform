/**
 * Data & helper MURNI landing supermarket (tenant `supermarket`, PT SMD; K18-01, docs/04 §28).
 * Kanal online SMD = pickup di toko + pre-order berjadwal (keputusan user 23 Nov 2026); tanpa dine-in/QR meja/reservasi.
 * ADR terkait (Proposed): ADR-034 (kanal online lewat BFF), ADR-019 (pembayaran), ADR-045 (PT supermarket demo).
 *
 * Copy = DRAF (menunggu Marketing; nama brand placeholder Q-SM1). Angka (harga, jumlah produk, menit siap, toko) TIDAK
 * ditulis di sini: dihitung dari katalog BFF saat render. Nama lorong = nama kategori POS di Odoo
 * (`efn_setup_supermarket_online`, dipakai katalog online `pos_categ_ids`) — tidak ditemukan -> tautan pencarian.
 *
 * Golden ratio: skala tipografi/spasi/tata letak di components/supermarket/supermarket.module.css diturunkan dari PHI.
 */
import type { CatalogCategory, CatalogChannel } from './catalog';
import { categoryHref, findCategory } from './landing-config';

/** φ — dipakai CSS (skala) dan test (memastikan skala konsisten). */
export const PHI = 1.618;

/** Skala modular φ dalam rem: φ^-2 … φ^4 (0.382, 0.618, 1, 1.618, 2.618, 4.236, 6.854). */
export function phiScale(): number[] {
  return [-2, -1, 0, 1, 2, 3, 4].map((n) => Math.round(Math.pow(PHI, n) * 1000) / 1000);
}

export type AisleIconId =
  | 'leaf'
  | 'fruit'
  | 'meat'
  | 'fish'
  | 'bread'
  | 'milk'
  | 'snow'
  | 'jar'
  | 'bottle'
  | 'snack'
  | 'care'
  | 'home'
  | 'star';

export interface Aisle {
  /** slug stabil (anchor & key). */
  id: string;
  /** Nama kategori katalog (POS category Odoo) — harus sama persis. */
  category: string;
  /** Label tampilan. */
  label: string;
  /** Satu kalimat (DRAF). */
  blurb: string;
  icon: AisleIconId;
  /** Dua warna gradasi dekoratif (hex) di balik ikon; teks tidak pernah di atas warna ini saja. */
  tone: readonly [string, string];
}

export const AISLES: readonly Aisle[] = [
  { id: 'sayur', category: 'Sayur', label: 'Sayur', blurb: 'Dipanen pagi, sampai di rak hari yang sama.', icon: 'leaf', tone: ['#cfe8c4', '#8cc49a'] },
  { id: 'buah', category: 'Buah', label: 'Buah', blurb: 'Manis alami, lokal hingga impor.', icon: 'fruit', tone: ['#ffd9c7', '#f4a38a'] },
  { id: 'daging', category: 'Daging', label: 'Daging', blurb: 'Potongan segar, ditimbang sesuai kebutuhan.', icon: 'meat', tone: ['#f7d0d3', '#e3949b'] },
  { id: 'ikan', category: 'Ikan & Seafood', label: 'Ikan & Seafood', blurb: 'Dari laut ke dapur Anda, tetap dingin.', icon: 'fish', tone: ['#c9e9ef', '#86c6d3'] },
  { id: 'bakery', category: 'Bakery', label: 'Bakery', blurb: 'Roti hangat yang dipanggang setiap hari.', icon: 'bread', tone: ['#f6e3c3', '#e2bb7c'] },
  { id: 'dairy', category: 'Dairy & Chilled', label: 'Dairy & Chilled', blurb: 'Susu, keju, dan yoghurt di suhu yang tepat.', icon: 'milk', tone: ['#dfe9f7', '#a7bfe6'] },
  { id: 'frozen', category: 'Frozen', label: 'Frozen', blurb: 'Stok praktis untuk hari-hari sibuk.', icon: 'snow', tone: ['#e2f1f7', '#a9d3e6'] },
  { id: 'dry', category: 'Dry Grocery', label: 'Bahan Pokok', blurb: 'Beras, minyak, bumbu, dan isi dapur lainnya.', icon: 'jar', tone: ['#efe4cf', '#d2b98a'] },
  { id: 'minuman', category: 'Minuman', label: 'Minuman', blurb: 'Pelepas dahaga untuk rumah dan kantor.', icon: 'bottle', tone: ['#fbefc0', '#eccb5c'] },
  { id: 'snack', category: 'Snack', label: 'Camilan', blurb: 'Teman nonton, bekal, dan perjalanan.', icon: 'snack', tone: ['#fde2c4', '#f2b36d'] },
  { id: 'personal', category: 'Personal Care', label: 'Perawatan Diri', blurb: 'Kebutuhan harian untuk seluruh keluarga.', icon: 'care', tone: ['#ece0f4', '#c7a8de'] },
  { id: 'household', category: 'Household', label: 'Rumah Tangga', blurb: 'Rumah bersih, hati tenang.', icon: 'home', tone: ['#d9efe6', '#99cdb7'] },
  { id: 'impor', category: 'Impor Premium', label: 'Impor Premium', blurb: 'Pilihan istimewa dari berbagai negara.', icon: 'star', tone: ['#f3e9cf', '#d6b56a'] },
];

/** Enam lorong unggulan di grid bento (urutan = urutan tampil; tile 1 & 4 tile besar). */
export const FEATURED_AISLE_IDS = ['sayur', 'buah', 'daging', 'ikan', 'bakery', 'dairy'] as const;

export function featuredAisles(): Aisle[] {
  return FEATURED_AISLE_IDS.map((id) => AISLES.find((a) => a.id === id)!).filter(Boolean);
}

/** Lorong untuk produk berkategori `categoryName` (tidak peka huruf besar), atau null. */
export function aisleOfCategory(categoryName: string | null | undefined): Aisle | null {
  if (!categoryName) return null;
  const n = categoryName.trim().toLowerCase();
  return AISLES.find((a) => a.category.toLowerCase() === n) ?? null;
}

/**
 * Tautan lorong: kategori katalog bila ada, kalau tidak pencarian nama lorong (bukan tautan mati).
 * `channel` hanya bila brand punya > 1 kanal (sama dengan landing brand lain).
 */
export function aisleHref(categories: readonly CatalogCategory[], aisle: Pick<Aisle, 'category' | 'label'>, channel: string | null): string {
  if (findCategory(categories, aisle.category)) return categoryHref(categories, aisle.category, channel);
  const p = new URLSearchParams();
  if (channel) p.set('channel', channel);
  p.set('q', aisle.label);
  return `/belanja?${p}`;
}

export interface StoreSummary {
  code: string;
  name: string;
  prepMinutes: number;
  preorder: boolean;
}

/** Toko pickup dari kanal katalog (tanpa duplikat kode, urut nama). */
export function pickupStores(channels: readonly CatalogChannel[]): StoreSummary[] {
  const seen = new Map<string, StoreSummary>();
  for (const c of channels) {
    for (const o of c.outlets) {
      if (!o.pickup_enabled || seen.has(o.outlet_code)) continue;
      seen.set(o.outlet_code, { code: o.outlet_code, name: o.public_name, prepMinutes: o.prep_minutes, preorder: o.slot_enabled === true });
    }
  }
  return [...seen.values()].sort((a, b) => a.name.localeCompare(b.name, 'id'));
}

/** Menit siap tercepat (> 0) dari toko pickup, atau null. */
export function fastestPrep(stores: readonly StoreSummary[]): number | null {
  const m = stores.map((s) => s.prepMinutes).filter((n) => Number.isFinite(n) && n > 0);
  return m.length > 0 ? Math.min(...m) : null;
}
