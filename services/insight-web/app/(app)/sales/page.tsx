import type { Metadata } from 'next';
import Link from 'next/link';
import { Block, ErrorCard } from '@/components/blocks';
import { ChartCard } from '@/components/ChartCard';
import { DataTable, type TableRow } from '@/components/DataTable';
import { FilterNotice } from '@/components/FilterNotice';
import { MixedScopeNote, NotApplicableSmd, SideNote, SmdBasisNote } from '@/components/SmdNotice';
import { SmTable } from '@/components/sm/SmTable';
import { KpiRow, KpiTile, PageHeader, Section } from '@/components/ui';
import { brandName } from '@/lib/brands';
import { summarize, type ChartSpec, type ColorToken } from '@/lib/chart-spec';
import { fmtDayShort, fmtIdr, fmtRange, fmtQty, WEEKDAYS } from '@/lib/format';
import { num, topMenuRow, type Result } from '@/lib/loaders';
import { pageCtx, type SearchParams } from '@/lib/page-ctx';
import { loadEsbTable, loadSmdProductNames, runEsb, runSm } from '@/lib/smd-data';
import {
  ZERO_SALES,
  addSales,
  allOk,
  esbOnlyMode,
  esbSalesSums,
  mergeCategories,
  mergeHeat,
  mergeSides,
  mergeWeekday,
  segParts,
  smdActive,
  smdExcludedNote,
  smdSalesSums,
  SMD_BASIS,
} from '@/lib/smd-merge';
import { toNum } from '@/lib/sm-parse';
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

type Kpi = {
  net_cur: number; net_prev: number; gross_cur: number; gross_prev: number; discount_cur: number; discount_prev: number;
  qty_cur: number; qty_prev: number; cogs_cur: number; cogs_prev: number;
};
interface CatRow { category: string; net_sales: number; qty_sold: number; margin_pct: number | null }
type RawRows = Record<string, unknown>[];

