import type { Metadata } from 'next';
import { Block } from '@/components/blocks';
import { ChartCard } from '@/components/ChartCard';
import { DataTable, type TableRow } from '@/components/DataTable';
import { FilterNotice } from '@/components/FilterNotice';
import { KpiRow, KpiTile, PageHeader, Section } from '@/components/ui';
import type { SeriesColor } from '@/lib/brands';
import { summarize, type ChartSeries, type ChartSpec } from '@/lib/chart-spec';
import { eachDay } from '@/lib/dates';
import { fmtDayShort, fmtRange } from '@/lib/format';
import { num, run, settle, TABLE_LOADERS } from '@/lib/loaders';
import { pageCtx, type SearchParams } from '@/lib/page-ctx';
import { TABLES } from '@/lib/table-defs';
import { T } from '@/lib/thresholds';
import { pctDelta, pctOf, ppDelta, withOther } from '@/lib/vm';

export const dynamic = 'force-dynamic';
export const metadata: Metadata = { title: 'Pembayaran' };

interface Kpi { amount_cur: number; amount_prev: number; count_cur: number; count_prev: number; mdr_cur: number; mdr_prev: number }
interface TrendRow { sales_date: string; payment_type: string; amount: number; payment_count: number }

const COLORS: SeriesColor[] = ['c1', 'c2', 'c3', 'c4', 'c5', 'c6'];

export default async function PaymentsPage({ searchParams }: { searchParams: SearchParams }) {
  const { ctx, adjusted } = await pageCtx(searchParams);
  const { from, to } = ctx.filters;

  const [kpiR, methodR, trendR, branchR] = await Promise.all([
    settle(run<Kpi>('pay_kpi', ctx)),
    settle(TABLE_LOADERS['pay-methods'](ctx)),
    settle(run<TrendRow>('pay_trend', ctx)),
    settle(TABLE_LOADERS['pay-branches'](ctx)),
  ]);

  const trendSpecs = (rows: TrendRow[]): ChartSpec[] => {
    const days = eachDay(from, to);
    const cats = days.map(fmtDayShort);
    const totals = new Map<string, number>();
    for (const r of rows) totals.set(r.payment_type, (totals.get(r.payment_type) ?? 0) + num(r.amount));
    const ranked = Array.from(totals.entries()).sort((a, b) => b[1] - a[1]).map(([t]) => t);
    const top = ranked.slice(0, 5);
    const bucket = (t: string): string => (top.includes(t) ? t : 'Lainnya');
    const types = ranked.length > 5 ? [...top, 'Lainnya'] : top;
    const grid = (pick: (r: TrendRow) => number): Map<string, number[]> => {
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
      <p className="muted small">Periode: {fmtRange(from, to)}. Nilai pembayaran tidak sama dengan penjualan bersih (pembayaran sudah termasuk PB1/service).</p>

      <Section title="Indikator utama" id="kpi">
        <Block result={kpiR} title="Indikator utama">
          {(rows) => {
            const k = rows[0];
            if (!k) return <p>Tidak ada pembayaran pada periode ini.</p>;
            const mC = pctOf(num(k.mdr_cur), num(k.amount_cur));
            const mP = pctOf(num(k.mdr_prev), num(k.amount_prev));
            const d = ppDelta(mC, mP);
            return (
              <KpiRow>
                <KpiTile label="Total pembayaran" value={num(k.amount_cur)} fmt="idr-short" delta={{ value: pctDelta(num(k.amount_cur), num(k.amount_prev)), kind: 'pct', goodWhen: 'up' }} />
                <KpiTile label="Jumlah pembayaran" value={num(k.count_cur)} fmt="int" delta={{ value: pctDelta(num(k.count_cur), num(k.count_prev)), kind: 'pct', goodWhen: 'up' }} />
                <KpiTile label="Total MDR" value={num(k.mdr_cur)} fmt="idr-short" delta={{ value: pctDelta(num(k.mdr_cur), num(k.mdr_prev)), kind: 'pct', goodWhen: 'down' }} />
                <KpiTile
                  label="MDR efektif"
                  value={mC}
                  fmt="pct"
                  status={d !== null && d > T.mdrWarnPp ? 'warn' : 'neutral'}
                  delta={{ value: d, kind: 'pp', goodWhen: 'down' }}
                  info={`MDR dibagi total pembayaran. Peringatan bila naik lebih dari ${T.mdrWarnPp} poin persentase.`}
                />
              </KpiRow>
            );
          }}
        </Block>
      </Section>

      <div className="grid-2">
        <Block result={methodR} title="Bauran metode">
          {(rows) => {
            const items = withOther(
              rows.map((r) => ({ label: `${String(r.method)} (${String(r.payment_type)})`, value: num(r.amount_total) })),
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
          }}
        </Block>
        <Block result={trendR} title="Tren per tipe">
          {(rows) => <ChartCard title="Tren per tipe pembayaran" subtitle="Area bertumpuk harian" specs={trendSpecs(rows)} />}
        </Block>
      </div>

      <Section title="MDR per metode" id="mdr">
        <Block result={methodR} title="MDR per metode">
          {(rows) => (
            <DataTable
              caption={TABLES['pay-methods'].caption}
              columns={TABLES['pay-methods'].columns}
              rows={rows as TableRow[]}
              csvName="pay-methods"
              defaultSort={{ key: 'amount_total', dir: 'desc' }}
            />
          )}
        </Block>
      </Section>

      <Section title="Per cabang" id="branch">
        <Block result={branchR} title="Per cabang">
          {(rows) => (
            <DataTable
              caption={TABLES['pay-branches'].caption}
              columns={TABLES['pay-branches'].columns}
              rows={rows as TableRow[]}
              csvName="pay-branches"
              defaultSort={{ key: 'amount', dir: 'desc' }}
            />
          )}
        </Block>
      </Section>
    </>
  );
}
