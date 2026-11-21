/**
 * Legend HTML grafik (poin 10, review 21 Nov 2026). Murni: item legend (nama, penanda bentuk, total/nilai terakhir) dihitung
 * dari ChartSpec; komponen ChartCard yang merender chip dan mengatur seri yang disembunyikan.
 */
import { markerFor } from './brands';
import type { ChartSeries, ChartSpec, ColorToken } from './chart-spec';
import { fmtValue } from './format';

/** Glyph bentuk per penanda (sama dengan tooltip grafik): bentuk + warna, tidak bergantung warna saja. */
export const MARKER_GLYPH: Readonly<Record<string, string>> = {
  circle: '●',
  rect: '■',
  triangle: '▲',
  diamond: '◆',
  arrow: '△',
  pin: '✖',
  roundRect: '▪',
};

export interface LegendItem {
  name: string;
  color: ColorToken;
  glyph: string;
  /** Teks nilai ringkas, mis. "Total Rp 12 jt" atau "Terakhir 48,2%"; kosong bila seri tanpa data. */
  value: string;
  dashed: boolean;
}

export function seriesGlyph(s: Pick<ChartSeries, 'marker' | 'color'>): string {
  // Sama dengan simbol garis/tooltip di echart-option.ts (token non-c1..c7 jatuh ke lingkaran).
  const m = s.marker ?? markerFor(s.color as never);
  return MARKER_GLYPH[m] ?? MARKER_GLYPH.circle;
}

/** Persentase/poin: dijumlahkan tidak bermakna -> nilai terakhir; selain itu total seri. */
export function legendValue(fmt: ChartSpec['fmt'], data: readonly (number | null)[]): string {
  const vals = data.filter((v): v is number => v !== null && Number.isFinite(v));
  if (vals.length === 0) return '';
  if (fmt === 'pct' || fmt === 'pp') return `Terakhir ${fmtValue(fmt, vals[vals.length - 1])}`;
  return `Total ${fmtValue(fmt === 'idr-short' ? 'idr' : fmt, vals.reduce((a, b) => a + b, 0))}`;
}

/** Item legend untuk chart multi-seri (>= 2 seri, bukan heatmap); selain itu kosong. */
export function legendItems(spec: ChartSpec): LegendItem[] {
  if (spec.kind === 'heat' || spec.series.length < 2) return [];
  return spec.series.map((s) => ({
    name: s.name,
    color: s.color,
    glyph: seriesGlyph(s),
    value: legendValue(spec.fmt, s.data),
    dashed: s.dashed === true,
  }));
}

/** Toggle nama pada daftar seri tersembunyi; tidak pernah menyembunyikan seri TERAKHIR yang tampil. */
export function toggleHidden(hidden: readonly string[], name: string, all: readonly string[]): string[] {
  if (hidden.includes(name)) return hidden.filter((h) => h !== name);
  const visible = all.filter((n) => !hidden.includes(n));
  if (visible.length <= 1) return [...hidden];
  return [...hidden, name];
}
