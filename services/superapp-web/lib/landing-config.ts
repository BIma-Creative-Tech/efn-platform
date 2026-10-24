/**
 * Konfigurasi landing page per brand (beranda `/` sebelum masuk e-commerce `/belanja`).
 *
 * Pola: "scroll story" (referensi gaya AURAL/ThreeUI, diadaptasi, tanpa menyalin kode). Panggung menempel di layar
 * menampilkan FOTO PRODUK ASLI dari katalog (Odoo -> BFF -> proxy same-origin) dalam kartu parallax 2.5D; tiap bab
 * = satu kategori katalog. Tidak ada aset 3D/ilustrasi (keputusan user 21 Okt 2026: pakai gambar real).
 *
 * Satu komponen untuk semua brand (R-T14): perbedaan hanya data di sini + CSS custom properties dari `lib/brands.ts`.
 * Brand tanpa entri -> beranda lama (`app/page.tsx`). Saat ini Paris Baguette, Wetzel's Pretzels (G24), Bacha Coffee
 * dan GrandLucky (G25: konsep landing PB, tema & tata letak per brand lewat `palette` + aturan `html[data-brand]`).
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
  /** Label kicker band (default "Katering"; Bacha: "Hadiah"). */
  label?: string;
  /** Teks tombol utama (default "Pre-order katering" / "Lihat menu katering"). */
  ctaLabel?: string;
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

/**
 * Palet aksen landing (hex). Tanpa palet: ink = fill = `theme.accent`, onFill = `theme.chrome` (PB, Wetzel's: aksen
 * kuning terang di latar gelap). Brand terang butuh pemisahan: aksen resmi sebagai BIDANG (tombol, garis) dan versi
 * gelapnya sebagai TEKS agar ≥ 4,5:1 di latar terang (diuji tests/landing.test.ts).
 */
export interface LandingPalette {
  /** Teks beraksen (kata penekanan, tautan bab, nomor langkah) di latar & kartu. */
  ink: string;
  /** Bidang aksen (tombol utama, garis kicker, HUD aktif). */
  fill: string;
  /** Teks di atas `fill`. */
  onFill: string;
}

/** Judul penutup dengan satu kata penekanan (huruf miring/aksen). */
export interface LandingClosing {
  title: string;
  /** Kata di `title` yang diberi penekanan; tidak ditemukan -> tanpa penekanan. */
  emphasis: string;
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
  /** Palet aksen (opsional; lihat LandingPalette). */
  palette?: LandingPalette;
  /** Judul bagian "Cara pesan" (default "Pesan dari ponsel, ambil di gerai."). */
  howTitle?: string;
  /** Judul penutup (default "Lapar? Pesan sekarang, ambil saat siap."). */
  closing?: LandingClosing;
}

/** Palet efektif: tanpa `palette` -> aksen tema untuk teks & bidang, chrome untuk teks di atas bidang (PB, Wetzel's). */
export function resolvePalette(theme: { accent: string; chrome: string }, p?: LandingPalette): LandingPalette {
  return p ?? { ink: theme.accent, fill: theme.accent, onFill: theme.chrome };
}

