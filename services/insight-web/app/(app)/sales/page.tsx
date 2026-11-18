import type { Metadata } from 'next';
import Link from 'next/link';
import { Block } from '@/components/blocks';
import { ChartCard } from '@/components/ChartCard';
import { DataTable, type TableRow } from '@/components/DataTable';
import { FilterNotice } from '@/components/FilterNotice';
import { KpiRow, KpiTile, PageHeader, Section } from '@/components/ui';
import { brandName } from '@/lib/brands';
import { summarize, type ChartSpec } from '@/lib/chart-spec';
import { fmtDayShort, fmtRange, WEEKDAYS } from '@/lib/format';
import { num, numOrNull, run, settle, TABLE_LOADERS, topMenuRow } from '@/lib/loaders';
import { pageCtx, type SearchParams } from '@/lib/page-ctx';
import { TABLES } from '@/lib/table-defs';
import { discountStatus, marginStatus, T } from '@/lib/thresholds';
import { pctDelta, pctOf, ppDelta, withOther } from '@/lib/vm';

export const dynamic = 'force-dynamic';
export const metadata: Metadata = { title: 'Penjualan & Menu' };

type Sort = 'net' | 'qty' | 'margin';
const SORTS: { id: Sort; label: string }[] = [
  { id: 'net', label: 'Penjualan bersih' },
  { id: 'qty', label: 'Qty' },
  { id: 'margin', label: 'Margin' },
];

function href(raw: Record<string, string | string[] | undefined>, set: Record<string, string>): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(raw)) {
    if (k in set || v === undefined) continue;
    for (const x of Array.isArray(v) ? v : [v]) p.append(k, x);
  }
  for (const [k, v] of Object.entries(set)) p.set(k, v);
  return `/sales?${p.toString()}`;
}

interface Kpi {
  net_cur: number; net_prev: number; gross_cur: number; gross_prev: number; discount_cur: number; discount_prev: number;
  qty_cur: number; qty_prev: number; cogs_cur: number; cogs_prev: number;
}
interface CatRow { category: string; net_sales: number; qty_sold: number; margin_pct: number | null }

