/**
 * Konfigurasi landing page per brand (beranda `/` sebelum masuk e-commerce `/belanja`).
 *
 * Pola: "scroll story" (referensi gaya AURAL/ThreeUI, diadaptasi, tanpa menyalin kode). Panggung menempel di layar
 * menampilkan FOTO PRODUK ASLI dari katalog (Odoo -> BFF -> proxy same-origin) dalam kartu parallax 2.5D; tiap bab
 * = satu kategori katalog. Tidak ada aset 3D/ilustrasi (keputusan user 21 Okt 2026: pakai gambar real).
 *
 * Satu komponen untuk semua brand (R-T14): perbedaan hanya data di sini + CSS custom properties dari `lib/brands.ts`.
 * Brand tanpa entri -> beranda lama (`app/page.tsx`). Saat ini Paris Baguette dan Wetzel's Pretzels (G24).
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

/** Satu baris "acara" atau "panduan porsi" di band katering (copy DRAF). */
export interface CateringRow {
  title: string;
  body: string;
}

/**
 * Band katering (fitur dari review parisbaguette.com/catering, parisbaguette.co.id/catering, wetzels.com/menu/catering,
 * 24 Okt 2026): paket katering dari kategori katalog + jenis acara + panduan porsi, CTA ke pre-order berjadwal
 * (`/belanja?mode=preorder`, G23 §15.4) bila ada outlet pre-order, kalau tidak ke kategori biasa. Tidak ada klaim
 * layanan baru (antar, minimal pesanan): katering = pesanan ambil di gerai yang dijadwalkan.
 */
export interface LandingCatering {
  title: string;
  body: string;
  /** Kategori katalog paket katering (foto & tautan). Tidak ada di katalog -> band tanpa rak foto. */
  categoryName: string;
  /** Jenis acara (judul + satu kalimat). */
  occasions: CateringRow[];
  /** Panduan porsi per ukuran grup (opsional). */
  guide: CateringRow[];
  /** Tautan konsultasi katering di situs resmi brand (https), atau null. */
  contactHref: string | null;
  contactLabel: string | null;
}

export interface LandingConfig {
  /** Satu kata di judul hero (`Brand.hero.title`) yang diberi gaya penekanan; null = tanpa penekanan. */
  emphasis: string | null;
  /** Nama produk untuk kartu foto pembuka (urutan prioritas). Kosong/tidak ada -> foto depan bab pertama. */
  heroFeatured: string[];
  chapters: LandingChapter[];
  /** Judul langkah 1 "Cara pesan" (DRAF), mis. "Pilih roti, kue, atau minuman". */
  pickStep: string;
  /** Band katering; null = tidak ditampilkan. */
  catering: LandingCatering | null;
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
    pickStep: 'Pilih roti, kue, atau minuman',
    // parisbaguette.co.id/catering: layanan katering untuk 4 jenis acara, pemesanan lewat kontak. Di Super-App: kue &
    // pastry dipesan sebagai pre-order berjadwal (ambil di gerai).
    catering: {
      title: 'Katering untuk setiap acara.',
      body: 'Roti, pastry, dan kue yang dipanggang segar setiap hari untuk rapat kantor, arisan, hingga hari bahagia. Pre-order kue dari aplikasi dan pilih jadwal ambil.',
      categoryName: 'Cakes',
      occasions: [
        { title: 'Rapat & kantor', body: 'Roti dan pastry segar untuk rapat pagi hingga acara perusahaan.' },
        { title: 'Arisan & komunitas', body: 'Kue dan pastry untuk dinikmati bersama.' },
        { title: 'Pernikahan & lamaran', body: 'Kue perayaan untuk momen paling istimewa.' },
        { title: 'Seminar & pelatihan', body: 'Camilan yang menjaga peserta tetap bersemangat.' },
      ],
      guide: [],
      contactHref: 'https://parisbaguette.co.id/catering/',
      contactLabel: 'Konsultasi katering',
    },
  },
  // G24 (docs/04 §16): Wetzel's Pretzels. Kategori = katalog DEMO dari wetzels.com (scripts/brand-catalog/scrape_wetzels.py).
  wetzels: {
    emphasis: 'hangat',
    heroFeatured: ['Original Pretzel', 'Sinful Cinnamon', 'Cheese Meltdown'],
    chapters: [
      {
        id: 'pretzel',
        label: 'Pretzel',
        title: 'Dipilin tangan, dipanggang segar.',
        body: 'Adonan dipilin satu per satu lalu dipanggang sampai keemasan. Asin, kayu manis, atau keju leleh: pilih favorit Anda.',
        categoryName: 'Pretzels',
        featured: ['Original Pretzel', 'Sinful Cinnamon', 'Jalaroni'],
        linkLabel: 'Lihat semua pretzel',
      },
      {
        id: 'bitz',
        label: 'Bitz & Dogs',
        title: 'Sekali gigit, susah berhenti.',
        body: 'Potongan pretzel mungil dan pretzel dog untuk camilan sambil jalan. Lengkapi dengan saus cocolan favorit.',
        categoryName: 'Bitz',
        featured: ['Cin-A-Bitz', 'Wetzel', 'Pizza'],
        linkLabel: 'Lihat semua bitz',
      },
      {
        id: 'minuman',
        label: 'Minuman',
        title: 'Segarnya lemonade.',
        body: 'Lemonade dan granita dingin, pasangan pas untuk pretzel hangat.',
        categoryName: 'Drinks',
        featured: ['Strawberry Lemonade', 'Lemonade', 'Blue Raspberry Granita'],
        linkLabel: 'Lihat semua minuman',
      },
    ],
    pickStep: 'Pilih pretzel, bitz, atau minuman',
    // wetzels.com/menu/catering: paket & tray ("serves 12"), jenis acara, panduan porsi per ukuran grup.
    catering: {
      title: 'Pretzel bikin pesta makin seru.',
      body: 'Paket pretzel, tray bitz, dan saus cocolan untuk rapat, ulang tahun, acara sekolah, atau nonton bareng. Pre-order dari aplikasi, pilih jadwal ambil, kami siapkan segar.',
      categoryName: 'Catering',
      occasions: [
        { title: 'Rapat kantor', body: 'Camilan praktis yang mudah dibagi.' },
        { title: 'Ulang tahun', body: 'Pretzel dan lemonade untuk pesta anak hingga dewasa.' },
        { title: 'Acara sekolah', body: 'Tray bitz yang mudah disajikan.' },
        { title: 'Nonton bareng', body: 'Pretzel dog dan saus keju untuk pertandingan seru.' },
      ],
      guide: [
        { title: '10–15 orang', body: 'Satu paket pretzel atau tray bitz dengan beberapa saus.' },
        { title: '25–40 orang', body: 'Beberapa paket atau tray dengan saus beragam.' },
        { title: '50+ orang', body: 'Beberapa jenis paket agar tamu bisa memilih sendiri.' },
      ],
      contactHref: null,
      contactLabel: null,
    },
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
