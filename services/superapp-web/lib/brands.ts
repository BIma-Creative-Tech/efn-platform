/**
 * Konfigurasi brand multi-tenant (host -> brand).
 *
 * ADR terkait: ADR multi-tenant by domain & unified cart lintas PT (status Proposed).
 * Tenant: `efn` (holding), `grandlucky` (PT GL), `parisbaguette` (PT PB), `bacha` (BCR butik + BCF kafe, satu domain),
 * `wetzels` (PT WTZ, G24 docs/04 §16).
 * Token visual: docs/design/brand-tokens.md.
 * Risiko dimitigasi: R-T14 (duplikasi komponen per brand) — tema hanya lewat CSS custom properties.
 *
 * File ini dipakai oleh proxy.ts (Next 16: runtime Node.js; dulu middleware Edge), jadi HARUS murni data/fungsi tanpa
 * import Node.js. Brand tidak dikenal -> brand default (`efn`), bukan error.
 */

export type BrandId = 'efn' | 'grandlucky' | 'parisbaguette' | 'bacha' | 'wetzels';

/** Kode company sesuai kontrak `res.company.efn_brand_code` (docs/04_SERVICE_CONTRACTS.md §1). */
export type CompanyCode = 'EFN' | 'GL' | 'PB' | 'BCR' | 'BCF' | 'WTZ' | 'FARM';

/**
 * Identitas PT saja. Pajak (label, tarif, inklusif/eksklusif) TIDAK di sini — configurable,
 * dibaca dari BFF/Odoo per baris (label `tax_labels`, `tax_groups` — docs/04 §10.3; frontend tidak menghitung pajak).
 */
export interface CompanyInfo {
  code: CompanyCode;
  /** Nama tampilan. Nama badan hukum final menunggu konfirmasi klien. */
  displayName: string;
  /**
   * Jenis usaha (docs/04 §13.5): `retail` = PT PPN (faktur atas nama dimungkinkan), `fnb` = PT PB1 (tidak).
   * null = bukan kanal belanja online / belum ditetapkan -> diperlakukan tidak memenuhi syarat.
   */
  businessType: 'retail' | 'fnb' | null;
  /**
   * Layanan meja (QR meja G22 + reservasi G23) — hanya PT F&B dengan area makan (`FNB_QR_COMPANIES`/`BOOKING_COMPANIES`
   * BFF = PB,BCF). WTZ (kios takeaway, G24) F&B tetapi tanpa layanan meja.
   */
  tableService: boolean;
}

export const COMPANIES: Record<CompanyCode, CompanyInfo> = {
  EFN: { code: 'EFN', displayName: 'Holding EFN', businessType: null, tableService: false },
  GL: { code: 'GL', displayName: 'PT Grand Lucky', businessType: 'retail', tableService: false },
  PB: { code: 'PB', displayName: 'PT Paris Baguette', businessType: 'fnb', tableService: true },
  BCR: { code: 'BCR', displayName: 'Bacha Coffee Retail', businessType: 'retail', tableService: false },
  BCF: { code: 'BCF', displayName: 'Bacha Coffee F&B', businessType: 'fnb', tableService: true },
  // G24 (docs/04 §16): nama badan hukum final menunggu konfirmasi klien.
  WTZ: { code: 'WTZ', displayName: "PT Wetzel's Pretzels", businessType: 'fnb', tableService: false },
  FARM: { code: 'FARM', displayName: 'FARM', businessType: null, tableService: false },
};

/** True bila semua kode PT adalah PT PPN (GL/BCR). Daftar kosong atau kode tak dikenal -> false. */
export function allPpnCompanies(codes: readonly string[]): boolean {
  if (codes.length === 0) return false;
  return codes.every((c) => {
    if (!Object.prototype.hasOwnProperty.call(COMPANIES, c)) return false;
    return COMPANIES[c as CompanyCode].businessType === 'retail';
  });
}

