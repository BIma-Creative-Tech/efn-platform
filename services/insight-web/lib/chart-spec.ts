/**
 * Spesifikasi chart yang DAPAT DISERIALISASI (server -> komponen klien). Fungsi formatter dan warna dirakit di klien
 * (lib/echart-option.ts) dari CSS custom properties, jadi tidak ada warna ter-hard-code (R-T14) dan tidak ada eval.
 */
import { fmtDayShort, fmtValue, type ValueFmt } from './format';
import type { Marker, SeriesColor } from './brands';

export type ChartKind = 'line' | 'bar' | 'barH' | 'heat';
export type ColorToken = SeriesColor | 'accent' | 'muted' | 'good' | 'warn' | 'bad';

export interface ChartSeries {
  name: string;
  data: (number | null)[];
  color: ColorToken;
  marker?: Marker;
  type?: 'line' | 'bar';
  dashed?: boolean;
  area?: boolean;
  stack?: string;
  /** Warna per butir (mis. batang menurut status). Menimpa `color` per indeks. */
  itemColors?: ColorToken[];
}

export interface ChartSpec {
  id: string;
  tabLabel?: string;
  kind: ChartKind;
  fmt: ValueFmt;
  categories: string[];
  /** Heatmap: label sumbu Y; `heat` = [xIndex, yIndex, nilai]. */
  yCategories?: string[];
  heat?: [number, number, number | null][];
  series: ChartSeries[];
  refLines?: { value: number; label: string }[];
  /** Klik/Enter pada butir ke-i menambah filter `param` = values[i]. */
  drill?: { param: 'branch' | 'cat' | 'product'; values: string[]; label: string };
  /** Ringkasan satu kalimat (aria-label), dihitung dari data. */
  summary: string;
  /** Format nilai sumbu (default = fmt; mis. 'idr-short' untuk sumbu, 'idr' untuk tooltip). */
  axisFmt?: ValueFmt;
}

/** Ringkasan: "<judul>: tertinggi X pada A, terendah Y pada B[, total Z]". */
export function summarize(
  title: string,
  categories: readonly string[],
  values: readonly (number | null)[],
  fmt: ValueFmt,
  withTotal: boolean,
  isDate = false,
): string {
  let hi = -1;
  let lo = -1;
  let total = 0;
  values.forEach((v, i) => {
    if (v === null || !Number.isFinite(v)) return;
    total += v;
    if (hi < 0 || v > (values[hi] as number)) hi = i;
    if (lo < 0 || v < (values[lo] as number)) lo = i;
  });
  if (hi < 0) return `${title}: tidak ada data`;
  const label = (i: number) => (isDate ? fmtDayShort(categories[i]) : categories[i]);
  const parts = [
    `tertinggi ${fmtValue(fmt, values[hi] as number)} pada ${label(hi)}`,
    `terendah ${fmtValue(fmt, values[lo] as number)} pada ${label(lo)}`,
  ];
  if (withTotal) parts.push(`total ${fmtValue(fmt, total)}`);
  return `${title}: ${parts.join(', ')}`;
}

export function hasData(spec: ChartSpec): boolean {
  if (spec.kind === 'heat') return (spec.heat ?? []).some((c) => c[2] !== null);
  return spec.series.some((s) => s.data.some((v) => v !== null && v !== 0));
}