export default async function SalesPage({ searchParams }: { searchParams: SearchParams }) {
  const { ctx, adjusted, raw, sel } = await pageCtx(searchParams);
  const { from, to } = ctx.filters;
  const rawSort = Array.isArray(raw.sort) ? raw.sort[0] : raw.sort;
  const sort: Sort = rawSort === 'qty' || rawSort === 'margin' ? rawSort : 'net';
  const bottom = (Array.isArray(raw.rank) ? raw.rank[0] : raw.rank) === 'bottom';
  const esbOn = sel.esb;
  // Filter "Kategori menu" adalah nama kategori ESB; tidak punya padanan di SMD -> SMD tidak ikut bila filter itu aktif.
  const smdOn = smdActive(sel, ctx.filters, 'cat');
  const mixed = esbOn && smdOn;
  const mode = esbOnlyMode({ ...sel, smd: smdOn });
  const excluded = smdExcludedNote(sel, ctx.filters, 'cat');
  const sm = (n: Parameters<typeof runSm>[0]) => (smdOn ? runSm(n, ctx, sel) : Promise.resolve(null));

  const [kpiR, dowR, heatR, catR, chanR, menuR, mixR, smDaily, smKpi, smMargin, smDow, smHeat, smCat, smTop] = await Promise.all([
    runEsb<Kpi>(esbOn, 'sales_kpi', ctx),
    runEsb<{ dow: number; avg_net: number; days: number }>(esbOn, 'sales_by_weekday', ctx),
    runEsb<{ week: string; dow: number; net: number }>(esbOn, 'sales_weekday_heatmap', ctx),
    runEsb<CatRow>(esbOn, 'sales_by_category', ctx),
    runEsb<{ brand: string; net_takeaway: number; net_dine_in: number }>(esbOn, 'sales_channel_split', ctx, { by_branch: 0 }),
    runEsb<{ brand: string; menu_code: string; menu_label: string; category: string; net_sales: number; qty_sold: number; margin_pct: number | null }>(esbOn, 'sales_top_menu', ctx, { sort }),
    loadEsbTable(esbOn, 'menu-mix-branch', ctx),
    sm('sm_fb_daily'),
    sm('sm_fb_sales_kpi'),
    sm('sm_ex_margin'),
    sm('sm_fb_weekday'),
    sm('sm_fb_heat'),
    sm('sm_fb_by_category'),
    sm('sm_fb_top_products'),
  ]);
  const prodNames = smTop && smTop.ok ? await loadSmdProductNames(ctx, sel, smTop.data.map((r) => toNum(r.product_id))) : new Map<number, string>();

  const asRaw = (r: Result<RawRows> | null): Result<RawRows> | null => r;
  const kpiSides = mergeSides(esbOn, kpiR, smdOn, smdOn ? allOk([smDaily, smKpi, smMargin].map((r) => r as Result<RawRows>)) : null);
  const dowSides = mergeSides(esbOn, dowR, smdOn, asRaw(smDow));
  const heatSides = mergeSides(esbOn, heatR, smdOn, asRaw(smHeat));
  const catSides = mergeSides(esbOn, catR, smdOn, asRaw(smCat));

  const catSpecs = (rows: CatRow[], smdRows: RawRows): ChartSpec[] => {
    const all = mergeCategories(rows, smdRows);
    const drillSet = new Set(all.filter((i) => i.drillable).map((i) => i.label));
    const segOf = new Map(all.map((i) => [i.label, i.segment]));
    const byMetric = (m: 'net' | 'qty'): ChartSpec => {
      const sorted = [...all].sort((a, b) => (m === 'net' ? b.net - a.net : b.qty - a.qty));
      const items = withOther(sorted.map((r) => ({ label: r.label, value: m === 'net' ? r.net : r.qty })), 8);
      const cats = items.map((i) => i.label);
      const vals = items.map((i) => i.value);
      return {
        id: m,
        tabLabel: m === 'net' ? (mixed ? 'Penjualan (basis campuran)' : 'Penjualan bersih') : 'Qty',
        kind: 'barH',
        fmt: m === 'net' ? 'idr' : 'num',
        axisFmt: m === 'net' ? 'idr-short' : 'num',
        categories: cats,
        series: [
          {
            name: m === 'net' ? (mixed ? 'Penjualan (basis campuran)' : 'Penjualan bersih') : 'Qty',
            data: vals,
            color: 'c1',
            // Mode campuran: batang Supermarket memakai warna c7 (label tetap berawalan "SMD:"); "Lainnya" = netral.
            ...(mixed ? { itemColors: cats.map((c): ColorToken => (c === 'Lainnya' ? 'muted' : segOf.get(c) === 'sm' ? 'c7' : 'c1')) } : {}),
          },
        ],
        drill: { param: 'cat', values: cats.map((c) => (drillSet.has(c) ? c : '')), label: 'kategori' },
        summary: summarize('Bauran kategori', cats, vals, m === 'net' ? 'idr-short' : 'num', true),
      };
    };
    // Margin per kategori: hanya kategori menu ESB (margin SMD ada di dashboard supermarket).
    const top = all.filter((i) => i.drillable).sort((a, b) => b.net - a.net).slice(0, 8);
    const mc = top.map((r) => r.label);
    const mv = top.map((r) => r.margin);
    return [
      byMetric('net'),
      byMetric('qty'),
      { id: 'margin', tabLabel: 'Margin (F&B)', kind: 'barH', fmt: 'pct', categories: mc, series: [{ name: 'Margin kotor %', data: mv, color: 'c3' }], summary: summarize('Margin kotor per kategori', mc, mv, 'pct', false) },
    ];
  };

  const menuRows = menuR.ok ? (bottom ? [...menuR.data].reverse() : menuR.data).slice(0, 10).map(topMenuRow) : [];

  return (
    <>
      <PageHeader title="Penjualan & Menu" question="Menu apa dan hari apa yang menghasilkan penjualan, dan bagaimana bauran takeaway vs dine-in?" />
      <FilterNotice adjusted={adjusted} />
      <p className="muted small">Periode: {fmtRange(from, to)}. Tidak ada data per jam.</p>
      {excluded ? <p className="notice" role="status">{excluded}</p> : null}
      {smdOn ? <SmdBasisNote mixed={mixed} /> : null}
      <SideNote note={kpiSides.note} />

      <Section title="Indikator utama" id="kpi">
        {kpiSides.fail ? (
          <ErrorCard title="Indikator utama" refId={kpiSides.fail.ref} reason={kpiSides.fail.reason} />
        ) : (
          (() => {
            const k = kpiSides.esb?.[0];
            const e = esbSalesSums(k);
            const s = kpiSides.smd ? smdSalesSums(kpiSides.smd[0], from, kpiSides.smd[1][0], kpiSides.smd[2][0]) : ZERO_SALES;
            const p = addSales(e, s);
            if (!k && p.net === 0 && p.netPrev === 0 && p.qty === 0) return <p>Tidak ada penjualan pada periode ini.</p>;
            const dPct = pctOf(p.disc, p.gross);
            const mC = pctOf(p.gm, p.base);
            const mP = pctOf(p.gmPrev, p.basePrev);
            const netLabel = mixed ? 'Penjualan (basis campuran)' : !esbOn ? `Omzet SMD (${SMD_BASIS})` : 'Penjualan bersih';
            return (
              <KpiRow>
                <KpiTile
                  label={netLabel}
                  value={p.net}
                  fmt="idr-short"
                  note={mixed ? 'F&B tanpa pajak, SMD termasuk PPN' : !esbOn ? SMD_BASIS : 'Sebelum PB1/service'}
                  parts={mixed ? segParts(e.net, s.net, { fmt: 'idr-short', smNote: SMD_BASIS }) : undefined}
                  delta={{ value: pctDelta(p.net, p.netPrev), kind: 'pct', goodWhen: 'up' }}
                />
                <KpiTile
                  label="Diskon"
                  value={p.disc}
                  fmt="idr-short"
                  status={discountStatus(dPct)}
                  parts={mixed ? segParts(e.disc, s.disc, { fmt: 'idr-short' }) : undefined}
                  note={dPct === null ? undefined : `${dPct.toFixed(1).replace('.', ',')}% dari penjualan kotor`}
                  delta={{ value: pctDelta(p.disc, p.discPrev), kind: 'pct', goodWhen: 'neutral' }}
                  info={`Peringatan bila diskon > ${T.discountWarnPct}% dari penjualan kotor (ambang konfigurasi).`}
                />
                <KpiTile label="Qty terjual" value={p.qty} fmt="num" note={mixed ? 'Satuan campuran (porsi F&B + unit SMD)' : undefined} parts={mixed ? segParts(e.qty, s.qty, { fmt: 'num', fbNote: 'porsi', smNote: 'unit' }) : undefined} delta={{ value: pctDelta(p.qty, p.qtyPrev), kind: 'pct', goodWhen: 'up' }} />
                <KpiTile label="Margin kotor" value={mC} fmt="pct" status={marginStatus(mC)} parts={mixed ? segParts(pctOf(e.gm, e.base), pctOf(s.gm, s.base), { fmt: 'pct' }) : undefined} delta={{ value: ppDelta(mC, mP), kind: 'pp', goodWhen: 'up' }} info="(Penjualan bersih tanpa pajak - HPP) / penjualan bersih tanpa pajak, di kedua sisi." />
              </KpiRow>
            );
          })()
        )}
      </Section>

      <div className="grid-2">
        {dowSides.fail ? (
          <ErrorCard title="Pola hari dalam minggu" refId={dowSides.fail.ref} reason={dowSides.fail.reason} />
        ) : (
          (() => {
            const vals = mergeWeekday(dowSides.esb ?? [], dowSides.smd ?? []);
            return (
              <ChartCard
                title="Rata-rata penjualan per hari dalam minggu"
                subtitle={mixed ? 'Basis campuran (SMD termasuk PPN)' : undefined}
                specs={[{ id: 'dow', kind: 'bar', fmt: 'idr', axisFmt: 'idr-short', categories: WEEKDAYS, series: [{ name: 'Rata-rata penjualan', data: vals, color: 'c1' }], summary: summarize('Rata-rata penjualan per hari', WEEKDAYS, vals, 'idr-short', false) }]}
              />
            );
          })()
        )}
        {heatSides.fail ? (
          <ErrorCard title="Minggu dan hari" refId={heatSides.fail.ref} reason={heatSides.fail.reason} />
        ) : (
          (() => {
            const rows = mergeHeat(heatSides.esb ?? [], heatSides.smd ?? []);
            const weeks = Array.from(new Set(rows.map((r) => r.week))).sort();
            const cells: [number, number, number | null][] = rows.map((r) => [r.dow - 1, weeks.indexOf(r.week), r.net]);
            return (
              <ChartCard
                title="Penjualan per minggu dan hari"
                subtitle={`Baris = awal minggu (Senin)${mixed ? '; basis campuran' : ''}`}
                height={Math.max(200, weeks.length * 34 + 60)}
                specs={[{ id: 'heat', kind: 'heat', fmt: 'idr', axisFmt: 'idr-short', categories: WEEKDAYS, yCategories: weeks.map(fmtDayShort), heat: cells, series: [{ name: 'Minggu', data: [], color: 'c1' }], summary: 'Peta panas penjualan per minggu dan hari; lihat tabel untuk nilai tiap sel.' }]}
              />
            );
          })()
        )}
        {catSides.fail ? (
          <ErrorCard title="Bauran kategori" refId={catSides.fail.ref} reason={catSides.fail.reason} />
        ) : (
          <ChartCard
            title="Bauran kategori"
            subtitle={`Klik batang untuk memfilter kategori menu${smdOn ? '; kategori SMD berawalan "SMD:" tidak bisa difilter' : ''}`}
            specs={catSpecs((catSides.esb ?? []) as CatRow[], catSides.smd ?? [])}
          />
        )}
        {mode === 'only-smd' ? (
          <NotApplicableSmd title="Takeaway vs dine-in" reason="Pemisahan takeaway/dine-in hanya ada pada penjualan F&B (nama menu ESB)." />
        ) : (
          <Block result={chanR} title="Takeaway vs dine-in">
            {(rows) => {
              const cats = rows.map((r) => brandName(r.brand));
              const tk = rows.map((r) => pctOf(num(r.net_takeaway), num(r.net_takeaway) + num(r.net_dine_in)));
              const di = tk.map((v) => (v === null ? null : 100 - v));
              return (
                <div>
                  <ChartCard
                    title="Takeaway vs dine-in"
                    subtitle="Porsi penjualan bersih; takeaway = perkiraan dari nama menu ESB"
                    specs={[{ id: 'chan', kind: 'bar', fmt: 'pct', axisFmt: 'pct', categories: cats, series: [{ name: 'Takeaway', data: tk, color: 'c1', stack: 'a' }, { name: 'Dine-in', data: di, color: 'c2', stack: 'a' }], summary: summarize('Porsi takeaway per brand', cats, tk, 'pct', false) }]}
                  />
                  {mode === 'mixed' ? <MixedScopeNote /> : null}
                </div>
              );
            }}
          </Block>
        )}
      </div>

      {smdOn ? (
        <Section title={`Produk terlaris SMD (${SMD_BASIS})`} id="smd-top">
          <Block result={smTop as Result<RawRows>} title="Produk terlaris SMD">
            {(rows) =>
              rows.length === 0 ? (
                <p>Tidak ada penjualan produk SMD pada periode ini.</p>
              ) : (
                <SmTable
                  caption={`10 produk SMD teratas menurut omzet (${SMD_BASIS})`}
                  rows={rows}
                  rowKey={(r) => String(toNum(r.product_id))}
                  columns={[
                    { header: 'Produk', cell: (r) => prodNames.get(toNum(r.product_id)) ?? `Produk #${toNum(r.product_id)}` },
                    { header: `Omzet (${SMD_BASIS})`, align: 'right', cell: (r) => fmtIdr(toNum(r.rev_sum)) },
                    { header: 'Qty', align: 'right', cell: (r) => fmtQty(toNum(r.qty_sum)) },
                  ]}
                />
              )
            }
          </Block>
        </Section>
      ) : null}

      <Section title="Menu" id="menu">
        {mode === 'only-smd' ? (
          <NotApplicableSmd title="Menu terlaris" reason="Menu dan kategori menu adalah konsep F&B. Produk SMD ada di tabel produk terlaris di atas." />
        ) : (
          <>
            {mode === 'mixed' ? <MixedScopeNote /> : null}
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
          </>
        )}
      </Section>

      <Section title="Bauran per cabang" id="mix">
        {mode === 'only-smd' ? (
          <NotApplicableSmd title="Bauran per cabang" />
        ) : (
          <>
            <p className="muted small">Data bulanan: rentang dibulatkan ke bulan penuh.</p>
            {mode === 'mixed' ? <MixedScopeNote /> : null}
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
          </>
        )}
      </Section>
    </>
  );
}