/**
 * Peta `brand_code` kanal (non-rahasia, docs/04 §12.1) -> kode kanal/PT. Kode kanal = kode PT.
 * Bacha = DUA kanal (BCR retail + BCF F&B) di satu domain. Dipakai tenant brand (`grandlucky`/`parisbaguette`/`bacha`)
 * sejak keputusan user 20 Okt 2026 (tenant per brand; L-33 diperbarui). Holding `efn` tetap `brandCode: null`.
 */
export const BRAND_CODE_CHANNELS: Readonly<Record<string, readonly CompanyCode[]>> = {
  GL: ['GL'],
  PB: ['PB'],
  BACHA: ['BCR', 'BCF'],
  WTZ: ['WTZ'],
};

/** Kanal/PT milik sebuah `brand_code`; kode tak dikenal -> daftar kosong. */
export function channelsOfBrandCode(brandCode: string | null | undefined): readonly CompanyCode[] {
  if (typeof brandCode !== 'string' || !Object.prototype.hasOwnProperty.call(BRAND_CODE_CHANNELS, brandCode)) return [];
  return BRAND_CODE_CHANNELS[brandCode]!;
}

/**
 * Token tema per brand. Semua nilai = KONSTANTA di kode (bukan input user) dan tetap divalidasi oleh `themeToCssVars`
 * sebelum masuk atribut `style` (defense in depth). Kontras teks/latar dihitung di docs/design/brand-tokens.md (≥ 4.5:1).
 */
export interface BrandTheme {
  /**
   * Skema warna seluruh situs brand. `dark` = latar gelap (mis. PB: navy resmi #002663, teks putih) -> atribut
   * `data-scheme="dark"` di <html> mengganti warna status (error/sukses/stok) & kontrol bawaan browser (globals.css).
   */
  scheme: 'light' | 'dark';
  /** Latar header/footer ("bingkai" situs) + teks di atasnya. Brand terang: sama dengan primary. */
  chrome: string;
  chromeContrast: string;
  /** Warna aksi: tombol utama, tautan, chip aktif, indikator. */
  primary: string;
  /** Teks di atas `primary` (tombol). */
  primaryContrast: string;
  /** Aksen dekoratif (garis header, badge). Bukan untuk teks di atas latar terang (kontras < 4.5:1 di beberapa brand). */
  accent: string;
  /** Latar lembut hero/promo; teks di atasnya = `text`. */
  accentSoft: string;
  /** Warna indikator fokus keyboard (≥ 3:1 terhadap latar, WCAG 1.4.11 / 2.4.13). */
  focus: string;
  surface: string;
  surfaceAlt: string;
  text: string;
  textMuted: string;
  border: string;
  /**
   * Peran font (semua self-host same-origin di public/fonts, @font-face di app/fonts.css; CSP `font-src 'self'`):
   * `fontDisplay` judul besar (h1, hero, judul penutup), `fontHeading` judul bagian (h2/h3), `fontBody` teks,
   * `fontLabel` label kecil berhuruf kapital (kicker, kepala tabel, h3 skema gelap, harga di kartu landing).
   * Sumber per brand: komentar font di atas BRANDS + docs/design/brand-tokens.md §Font.
   */
  fontDisplay: string;
  fontBody: string;
  fontHeading: string;
  fontLabel: string;
  /** Menu header, tombol, dan logotipe teks. */
  fontNav: string;
  headingWeight: string;
  headingLetterSpacing: string;
  /** Huruf judul (Bacha & Wetzel's resmi: kapital). */
  headingTransform: 'none' | 'uppercase';
  /** Huruf menu header & tombol (Bacha & Wetzel's resmi: kapital). */
  navTransform: 'none' | 'uppercase';
  /** Tebal menu & tombol. Font satu bobot (PB Signature, Londrina) = '400' agar browser tidak membuat tebal palsu. */
  uiWeight: string;
  /** Tebal logotipe teks di header. */
  logoWeight: string;
  logoTransform: 'none' | 'uppercase';
  logoLetterSpacing: string;
  radius: string;
  cardShadow: string;
}

/** Copy beranda per brand (DRAF — menunggu tinjauan tim brand/marketing). */
export interface BrandHero {
  eyebrow: string;
  title: string;
  lead: string;
}

