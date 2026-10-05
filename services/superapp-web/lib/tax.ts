/**
 * Perhitungan & format pajak per PT — MURNI (dipakai server & client, diuji unit).
 *
 * Keputusan user (Gelombang 5): harga/pajak CONFIGURABLE. Tidak ada tarif PPN/PB1 yang ditulis di UI;
 * komponen hanya menerima `TaxConfig` dari sumber data (sekarang `lib/mock-catalog.ts` berlabel MOCK,
 * kelak BFF -> Odoo fiscal position / account.tax per company). Angka final selalu dihitung Odoo saat checkout.
 *
 * Bentuk `TaxConfig` sengaja snake_case agar sama dengan payload BFF yang diharapkan
 * (`{ tax_label, rate, price_include }`, mirip `account.tax.price_include` di Odoo).
 */

export interface TaxConfig {
  /** Label tampilan, mis. "PPN", "PB1". Berasal dari konfigurasi, bukan dari kode UI. */
  tax_label: string;
  /** Tarif desimal efektif, 0 <= rate < 1 (0,11 = 11%). */
  rate: number;
  /** true = harga katalog sudah termasuk pajak; false = pajak ditambahkan di atas harga. */
  price_include: boolean;
}

export interface TaxBreakdown {
  /** Jumlah harga tercantum (sesuai label harga katalog). */
  listed: number;
  /** Dasar pengenaan pajak (harga sebelum pajak). */
  base: number;
  /** Komponen pajak. Bila inklusif: bagian dari `listed`; bila eksklusif: tambahan. */
  tax: number;
  /** Yang dibayar. */
  total: number;
}

/** Validasi bentuk konfigurasi (data dari luar kode — mock hari ini, BFF besok). */
export function isValidTaxConfig(value: unknown): value is TaxConfig {
  if (!value || typeof value !== 'object') return false;
  const v = value as Record<string, unknown>;
  return (
    typeof v.tax_label === 'string' &&
    v.tax_label.trim().length > 0 &&
    v.tax_label.length <= 40 &&
    typeof v.rate === 'number' &&
    Number.isFinite(v.rate) &&
    v.rate >= 0 &&
    v.rate < 1 &&
    typeof v.price_include === 'boolean'
  );
}

/**
 * Hitung pajak atas jumlah harga tercantum (Rupiah). Pembulatan ke rupiah terdekat, per kelompok
 * (ILUSTRASI — aturan pembulatan per baris/per dokumen final mengikuti Odoo).
 */
export function computeTax(listedAmount: number, cfg: TaxConfig): TaxBreakdown {
  const listed = Number.isFinite(listedAmount) ? Math.round(Math.max(0, listedAmount)) : 0;
  if (cfg.price_include) {
    const base = Math.round(listed / (1 + cfg.rate));
    return { listed, base, tax: listed - base, total: listed };
  }
  const tax = Math.round(listed * cfg.rate);
  return { listed, base: listed, tax, total: listed + tax };
}

const percentFmt = new Intl.NumberFormat('id-ID', { maximumFractionDigits: 2 });

/** 0.11 -> "11%", 0.1 -> "10%", 0.0125 -> "1,25%". */
export function formatTaxRate(rate: number): string {
  // Hindari artefak floating point (0.07 * 100 = 7.000000000000001).
  const pct = Math.round(rate * 100 * 100) / 100;
  return `${percentFmt.format(pct)}%`;
}

/** "PPN 11% (sudah termasuk)" / "PB1 10% (ditambahkan)". */
export function describeTax(cfg: TaxConfig): string {
  return `${cfg.tax_label} ${formatTaxRate(cfg.rate)} ${cfg.price_include ? '(sudah termasuk)' : '(ditambahkan)'}`;
}

/** Keterangan pendek di kartu produk: "termasuk PPN" / "belum termasuk PB1". */
export function priceTaxHint(cfg: TaxConfig): string {
  return cfg.price_include ? `termasuk ${cfg.tax_label}` : `belum termasuk ${cfg.tax_label}`;
}
