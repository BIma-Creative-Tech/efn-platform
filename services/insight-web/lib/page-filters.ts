/**
 * Filter yang berlaku per halaman (docs/design/insight-web.md §3.2). Kontrol yang tidak berlaku ditampilkan
 * nonaktif dengan keterangan, bukan disembunyikan. Murni; dipakai klien (FilterBar) dan navigasi.
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
};

/**
 * Filter tambahan yang aktif hanya bila SMD boleh dipilih (sesi exec, G51): /forecast ESB masih "segera" (tanpa filter), tetapi
 * forecast SMD memakai Brand / PT dan Cabang.
 */
export const SMD_ONLY_FILTERS: Record<string, readonly FilterKey[]> = {
  '/forecast': ['pt', 'branch'],
};

export function appliesTo(pathname: string, key: FilterKey, smdAllowed = false): boolean {
  if (smdAllowed && (SMD_ONLY_FILTERS[pathname] ?? []).includes(key)) return true;
  return (PAGE_FILTERS[pathname] ?? PAGE_FILTERS['/']).includes(key);
}

export interface NavItem {
  href: string;
  label: string;
  icon: string;
  soon?: boolean;
}

export const NAV_ITEMS: readonly NavItem[] = [
  { href: '/', label: 'Ringkasan', icon: 'home' },
  { href: '/flow', label: 'Produksi vs Penjualan', icon: 'flow' },
  { href: '/sales', label: 'Penjualan & Menu', icon: 'cart' },
  { href: '/payments', label: 'Pembayaran', icon: 'card' },
  { href: '/distribution', label: 'Distribusi', icon: 'truck' },
  { href: '/data-quality', label: 'Kualitas Data', icon: 'check' },
  { href: '/forecast', label: 'Forecast', icon: 'trend', soon: true },
];