export interface Brand {
  id: BrandId;
  name: string;
  tagline: string;
  /** Teks logo (dipakai bila tanpa logo grafis, atau di samping logo bertipe `mark`). */
  logoText: string;
  /**
   * Path logo resmi same-origin (`/brands/<id>/<nama>.svg|png` di public/). null = pakai `logoText`.
   * Sumber file dicatat di public/brands/README.md (file dari tim brand / pemegang hak brand Indonesia).
   */
  logoSrc: string | null;
  /** `wordmark` = logo berisi nama brand (tampil sendiri); `mark` = lencana/monogram (tampil + `logoText`). */
  logoKind: 'wordmark' | 'mark';
  /** Petunjuk mode pre-order (lib/order-modes.ts); tidak ada -> petunjuk bawaan per jenis usaha. */
  preorderHint?: string;
  /** Prefix subdomain untuk pencocokan di domain apa pun (mis. `grandlucky.` -> grandlucky). null = tidak ada. */
  subdomain: string | null;
  /** Prefix subdomain lama yang masih diterima (mis. `mart.` -> grandlucky) agar tautan/bookmark dev lama tidak putus. */
  subdomainAliases: string[];
  /** Host eksplisit (dev/staging). */
  hosts: string[];
  /** Company yang katalognya ditampilkan di brand ini. */
  companies: CompanyCode[];
  /**
   * Kode brand katalog (non-rahasia; docs/04 §11.3 no. 4) = `efn.online.channel.brand_code` (mis. `GL`, `BACHA`, `PB`).
   * Dipakai sebagai `?brand=` saat memuat kanal di domain brand. null = semua kanal (brand holding).
   * Pembatasan UI saja — BFF tidak mempercayai domain.
   * L-33 (diperbarui 20 Okt 2026): user memutuskan tenant per brand, jadi tenant brand memakai kodenya;
   * holding `efn` tetap null. Kanal tanpa `brand_code` di Odoo tidak tampil di domain brand.
   */
  brandCode: string | null;
  hero: BrandHero;
  theme: BrandTheme;
}

export const DEFAULT_BRAND_ID: BrandId = 'efn';

const SANS_SYSTEM = "system-ui, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif";
const SERIF_SYSTEM = "Georgia, 'Times New Roman', Times, serif";
const MONO_SYSTEM = "ui-monospace, 'SFMono-Regular', Menlo, Consolas, monospace";

/*
 * Font per brand (diukur dari situs resmi lewat getComputedStyle, 24 Okt 2026; detail docs/design/brand-tokens.md §Font).
 * Semua file self-host di public/fonts/<brand>/ (@font-face: app/fonts.css). Font proprietary (PB Signature,
 * PB Special Elite, ITC Quorum) disalin dari situs brand atas keputusan user 24 Okt 2026 (EFN pemegang hak brand di
 * Indonesia; risiko lisensi ditanggung pemilik proyek) — ganti dengan file berlisensi resmi dari tim brand bila ada.
 *   PB (parisbaguette.com): h1 & menu "PB Signature Bold Display", h2/h3 "PB Special Elite", teks Roboto.
 *   Bacha (bachacoffee.com): judul ITC Quorum 500/700 kapital, teks & menu Lora.
 *   Wetzel's (wetzels.com): judul, menu & tombol Londrina Solid kapital (OFL), teks Roboto (OFL).
 *   GrandLucky (grandlucky.co.id): Lato (OFL) untuk semua peran.
 */
const PB_DISPLAY = `'PB Signature Display', 'Fraunces Variable', ${SERIF_SYSTEM}`;
const PB_SPECIAL = `'PB Special Elite', 'Courier New', ${MONO_SYSTEM}`;
const QUORUM = `'ITC Quorum', ${SERIF_SYSTEM}`;
const LORA = `'Bacha Lora', ${SERIF_SYSTEM}`;
const ROBOTO = `Roboto, ${SANS_SYSTEM}`;
const LATO = `Lato, ${SANS_SYSTEM}`;
const LONDRINA = `'Londrina Solid', 'Arial Black', ${SANS_SYSTEM}`;

