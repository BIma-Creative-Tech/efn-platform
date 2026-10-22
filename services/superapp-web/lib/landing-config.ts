/**
 * Konfigurasi landing page per brand (beranda `/` sebelum masuk e-commerce `/belanja`).
 *
 * Pola: "scroll story" (referensi gaya AURAL/ThreeUI, diadaptasi, tanpa menyalin kode). Panggung menempel di layar
 * menampilkan FOTO PRODUK ASLI dari katalog (Odoo -> BFF -> proxy same-origin) dalam kartu parallax 2.5D; tiap bab
 * = satu kategori katalog. Tidak ada aset 3D/ilustrasi (keputusan user 21 Okt 2026: pakai gambar real).
 *
 * Satu komponen untuk semua brand (R-T14): perbedaan hanya data di sini + CSS custom properties dari `lib/brands.ts`.
 * Brand tanpa entri -> beranda lama (`app/page.tsx`). Saat ini baru Paris Baguette.
 *
 * Copy bab = DRAF (menunggu tim brand/marketing PB). Angka (harga, jumlah pilihan) & foto TIDAK ditulis di sini:
 * diambil dari katalog BFF saat render.
 *
 * File ini murni data/fungsi (diuji di tests/landing.test.ts).
 */
import type { BrandId } from './brands';

export interface LandingChapter {
  /** slug stabil (id anchor `bab-<id>`). */
  id: string;
  /** Label bab (mono, huruf kapital di UI), mis. "Pastry". */
  label: string;
  /** Judul bab (DRAF). */
  title: string;
  /** Satu kalimat pendukung (DRAF). */
  body: string;
  /** Nama kategori katalog (Odoo): sumber foto, statistik, dan tautan bab. Tidak ditemukan -> bab tanpa foto/statistik. */
  categoryName: string;
  /**
   * Nama produk yang diutamakan sebagai foto depan (cocok tanpa peka huruf besar). Tidak ada di katalog -> produk
   * pertama kategori yang punya gambar.
   */
  featured: string[];
  linkLabel: string;
}

export interface LandingConfig {
  /** Satu kata di judul hero (`Brand.hero.title`) yang diberi gaya penekanan; null = tanpa penekanan. */
  emphasis: string | null;
  /** Nama produk untuk kartu foto pembuka (urutan prioritas). Kosong/tidak ada -> foto depan bab pertama. */
  heroFeatured: string[];
  chapters: LandingChapter[];
}

export const LANDINGS: Partial<Record<BrandId, LandingConfig>> = {
  parisbaguette: {
    emphasis: 'segar',
    heroFeatured: ['Pain Au Chocolat', 'Kouign Amann'],
    chapters: [
      {
        id: 'pastry',
        label: 'Pastry',
        title: 'Berlapis, renyah, keemasan.',
        body: 'Lapis demi lapis adonan, dipanggang sampai keemasan. Pilih favorit Anda, bayar di aplikasi, lalu ambil di gerai.',
        categoryName: 'Pastries',
        featured: ['Croissant', 'Pain Au Chocolat', 'Kouign Amann'],
        linkLabel: 'Lihat semua pastry',
      },
      {
        id: 'kue',
        label: 'Kue',
        title: 'Untuk setiap perayaan.',
        body: 'Ulang tahun, arisan, atau sekadar akhir pekan. Pilih kuenya, kami siapkan, Anda tinggal mengambil.',
        categoryName: 'Cakes',
        featured: ['PB Signature Fresh Cream Cake'],
        linkLabel: 'Lihat semua kue',
      },
      {
        id: 'roti',
        label: 'Roti',
        title: 'Roti hangat untuk pagi Anda.',
        body: 'Roti tawar dan roti manis untuk sarapan atau bekal. Pesan dari ponsel, ambil dalam perjalanan.',
        categoryName: 'Breads',
        featured: ['Multigrain Toast Bread'],
        linkLabel: 'Lihat semua roti',
      },
    ],
  },
};

/** Konfigurasi landing brand; tanpa bab -> null (beranda lama). */
export function landingFor(brandId: BrandId): LandingConfig | null {
  const cfg = LANDINGS[brandId];
  return cfg && cfg.chapters.length > 0 ? cfg : null;
}

/** Kategori bernama `name` (tidak peka huruf besar/spasi tepi), atau null. */
export function findCategory<T extends { category_id: number; name: string }>(categories: readonly T[], name: string): T | null {
  const n = name.trim().toLowerCase();
  return categories.find((c) => c.name.trim().toLowerCase() === n) ?? null;
}

/**
 * Tautan katalog untuk kategori bernama `name`. Tidak ditemukan -> `/belanja`.
 * `channel` hanya disertakan bila brand punya > 1 kanal (sama dengan app/belanja/page.tsx).
 */
export function categoryHref(
  categories: ReadonlyArray<{ category_id: number; name: string }>,
  name: string,
  channel: string | null,
): string {
  const found = findCategory(categories, name);
  const p = new URLSearchParams();
  if (channel) p.set('channel', channel);
  if (found) p.set('category', String(found.category_id));
  const s = p.toString();
  return s ? `/belanja?${s}` : '/belanja';
}

/**
 * Pilih hingga `max` produk bergambar untuk kartu foto: urutan `featured` dulu (yang ada di katalog), lalu sisa
 * produk bergambar sesuai urutan katalog. Tanpa duplikat.
 */
export function pickPhotos<T extends { product_id: number; name: string; image_sha: string | null }>(
  items: readonly T[],
  featured: readonly string[],
  max = 3,
): T[] {
  const withImg = items.filter((p) => typeof p.image_sha === 'string' && p.image_sha.length > 0);
  const out: T[] = [];
  const seen = new Set<number>();
  for (const name of featured) {
    const n = name.trim().toLowerCase();
    const hit = withImg.find((p) => p.name.trim().toLowerCase() === n);
    if (hit && !seen.has(hit.product_id)) {
      out.push(hit);
      seen.add(hit.product_id);
    }
    if (out.length >= max) return out;
  }
  for (const p of withImg) {
    if (out.length >= max) break;
    if (!seen.has(p.product_id)) {
      out.push(p);
      seen.add(p.product_id);
    }
  }
  return out;
}

/** Statistik bab dari halaman produk kategori (data katalog asli). */
export interface ChapterStats {
  /** Jumlah produk di halaman yang dimuat; `more` = masih ada halaman berikutnya (tampil "N+"). */
  count: number;
  more: boolean;
  /** Harga terendah (angka mentah sesuai jenis usaha; format di UI). null = tidak ada produk. */
  minPrice: number | null;
  /** Hingga 3 nama produk pertama. */
  names: string[];
}

export function chapterStats(
  items: ReadonlyArray<{ name: string; price_incl: number; price_excl: number }>,
  nextCursor: string | null,
  businessType: 'retail' | 'fnb' | null,
): ChapterStats {
  const prices = items.map((p) => (businessType === 'fnb' ? p.price_excl : p.price_incl)).filter((v) => Number.isFinite(v) && v > 0);
  return {
    count: items.length,
    more: nextCursor !== null,
    minPrice: prices.length > 0 ? Math.min(...prices) : null,
    names: items.slice(0, 3).map((p) => p.name),
  };
}
