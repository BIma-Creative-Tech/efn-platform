/**
 * Filter yang berlaku per halaman (docs/design/insight-web.md §3.2). Toolbar hanya menampilkan kontrol yang berlaku di
 * halaman itu; untuk halaman yang tidak memakai rentang tanggal, toolbar menampilkan keterangan (DATE_NA_REASON), bukan
 * diam. Murni; dipakai klien (FilterBar) dan navigasi.
 */
export type FilterKey = 'date' | 'pt' | 'branch' | 'cat' | 'product';

export const PAGE_FILTERS: Record<string, readonly FilterKey[]> = {
  '/': ['date', 'pt', 'branch'],
  '/flow': ['date', 'pt', 'branch', 'product'],
  '/sales': ['date', 'pt', 'branch', 'cat'],
  '/payments': ['date', 'pt', 'branch'],
  '/distribution': ['date', 'pt', 'branch', 'product'],
  '/data-quality': ['pt'],
  '/forecast': [],
  '/glosarium': [],
  // G52 (§28.15.9): Eksekutif dan Marketing F&B memakai filter global; Toko memegang hari dan cabang sendiri di halamannya.
  '/fnb/exec': ['date', 'pt', 'branch'],
  '/fnb/marketing': ['date', 'pt', 'branch'],
  '/fnb/store': [],
};

/**
 * Filter tambahan yang aktif hanya bila SMD boleh dipilih (sesi exec, G51): forecast SMD memakai Brand / PT dan Cabang.
 */
export const SMD_ONLY_FILTERS: Record<string, readonly FilterKey[]> = {
  '/forecast': ['pt', 'branch'],
};

/** Halaman yang tidak memakai rentang tanggal dan alasannya (ditampilkan di toolbar filter dan halaman). */
export const DATE_NA_REASON: Record<string, string> = {
  '/data-quality': 'halaman ini menampilkan status terkini (kesegaran dan temuan terakhir), bukan angka per periode.',
  '/forecast': 'forecast memakai 8 minggu terakhir untuk evaluasi dan 14 hari ke depan untuk prediksi.',
  '/store': 'halaman ini memakai minggu berjalan (Senin s.d. hari ini) dibanding minggu lalu.',
  '/fnb/store': 'halaman ini memakai satu hari (pilih di halaman) dibanding hari yang sama minggu lalu.',
};

/**
 * Halaman persona F&B (`/fnb/*`, G52) hanya memakai data ESB: SMD (dan cabang `sm:`) tidak boleh ditawarkan walau sesi exec ber-SMD;
 * server (`fnbRangePage`) memang menolak `pt=SMD` ("filter tidak valid"). Mengembalikan kode yang boleh dipilih di toolbar.
 */
export function selectableCodes(pathname: string, sessionCodes: readonly string[]): string[] {
  return pathname.startsWith('/fnb/') ? sessionCodes.filter((c) => c !== 'SMD') : [...sessionCodes];
}

export function appliesTo(pathname: string, key: FilterKey, smdAllowed = false): boolean {
  if (smdAllowed && (SMD_ONLY_FILTERS[pathname] ?? []).includes(key)) return true;
  return (PAGE_FILTERS[pathname] ?? PAGE_FILTERS['/']).includes(key);
}

export interface NavItem {
  href: string;
  label: string;
  icon: string;
  /**
   * Halaman bersama F&B + Supermarket (Penjualan, Pembayaran, Forecast) yang tampil di dua grup untuk sesi exec ber-SMD:
   * `esb` = tautan grup F&B (SMD dibuang dari `pt`), `smd` = tautan grup Supermarket (`pt=SMD`). Lihat `scopedQuery`.
   */
  scope?: 'esb' | 'smd';
}

/** Katalog item menu (satu sumber; pengelompokan dan visibilitas ada di lib/sm-personas.ts navGroups). */
export const NAV = {
  overview: { href: '/', label: 'Ikhtisar', icon: 'home' },
  sales: { href: '/sales', label: 'Penjualan', icon: 'cart' },
  payments: { href: '/payments', label: 'Pembayaran', icon: 'card' },
  flow: { href: '/flow', label: 'Produksi vs Penjualan', icon: 'flow' },
  distribution: { href: '/distribution', label: 'Distribusi CK', icon: 'truck' },
  dataQuality: { href: '/data-quality', label: 'Kualitas Data ESB', icon: 'check' },
  forecast: { href: '/forecast', label: 'Forecast', icon: 'trend' },
} as const satisfies Record<string, NavItem>;

/** Item grup Ringkasan (F&B + Supermarket). Keputusan pemilik proyek 22 Nov 2026: hanya Ikhtisar. */
export const SUMMARY_ITEMS: readonly NavItem[] = [NAV.overview];
/** Item grup Food & Beverage (22 Nov 2026: Penjualan, Pembayaran, Forecast juga di grup Supermarket). */
export const FNB_ITEMS: readonly NavItem[] = [NAV.sales, NAV.payments, NAV.flow, NAV.distribution, NAV.forecast, NAV.dataQuality];
/** Halaman bersama yang digandakan ke grup Supermarket (sesi exec ber-SMD). */
export const SHARED_HREFS: readonly string[] = [NAV.sales.href, NAV.payments.href, NAV.forecast.href];