export const BRANDS: Record<BrandId, Brand> = {
  efn: {
    id: 'efn',
    name: 'EFN Super-App',
    tagline: "Belanja GrandLucky, Paris Baguette, Bacha Coffee, dan Wetzel's Pretzels dalam satu aplikasi.",
    logoText: 'EFN',
    logoSrc: null,
    logoKind: 'wordmark',
    subdomain: 'app',
    subdomainAliases: [],
    hosts: ['app.efn.local', 'efn.local', 'localhost'],
    companies: ['GL', 'PB', 'BCR', 'BCF', 'WTZ'],
    brandCode: null,
    hero: {
      eyebrow: 'Erajaya Food & Nourishment',
      title: 'Semua brand EFN, satu akun',
      lead: 'Belanja di GrandLucky Superstore, pesan roti Paris Baguette, dan kopi Bacha Coffee. Satu akun, satu saldo poin.',
    },
    // Skema GELAP. Palet mengikuti logo resmi PT Era Boga Nusantara (EBN = Erajaya Food & Nourishment) di
    // erajaya.com/line-of-verticals (diukur dari file logo 23 Okt 2026: emas #dfb422, teal #0dab9d) di atas hijau tua
    // #062e2a; sama dengan landing Super-App (components/landing/superapp.module.css) agar header, footer, dan semua
    // halaman satu bahasa visual. Font = Fraunces/Geist self-host (OFL).
    // Kontras WCAG (diuji tests/brands.test.ts + tests/portfolio.test.ts): putih/#062e2a 14.67, #bfe0da/#0a3d38 8.57,
    // emas/#0a3d38 6.15, #062e2a di atas emas 7.46.
    theme: {
      scheme: 'dark',
      chrome: '#062e2a',
      chromeContrast: '#ffffff',
      primary: '#dfb422',
      primaryContrast: '#062e2a',
      accent: '#dfb422',
      accentSoft: '#08362f',
      focus: '#dfb422',
      surface: '#0a3d38',
      surfaceAlt: '#062e2a',
      text: '#ffffff',
      textMuted: '#bfe0da',
      border: '#2a6b63',
      fontDisplay: `'Fraunces Variable', ${SERIF_SYSTEM}`,
      fontBody: `'Geist Variable', Inter, ${SANS_SYSTEM}`,
      fontHeading: `'Fraunces Variable', ${SERIF_SYSTEM}`,
      fontLabel: `'Geist Mono Variable', ${MONO_SYSTEM}`,
      fontNav: `'Geist Variable', Inter, ${SANS_SYSTEM}`,
      headingWeight: '500',
      headingLetterSpacing: '-0.02em',
      headingTransform: 'none',
      navTransform: 'none',
      uiWeight: '600',
      logoWeight: '800',
      logoTransform: 'uppercase',
      logoLetterSpacing: '0.08em',
      radius: '10px',
      cardShadow: '0 12px 32px rgba(0, 18, 16, 0.35)',
    },
  },
  grandlucky: {
    id: 'grandlucky',
    name: 'GrandLucky Superstore',
    tagline: 'Supermarket premium: buah, sayur, dan seafood segar, lokal maupun impor.',
    logoText: 'GrandLucky',
    // Logo resmi grandlucky.co.id (wp-content/uploads/2015/07/GrandLucky-Logo.png, biru/merah/kuning) -> header PUTIH
    // seperti situs resmi (globals.css `html[data-brand='grandlucky']`), G25.
    logoSrc: '/brands/grandlucky/logo.png',
    logoKind: 'wordmark',
    subdomain: 'grandlucky',
    subdomainAliases: ['mart'],
    hosts: ['grandlucky.efn.local'],
    companies: ['GL'],
    brandCode: 'GL',
    hero: {
      eyebrow: 'Supermarket premium sejak 1992',
      title: 'Segar setiap hari, lokal hingga impor',
      lead: 'Buah, sayur, daging, dan seafood pilihan. Pesan online, bayar di aplikasi, lalu ambil di toko GrandLucky.',
    },
    theme: {
      scheme: 'light',
      chrome: '#39439b',
      chromeContrast: '#ffffff',
      primary: '#39439b',
      primaryContrast: '#ffffff',
      accent: '#9aa1dc', // turunan terang #39439b (dekoratif saja); warna promo resmi menunggu tim brand GrandLucky
      accentSoft: '#eef0fa',
      focus: '#39439b',
      surface: '#ffffff',
      surfaceAlt: '#f2f3f9',
      text: '#1c2033',
      textMuted: '#4d5268',
      border: '#d3d6e8',
      fontDisplay: LATO,
      fontBody: LATO,
      fontHeading: LATO,
      fontLabel: LATO,
      fontNav: LATO,
      headingWeight: '800',
      headingLetterSpacing: 'normal',
      headingTransform: 'none',
      navTransform: 'none',
      uiWeight: '700',
      logoWeight: '800',
      logoTransform: 'none',
      logoLetterSpacing: 'normal',
      radius: '8px',
      cardShadow: 'none',
    },
  },
  parisbaguette: {
    id: 'parisbaguette',
    name: 'Paris Baguette',
    tagline: 'Bakery café: roti, kue, dan minuman yang dibuat segar setiap hari.',
    logoText: 'Paris Baguette',
    // Logo resmi dari user (Downloads/PB Logo, 24 Okt 2026): lencana bulat navy berhuruf PB -> tampil + logoText.
    logoSrc: '/brands/parisbaguette/logo.png',
    logoKind: 'mark',
    subdomain: 'parisbaguette',
    subdomainAliases: ['kitchen'],
    hosts: ['parisbaguette.efn.local'],
    companies: ['PB'],
    brandCode: 'PB',
    hero: {
      eyebrow: 'Bakery café',
      title: 'Roti dan kue segar, setiap hari',
      lead: 'Pesan roti, kue, dan minuman favorit, lalu ambil di gerai Paris Baguette. Sedang di gerai? Pindai QR di meja untuk memesan.',
    },
    // Warna resmi situs parisbaguette.co.id (dicek 21 Okt 2026): navy #002663 (header/footer/judul), putih, biru muda
    // #aebcd3 (teks sekunder), kuning #fcb134 (aksen); tombol radius 10px + garis 2px. Skema GELAP atas keputusan user
    // 21 Okt 2026: seluruh situs PB berlatar navy resmi, teks putih, aksi kuning. Font = font resmi parisbaguette.com
    // (PB Signature Display / PB Special Elite / Roboto, self-host; lihat komentar font di atas).
    // Kontras WCAG (diuji tests/brands.test.ts): putih/#002663 14.41, #aebcd3/#002663 7.50, kuning/#002663 7.88,
    // navy di atas kuning 7.88, putih/#0c3170 (kartu) 12.43, #aebcd3/#0c3170 6.47, kuning/#0c3170 6.80.
    theme: {
      scheme: 'dark',
      chrome: '#002663',
      chromeContrast: '#ffffff',
      primary: '#fcb134',
      primaryContrast: '#002663',
      accent: '#fcb134',
      accentSoft: '#0a2d6b',
      focus: '#fcb134',
      surface: '#0c3170',
      surfaceAlt: '#002663',
      text: '#ffffff',
      textMuted: '#aebcd3',
      border: '#34548c',
      fontDisplay: PB_DISPLAY,
      fontBody: ROBOTO,
      fontHeading: PB_SPECIAL,
      fontLabel: PB_SPECIAL,
      fontNav: PB_DISPLAY,
      headingWeight: '400',
      headingLetterSpacing: 'normal',
      headingTransform: 'none',
      navTransform: 'none',
      uiWeight: '400',
      logoWeight: '400',
      logoTransform: 'uppercase',
      logoLetterSpacing: '0.04em',
      radius: '10px',
      cardShadow: '0 12px 32px rgba(0, 10, 40, 0.35)',
    },
  },
  bacha: {
    id: 'bacha',
    name: 'Bacha Coffee',
    tagline: 'Rumah kopi Maroko sejak 1910.',
    logoText: 'Bacha Coffee',
    // Lencana unta resmi bachacoffee.com (App_Themes/BachaCore/images/icons/logo-bacha.svg, krem #f6eae2) di header navy,
    // tampil bersama nama brand (G25).
    logoSrc: '/brands/bacha/logo.svg',
    logoKind: 'mark',
    subdomain: 'bacha',
    subdomainAliases: [],
    hosts: ['bacha.efn.local'],
    companies: ['BCR', 'BCF'],
    brandCode: 'BACHA',
    hero: {
      eyebrow: 'Marrakech · sejak 1910',
      title: 'Kopi pilihan dari rumah kopi bersejarah Maroko',
      lead: 'Belanja kopi dan merchandise dari butik, atau pesan dari kafe Bacha Coffee. Butik dan kafe dikelola dua PT berbeda, jadi rinciannya ditampilkan terpisah di keranjang.',
    },
    // Warna & font = bachacoffee.com/en (diukur getComputedStyle 24 Okt 2026): latar halaman #ffeee2, navy #1a2548
    // (header, teks), oranye #e07e27 (tombol, aksen judul kecil); judul ITC Quorum kapital, teks Lora.
    // Kontras (tests/brands.test.ts): navy/#ffeee2 13.25, #585c6e/#ffeee2 5.86, oranye/navy 5.12 (garis aksen header).
    theme: {
      scheme: 'light',
      chrome: '#1a2548',
      chromeContrast: '#fffef1',
      primary: '#1a2548',
      primaryContrast: '#fffef1',
      accent: '#e07e27',
      accentSoft: '#ffe7d5',
      focus: '#1a2548',
      surface: '#fffef1',
      surfaceAlt: '#ffeee2',
      text: '#1a2548',
      textMuted: '#585c6e',
      border: '#e2d6b8',
      fontDisplay: QUORUM,
      fontBody: LORA,
      fontHeading: QUORUM,
      fontLabel: QUORUM,
      fontNav: LORA,
      headingWeight: '500',
      headingLetterSpacing: '0.03em',
      headingTransform: 'uppercase',
      navTransform: 'uppercase',
      uiWeight: '400',
      logoWeight: '500',
      logoTransform: 'uppercase',
      logoLetterSpacing: '0.2em',
      radius: '2px',
      cardShadow: 'none',
    },
  },
  wetzels: {
    id: 'wetzels',
    name: "Wetzel's Pretzels",
    tagline: 'Pretzel hangat yang dipilin tangan dan dipanggang segar.',
    logoText: "Wetzel's Pretzels",
    // Logo dari wetzels.com (assets/img/nav/logo.svg, biru #003783) — hak eksklusif brand Indonesia dipegang Erajaya
    // (docs/04 §16). Tampil di header kuning seperti situs resmi.
    logoSrc: '/brands/wetzels/logo.svg',
    logoKind: 'wordmark',
    preorderHint: 'Paket katering, pilih jadwal ambil',
    subdomain: 'wetzels',
    subdomainAliases: [],
    hosts: ['wetzels.efn.local'],
    companies: ['WTZ'],
    brandCode: 'WTZ',
    hero: {
      eyebrow: 'Pretzel hangat, dibuat di depan Anda',
      title: 'Pretzel hangat, dipilin dengan tangan',
      lead: 'Pretzel, bitz, dan lemonade favorit dipanggang segar. Pesan dari ponsel lalu ambil di gerai, atau pre-order paket katering untuk acara Anda.',
    },
    // Warna = wetzels.com (diukur getComputedStyle 24 Okt 2026): biru #004089 (bilah atas, footer, tombol biru, judul),
    // kuning #fed900 (navigasi, latar halaman, tombol kuning berteks biru), biru muda #b7e5f0 (latar judul). Skema GELAP
    // biru (pola PB: landing & halaman dalam satu bahasa visual), header kuning seperti situs resmi (globals.css
    // `html[data-brand='wetzels']`). Kontras WCAG (tests/brands.test.ts): putih/#004089 10.03, #b7e5f0/#004089 7.39,
    // kuning/#004089 7.23, putih/#0a4c97 8.42, #b7e5f0/#0a4c97 6.21, kuning/#0a4c97 6.07.
    theme: {
      scheme: 'dark',
      chrome: '#004089',
      chromeContrast: '#ffffff',
      primary: '#fed900',
      primaryContrast: '#004089',
      accent: '#fed900',
      accentSoft: '#08488f',
      focus: '#fed900',
      surface: '#0a4c97',
      surfaceAlt: '#004089',
      text: '#ffffff',
      textMuted: '#b7e5f0',
      border: '#3f72b0',
      fontDisplay: LONDRINA,
      fontBody: ROBOTO,
      fontHeading: LONDRINA,
      fontLabel: LONDRINA,
      fontNav: LONDRINA,
      headingWeight: '400',
      headingLetterSpacing: '0.01em',
      headingTransform: 'uppercase',
      navTransform: 'uppercase',
      uiWeight: '400',
      logoWeight: '400',
      logoTransform: 'uppercase',
      logoLetterSpacing: 'normal',
      radius: '10px',
      cardShadow: '0 12px 32px rgba(0, 20, 60, 0.35)',
    },
  },
};

