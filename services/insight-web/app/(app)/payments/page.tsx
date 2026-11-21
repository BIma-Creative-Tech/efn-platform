import type { Metadata } from 'next';
import { ErrorCard } from '@/components/blocks';
import { ChartCard } from '@/components/ChartCard';
import { DataTable, type TableRow } from '@/components/DataTable';
import { FilterNotice } from '@/components/FilterNotice';
import { SideNote } from '@/components/SmdNotice';
import { KpiRow, KpiTile, PageHeader, Section } from '@/components/ui';
import type { SeriesColor } from '@/lib/brands';
import { summarize, type ChartSeries, type ChartSpec } from '@/lib/chart-spec';
import { eachDay } from '@/lib/dates';
import { fmtDayShort, fmtRange } from '@/lib/format';
import { num, type Result } from '@/lib/loaders';
import { pageCtx, type SearchParams } from '@/lib/page-ctx';
import { loadEsbTable, loadSmdNames, runEsb, runSm } from '@/lib/smd-data';
import { mergeSides, smdActive, smdPayBranchRows, smdPayMethodRows, smdPayTrendRows, type PayTrendRow } from '@/lib/smd-merge';
import { toNum } from '@/lib/sm-parse';
import { TABLES } from '@/lib/table-defs';
import { T } from '@/lib/thresholds';
import { pctDelta, pctOf, ppDelta, withOther } from '@/lib/vm';

export const dynamic = 'force-dynamic';
export const metadata: Metadata = { title: 'Pembayaran' };

interface Kpi { amount_cur: number; amount_prev: number; count_cur: number; count_prev: number; mdr_cur: number; mdr_prev: number }
type RawRows = Record<string, unknown>[];

const COLORS: SeriesColor[] = ['c1', 'c2', 'c3', 'c4', 'c5', 'c6'];

