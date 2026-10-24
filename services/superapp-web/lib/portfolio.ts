/**
 * Portofolio brand Erajaya Food & Nourishment (PT Era Boga Nusantara / "EBN") untuk landing Super-App `app.`.
 * DATA MURNI (tanpa import server/klien) — diuji tests/portfolio.test.ts.
 *
 * Sumber fakta (dicek 23 Okt 2026, semua copy tetap DRAF menunggu Marketing):
 * - erajaya.com/line-of-verticals: "Paris Baguette, Bacha Coffee, Curry Up, CHAGEE, Wetzel's Pretzels dan GrandLucky
 *   Superstore" sebagai portofolio vertikal EFN.
 * - erajaya.com/news (CHAGEE gerai pertama Jakarta, 11 Apr 2025; Curry Up bersama Anders Hospitality Group, 7 Jun 2024),
 *   PR Newswire (Wetzel's Pretzels: hak eksklusif Indonesia), Bisnis.com 2 Okt 2026 (I'm Donut ? dari Jepang, Q4 2026),
 *   The Star 7 Agu 2026 + CNBC Indonesia (Oriental Kopi dari Malaysia, gerai pertama ditargetkan akhir 2026).
 *
 * Logo: file resmi same-origin di `public/brands/<id>/logo.(svg|png)` (sumber per file: public/brands/README.md) lalu
 * diisi di `logo`. Dinding & deretan logo merender logo sebagai siluet putih (CSS filter), jadi hanya logo bertipe
 * wordmark tanpa latar (Wetzel's) yang dipakai di sini; lencana bulat PB tetap logotipe teks. null -> logotipe teks.
 * G24 (24 Okt 2026): Wetzel's Pretzels kini tenant Super-App (`wetzels.`, PT WTZ) -> status online.
 */

export type PortfolioStatus = 'online' | 'store' | 'soon';

export interface PortfolioBrand {
  id: string;
  name: string;
  /** Kategori singkat (label). */
  category: string;
  /** Asal brand / sejak (label kecil). null = belum diverifikasi. */
  origin: string | null;
  /** Satu kalimat profil (DRAF). */
  line: string;
  status: PortfolioStatus;
  /** Label waktu untuk status `soon` (mis. "Q4 2026"); hanya dari sumber yang tercatat di atas. */
  eta: string | null;
  /** Path logo same-origin `/brands/<id>/<nama>.(svg|png)`; null = logotipe teks. */
  logo: string | null;
  /** Tenant/brand Super-App yang punya katalog online (BrandId), atau null. */
  tenant: 'parisbaguette' | 'bacha' | 'grandlucky' | 'wetzels' | null;
}

export const PORTFOLIO: readonly PortfolioBrand[] = [
  {
    id: 'parisbaguette',
    name: 'Paris Baguette',
    category: 'Bakery café',
    origin: 'Korea Selatan',
    line: 'Roti, pastry, dan kue segar untuk pagi hingga perayaan.',
    status: 'online',
    eta: null,
    logo: null,
    tenant: 'parisbaguette',
  },
  {
    id: 'bacha',
    name: 'Bacha Coffee',
    category: 'Rumah kopi & butik',
    origin: 'Marrakech · sejak 1910',
    line: 'Kopi arabika pilihan dari rumah kopi bersejarah Maroko.',
    status: 'online',
    eta: null,
    logo: null,
    tenant: 'bacha',
  },
  {
    id: 'grandlucky',
    name: 'GrandLucky Superstore',
    category: 'Supermarket premium',
    origin: 'Indonesia · sejak 1992',
    line: 'Buah, sayur, daging, dan seafood segar, lokal hingga impor.',
    status: 'online',
    eta: null,
    logo: null,
    tenant: 'grandlucky',
  },
  {
    id: 'chagee',
    name: 'CHAGEE',
    category: 'Teh premium',
    origin: 'Internasional',
    line: 'Teh susu segar bergaya modern, hadir di Indonesia sejak 2025.',
    status: 'store',
    eta: null,
    logo: null,
    tenant: null,
  },
  {
    id: 'wetzels',
    name: "Wetzel's Pretzels",
    category: 'Pretzel',
    origin: 'Amerika Serikat',
    line: 'Pretzel hangat yang dipilin tangan, dibuat di depan Anda.',
    status: 'online',
    eta: null,
    logo: '/brands/wetzels/logo.svg',
    tenant: 'wetzels',
  },
  {
    id: 'curryup',
    name: 'Curry Up',
    category: 'Restoran kari',
    origin: 'Bersama Anders Hospitality Group',
    line: 'Hidangan kari khas berbagai negara dalam satu meja.',
    status: 'store',
    eta: null,
    logo: null,
    tenant: null,
  },
  {
    id: 'imdonut',
    name: "I'm Donut ?",
    category: 'Donat',
    origin: 'Jepang',
    line: 'Donat lembut bertekstur khas yang viral dari Jepang.',
    status: 'soon',
    eta: 'Q4 2026',
    logo: null,
    tenant: null,
  },
  {
    id: 'orientalkopi',
    name: 'Oriental Kopi',
    category: 'Kopitiam',
    origin: 'Malaysia',
    line: 'Kopi dan hidangan kopitiam khas Malaysia.',
    status: 'soon',
    eta: 'Akhir 2026',
    logo: null,
    tenant: null,
  },
];

export const STATUS_LABEL: Record<PortfolioStatus, string> = {
  online: 'Pesan di Super-App',
  store: 'Kunjungi gerai',
  soon: 'Segera hadir',
};

/** Logo hanya path same-origin di /brands/<id>/ (CSP img-src 'self'); selain itu null -> logotipe teks. */
export function safePortfolioLogo(b: Pick<PortfolioBrand, 'logo'>): string | null {
  const src = b.logo;
  if (typeof src !== 'string') return null;
  return /^\/brands\/[a-z0-9-]{1,32}\/[a-z0-9-]{1,64}\.(svg|png)$/.test(src) ? src : null;
}

/** Jumlah per status + total, untuk filter etalase portofolio. */
export function portfolioCounts(list: readonly PortfolioBrand[]): Record<PortfolioStatus | 'all', number> {
  const out = { all: list.length, online: 0, store: 0, soon: 0 };
  for (const b of list) out[b.status] += 1;
  return out;
}