export function isBrandId(value: unknown): value is BrandId {
  return typeof value === 'string' && Object.prototype.hasOwnProperty.call(BRANDS, value);
}

export function getBrandById(id: string | null | undefined): Brand {
  return isBrandId(id) ? BRANDS[id] : BRANDS[DEFAULT_BRAND_ID];
}

/** Normalisasi header Host: lowercase, buang port, buang titik di akhir. */
export function normalizeHost(host: string | null | undefined): string {
  if (!host) return '';
  const first = host.split(',')[0]!.trim().toLowerCase();
  // IPv6 literal `[::1]:3001`
  if (first.startsWith('[')) return first.slice(0, first.indexOf(']') + 1);
  return first.replace(/:\d+$/, '').replace(/\.$/, '');
}

/**
 * Host tempat override brand dev (`?brand=` + cookie `efn_dev_brand`, proxy.ts) boleh MENEMPEL: hanya host tanpa
 * nama domain brand (localhost, IP, nama tanpa titik). Di domain bernama (`app.efn.local`, `bacha.efn.local`, ...)
 * cookie lama tidak dipakai — sebelumnya cookie `?brand=parisbaguette` yang tertinggal membuat `app.efn.local`
 * tampil sebagai situs Paris Baguette tanpa batas waktu.
 */
