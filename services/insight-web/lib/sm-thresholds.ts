/**
 * Konfigurasi KPI supermarket efektif (docs/04 §28.13.8c): baris `v_sm_kpi_config` + fallback default. Murni (tanpa I/O).
 * Aturan gabung antar PT SM (saat ini hanya SMD, jadi satu company): kunci `fresh.*` memakai nilai TERKECIL (paling ketat,
 * §28.13.10); kunci lain memakai nilai company ber-id terkecil yang sah (deterministik). Nilai di luar pilihan/rentang atau
 * pasangan ambang yang tidak konsisten (warn >= bad) dikembalikan ke default, tidak pernah melempar galat.
 */
import { K_ANON, SM_KPI_DEFAULTS, SM_KPI_ENUMS, SM_KPI_RANGES, type KpiValue } from './sm-defaults';

export interface KpiConfigRow {
  company_id: number;
  key: string;
  value_num: number | null;
  value_str: string;
  is_default: number;
  effective_since: string | null;
}

export interface SmConfig {
  values: Readonly<Record<string, KpiValue>>;
  /** Waktu perubahan definisi terbaru (untuk tombol "i"); null bila semua default. */
  effectiveSince: string | null;
  /** true bila tidak ada baris sah dari ClickHouse (kueri gagal / kosong). */
  fallback: boolean;
}

export interface FreshLimits {
  warnMin: number;
  staleMin: number;
}

export interface SmThresholds {
  oosWarn: number;
  oosBad: number;
  fillGood: number;
  fillWarn: number;
  wasteWarn: number;
  wasteBad: number;
  wapeWarn: number;
  wapeBad: number;
  biasWarn: number;
  biasBad: number;
  priceWarn: number;
  priceBad: number;
  priceBelow: number;
  priceMinSamples: number;
  poLateWarn: number;
  poLateBad: number;
  cutoffUrgentMin: number;
  stockAgeDays: number;
  fresh: { near_live: FreshLimits; stock: FreshLimits; daily: FreshLimits };
  kAnonymity: number;
}

/** Pasangan [ambang peringatan, ambang bermasalah]: nilai pertama harus < nilai kedua (konsisten, §28.13.8c). */
const PAIRS: readonly (readonly [string, string])[] = [
  ['thr.oos.warn_pct', 'thr.oos.bad_pct'],
  ['thr.waste.warn_pct', 'thr.waste.bad_pct'],
  ['thr.wape.warn_pct', 'thr.wape.bad_pct'],
  ['thr.bias.warn_pct', 'thr.bias.bad_pct'],
  ['thr.price.warn_pct', 'thr.price.bad_pct'],
  ['thr.po_late.warn', 'thr.po_late.bad'],
  ['fresh.near_live.warn_min', 'fresh.near_live.stale_min'],
  ['fresh.stock.warn_min', 'fresh.stock.stale_min'],
  ['fresh.daily.warn_min', 'fresh.daily.stale_min'],
];
/** Fill rate: ambang naik = baik, jadi warn < good. */
const FILL_PAIR: readonly [string, string] = ['thr.fill.warn_pct', 'thr.fill.good_pct'];

function validNumber(key: string, v: number | null): v is number {
  if (v === null || typeof v !== 'number' || !Number.isFinite(v)) return false;
  const r = SM_KPI_RANGES[key];
  return r === undefined ? true : v >= r[0] && v <= r[1];
}

export function buildSmConfig(rows: readonly KpiConfigRow[]): SmConfig {
  const values: Record<string, KpiValue> = { ...SM_KPI_DEFAULTS };
  const sorted = [...rows].sort((a, b) => a.company_id - b.company_id);
  let used = 0;
  let since: string | null = null;

  for (const key of Object.keys(SM_KPI_DEFAULTS)) {
    const mine = sorted.filter((r) => r.key === key);
    if (mine.length === 0) continue;
    const enumChoices = SM_KPI_ENUMS[key];
    if (enumChoices) {
      const hit = mine.find((r) => enumChoices.includes(r.value_str));
      if (hit) {
        values[key] = hit.value_str;
        used += 1;
      }
    } else {
      const ok = mine.filter((r) => validNumber(key, r.value_num)).map((r) => r.value_num as number);
      if (ok.length > 0) {
        values[key] = key.startsWith('fresh.') ? Math.min(...ok) : ok[0];
        used += 1;
      }
    }
    for (const r of mine) {
      if (r.is_default === 0 && r.effective_since && (since === null || r.effective_since > since)) since = r.effective_since;
    }
  }

  const resetPair = (a: string, b: string): void => {
    values[a] = SM_KPI_DEFAULTS[a];
    values[b] = SM_KPI_DEFAULTS[b];
  };
  for (const [lo, hi] of PAIRS) {
    if (!((values[lo] as number) < (values[hi] as number))) resetPair(lo, hi);
  }
  if (!((values[FILL_PAIR[0]] as number) < (values[FILL_PAIR[1]] as number))) resetPair(FILL_PAIR[0], FILL_PAIR[1]);

  return { values, effectiveSince: since, fallback: used === 0 };
}

export const cfgNum = (c: SmConfig, key: string): number => {
  const v = c.values[key];
  return typeof v === 'number' ? v : (SM_KPI_DEFAULTS[key] as number);
};
export const cfgStr = (c: SmConfig, key: string): string => {
  const v = c.values[key];
  return typeof v === 'string' ? v : String(SM_KPI_DEFAULTS[key]);
};

export function buildThresholds(c: SmConfig): SmThresholds {
  const n = (k: string): number => cfgNum(c, k);
  return {
    oosWarn: n('thr.oos.warn_pct'),
    oosBad: n('thr.oos.bad_pct'),
    fillGood: n('thr.fill.good_pct'),
    fillWarn: n('thr.fill.warn_pct'),
    wasteWarn: n('thr.waste.warn_pct'),
    wasteBad: n('thr.waste.bad_pct'),
    wapeWarn: n('thr.wape.warn_pct'),
    wapeBad: n('thr.wape.bad_pct'),
    biasWarn: n('thr.bias.warn_pct'),
    biasBad: n('thr.bias.bad_pct'),
    priceWarn: n('thr.price.warn_pct'),
    priceBad: n('thr.price.bad_pct'),
    priceBelow: n('thr.price.below_pct'),
    priceMinSamples: n('thr.price.min_samples'),
    poLateWarn: n('thr.po_late.warn'),
    poLateBad: n('thr.po_late.bad'),
    cutoffUrgentMin: n('thr.cutoff.urgent_min'),
    stockAgeDays: n('thr.stock_age.days'),
    fresh: {
      near_live: { warnMin: n('fresh.near_live.warn_min'), staleMin: n('fresh.near_live.stale_min') },
      stock: { warnMin: n('fresh.stock.warn_min'), staleMin: n('fresh.stock.stale_min') },
      daily: { warnMin: n('fresh.daily.warn_min'), staleMin: n('fresh.daily.stale_min') },
    },
    kAnonymity: K_ANON,
  };
}

/** Konfigurasi default penuh (dipakai bila kueri `sm_kpi_config` gagal atau halaman tanpa konfigurasi). */
export const DEFAULT_SM_CONFIG: SmConfig = buildSmConfig([]);