export default async function SalesPage({ searchParams }: { searchParams: SearchParams }) {
  const { ctx, adjusted, raw } = await pageCtx(searchParams);
  const { from, to } = ctx.filters;
  const rawSort = Array.isArray(raw.sort) ? raw.sort[0] : raw.sort;
  const sort: Sort = rawSort === 'qty' || rawSort === 'margin' ? rawSort : 'net';
  const bottom = (Array.isArray(raw.rank) ? raw.rank[0] : raw.rank) === 'bottom';

  const [kpiR, dowR, heatR, catR, chanR, menuR, mixR] = await Promise.all([
    settle(run<Kpi>('sales_kpi', ctx)),
    settle(run<{ dow: number; avg_net: number; days: number }>('sales_by_weekday', ctx)),
    settle(run<{ week: string; dow: number; net: number }>('sales_weekday_heatmap', ctx)),
    settle(run<CatRow>('sales_by_category', ctx)),
    settle(run<{ brand: string; net_takeaway: number; net_dine_in: number }>('sales_channel_split', ctx, { by_branch: 0 })),
    settle(
      run<{ brand: string; menu_code: string; menu_label: string; category: string; net_sales: number; qty_sold: number; margin_pct: number | null }>(
        'sales_top_menu',
        ctx,
        { sort },
      ),
    ),
    settle(TABLE_LOADERS['menu-mix-branch'](ctx)),
  ]);

  const catSpecs = (rows: CatRow[]): ChartSpec[] => {
    const byMetric = (m: 'net' | 'qty'): ChartSpec => {
      const sorted = [...rows].sort((a, b) => (m === 'net' ? num(b.net_sales) - num(a.net_sales) : num(b.qty_sold) - num(a.qty_sold)));
      const items = withOther(sorted.map((r) => ({ label: r.category, value: m === 'net' ? num(r.net_sales) : num(r.qty_sold) })), 8);
      const cats = items.map((i) => i.label);
      const vals = items.map((i) => i.value);
      return {
        id: m,
        tabLabel: m === 'net' ? 'Penjualan bersih' : 'Qty',
        kind: 'barH',
        fmt: m === 'net' ? 'idr' : 'num',
        axisFmt: m === 'net' ? 'idr-short' : 'num',
        categories: cats,
        series: [{ name: m === 'net' ? 'Penjualan bersih' : 'Qty', data: vals, color: 'c1' }],
        drill: { param: 'cat', values: cats.map((c) => (c === 'Lainnya' || c === 'Tanpa kategori' ? '' : c)), label: 'kategori' },
        summary: summarize('Bauran kategori', cats, vals, m === 'net' ? 'idr-short' : 'num', true),
      };
    };
    const top = [...rows].sort((a, b) => num(b.net_sales) - num(a.net_sales)).slice(0, 8);
    const mc = top.map((r) => r.category);
    const mv = top.map((r) => numOrNull(r.margin_pct));
    return [
      byMetric('net'),
      byMetric('qty'),
      { id: 'margin', tabLabel: 'Margin', kind: 'barH', fmt: 'pct', categories: mc, series: [{ name: 'Margin kotor %', data: mv, color: 'c3' }], summary: summarize('Margin kotor per kategori', mc, mv, 'pct', false) },
    ];
  };

  const menuRows = menuR.ok ? (bottom ? [...menuR.data].reverse() : menuR.data).slice(0, 10).map(topMenuRow) : [];

  return (
    <>
      <PageHeader title="Penjualan & Menu" question="Menu apa dan hari apa yang menghasilkan penjualan, dan bagaimana bauran takeaway vs dine-in?" />
      <FilterNotice adjusted={adjusted} />
      <p className="muted small">Periode: {fmtRange(from, to)}. Tidak ada data per jam.</p>

      <Section title="Indikator utama" id="kpi">
        <Block result={kpiR} title="Indikator utama">
          {(rows) => {
            const k = rows[0];
            if (!k) return <p>Tidak ada penjualan pada periode ini.</p>;
            const dPct = pctOf(num(k.discount_cur), num(k.gross_cur));
            const mC = pctOf(num(k.net_cur) - num(k.cogs_cur), num(k.net_cur));
            const mP = pctOf(num(k.net_prev) - num(k.cogs_prev), num(k.net_prev));
            return (
              <KpiRow>
                <KpiTile label="Penjualan bersih" value={num(k.net_cur)} fmt="idr-short" note="Sebelum PB1/service" delta={{ value: pctDelta(num(k.net_cur), num(k.net_prev)), kind: 'pct', goodWhen: 'up' }} />
                <KpiTile
                  label="Diskon"
                  value={num(k.discount_cur)}
                  fmt="idr-short"
                  status={discountStatus(dPct)}
                  note={dPct === null ? undefined : `${dPct.toFixed(1).replace('.', ',')}% dari penjualan kotor`}
                  delta={{ value: pctDelta(num(k.discount_cur), num(k.discount_prev)), kind: 'pct', goodWhen: 'neutral' }}
                  info={`Peringatan bila diskon > ${T.discountWarnPct}% dari penjualan kotor (ambang konfigurasi).`}
                />
                <KpiTile label="Qty terjual" value={num(k.qty_cur)} fmt="num" delta={{ value: pctDelta(num(k.qty_cur), num(k.qty_prev)), kind: 'pct', goodWhen: 'up' }} />
                <KpiTile label="Margin kotor" value={mC} fmt="pct" status={marginStatus(mC)} delta={{ value: ppDelta(mC, mP), kind: 'pp', goodWhen: 'up' }} info="(Penjualan bersih - HPP) / penjualan bersih." />
              </KpiRow>
            );
          }}
        </Block>
      </Section>

      <div className="grid-2">
        <Block result={dowR} title="Pola hari dalam minggu">
          {(rows) => {
            const vals: (number | null)[] = WEEKDAYS.map((_, i) => {
              const r = rows.find((x) => Number(x.dow) === i + 1);
              return r ? num(r.avg_net) : null;
            });
            return (
              <ChartCard
                title="Rata-rata penjualan per hari dalam minggu"
                specs={[{ id: 'dow', kind: 'bar', fmt: 'idr', axisFmt: 'idr-short', categories: WEEKDAYS, series: [{ name: 'Rata-rata penjualan bersih', data: vals, color: 'c1' }], summary: summarize('Rata-rata penjualan per hari', WEEKDAYS, vals, 'idr-short', false) }]}
              />
            );
          }}
        </Block>
        <Block result={heatR} title="Minggu dan hari">
          {(rows) => {
            const weeks = Array.from(new Set(rows.map((r) => String(r.week)))).sort();
            const cells: [number, number, number | null][] = rows.map((r) => [Number(r.dow) - 1, weeks.indexOf(String(r.week)), num(r.net)]);
            return (
              <ChartCard
                title="Penjualan per minggu dan hari"
                subtitle="Baris = awal minggu (Senin)"
                height={Math.max(200, weeks.length * 34 + 60)}
                specs={[{ id: 'heat', kind: 'heat', fmt: 'idr', axisFmt: 'idr-short', categories: WEEKDAYS, yCategories: weeks.map(fmtDayShort), heat: cells, series: [{ name: 'Minggu', data: [], color: 'c1' }], summary: 'Peta panas penjualan bersih per minggu dan hari; lihat tabel untuk nilai tiap sel.' }]}
              />
            );
          }}
        </Block>
        <Block result={catR} title="Bauran kategori">
          {(rows) => <ChartCard title="Bauran kategori" subtitle="Klik batang untuk memfilter kategori" specs={catSpecs(rows)} />}
        </Block>
        <Block result={chanR} title="Takeaway vs dine-in">
          {(rows) => {
            const cats = rows.map((r) => brandName(r.brand));
            const tk = rows.map((r) => pctOf(num(r.net_takeaway), num(r.net_takeaway) + num(r.net_dine_in)));
            const di = tk.map((v) => (v === null ? null : 100 - v));
            return (
              <ChartCard
                title="Takeaway vs dine-in"
                subtitle="Porsi penjualan bersih; takeaway = perkiraan dari nama menu ESB"
                specs={[{ id: 'chan', kind: 'bar', fmt: 'pct', axisFmt: 'pct', categories: cats, series: [{ name: 'Takeaway', data: tk, color: 'c1', stack: 'a' }, { name: 'Dine-in', data: di, color: 'c2', stack: 'a' }], summary: summarize('Porsi takeaway per brand', cats, tk, 'pct', false) }]}
              />
            );
          }}
        </Block>
      </div>

      <Section title="Menu" id="menu">
        <div className="tabs-links" aria-label="Urutan menu">
          {SORTS.map((s) => (
            <Link key={s.id} className="btn btn-sm" href={href(raw, { sort: s.id })} aria-current={sort === s.id ? 'true' : undefined}>
              Berdasarkan {s.label}
            </Link>
          ))}
          <Link className="btn btn-sm" href={href(raw, { rank: bottom ? 'top' : 'bottom' })}>
            {bottom ? 'Tampilkan 10 teratas' : 'Tampilkan 10 terbawah'}
          </Link>
        </div>
        <Block result={menuR} title="Menu">
          {() => (
            <DataTable
              caption={`${bottom ? '10 menu terbawah' : '10 menu teratas'} berdasarkan ${SORTS.find((s) => s.id === sort)?.label ?? ''}`}
              columns={TABLES['top-menu'].columns}
              rows={menuRows as TableRow[]}
              csvName="top-menu"
              pageSize={10}
            />
          )}
        </Block>
      </Section>

      <Section title="Bauran per cabang" id="mix">
        <p className="muted small">Data bulanan: rentang dibulatkan ke bulan penuh.</p>
        <Block result={mixR} title="Bauran per cabang">
          {(rows) => (
            <DataTable
              caption={TABLES['menu-mix-branch'].caption}
              columns={TABLES['menu-mix-branch'].columns}
              rows={rows as TableRow[]}
              csvName="menu-mix-branch"
              defaultSort={{ key: 'share_pct', dir: 'desc' }}
            />
          )}
        </Block>
      </Section>
    </>
  );
}
