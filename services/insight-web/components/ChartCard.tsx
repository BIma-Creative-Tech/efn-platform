'use client';
/**
 * Kartu chart (spec §5-§6): <figure> + <figcaption>, ringkasan aria-label, toggle "Tampilkan sebagai tabel"
 * (tabel alternatif dari data yang sama), toggle pola (dekal), tab metrik bila ada beberapa spec, state kosong.
 */
import dynamic from 'next/dynamic';
import { useId, useMemo, useState } from 'react';
import { legendItems, toggleHidden } from '@/lib/chart-legend';
import { hasData, type ChartSpec } from '@/lib/chart-spec';
import { fmtValue } from '@/lib/format';
import { TermHints } from './Term';
import { EmptyState } from './ui';

const NO_HIDDEN: string[] = [];

const EChart = dynamic(() => import('./EChart'), {
  ssr: false,
  loading: () => <div className="chart-skeleton" aria-hidden="true" />,
});

function SpecTable({ spec }: { spec: ChartSpec }): React.ReactNode {
  if (spec.kind === 'heat') {
    const rows = spec.yCategories ?? [];
    const map = new Map<string, number | null>();
    for (const [x, y, v] of spec.heat ?? []) map.set(`${x}:${y}`, v);
    return (
      <div className="table-wrap" tabIndex={0} role="region" aria-label="Tabel data chart">
        <table className="table">
          <thead>
            <tr>
              <th scope="col">{spec.series[0]?.name ?? ''}</th>
              {spec.categories.map((c) => (
                <th key={c} scope="col" className="num">
                  {c}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r, y) => (
              <tr key={r}>
                <th scope="row">{r}</th>
                {spec.categories.map((c, x) => {
                  const v = map.get(`${x}:${y}`);
                  return (
                    <td key={c} className="num">
                      {v === null || v === undefined ? '-' : fmtValue(spec.fmt, v)}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }
  return (
    <div className="table-wrap" tabIndex={0} role="region" aria-label="Tabel data chart">
      <table className="table">
        <thead>
          <tr>
            <th scope="col">Kategori</th>
            {spec.series.map((s) => (
              <th key={s.name} scope="col" className="num">
                {s.name}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {spec.categories.map((c, i) => (
            <tr key={`${c}-${i}`}>
              <th scope="row">{c}</th>
              {spec.series.map((s) => (
                <td key={s.name} className="num">
                  {s.data[i] === null || s.data[i] === undefined ? '-' : fmtValue(spec.fmt, s.data[i] as number)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function HeatLegend({ spec }: { spec: ChartSpec }): React.ReactNode {
  const vals = (spec.heat ?? []).map((c) => c[2]).filter((v): v is number => v !== null);
  if (vals.length === 0) return null;
  return (
    <div className="heat-legend" aria-hidden="true">
      <span>{fmtValue(spec.axisFmt ?? spec.fmt, Math.min(...vals))}</span>
      <span className="heat-bar" />
      <span>{fmtValue(spec.axisFmt ?? spec.fmt, Math.max(...vals))}</span>
    </div>
  );
}

export function ChartCard({
  title,
  subtitle,
  specs,
  height = 280,
  emptyText = 'Tidak ada data pada periode ini.',
  wide = false,
}: {
  title: string;
  subtitle?: string;
  specs: ChartSpec[];
  height?: number;
  emptyText?: string;
  wide?: boolean;
}): React.ReactNode {
  const uid = useId();
  const [tab, setTab] = useState(0);
  const [asTable, setAsTable] = useState(false);
  const [pattern, setPattern] = useState(false);
  const [hiddenMap, setHiddenMap] = useState<Record<string, string[]>>({});
  const spec = specs[Math.min(tab, specs.length - 1)];
  const ready = useMemo(() => (spec ? hasData(spec) : false), [spec]);
  const summaryId = `${uid}-sum`;
  const hidden = useMemo(() => (spec ? (hiddenMap[spec.id] ?? NO_HIDDEN) : NO_HIDDEN), [hiddenMap, spec]);
  const items = useMemo(() => (spec ? legendItems(spec) : []), [spec]);

  return (
    <figure className={`card chart-card${wide ? ' wide' : ''}`}>
      <figcaption className="card-head">
        <div>
          <span className="card-title">{title}</span>
          <TermHints text={title} />
          {subtitle ? <span className="card-sub">{subtitle}</span> : null}
        </div>
        {ready ? (
          <div className="card-tools">
            {specs.length > 1 ? (
              <div role="group" aria-label={`Metrik ${title}`} className="seg">
                {specs.map((s, i) => (
                  <button key={s.id} type="button" aria-pressed={i === tab} onClick={() => setTab(i)}>
                    {s.tabLabel ?? s.id}
                  </button>
                ))}
              </div>
            ) : null}
            {!asTable && spec.kind !== 'heat' ? (
              <button type="button" className="btn btn-sm" aria-pressed={pattern} onClick={() => setPattern((p) => !p)}>
                Pola
              </button>
            ) : null}
            <button type="button" className="btn btn-sm" aria-pressed={asTable} onClick={() => setAsTable((p) => !p)}>
              Tampilkan sebagai tabel
            </button>
          </div>
        ) : null}
      </figcaption>
      {!spec || !ready ? (
        <EmptyState title={emptyText} reason="Filter mungkin terlalu sempit, atau data belum masuk. Periksa halaman Kualitas Data." />
      ) : asTable ? (
        <SpecTable spec={spec} />
      ) : (
        <>
          {items.length > 0 ? (
            <ul className="legend" aria-label={`Legend: ${title}`}>
              {items.map((it) => {
                const on = !hidden.includes(it.name);
                return (
                  <li key={it.name}>
                    <button
                      type="button"
                      className="legend-chip"
                      aria-pressed={on}
                      title={`${on ? 'Sembunyikan' : 'Tampilkan'} seri ${it.name}`}
                      onClick={() =>
                        setHiddenMap((m) => ({
                          ...m,
                          [spec.id]: toggleHidden(m[spec.id] ?? NO_HIDDEN, it.name, items.map((x) => x.name)),
                        }))
                      }
                    >
                      <span className={`legend-mark sw-${it.color}`} aria-hidden="true">
                        {it.glyph}
                      </span>
                      <span className="legend-name">{it.name}</span>
                      {it.dashed ? <span className="legend-dash" aria-hidden="true">putus-putus</span> : null}
                      {it.value ? <span className="legend-value">{it.value}</span> : null}
                    </button>
                  </li>
                );
              })}
            </ul>
          ) : null}
          <EChart spec={spec} height={height} summaryId={summaryId} pattern={pattern} hidden={hidden} />
          {spec.kind === 'heat' ? <HeatLegend spec={spec} /> : null}
        </>
      )}
      <p id={summaryId} className="sr-only">
        {spec?.summary}
      </p>
    </figure>
  );
}