export const DEFAULT_HOW_TITLE = 'Pesan dari ponsel, ambil di gerai.';
export const DEFAULT_CLOSING: LandingClosing = { title: 'Lapar? Pesan sekarang, ambil saat siap.', emphasis: 'siap' };

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
  // G25: Bacha Coffee (butik BCR). Kategori = katalog butik (data/brand-catalog/bacha, dari bachacoffee.com). Tema mewah
  // bachacoffee.com: latar peach #ffeee2, navy #1a2548, tombol oranye #e07e27 berteks navy (5.12), aksen teks oranye tua
  // #9a4a0c (5.53 di peach, 6.16 di krem) karena oranye resmi hanya 2.59 di peach. Band "Hadiah" = gift set
  // (bachacoffee.com "Personalise your gifts"), bukan katering.
  bacha: {
    emphasis: 'maroko',
    heroFeatured: ['Marrakech Morning Coffee', 'Marrakech Press Coffee Pot in Gold, 800ml', 'Night in Moka Decaffeinated Coffee'],
    chapters: [
      {
        id: 'kopi',
        label: 'Kopi',
        title: 'Seratus persen Arabika.',
        body: 'Ratusan racikan dari kebun pilihan di seluruh dunia, dari single origin hingga kopi beraroma. Pilih favorit Anda, ambil di butik.',
        categoryName: 'Loose Coffee',
        featured: ['Moroccan Sahara Coffee', 'King of Africa Coffee', 'Marrakech Morning Coffee'],
        linkLabel: 'Lihat semua kopi',
      },
      {
        id: 'kemasan',
        label: 'Kemasan',
        title: 'Biji utuh, bubuk, atau kantong seduh.',
        body: 'Racikan favorit dalam kemasan siap bawa pulang, sesuai cara seduh Anda di rumah.',
        categoryName: 'Packed Coffee',
        featured: ['Birthday Wish Coffee - Whole Beans', 'Rwanda Superior Coffee - Ground Beans', 'King of Africa Coffee Bags'],
        linkLabel: 'Lihat semua kemasan',
      },
      {
        id: 'aksesori',
        label: 'Aksesori',
        title: 'Ritual kopi yang anggun.',
        body: 'Teko press berlapis emas, cangkir, dan kaleng kopi berwarna khas Marrakech untuk melengkapi meja Anda.',
        categoryName: 'Accessories',
        featured: ['Marrakech Press Coffee Pot in Gold, 800ml', 'Tokyo Press Coffee Pot in Gold, 1000ml', 'Fleur du Maroc Scented Candle, 140g'],
        linkLabel: 'Lihat semua aksesori',
      },
      {
        id: 'gourmet',
        label: 'Gourmet',
        title: 'Teman manis secangkir kopi.',
        body: 'Biji kopi berlapis cokelat, biskuit, dan nougat untuk dinikmati bersama kopi atau dijadikan bingkisan.',
        categoryName: 'Gourmet',
        featured: ['Assorted Chocolate Covered Coffee Beans', 'Biscuit Assortment', 'Bacha Coffee Jelly'],
        linkLabel: 'Lihat semua gourmet',
      },
    ],
    pickStep: 'Pilih kopi, aksesori, atau hadiah',
    catering: {
      label: 'Hadiah',
      ctaLabel: 'Lihat semua gift set',
      title: 'Hadiah untuk setiap momen.',
      body: 'Kotak hadiah berisi racikan kopi pilihan dan aksesori Bacha Coffee, dikemas dalam kotak khas berwarna cerah. Pesan dari aplikasi, ambil di butik.',
      categoryName: 'Gift Sets',
      occasions: [
        { title: 'Hadiah korporat', body: 'Bingkisan berkelas untuk klien dan rekan kerja.' },
        { title: 'Ulang tahun', body: 'Kopi istimewa untuk orang tersayang.' },
        { title: 'Pernikahan', body: 'Kenang-kenangan yang dinikmati bersama.' },
        { title: 'Hari raya', body: 'Hampers kopi untuk keluarga dan sahabat.' },
      ],
      guide: [],
      contactHref: null,
      contactLabel: null,
    },
    palette: { ink: '#9a4a0c', fill: '#e07e27', onFill: '#1a2548' },
    howTitle: 'Pesan dari ponsel, ambil di butik.',
    closing: { title: 'Kopi favorit Anda, siap diambil di butik.', emphasis: 'butik' },
  },
  // G25: GrandLucky Superstore (PT GL, ritel). Kategori = katalog GL (data/brand-catalog/grandlucky). Warna = logo resmi
  // grandlucky.co.id (biru #39439b, merah #d03020, kuning #f0d000): tombol biru berteks putih (8.57), aksen teks merah
  // tua #c42a1c (5.13 di #f2f3f9, 5.68 di putih), latar kertas situs resmi (paper_@2X.png).
  grandlucky: {
    emphasis: 'segar',
    heroFeatured: ['Anggur Autumn Royal USA /100 gr', 'Pear Nikkori Jepang /100 gr', 'Pollo Ayam Utuh /kg'],
    chapters: [
      {
        id: 'buah',
        label: 'Buah',
        title: 'Buah pilihan, lokal hingga impor.',
        body: 'Stroberi Korea, pir Jepang, hingga anggur Amerika, dipilih setiap hari. Pesan online, ambil di toko.',
        categoryName: 'Buah',
        featured: ['Berry Licious Strawberry Korea 250 gr /pack', 'Anggur Autumn Royal USA /100 gr', 'Pear Nikkori Jepang /100 gr'],
        linkLabel: 'Lihat semua buah',
      },
      {
        id: 'daging',
        label: 'Daging',
        title: 'Daging dan unggas untuk menu keluarga.',
        body: 'Potongan daging sapi dan ayam utuh, ditimbang per gram sesuai kebutuhan dapur Anda.',
        categoryName: 'Daging & Unggas',
        featured: ['GL Prime Beef Shabu Shabu /100 gr', 'Pollo Ayam Utuh /kg'],
        linkLabel: 'Lihat semua daging',
      },
      {
        id: 'sarapan',
        label: 'Sarapan',
        title: 'Mulai pagi dengan lengkap.',
        body: 'Selai, granola, kopi, dan teh untuk sarapan di rumah maupun bekal ke kantor.',
        categoryName: 'Sarapan, Kopi & Teh',
        featured: ['Peanut Butter Creamy/Crunchy 250 gr /jar', 'Verlin Crunchy Granola High Fiber Chia seed 200 gr /pack'],
        linkLabel: 'Lihat semua sarapan',
      },
      {
        id: 'camilan',
        label: 'Camilan',
        title: 'Camilan dan cokelat impor.',
        body: 'Kacang, keripik, dan cokelat pilihan untuk teman nonton, bekal, atau bingkisan.',
        categoryName: 'Camilan & Cokelat',
        featured: ['Lindt Lindor Assorted/Milk Gift Box 168 gr /box', 'Comextra Kacang Mede Sulawesi Goreng 250 gr /pack'],
        linkLabel: 'Lihat semua camilan',
      },
    ],
    pickStep: 'Pilih buah, daging, atau kebutuhan rumah',
    catering: null,
    palette: { ink: '#c42a1c', fill: '#39439b', onFill: '#ffffff' },
    howTitle: 'Belanja dari ponsel, ambil di toko.',
    closing: { title: 'Daftar belanja beres, tinggal ambil di toko.', emphasis: 'ambil' },
  },
};

