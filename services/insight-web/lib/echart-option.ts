/**
 * Perakit opsi ECharts dari ChartSpec (dijalankan di KLIEN). Semua warna dibaca dari CSS custom properties saat mount
 * dan dibaca ulang saat tema berubah -- tidak ada warna ter-hard-code. Renderer SVG; tidak ada eval/new Function.
 * Tooltip memakai HTML minimal tanpa atribut style (penanda = glyph bentuk, bukan hanya warna).
 */
import type { EChartsCoreOption } from 'echarts/core';
import { markerFor } from './brands';
import type { ChartSpec, ColorToken } from './chart-spec';
import { textOn } from './contrast';
import { escapeHtml, fmtValue, type ValueFmt } from './format';

export interface ThemeColors {
  text: string;
  muted: string;
  surface: string;
  border: string;
  grid: string;
  accent: string;
  good: string;
  warn: string;
  bad: string;
  c: Record<string, string>;
  seq: string[];
  font: string;
}

function css(style: CSSStyleDeclaration, name: string, fallback: string): string {
  const v = style.getPropertyValue(name).trim();
  return v === '' ? fallback : v;
}

export function readTheme(el: Element = document.documentElement): ThemeColors {
  const s = getComputedStyle(el);
  const c: Record<string, string> = {};
  for (let i = 1; i <= 7; i += 1) c[`c${i}`] = css(s, `--c${i}`, '#6b7a77');
  return {
    text: css(s, '--text', '#10201e'),
    muted: css(s, '--text-muted', '#4a5b58'),
    surface: css(s, '--surface', '#ffffff'),
    border: css(s, '--border', '#d5dedc'),
    grid: css(s, '--grid', '#e3eae8'),
    accent: css(s, '--accent', '#0a5c54'),
    good: css(s, '--good', '#0f7a3d'),
    warn: css(s, '--warn', '#8a5a00'),
    bad: css(s, '--bad', '#b42318'),
    c,
    seq: [1, 2, 3, 4, 5].map((i) => css(s, `--seq-${i}`, '#6fbcb1')),
    font: css(s, '--font-body', 'system-ui, sans-serif'),
  };
}

export function resolveColor(t: ThemeColors, token: ColorToken): string {
  switch (token) {
    case 'accent':
      return t.accent;
    case 'muted':
      return t.muted;
    case 'good':
      return t.good;
    case 'warn':
      return t.warn;
    case 'bad':
      return t.bad;
    default:
      return t.c[token] ?? t.muted;
  }
}

const GLYPH: Record<string, string> = {
  circle: '●',
  rect: '■',
  triangle: '▲',
  diamond: '◆',
  arrow: '△',
  pin: '✖',
  roundRect: '▪',
};

interface TipParam {
  seriesIndex: number;
  seriesName: string;
  value: number | null | (number | null)[];
  name: string;
  axisValueLabel?: string;
}

function scalar(v: TipParam['value']): number | null {
  return Array.isArray(v) ? (v.length > 0 ? v[v.length - 1] : null) : v;
}

export interface BuildOpts {
  animate: boolean;
  decal: boolean;
}

