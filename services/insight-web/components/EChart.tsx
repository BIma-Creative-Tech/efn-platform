'use client';
/**
 * Pembungkus ECharts (impor modular, renderer SVG). Dimuat dinamis oleh ChartCard (ssr:false) sehingga bundle halaman
 * tanpa chart tidak memuat ECharts. Tema dibaca dari CSS var dan dibangun ulang saat `data-theme` / skema warna berubah.
 * Keyboard (spec §5): panah kiri/kanan antar butir, Home/End, Enter = drill-down, Esc = tutup tooltip.
 */
import { BarChart, HeatmapChart, LineChart } from 'echarts/charts';
import { AriaComponent, GridComponent, LegendComponent, MarkLineComponent, TooltipComponent } from 'echarts/components';
import * as echarts from 'echarts/core';
import { SVGRenderer } from 'echarts/renderers';
import { useCallback, useEffect, useRef, useState } from 'react';
import type { ChartSpec } from '@/lib/chart-spec';
import { announce, useDrill } from '@/lib/client-nav';
import { buildOption, readTheme } from '@/lib/echart-option';
import { fmtValue } from '@/lib/format';

echarts.use([LineChart, BarChart, HeatmapChart, GridComponent, TooltipComponent, LegendComponent, AriaComponent, MarkLineComponent, SVGRenderer]);

interface Props {
  spec: ChartSpec;
  height: number;
  summaryId: string;
  pattern: boolean;
}

export default function EChart({ spec, height, summaryId, pattern }: Props): React.ReactNode {
  const host = useRef<HTMLDivElement>(null);
  const chart = useRef<echarts.ECharts | null>(null);
  const idx = useRef(0);
  const [themeVersion, setThemeVersion] = useState(0);
  const drill = useDrill();
  // Ref ke spec/drill terbaru agar handler klik (didaftarkan sekali) tidak memakai closure basi.
  const latest = useRef({ spec, drill });
  useEffect(() => {
    latest.current = { spec, drill };
  }, [spec, drill]);

  // Inisialisasi + ukuran kontainer.
  useEffect(() => {
    const el = host.current;
    if (!el) return;
    const c = echarts.init(el, undefined, { renderer: 'svg' });
    chart.current = c;
    const ro = new ResizeObserver(() => c.resize());
    ro.observe(el);
    c.on('click', (p: unknown) => {
      const di = (p as { dataIndex?: number }).dataIndex;
      const { spec: s, drill: d } = latest.current;
      if (s.drill && typeof di === 'number' && s.drill.values[di]) {
        d(s.drill.param, s.drill.values[di], s.categories[di] ?? s.drill.values[di]);
      }
    });
    return () => {
      ro.disconnect();
      c.dispose();
      chart.current = null;
    };
  }, []);

  // Baca ulang tema saat data-theme atau skema warna sistem berubah.
  useEffect(() => {
    const bump = () => setThemeVersion((v) => v + 1);
    const mo = new MutationObserver(bump);
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
    const mq = window.matchMedia('(prefers-color-scheme: dark)');
    mq.addEventListener('change', bump);
    return () => {
      mo.disconnect();
      mq.removeEventListener('change', bump);
    };
  }, []);

  // Pasang opsi.
  useEffect(() => {
    const c = chart.current;
    if (!c) return;
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const contrast = window.matchMedia('(prefers-contrast: more)').matches;
    c.setOption(buildOption(spec, readTheme(), { animate: !reduced, decal: pattern || contrast }), true);
  }, [spec, pattern, themeVersion]);

  const count = spec.categories.length;
  const describe = useCallback(
    (i: number): string => {
      const parts = spec.series.map((s) => {
        const v = s.data[i];
        return `${s.name} ${v === null || v === undefined ? 'tidak ada data' : fmtValue(spec.fmt, v)}`;
      });
      return `${spec.categories[i]}: ${parts.join(', ')}. Butir ${i + 1} dari ${count}.`;
    },
    [spec, count],
  );

  const show = useCallback(
    (i: number) => {
      const c = chart.current;
      if (!c || count === 0) return;
      idx.current = Math.max(0, Math.min(count - 1, i));
      c.dispatchAction({ type: 'showTip', seriesIndex: 0, dataIndex: idx.current });
      announce(describe(idx.current));
    },
    [count, describe],
  );

  const onKeyDown = (e: React.KeyboardEvent<HTMLDivElement>): void => {
    if (spec.kind === 'heat') return;
    switch (e.key) {
      case 'ArrowRight':
      case 'ArrowDown':
        e.preventDefault();
        show(idx.current + 1);
        break;
      case 'ArrowLeft':
      case 'ArrowUp':
        e.preventDefault();
        show(idx.current - 1);
        break;
      case 'Home':
        e.preventDefault();
        show(0);
        break;
      case 'End':
        e.preventDefault();
        show(count - 1);
        break;
      case 'Enter':
        if (spec.drill && spec.drill.values[idx.current]) {
          e.preventDefault();
          drill(spec.drill.param, spec.drill.values[idx.current], spec.categories[idx.current]);
        }
        break;
      case 'Escape':
        chart.current?.dispatchAction({ type: 'hideTip' });
        break;
      default:
    }
  };

  return (
    <div
      ref={host}
      className="chart-host"
      role="img"
      aria-label={spec.summary}
      aria-describedby={summaryId}
      tabIndex={0}
      onKeyDown={onKeyDown}
      style={{ height }}
    />
  );
}
