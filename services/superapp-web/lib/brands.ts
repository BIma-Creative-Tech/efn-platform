/**
 * Konfigurasi brand multi-tenant (host -> brand).
 *
 * ADR terkait: ADR multi-tenant by domain & unified cart lintas PT (status Proposed).
 * Tenant: `efn` (holding), `grandlucky` (PT GL), `parisbaguette` (PT PB), `bacha` (BCR butik + BCF kafe, satu domain).
 * Token visual: docs/design/brand-tokens.md.
 * Risiko dimitigasi: R-T14 (duplikasi komponen per brand) — tema hanya lewat CSS custom properties.
 *
 * File ini dipakai oleh proxy.ts (Next 16: runtime Node.js; dulu middleware Edge), jadi HARUS murni data/fungsi tanpa
 * import Node.js. Brand tidak dikenal -> brand default (`efn`), bukan error.
 */

export type BrandId = 'efn' | 'grandlucky' | 'parisbaguette' | 'bacha';

/** Kode company sesuai kontrak `res.company.efn_brand_code` (docs/04_SERVICE_CONTRACTS.md §1). */
export type CompanyCode = 'EFN' | 'GL' | 'PB' | 'BCR' | 'BCF' | 'FARM';

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
}

export const COMPANIES: Record<CompanyCode, CompanyInfo> = {
  EFN: { code: 'EFN', displayName: 'Holding EFN', businessType: null },
  GL: { code: 'GL', displayName: 'PT Grand Lucky', businessType: 'retail' },
  PB: { code: 'PB', displayName: 'PT Paris Baguette', businessType: 'fnb' },
  BCR: { code: 'BCR', displayName: 'Bacha Coffee Retail', businessType: 'retail' },
  BCF: { code: 'BCF', displayName: 'Bacha Coffee F&B', businessType: 'fnb' },
  FARM: { code: 'FARM', displayName: 'FARM', businessType: null },
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
  /** Font stack TANPA font eksternal (CSP `font-src 'self'`); font resmi berlisensi menyusul lewat @font-face di public/fonts. */
  fontBody: string;
  fontHeading: string;
  headingWeight: string;
  headingLetterSpacing: string;
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
  /** Teks logo (logo grafis resmi menyusul dari tim brand). */
  logoText: string;
  /**
   * Path logo SVG resmi same-origin (mis. `/brands/<id>/logo.svg` di public/). null = pakai `logoText`.
   * File logo TIDAK disalin dari situs brand; hanya dari tim brand pemegang aset.
   */
  logoSrc: string | null;
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

export const BRANDS: Record<BrandId, Brand> = {
  efn: {
    id: 'efn',
    name: 'EFN Super-App',
    tagline: 'Belanja GrandLucky, Paris Baguette, dan Bacha Coffee dalam satu aplikasi.',
    logoText: 'EFN',
    logoSrc: null,
    subdomain: 'app',
    subdomainAliases: [],
    hosts: ['app.efn.local', 'efn.local', 'localhost'],
    companies: ['GL', 'PB', 'BCR', 'BCF'],
    brandCode: null,
    hero: {
      eyebrow: 'Erajaya Food & Nourishment',
      title: 'Semua brand EFN, satu akun',
      lead: 'Belanja di GrandLucky Superstore, pesan roti Paris Baguette, dan kopi Bacha Coffee. Satu akun, satu saldo poin.',
    },
    theme: {
      scheme: 'light',
      chrome: '#0b5d4b',
      chromeContrast: '#ffffff',
      primary: '#0b5d4b',
      primaryContrast: '#ffffff',
      accent: '#f2a900',
      accentSoft: '#e7f2ef',
      focus: '#0b5d4b',
      surface: '#ffffff',
      surfaceAlt: '#f3f6f5',
      text: '#14211d',
      textMuted: '#4a5a55',
      border: '#cfd8d5',
      fontBody: SANS_SYSTEM,
      fontHeading: SANS_SYSTEM,
      headingWeight: '800',
      headingLetterSpacing: 'normal',
      logoTransform: 'none',
      logoLetterSpacing: 'normal',
      radius: '10px',
      cardShadow: 'none',
    },
  },
  grandlucky: {
    id: 'grandlucky',
    name: 'GrandLucky Superstore',
    tagline: 'Supermarket premium: buah, sayur, dan seafood segar, lokal maupun impor.',
    logoText: 'GrandLucky',
    logoSrc: null,
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
      fontBody: `Lato, ${SANS_SYSTEM}`,
      fontHeading: `Lato, ${SANS_SYSTEM}`,
      headingWeight: '800',
      headingLetterSpacing: 'normal',
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
    logoSrc: null,
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
    // 21 Okt 2026: seluruh situs PB berlatar navy resmi, teks putih, aksi kuning. Font resmi "PB Signature" proprietary
    // -> pengganti Fraunces (OFL, self-host) sampai tim brand memberi file berlisensi.
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
      fontBody: `'Geist Variable', Inter, ${SANS_SYSTEM}`,
      fontHeading: `'Fraunces Variable', ${SERIF_SYSTEM}`,
      headingWeight: '500',
      headingLetterSpacing: '-0.02em',
      logoTransform: 'uppercase',
      logoLetterSpacing: '0.08em',
      radius: '10px',
      cardShadow: '0 12px 32px rgba(0, 10, 40, 0.35)',
    },
  },
  bacha: {
    id: 'bacha',
    name: 'Bacha Coffee',
    tagline: 'Rumah kopi Maroko sejak 1910.',
    logoText: 'Bacha Coffee',
    logoSrc: null,
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
    theme: {
      scheme: 'light',
      chrome: '#1a2548',
      chromeContrast: '#fffef1',
      primary: '#1a2548',
      primaryContrast: '#fffef1',
      accent: '#ae944d',
      accentSoft: '#ffe7d5',
      focus: '#1a2548',
      surface: '#fffef1',
      surfaceAlt: '#fbf6ec',
      text: '#1a2548',
      textMuted: '#585c6e',
      border: '#e2d6b8',
      fontBody: `Lora, ${SERIF_SYSTEM}`,
      fontHeading: SERIF_SYSTEM,
      headingWeight: '400',
      headingLetterSpacing: '0.02em',
      logoTransform: 'uppercase',
      logoLetterSpacing: '0.2em',
      radius: '2px',
      cardShadow: 'none',
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
  ['fontBody', '--font-body', 'font'],
  ['fontHeading', '--font-heading', 'font'],
  ['headingWeight', '--heading-weight', 'weight'],
  ['headingLetterSpacing', '--heading-letter-spacing', 'spacing'],
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

/** Logo hanya dari path same-origin di public/brands/<id>/ (CSP `img-src 'self'`); selain itu null -> logo teks. */
export function safeLogoSrc(brand: Brand): string | null {
  const src = brand.logoSrc;
  if (typeof src !== 'string') return null;
  return /^\/brands\/[a-z0-9-]{1,32}\/[a-z0-9-]{1,64}\.svg$/.test(src) ? src : null;
}