export function isDevOverrideHost(host: string | null | undefined): boolean {
  const h = normalizeHost(host);
  if (!h) return true;
  if (h === 'localhost' || h.startsWith('[')) return true;
  if (/^\d{1,3}(\.\d{1,3}){3}$/.test(h)) return true;
  return !h.includes('.');
}

/**
 * Host -> brand. Urutan: host eksplisit, lalu prefix subdomain (agar domain prod
 * seperti `grandlucky.<domain-produksi>` jalan tanpa ubah kode), lalu alias subdomain lama (`mart.`, `kitchen.`), lalu default.
 */
export function resolveBrandIdFromHost(host: string | null | undefined): BrandId {
  const h = normalizeHost(host);
  if (!h) return DEFAULT_BRAND_ID;
  for (const brand of Object.values(BRANDS)) {
    if (brand.hosts.includes(h)) return brand.id;
  }
  if (!h.includes('.')) return DEFAULT_BRAND_ID;
  const label = h.split('.')[0];
  for (const brand of Object.values(BRANDS)) {
    if (brand.subdomain && brand.subdomain === label) return brand.id;
  }
  for (const brand of Object.values(BRANDS)) {
    if (brand.subdomainAliases.includes(label!)) return brand.id;
  }
  return DEFAULT_BRAND_ID;
}

