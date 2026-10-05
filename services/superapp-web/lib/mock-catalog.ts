/**
 * !!! DATA MOCK / PLACEHOLDER !!!
 * Katalog & konfigurasi pajak statis untuk demo konsep cart per-PT (ADR unified cart, Proposed).
 * Bukan data produk nyata, harga fiktif, tarif pajak fiktif/asumsi.
 *
 * Sumber nyata kelak: BFF (endpoint katalog & konfigurasi pajak per company belum ada di
 * docs/04_SERVICE_CONTRACTS.md) yang membaca Odoo (account.tax / fiscal position per company).
 * Bentuk `TaxConfig` = `{ tax_label, rate, price_include }` sengaja sama dengan payload BFF yang diharapkan,
 * sehingga mengganti sumber cukup di `loadTaxConfigs()` tanpa menyentuh komponen UI.
 */
import type { CompanyCode } from './brands';
import { computeTax, isValidTaxConfig, type TaxConfig } from './tax';

export const CATALOG_IS_MOCK = true as const;

export interface MockProduct {
  id: string;
  name: string;
  unit: string;
  /** Harga tercantum dalam Rupiah (integer). Termasuk/tidak termasuk pajak ditentukan `TaxConfig.price_include` PT-nya. */
  price: number;
  company: CompanyCode;
}

export const MOCK_PRODUCTS: MockProduct[] = [
  { id: 'mock-rtl-001', name: 'Beras Premium (MOCK)', unit: '5 kg', price: 78500, company: 'RTL' },
  { id: 'mock-rtl-002', name: 'Minyak Goreng (MOCK)', unit: '2 L', price: 36900, company: 'RTL' },
  { id: 'mock-rtl-003', name: 'Telur Ayam (MOCK)', unit: '1 kg', price: 29500, company: 'RTL' },
  { id: 'mock-rtl-004', name: 'Susu UHT (MOCK)', unit: '1 L', price: 19900, company: 'RTL' },
  { id: 'mock-fnb-001', name: 'Ayam Geprek (MOCK)', unit: '1 porsi', price: 25000, company: 'FNB' },
  { id: 'mock-fnb-002', name: 'Nasi Goreng Spesial (MOCK)', unit: '1 porsi', price: 32000, company: 'FNB' },
  { id: 'mock-fnb-003', name: 'Es Teh Manis (MOCK)', unit: '1 gelas', price: 8000, company: 'FNB' },
  { id: 'mock-fnb-004', name: 'Paket Geprek + Nasi + Es Teh (MOCK)', unit: '1 paket', price: 35000, company: 'FNB' },
];

/**
 * !!! MOCK !!! Konfigurasi pajak per PT. Nilai ASUMSI untuk demo, BUKAN keputusan pajak:
 * - RTL: PPN, harga katalog sudah termasuk (tarif efektif asumsi).
 * - FNB: PB1/PBJT makan-minum, ditambahkan di atas harga (tarif bergantung perda kab/kota).
 * Wajib dikonfirmasi tim pajak/Finance; sumber final = Odoo per company.
 */
export const MOCK_TAX_CONFIG: Readonly<Record<CompanyCode, TaxConfig>> = {
  RTL: { tax_label: 'PPN', rate: 0.11, price_include: true },
  FNB: { tax_label: 'PB1', rate: 0.1, price_include: false },
};

export type TaxConfigMap = Partial<Record<CompanyCode, TaxConfig>>;

/**
 * Titik tunggal pengambilan konfigurasi pajak. Hari ini membaca MOCK; kelak diganti panggilan BFF
 * (server-side, di RSC). Konfigurasi tidak valid dibuang -> UI menampilkan "pajak dihitung saat checkout".
 */
export async function loadTaxConfigs(companies: CompanyCode[]): Promise<TaxConfigMap> {
  const out: TaxConfigMap = {};
  for (const c of companies) {
    const cfg = MOCK_TAX_CONFIG[c];
    if (isValidTaxConfig(cfg)) out[c] = { ...cfg };
  }
  return out;
}

export function productsForCompanies(companies: CompanyCode[]): MockProduct[] {
  return MOCK_PRODUCTS.filter((p) => companies.includes(p.company));
}

export interface CartLine {
  product: MockProduct;
  qty: number;
}

export interface CompanyGroupTotals {
  company: CompanyCode;
  lines: CartLine[];
  /** Konfigurasi pajak yang dipakai; null = tidak tersedia (pajak tidak diperkirakan). */
  taxConfig: TaxConfig | null;
  /** Jumlah harga tercantum. */
  itemsTotal: number;
  /** Dasar pengenaan pajak (harga sebelum pajak). Sama dengan itemsTotal bila eksklusif/tanpa konfigurasi. */
  base: number;
  /** Komponen pajak (bagian dari itemsTotal bila inklusif, tambahan bila eksklusif). */
  tax: number;
  /** Yang dibayar untuk pesanan PT ini. */
  grandTotal: number;
}

/**
 * Hitung per PT. Perhitungan pajak HANYA ILUSTRASI; tarif & pembulatan final ditentukan
 * Odoo (fiscal position per company) saat checkout dibangun.
 */
export function groupCartByCompany(lines: CartLine[], taxConfigs: TaxConfigMap): CompanyGroupTotals[] {
  const groups = new Map<CompanyCode, CartLine[]>();
  for (const line of lines) {
    if (line.qty <= 0) continue;
    const list = groups.get(line.product.company) ?? [];
    list.push(line);
    groups.set(line.product.company, list);
  }
  return [...groups.entries()].map(([company, groupLines]) => {
    const itemsTotal = groupLines.reduce((sum, l) => sum + l.product.price * l.qty, 0);
    const cfg = taxConfigs[company];
    const taxConfig = cfg && isValidTaxConfig(cfg) ? cfg : null;
    if (!taxConfig) {
      return { company, lines: groupLines, taxConfig: null, itemsTotal, base: itemsTotal, tax: 0, grandTotal: itemsTotal };
    }
    const b = computeTax(itemsTotal, taxConfig);
    return { company, lines: groupLines, taxConfig, itemsTotal, base: b.base, tax: b.tax, grandTotal: b.total };
  });
}