/**
 * Judul band "Cara pesan" dari mode pesan yang aktif (bukan teks tetap: tiap brand punya mode berbeda, mis. Wetzel's
 * tanpa reservasi). >= 2 mode -> "Ambil di gerai, pre-order, atau reservasi meja."; 1 mode -> "<mode>, bayar di
 * aplikasi."; 0 -> null (band disembunyikan pemanggil).
 */
export function modesHeadline(labels: readonly string[]): string | null {
  const l = labels.map((s) => s.trim()).filter(Boolean);
  if (l.length === 0) return null;
  if (l.length === 1) return `${l[0]}, bayar di aplikasi.`;
  const lower = l.map((s, i) => (i === 0 ? s : s.charAt(0).toLowerCase() + s.slice(1)));
  return `${lower.slice(0, -1).join(', ')}, atau ${lower[lower.length - 1]}.`;
}

/** Pecah judul jadi [sebelum, kata penekanan, sesudah]; penekanan tidak ada -> [judul, '', '']. */
export function splitEmphasis(title: string, emphasis: string): [string, string, string] {
  const e = emphasis.trim();
  const i = e ? title.toLowerCase().indexOf(e.toLowerCase()) : -1;
  if (i < 0) return [title, '', ''];
  return [title.slice(0, i), title.slice(i, i + e.length), title.slice(i + e.length)];
}

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