/** Pola nilai aman per jenis token. Apa pun yang tidak cocok dibuang -> fallback `:root` di globals.css. */
const SAFE = {
  color: /^#[0-9a-fA-F]{6}$/,
  // Hanya huruf/angka/spasi/koma/tanda hubung/kutip tunggal: tanpa `;`, `(`, `url`, `\`, kutip ganda.
  font: /^[A-Za-z0-9 ,'-]{1,200}$/,
  weight: /^[1-9]00$/,
  spacing: /^(normal|0|-?0?\.\d{1,3}em)$/,
  transform: /^(none|uppercase)$/,
  length: /^(0|\d{1,2}(\.\d{1,3})?(px|rem))$/,
  shadow: /^(none|0 \d{1,2}px \d{1,2}px rgba\(\d{1,3}, \d{1,3}, \d{1,3}, 0?\.\d{1,2}\))$/,
  scheme: /^(light|dark)$/,
} as const;

const THEME_VARS: ReadonlyArray<readonly [keyof BrandTheme, string, keyof typeof SAFE]> = [
  ['chrome', '--color-chrome', 'color'],
  ['chromeContrast', '--color-chrome-contrast', 'color'],
  ['primary', '--color-primary', 'color'],
  ['primaryContrast', '--color-primary-contrast', 'color'],
  ['accent', '--color-accent', 'color'],
  ['accentSoft', '--color-accent-soft', 'color'],
  ['focus', '--color-focus', 'color'],
  ['surface', '--color-surface', 'color'],
  ['surfaceAlt', '--color-surface-alt', 'color'],
  ['text', '--color-text', 'color'],
  ['textMuted', '--color-text-muted', 'color'],
  ['border', '--color-border', 'color'],
  ['fontDisplay', '--font-display', 'font'],
  ['fontBody', '--font-body', 'font'],
  ['fontHeading', '--font-heading', 'font'],
  ['fontLabel', '--font-label', 'font'],
  ['fontNav', '--font-nav', 'font'],
  ['headingWeight', '--heading-weight', 'weight'],
  ['headingLetterSpacing', '--heading-letter-spacing', 'spacing'],
  ['headingTransform', '--heading-transform', 'transform'],
  ['navTransform', '--nav-transform', 'transform'],
  ['uiWeight', '--ui-weight', 'weight'],
  ['logoWeight', '--logo-weight', 'weight'],
  ['logoTransform', '--logo-transform', 'transform'],
  ['logoLetterSpacing', '--logo-letter-spacing', 'spacing'],
  ['radius', '--radius', 'length'],
  ['cardShadow', '--card-shadow', 'shadow'],
];

/** True bila nilai token aman dimasukkan ke atribut `style` untuk jenisnya. */
export function isSafeThemeValue(kind: keyof typeof SAFE, value: unknown): value is string {
  return typeof value === 'string' && SAFE[kind].test(value);
}

/**
 * Map tema -> CSS custom properties untuk atribut `style` di <html>. Nilai yang tidak lolos validasi DIBUANG
 * (bukan di-escape) sehingga CSS memakai fallback `:root` — tidak pernah meneruskan string sembarang ke `style`.
 */
export function themeToCssVars(theme: BrandTheme): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [key, cssVar, kind] of THEME_VARS) {
    const v = theme[key];
    if (isSafeThemeValue(kind, v)) out[cssVar] = v;
  }
  return out;
}

/** Skema untuk atribut `data-scheme` di <html>; nilai tak valid -> `light`. */
export function safeScheme(theme: BrandTheme): 'light' | 'dark' {
  return theme.scheme === 'dark' ? 'dark' : 'light';
}

/** Logo hanya dari path same-origin di public/brands/<id>/ (svg/png; CSP `img-src 'self'`); selain itu null -> logo teks. */
export function safeLogoSrc(brand: Pick<Brand, 'logoSrc'>): string | null {
  const src = brand.logoSrc;
  if (typeof src !== 'string') return null;
  return /^\/brands\/[a-z0-9-]{1,32}\/[a-z0-9-]{1,64}\.(svg|png)$/.test(src) ? src : null;
}
