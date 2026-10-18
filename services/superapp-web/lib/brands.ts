/**
 * Konfigurasi brand multi-tenant (host -> brand).
 *
 * ADR terkait: ADR multi-tenant by domain & unified cart lintas PT (status Proposed).
 * Risiko dimitigasi: R-T14 (duplikasi komponen per brand) — tema hanya lewat CSS custom properties.
 *
 * File ini dipakai oleh proxy.ts (Next 16: runtime Node.js; dulu middleware Edge), jadi HARUS murni data/fungsi tanpa
 * import Node.js. Brand tidak dikenal -> brand default (`efn`), bukan error.
 */

export type BrandId = 'efn' | 'efn-mart' | 'efn-kitchen';

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
}

export const COMPANIES: Record<CompanyCode, CompanyInfo> = {
  EFN: { code: 'EFN', displayName: 'Holding EFN' },
  GL: { code: 'GL', displayName: 'PT Grand Lucky' },
  PB: { code: 'PB', displayName: 'PT Paris Baguette' },
  BCR: { code: 'BCR', displayName: 'Bacha Coffee Retail' },
  BCF: { code: 'BCF', displayName: 'Bacha Coffee F&B' },
  FARM: { code: 'FARM', displayName: 'FARM' },
};

/**
 * Peta `brand_code` kanal (non-rahasia, docs/04 §12.1) -> kode kanal/PT. Kode kanal = kode PT.
 * Bacha = DUA kanal (BCR retail + BCF F&B) di satu domain. Hanya peta; belum dipakai tenant mana pun
 * (semua tenant tetap `brandCode: null`, L-33, sampai Q-F19-5 / pemetaan domain diputuskan klien).
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

export interface BrandTheme {
  primary: string;
  primaryContrast: string;
  accent: string;
  surface: string;
  surfaceAlt: string;
  text: string;
  textMuted: string;
  border: string;
}

export interface Brand {
  id: BrandId;
  name: string;
  tagline: string;
  /** Teks logo sementara (logo grafis menyusul dari tim brand). */
  logoText: string;
  /** Prefix subdomain untuk pencocokan di domain apa pun (mis. `mart.` -> efn-mart). null = default. */
  subdomain: string | null;
  /** Host eksplisit (dev/staging). */
  hosts: string[];
  /** Company yang katalognya ditampilkan di brand ini. */
  companies: CompanyCode[];
  /**
   * Kode brand katalog (non-rahasia; docs/04 §11.3 no. 4) = `efn.online.channel.brand_code` (mis. `GL`, `BACHA`, `PB`).
   * Dipakai sebagai `?brand=` saat memuat kanal di domain brand. null = semua kanal (brand holding).
   * Pembatasan UI saja — BFF tidak mempercayai domain. Nilai di sini ASUMSI sampai klien menetapkan pemetaan domain.
   */
  brandCode: string | null;
  theme: BrandTheme;
}

export const DEFAULT_BRAND_ID: BrandId = 'efn';

export const BRANDS: Record<BrandId, Brand> = {
  efn: {
    id: 'efn',
    name: 'EFN Super-App',
    tagline: 'Belanja supermarket dan pesan makanan dalam satu aplikasi.',
    logoText: 'EFN',
    subdomain: 'app',
    hosts: ['app.efn.local', 'efn.local', 'localhost'],
    companies: ['GL', 'PB', 'BCR', 'BCF'],
    brandCode: null,
    theme: {
      primary: '#0b5d4b',
      primaryContrast: '#ffffff',
      accent: '#f2a900',
      surface: '#ffffff',
      surfaceAlt: '#f3f6f5',
      text: '#14211d',
      textMuted: '#4a5a55',
      border: '#cfd8d5',
    },
  },
  'efn-mart': {
    id: 'efn-mart',
    name: 'EFN Mart',
    tagline: 'Supermarket segar, antar di hari yang sama.',
    logoText: 'EFN Mart',
    subdomain: 'mart',
    hosts: ['mart.efn.local'],
    companies: ['GL'],
    brandCode: null, // L-33: menunggu Q-F19-5 (domain → brand_code); 'GL' setelah pemetaan domain diputuskan
    theme: {
      primary: '#1d4ed8',
      primaryContrast: '#ffffff',
      accent: '#16a34a',
      surface: '#ffffff',
      surfaceAlt: '#f1f5fb',
      text: '#111827',
      textMuted: '#4b5563',
      border: '#d1d9e6',
    },
  },
  'efn-kitchen': {
    id: 'efn-kitchen',
    name: 'EFN Kitchen',
    tagline: 'Masakan hangat, diantar cepat.',
    logoText: 'EFN Kitchen',
    subdomain: 'kitchen',
    hosts: ['kitchen.efn.local'],
    companies: ['PB'],
    brandCode: null, // L-33: menunggu Q-F19-5; 'PB' setelah pemetaan domain diputuskan
    theme: {
      primary: '#b42318',
      primaryContrast: '#ffffff',
      accent: '#f79009',
      surface: '#ffffff',
      surfaceAlt: '#fdf4f2',
      text: '#1f1412',
      textMuted: '#5c4a46',
      border: '#e8d3cf',
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
 * seperti `mart.<domain-produksi>` jalan tanpa ubah kode), lalu default.
 */
export function resolveBrandIdFromHost(host: string | null | undefined): BrandId {
  const h = normalizeHost(host);
  if (!h) return DEFAULT_BRAND_ID;
  for (const brand of Object.values(BRANDS)) {
    if (brand.hosts.includes(h)) return brand.id;
  }
  const label = h.split('.')[0];
  for (const brand of Object.values(BRANDS)) {
    if (brand.subdomain && brand.subdomain === label && h.includes('.')) return brand.id;
  }
  return DEFAULT_BRAND_ID;
}

/** Map tema -> CSS custom properties untuk atribut `style` di <html>. */
export function themeToCssVars(theme: BrandTheme): Record<string, string> {
  return {
    '--color-primary': theme.primary,
    '--color-primary-contrast': theme.primaryContrast,
    '--color-accent': theme.accent,
    '--color-surface': theme.surface,
    '--color-surface-alt': theme.surfaceAlt,
    '--color-text': theme.text,
    '--color-text-muted': theme.textMuted,
    '--color-border': theme.border,
  };
}
