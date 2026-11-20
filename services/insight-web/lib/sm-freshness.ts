/**
 * Kesegaran per mart/kartu (docs/04 §28.13.10, spesifikasi UX §5.2) dan empat keadaan "tidak ada angka" (§6.5). Murni.
 * Ambang efektif dari `v_sm_kpi_config` (kunci `fresh.<kelas>.warn_min/stale_min`) lewat SmThresholds; tidak ada angka lain.
 */
import { fmtAgeMin, fmtStampWib } from './sm-format';
import type { MartFreshness } from './sm-parse';
import type { SmThresholds } from './sm-thresholds';

export type FreshLevel = 'good' | 'warn' | 'bad' | 'unknown';

/** Mart -> kartu (kontrak §28.13.10). Dipakai untuk menyusun badge kesegaran per halaman. */
export const MARTS = {
  replenishment: 'replenishment',
  dcPurchase: 'dc_purchase',
  stock: 'stock',
  sales: 'sales',
  waste: 'waste',
  forecastAccuracy: 'forecast_accuracy',
  dcFillRate: 'dc_fill_rate',
  stockValue: 'stock_value',
  expiry: 'expiry',
  priceHunter: 'price_hunter',
  visitor: 'visitor',
  campaign: 'campaign',
} as const;

export const MART_LABEL: Readonly<Record<string, string>> = {
  replenishment: 'Usulan order',
  dc_purchase: 'PO supplier',
  stock: 'Stok',
  sales: 'Penjualan',
  waste: 'Susut',
  forecast_accuracy: 'Akurasi forecast',
  dc_fill_rate: 'Fill rate DC',
  stock_value: 'Nilai stok',
  expiry: 'Kedaluwarsa',
  price_hunter: 'Harga pasar',
  visitor: 'Perilaku pengunjung',
  campaign: 'Kampanye',
};

export interface FreshResult {
  level: FreshLevel;
  /** Umur data dalam menit (null = tidak diketahui). */
  ageMin: number | null;
  /** Waktu acuan tampilan (yang lebih tua dari data_as_of dan source_as_of). */
  asOf: Date | null;
}

/**
 * Tingkat kesegaran satu mart. Acuan = waktu TERTUA dari `data_as_of` dan `source_as_of` (snapshot stok yang macet tidak
 * boleh tampak segar hanya karena ETL berjalan). `data_as_of` kosong = "Tidak diketahui" (BUKAN baik). Kelas `daily` dengan
 * `last_status != 'ok'` = Bermasalah (§28.13.10).
 */
export function freshnessOf(f: MartFreshness | undefined, now: Date, t: SmThresholds): FreshResult {
  if (!f || f.dataAsOf === null) return { level: 'unknown', ageMin: null, asOf: null };
  const ref = f.sourceAsOf !== null && f.sourceAsOf < f.dataAsOf ? f.sourceAsOf : f.dataAsOf;
  const ageMin = Math.max(0, (now.getTime() - ref.getTime()) / 60_000);
  const lim = t.fresh[f.cls];
  let level: FreshLevel = ageMin <= lim.warnMin ? 'good' : ageMin <= lim.staleMin ? 'warn' : 'bad';
  if (f.cls === 'daily' && f.lastStatus !== null && f.lastStatus !== 'ok') level = 'bad';
  return { level, ageMin, asOf: ref };
}

/** Mart belum pernah berjalan (job mati / flag ETL off) atau tidak ada di v_sm_freshness = fitur belum aktif. */
export function martOff(f: MartFreshness | undefined): boolean {
  return !f || (f.dataAsOf === null && f.lastRunAt === null);
}

const RANK: Record<FreshLevel, number> = { good: 0, warn: 1, unknown: 2, bad: 3 };
export function worstLevel(levels: readonly FreshLevel[]): FreshLevel {
  return levels.reduce<FreshLevel>((a, b) => (RANK[b] > RANK[a] ? b : a), 'good');
}

/** "Data per 14:45 WIB (12 menit lalu)" atau "Kesegaran tidak diketahui". */
export function asOfText(r: FreshResult, now: Date): string {
  if (r.asOf === null || r.ageMin === null) return 'Kesegaran data tidak diketahui';
  return `Data per ${fmtStampWib(r.asOf, now)} (${fmtAgeMin(r.ageMin)})`;
}

/** Spanduk kartu untuk kelas hampir-langsung / stok: peringatan sebelum bertindak (§5.2). Kosong bila segar. */
export function staleBanner(level: FreshLevel): string | null {
  if (level === 'warn') return 'Data mungkin tertinggal; cek di Odoo sebelum bertindak.';
  if (level === 'bad') return 'Data usang; cek di Odoo sebelum bertindak.';
  return null;
}

// ---------------------------------------------------------------- empat keadaan
export type EmptyKind = 'zero' | 'no-data' | 'feature-off' | 'stale';

export type CardState =
  | { kind: 'ready'; level: FreshLevel }
  | { kind: 'empty'; empty: EmptyKind; level: FreshLevel }
  | { kind: 'unknown' };

/**
 * Menentukan keadaan kartu dari kesegaran mart + ada/tidaknya baris:
 *  - mart tidak ada di `v_sm_freshness`, atau job belum pernah jalan (data_as_of dan last_run_at kosong) -> fitur belum aktif;
 *  - job pernah jalan tetapi belum pernah sukses (data_as_of kosong) -> "unknown" (kesegaran tidak diketahui, bukan nol);
 *  - tanpa baris + data usang -> keadaan "usang" (jangan menyatakan nol);
 *  - tanpa baris + data segar -> `zeroMeans` ('zero' = nol sungguhan, mis. tidak ada lot; 'no-data' = belum ada data periode itu);
 *  - ada baris -> siap (level dibawa untuk spanduk usang).
 */
export function resolveCardState(input: {
  fresh: MartFreshness | undefined;
  level: FreshLevel;
  hasRows: boolean;
  zeroMeans: 'zero' | 'no-data';
}): CardState {
  const { fresh, level, hasRows, zeroMeans } = input;
  if (!fresh || (fresh.dataAsOf === null && fresh.lastRunAt === null)) return { kind: 'empty', empty: 'feature-off', level: 'unknown' };
  if (fresh.dataAsOf === null) return { kind: 'unknown' };
  if (hasRows) return { kind: 'ready', level };
  if (level === 'bad') return { kind: 'empty', empty: 'stale', level };
  return { kind: 'empty', empty: zeroMeans, level };
}

/** Teks baku keadaan kosong; `zeroText`/`offText` kartu-spesifik, sisanya baku (§6.5). */
export function emptyText(
  kind: EmptyKind,
  texts: { zero: string; off: string; noData?: string },
  staleDetail?: string,
): { title: string; icon: 'check' | 'clock' | 'info' | 'warn' } {
  switch (kind) {
    case 'zero':
      return { title: texts.zero, icon: 'check' };
    case 'no-data':
      return { title: texts.noData ?? 'Belum ada data untuk periode ini. Data masuk tiap 15 menit.', icon: 'clock' };
    case 'feature-off':
      return { title: texts.off, icon: 'info' };
    case 'stale':
      return { title: staleDetail ?? 'Data terakhir sudah lama; mungkin belum mencakup transaksi terbaru.', icon: 'warn' };
  }
}