export function buildOption(spec: ChartSpec, t: ThemeColors, o: BuildOpts): EChartsCoreOption {
  const tipFmt: ValueFmt = spec.fmt;
  const axisFmt: ValueFmt = spec.axisFmt ?? (spec.fmt === 'idr' ? 'idr-short' : spec.fmt);
  const showLegend = spec.series.length > 3;
  const base = {
    aria: { enabled: true, decal: { show: o.decal } },
    animation: o.animate,
    animationDuration: 300,
    animationEasing: 'cubicOut',
    textStyle: { fontFamily: t.font, color: t.text },
    legend: showLegend
      ? { type: 'plain', top: 0, left: 0, textStyle: { color: t.text }, inactiveColor: t.muted, icon: 'roundRect' }
      : { show: false },
  };

  if (spec.kind === 'heat') return heatOption(spec, t, axisFmt, base);

  const horizontal = spec.kind === 'barH';
  const catAxis = {
    type: 'category',
    data: spec.categories,
    inverse: horizontal,
    axisTick: { show: false },
    axisLine: { lineStyle: { color: t.grid } },
    axisLabel: { color: t.text, hideOverlap: true, width: horizontal ? 160 : undefined, overflow: 'truncate' },
  };
  const valAxis = {
    type: 'value',
    axisLabel: { color: t.muted, hideOverlap: true, formatter: (v: number) => fmtValue(axisFmt, v) },
    axisLine: { show: false },
    axisTick: { show: false },
    splitLine: horizontal ? { show: false } : { lineStyle: { color: t.grid, width: 1 } },
  };

  const series = spec.series.map((s, si) => {
    const color = resolveColor(t, s.color);
    const type = s.type ?? (spec.kind === 'line' ? 'line' : 'bar');
    const data = s.itemColors
      ? s.data.map((v, i) => ({ value: v, itemStyle: { color: resolveColor(t, s.itemColors![i] ?? s.color) } }))
      : s.data;
    const common: Record<string, unknown> = { type, name: s.name, data, stack: s.stack, emphasis: { focus: 'series' } };
    if (type === 'line') {
      Object.assign(common, {
        symbol: s.marker ?? markerFor(s.color as never),
        symbolSize: 6,
        showSymbol: spec.categories.length <= 45,
        lineStyle: { color, width: s.dashed ? 1.5 : 2, type: s.dashed ? 'dashed' : 'solid' },
        itemStyle: { color },
        areaStyle: s.area ? { color, opacity: 0.18 } : undefined,
        connectNulls: false,
      });
    } else {
      Object.assign(common, {
        barMaxWidth: 28,
        itemStyle: { color },
        label: horizontal
          ? {
              show: true,
              position: 'right',
              color: t.text,
              formatter: (p: TipParam) => {
                const v = scalar(p.value);
                return v === null ? '' : fmtValue(tipFmt, v);
              },
            }
          : { show: false },
        labelLayout: { hideOverlap: true },
      });
    }
    if (si === 0 && spec.refLines && spec.refLines.length > 0) {
      common.markLine = {
        silent: true,
        symbol: 'none',
        lineStyle: { color: t.muted, width: 2, type: 'dashed' },
        label: { color: t.muted },
        data: spec.refLines.map((r) =>
          horizontal ? { xAxis: r.value, label: { formatter: r.label } } : { yAxis: r.value, label: { formatter: r.label } },
        ),
      };
    }
    return common;
  });

  return {
    ...base,
    grid: {
      left: 8,
      right: horizontal ? 72 : 24,
      top: showLegend ? 36 : 16,
      bottom: 8,
      containLabel: true,
    },
    xAxis: horizontal ? valAxis : catAxis,
    yAxis: horizontal ? catAxis : valAxis,
    tooltip: {
      trigger: 'axis',
      confine: true,
      axisPointer: { type: horizontal ? 'shadow' : 'line' },
      backgroundColor: t.surface,
      borderColor: t.border,
      textStyle: { color: t.text },
      formatter: (params: TipParam | TipParam[]) => {
        const list = Array.isArray(params) ? params : [params];
        if (list.length === 0) return '';
        const title = escapeHtml(String(list[0].axisValueLabel ?? list[0].name));
        const rows = list.map((p) => {
          const v = scalar(p.value);
          const sp = spec.series[p.seriesIndex];
          const glyph = GLYPH[sp?.marker ?? markerFor((sp?.color ?? 'c7') as never)] ?? GLYPH.circle;
          return `${glyph} ${escapeHtml(p.seriesName)}: <b>${v === null ? '-' : fmtValue(tipFmt, v)}</b>`;
        });
        return `<b>${title}</b><br/>${rows.join('<br/>')}`;
      },
    },
    series,
  } as unknown as EChartsCoreOption;
}

function heatOption(spec: ChartSpec, t: ThemeColors, axisFmt: ValueFmt, base: object): EChartsCoreOption {
  const cells = spec.heat ?? [];
  const vals = cells.map((c) => c[2]).filter((v): v is number => v !== null);
  const min = vals.length ? Math.min(...vals) : 0;
  const max = vals.length ? Math.max(...vals) : 0;
  const data = cells.map(([x, y, v]) => {
    if (v === null) return { value: [x, y, '-'], itemStyle: { color: t.surface, borderColor: t.grid }, label: { show: false } };
    const idx = max === min ? 2 : Math.min(4, Math.floor(((v - min) / (max - min)) * 5));
    const bg = t.seq[idx];
    let fg = t.text;
    try {
      fg = textOn(bg);
    } catch {
      /* warna bukan hex 6 digit: pakai teks default */
    }
    return { value: [x, y, v], itemStyle: { color: bg, borderColor: t.surface, borderWidth: 1 }, label: { color: fg } };
  });
  return {
    ...base,
    grid: { left: 8, right: 16, top: 16, bottom: 8, containLabel: true },
    xAxis: { type: 'category', data: spec.categories, splitArea: { show: false }, axisTick: { show: false }, axisLabel: { color: t.text } },
    yAxis: {
      type: 'category',
      data: spec.yCategories ?? [],
      inverse: true,
      axisTick: { show: false },
      axisLabel: { color: t.text },
    },
    tooltip: {
      trigger: 'item',
      confine: true,
      backgroundColor: t.surface,
      borderColor: t.border,
      textStyle: { color: t.text },
      formatter: (p: { value: [number, number, number | string] }) => {
        const [x, y, v] = p.value;
        const val = typeof v === 'number' ? fmtValue(spec.fmt, v) : '-';
        return `<b>${escapeHtml(spec.yCategories?.[y] ?? '')} / ${escapeHtml(spec.categories[x] ?? '')}</b><br/>${val}`;
      },
    },
    series: [
      {
        type: 'heatmap',
        name: spec.series[0]?.name ?? '',
        data,
        label: {
          show: true,
          formatter: (p: { value: [number, number, number | string] }) =>
            typeof p.value[2] === 'number' ? fmtValue(axisFmt, p.value[2]) : '',
        },
        emphasis: { itemStyle: { borderColor: t.text, borderWidth: 2 } },
      },
    ],
  } as unknown as EChartsCoreOption;
}