export default async function PaymentsPage({ searchParams }: { searchParams: SearchParams }) {
  const { ctx, adjusted, sel } = await pageCtx(searchParams);
  const { from, to } = ctx.filters;
  const esbOn = sel.esb;
  const smdOn = smdActive(sel, ctx.filters, null);
  const mixed = esbOn && smdOn;
  const sm = (n: Parameters<typeof runSm>[0]) => (smdOn ? runSm(n, ctx, sel) : Promise.resolve(null));

  const [kpiR, methodR, trendR, branchR, smSums, smMatrix, smTrend, names] = await Promise.all([
    runEsb<Kpi>(esbOn, 'pay_kpi', ctx),
    loadEsbTable(esbOn, 'pay-methods', ctx),
    runEsb<PayTrendRow>(esbOn, 'pay_trend', ctx),
    loadEsbTable(esbOn, 'pay-branches', ctx),
    sm('sm_fb_pay_sums'),
    sm('sm_fb_pay_matrix'),
    sm('sm_fb_pay_trend'),
    smdOn ? loadSmdNames(ctx, sel) : Promise.resolve(null),
  ]);

  const asRaw = (r: Result<RawRows> | null): Result<RawRows> | null => r;
  const kpiSides = mergeSides(esbOn, kpiR, smdOn, asRaw(smSums));
  const methodSides = mergeSides(esbOn, methodR, smdOn, asRaw(smMatrix));
  const trendSides = mergeSides(esbOn, trendR, smdOn, asRaw(smTrend));
  const branchSides = mergeSides(esbOn, branchR, smdOn, asRaw(smMatrix));

  const methodRows = [...(methodSides.esb ?? []), ...(methodSides.smd ? smdPayMethodRows(methodSides.smd) : [])];
  const branchRows = [...(branchSides.esb ?? []), ...(branchSides.smd ? smdPayBranchRows(branchSides.smd, names?.wh ?? new Map<number, string>()) : [])];
  const trendRows: PayTrendRow[] = [...(trendSides.esb ?? []), ...(trendSides.smd ? smdPayTrendRows(trendSides.smd) : [])];

  const trendSpecs = (rows: PayTrendRow[]): ChartSpec[] => {
    const days = eachDay(from, to);
    const cats = days.map(fmtDayShort);
    const totals = new Map<string, number>();
    for (const r of rows) totals.set(r.payment_type, (totals.get(r.payment_type) ?? 0) + num(r.amount));
    const ranked = Array.from(totals.entries()).sort((a, b) => b[1] - a[1]).map(([t]) => t);
    const top = ranked.slice(0, 5);
    const bucket = (t: string): string => (top.includes(t) ? t : 'Lainnya');
    const types = ranked.length > 5 ? [...top, 'Lainnya'] : top;
    const grid = (pick: (r: PayTrendRow) => number): Map<string, number[]> => {
      const m = new Map<string, number[]>(types.map((t) => [t, Array<number>(days.length).fill(0)]));
      for (const r of rows) {
        const i = days.indexOf(r.sales_date);
        const arr = m.get(bucket(r.payment_type));
        if (i >= 0 && arr) arr[i] += pick(r);
      }
      return m;
    };
    const build = (id: string, tab: string, m: Map<string, number[]>, fmt: 'idr' | 'int' | 'pct', percent: boolean): ChartSpec => {
      const dayTotal = days.map((_, i) => Array.from(m.values()).reduce((s, a) => s + a[i], 0));
      const series: ChartSeries[] = types.map((t, ti) => ({
        name: t === '' ? 'Tidak diketahui' : t,
        color: t === 'Lainnya' ? 'c7' : COLORS[ti % COLORS.length],
        area: true,
        stack: 'a',
        data: (m.get(t) ?? []).map((v, i) => (percent ? (dayTotal[i] > 0 ? (v / dayTotal[i]) * 100 : 0) : v)),
      }));
      return {
        id,
        tabLabel: tab,
        kind: 'line',
        fmt,
        axisFmt: fmt === 'idr' ? 'idr-short' : fmt,
        categories: cats,
        series,
        summary: summarize(`Pembayaran harian (${tab.toLowerCase()})`, cats, dayTotal, fmt === 'pct' ? 'num' : fmt === 'idr' ? 'idr-short' : 'int', fmt !== 'pct'),
      };
    };
    return [
      build('amt', 'Nilai', grid((r) => num(r.amount)), 'idr', false),
      build('cnt', 'Jumlah transaksi', grid((r) => num(r.payment_count)), 'int', false),
      build('pct', 'Persen', grid((r) => num(r.amount)), 'pct', true),
    ];
  };

  return (
    <>
      <PageHeader title="Pembayaran" question="Pelanggan membayar dengan apa, dan berapa biaya (MDR) yang kita tanggung?" />
      <FilterNotice adjusted={adjusted} />
      <p className="muted small">Periode: {fmtRange(from, to)}. Nilai pembayaran tidak sama dengan penjualan bersih (pembayaran sudah termasuk PB1/service; SMD termasuk PPN).</p>
      {smdOn ? (
        <p className="notice" role="note">
          SMD ikut dihitung: nominal uang diterima bersih (termasuk PPN, retur dan kembalian sudah diperhitungkan). Pembayaran sebasis dengan brand F&amp;B. MDR hanya untuk brand F&amp;B; SMD tidak punya data MDR (n/a).
        </p>
      ) : null}
      <SideNote note={kpiSides.note} />

      <Section title="Indikator utama" id="kpi">
        {kpiSides.fail ? (
          <ErrorCard title="Indikator utama" refId={kpiSides.fail.ref} reason={kpiSides.fail.reason} />
        ) : (
          (() => {
            const k = kpiSides.esb?.[0];
            const s = kpiSides.smd?.[0];
            if (!k && !s) return <p>Tidak ada pembayaran pada periode ini.</p>;
            const amtC = num(k?.amount_cur) + toNum(s?.amount_cur);
            const amtP = num(k?.amount_prev) + toNum(s?.amount_prev);
            const cntC = num(k?.count_cur) + toNum(s?.count_cur);
            const cntP = num(k?.count_prev) + toNum(s?.count_prev);
            // MDR hanya brand F&B: penyebut = pembayaran F&B saja (SMD tidak punya MDR).
            const mC = k ? pctOf(num(k.mdr_cur), num(k.amount_cur)) : null;
            const mP = k ? pctOf(num(k.mdr_prev), num(k.amount_prev)) : null;
            const d = ppDelta(mC, mP);
            return (
              <KpiRow>
                <KpiTile label="Total pembayaran" value={amtC} fmt="idr-short" note={mixed ? 'F&B + SMD (uang diterima)' : undefined} delta={{ value: pctDelta(amtC, amtP), kind: 'pct', goodWhen: 'up' }} />
                <KpiTile label="Jumlah pembayaran" value={cntC} fmt="int" delta={{ value: pctDelta(cntC, cntP), kind: 'pct', goodWhen: 'up' }} />
                <KpiTile label="Total MDR" value={k ? num(k.mdr_cur) : null} fmt="idr-short" note={smdOn ? 'Hanya brand F&B' : undefined} delta={{ value: k ? pctDelta(num(k.mdr_cur), num(k.mdr_prev)) : null, kind: 'pct', goodWhen: 'down' }} />
                <KpiTile
                  label="MDR efektif"
                  value={mC}
                  fmt="pct"
                  status={d !== null && d > T.mdrWarnPp ? 'warn' : 'neutral'}
                  note={smdOn ? 'Hanya brand F&B' : undefined}
                  delta={{ value: d, kind: 'pp', goodWhen: 'down' }}
                  info={`MDR dibagi total pembayaran. Peringatan bila naik lebih dari ${T.mdrWarnPp} poin persentase.`}
                />
              </KpiRow>
            );
          })()
        )}
      </Section>

      <div className="grid-2">
        {methodSides.fail ? (
          <ErrorCard title="Bauran metode" refId={methodSides.fail.ref} reason={methodSides.fail.reason} />
        ) : (
          (() => {
            const ranked = [...methodRows].sort((a, b) => num(b.amount_total) - num(a.amount_total));
            const items = withOther(
              ranked.map((r) => ({ label: `${String(r.method)} (${String(r.payment_type)})`, value: num(r.amount_total) })),
              8,
            );
            const cats = items.map((i) => i.label);
            const vals = items.map((i) => i.value);
            return (
              <ChartCard
                title="Bauran metode pembayaran"
                specs={[{ id: 'm', kind: 'barH', fmt: 'idr', axisFmt: 'idr-short', categories: cats, series: [{ name: 'Nilai pembayaran', data: vals, color: 'c1' }], summary: summarize('Nilai pembayaran per metode', cats, vals, 'idr-short', true) }]}
              />
            );
          })()
        )}
        {trendSides.fail ? (
          <ErrorCard title="Tren per tipe" refId={trendSides.fail.ref} reason={trendSides.fail.reason} />
        ) : (
          <ChartCard title="Tren per tipe pembayaran" subtitle="Area bertumpuk harian" specs={trendSpecs(trendRows)} />
        )}
      </div>

      <Section title="MDR per metode" id="mdr">
        {methodSides.fail ? (
          <ErrorCard title="MDR per metode" refId={methodSides.fail.ref} reason={methodSides.fail.reason} />
        ) : (
          <>
            <SideNote note={methodSides.note} />
            <DataTable
              caption={TABLES['pay-methods'].caption}
              columns={TABLES['pay-methods'].columns}
              rows={methodRows as TableRow[]}
              csvName={smdOn ? undefined : 'pay-methods'}
              defaultSort={{ key: 'amount_total', dir: 'desc' }}
            />
          </>
        )}
      </Section>

      <Section title="Per cabang" id="branch">
        {branchSides.fail ? (
          <ErrorCard title="Per cabang" refId={branchSides.fail.ref} reason={branchSides.fail.reason} />
        ) : (
          <>
            <SideNote note={branchSides.note} />
            <DataTable
              caption={TABLES['pay-branches'].caption}
              columns={TABLES['pay-branches'].columns}
              rows={branchRows as TableRow[]}
              csvName={smdOn ? undefined : 'pay-branches'}
              defaultSort={{ key: 'amount', dir: 'desc' }}
            />
          </>
        )}
      </Section>
    </>
  );
}
