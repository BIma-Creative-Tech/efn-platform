/**
 * Default definisi & ambang KPI supermarket (docs/04 §28.13.8c, L-200). Murni (tanpa I/O).
 *
 * Tiga tempat wajib sama (L-200): registri Odoo `KPI_REGISTRY`, literal fallback view `efn_dwh.v_sm_kpi_config`, dan file ini.
 * Kesamaan dijaga tests/sm-kpi-config.test.ts, yang membandingkan dengan `docs/contracts/insight_sm_kpi_defaults.json`
 * (normatif, dibuat builder Odoo) bila file itu ada.
 *
 * Nilai efektif per PT dibaca dari `v_sm_kpi_config` saat runtime (lib/sm-config.ts); file ini hanya fallback bila baris
 * tidak ada, kueri gagal, atau nilai di luar pilihan/rentang.
 */

/** k-anonim (keputusan DPO Q-SM17): tidak configurable dari Odoo. */
export const K_ANON = 10;

export type KpiValue = number | string;

export const SM_KPI_DEFAULTS: Readonly<Record<string, KpiValue>> = {
  'gmroi.period': 'month',
  'gmroi.stock_avg': 'daily',
  'gmroi.include_dc': 1,
  'gmroi.annualize': 0,
  'oos.universe': 'orderpoint',
  'oos.basis': 'on_hand',
  'oos.threshold_qty': 0,
  'oos.include_dc': 0,
  'critical.rule': 'min_or_cover',
  'critical.cover_days': 2,
  'critical.sales_window_days': 28,
  'fill.basis': 'shipped',
  'fill.level': 'qty',
  'fill.grace_days': 0,
  'waste.basis': 'sales_excl_tax',
  'thr.oos.warn_pct': 2,
  'thr.oos.bad_pct': 5,
  'thr.fill.good_pct': 95,
  'thr.fill.warn_pct': 90,
  'thr.waste.warn_pct': 2,
  'thr.waste.bad_pct': 4,
  'thr.wape.warn_pct': 25,
  'thr.wape.bad_pct': 40,
  'thr.bias.warn_pct': 5,
  'thr.bias.bad_pct': 10,
  'thr.price.warn_pct': 10,
  'thr.price.bad_pct': 20,
  'thr.price.below_pct': 10,
  'thr.price.min_samples': 3,
  'thr.po_late.warn': 1,
  'thr.po_late.bad': 3,
  'thr.cutoff.urgent_min': 120,
  'thr.stock_age.days': 30,
  'fresh.near_live.warn_min': 30,
  'fresh.near_live.stale_min': 60,
  'fresh.stock.warn_min': 75,
  'fresh.stock.stale_min': 135,
  'fresh.daily.warn_min': 1560,
  'fresh.daily.stale_min': 2880,
};

/** Kunci enum dan pilihan sahnya (§28.13.8c). Nilai di luar pilihan -> default. */
export const SM_KPI_ENUMS: Readonly<Record<string, readonly string[]>> = {
  'gmroi.period': ['month', 'quarter', 'year'],
  'gmroi.stock_avg': ['daily', 'month_end'],
  'oos.universe': ['orderpoint', 'orderpoint_sold_28d'],
  'oos.basis': ['on_hand', 'free'],
  'critical.rule': ['min_or_cover', 'cover_only', 'min_only'],
  'fill.basis': ['shipped', 'received'],
  'fill.level': ['qty', 'line'],
  'waste.basis': ['sales_excl_tax', 'cogs'],
};

/** Rentang numerik sah per kunci (tabel §28.13.8c). Kunci bool/enum tidak ada di sini. */
export const SM_KPI_RANGES: Readonly<Record<string, readonly [number, number]>> = {
  'gmroi.include_dc': [0, 1],
  'gmroi.annualize': [0, 1],
  'oos.include_dc': [0, 1],
  'oos.threshold_qty': [0, 1000],
  'critical.cover_days': [0.5, 30],
  'critical.sales_window_days': [7, 90],
  'fill.grace_days': [0, 7],
  'thr.oos.warn_pct': [0, 100],
  'thr.oos.bad_pct': [0, 100],
  'thr.fill.good_pct': [0, 100],
  'thr.fill.warn_pct': [0, 100],
  'thr.waste.warn_pct': [0, 100],
  'thr.waste.bad_pct': [0, 100],
  'thr.wape.warn_pct': [0, 500],
  'thr.wape.bad_pct': [0, 500],
  'thr.bias.warn_pct': [0, 500],
  'thr.bias.bad_pct': [0, 500],
  'thr.price.warn_pct': [0, 500],
  'thr.price.bad_pct': [0, 500],
  'thr.price.below_pct': [0, 500],
  'thr.price.min_samples': [1, 50],
  'thr.po_late.warn': [0, 1000],
  'thr.po_late.bad': [0, 1000],
  'thr.cutoff.urgent_min': [15, 1440],
  'thr.stock_age.days': [1, 365],
  'fresh.near_live.warn_min': [5, 10080],
  'fresh.near_live.stale_min': [5, 10080],
  'fresh.stock.warn_min': [5, 10080],
  'fresh.stock.stale_min': [5, 10080],
  'fresh.daily.warn_min': [60, 20160],
  'fresh.daily.stale_min': [60, 20160],
};

/**
 * Ambang yang BUKAN bagian konfigurasi Odoo (spesifikasi UX §4.5): biaya voucher vs anggaran. Usulan; bukan keputusan bisnis.
 */
export const SM_FIXED = {
  costWarnPct: 90,
  costBadPct: 100,
} as const;
