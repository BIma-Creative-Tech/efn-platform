import type { Metadata } from 'next';
import type { ReactNode } from 'react';
import { ErrorCard } from '@/components/blocks';
import { ChartCard } from '@/components/ChartCard';
import { DataTable, type TableRow } from '@/components/DataTable';
import { FilterNotice } from '@/components/FilterNotice';
import { G67Form } from '@/components/g67/G67Form';
import { EmptyState, KpiRow, KpiTile, PageHeader, Section } from '@/components/ui';
import { codeName } from '@/lib/brands';
import { summarize, type ChartSpec } from '@/lib/chart-spec';
import { fmtDate, fmtIdrShort, fmtMonth, fmtRange } from '@/lib/format';
import { COVERAGE_KEYS, coverageHint } from '@/lib/g67-vm';
import { g67Page, type G67SearchParams } from '@/lib/g67-page';
import { loadCoverage, num, type Result } from '@/lib/loaders';
import {
  buildStatement,
  changePct,
  parseMonthRange,
  pctOf,
  PL_LABEL,
  pnlSides,
  statementColumns,
  UNALLOCATED_LABEL,
  type PnlKpiRaw,
  type PnlSide,
  type StatementRaw,
} from '@/lib/pnl';
import { runEsb } from '@/lib/smd-data';
import type { ColumnDef } from '@/lib/table-defs';

export const dynamic = 'force-dynamic';
export const metadata: Metadata = { title: 'Laba Rugi Outlet' };

type Raw = Record<string, unknown>;
const L = 'left' as const;
const R = 'right' as const;

const RANK_COLS: ColumnDef[] = [
  { key: 'branch_label', header: 'Outlet', align: L },
  { key: 'company_code', header: 'PT', align: L, filterable: true },
  { key: 'net_sales', header: 'Penjualan bersih', fmt: 'idr', align: R },
  { key: 'gp_pct', header: 'Laba kotor % penjualan', fmt: 'pct', align: R },
  { key: 'ebitda', header: 'EBITDA', fmt: 'idr', align: R },
  { key: 'ebitda_pct', header: 'EBITDA % penjualan', fmt: 'pct', align: R },
  { key: 'np_pct', header: 'Laba bersih % penjualan', fmt: 'pct', align: R },
];

const ACCOUNT_COLS: ColumnDef[] = [
  { key: 'section', header: 'Seksi', align: L, filterable: true },
  { key: 'account_no', header: 'No. akun', align: L },
  { key: 'account_name', header: 'Nama akun', align: L },
  { key: 'amount', header: 'Jumlah periode', fmt: 'idr', align: R },
  { key: 'share', header: '% dari penjualan bersih', fmt: 'pct', align: R },
];

const one = (v: string | string[] | undefined): string | undefined => (Array.isArray(v) ? v[0] : v);

function fail(r: Result<unknown>, title: string): ReactNode | null {
  return r.ok ? null : <ErrorCard title={title} refId={r.ref} reason={r.reason} />;
}

function posSpec(cur: PnlSide): ChartSpec {
  const cats = ['Penjualan bersih', 'Laba kotor', 'Biaya operasional', 'EBITDA', 'Laba bersih'];
  const vals = [cur.net, cur.gp, cur.opex, cur.ebitda, cur.np];
  return {
    id: 'pnl-pos',
    kind: 'bar',
    fmt: 'idr',
    axisFmt: 'idr-short',
    categories: cats,
    // Tanda biaya operasional di laporan ESB belum terverifikasi (Q-ESB34): tidak diwarnai baik/buruk.
    series: [{ name: 'Nilai periode', data: vals, color: 'c1', itemColors: ['c1', vals[1] >= 0 ? 'good' : 'bad', 'muted', vals[3] >= 0 ? 'good' : 'bad', vals[4] >= 0 ? 'good' : 'bad'] }],
    summary: summarize('Pos laba rugi periode terpilih', cats, vals, 'idr-short', false),
  };
}

function tiles(cur: PnlSide, ly: PnlSide | null): ReactNode {
  const lyNote = (v: number | undefined): string => (ly === null || v === undefined ? 'Tahun lalu: n/a' : `Tahun lalu ${fmtIdrShort(v)}`);
  const d = (c: number, p: number | undefined) => ({ value: changePct(c, ly === null ? null : (p ?? null)), kind: 'pct' as const, goodWhen: 'up' as const, compareLabel: 'tahun lalu' });
  return (
    <KpiRow>
      <KpiTile label="Penjualan bersih" value={cur.net} fmt="idr-short" note={lyNote(ly?.net)} delta={d(cur.net, ly?.net)} />
      <KpiTile label="Laba kotor" value={cur.gp} fmt="idr-short" note={cur.gpPct === null ? 'Persen: n/a' : `${cur.gpPct.toFixed(1).replace('.', ',')}% dari penjualan`} delta={d(cur.gp, ly?.gp)} info="Laba kotor menurut laporan laba rugi ESB." />
      <KpiTile label="Biaya operasional" value={cur.opex} fmt="idr-short" note={lyNote(ly?.opex)} delta={{ value: changePct(cur.opex, ly === null ? null : ly.opex), kind: 'pct', goodWhen: 'neutral', compareLabel: 'tahun lalu' }} info="Ditampilkan apa adanya dari laporan ESB (tanda mengikuti ESB)." />
      <KpiTile label="EBITDA" value={cur.ebitda} fmt="idr-short" note={cur.ebitdaPct === null ? 'Persen: n/a' : `${cur.ebitdaPct.toFixed(1).replace('.', ',')}% dari penjualan`} delta={d(cur.ebitda, ly?.ebitda)} />
      <KpiTile label="Laba bersih" value={cur.np} fmt="idr-short" note={cur.npPct === null ? 'Persen: n/a' : `${cur.npPct.toFixed(1).replace('.', ',')}% dari penjualan`} delta={d(cur.np, ly?.np)} />
    </KpiRow>
  );
}

/** Laba Rugi Outlet (docs/04 §31.8-§31.10): hanya persona Eksekutif; basis = angka laporan ESB, tanpa penyesuaian EFN. */
export default async function PnlPage({ searchParams }: { searchParams: G67SearchParams }) {
  const { ctx, adjusted, raw } = await g67Page('/pnl', searchParams);
  const mr = parseMonthRange(raw, new Date());
  const extra = { m_from: mr.from, m_to: mr.to, yoy_from: mr.ly.from, yoy_to: mr.ly.to };
  const ptNames = ctx.filters.pt.map(codeName).join(', ');

  const [kpiR, trendR, stmtR, rankR, accR, cov] = await Promise.all([
    runEsb<Raw>(true, 'pnl_kpi', ctx, extra),
    runEsb<Raw>(true, 'pnl_trend_monthly', ctx, extra),
    runEsb<Raw>(true, 'pnl_statement', ctx, extra),
    runEsb<Raw>(true, 'pnl_branch_rank', ctx, extra),
    runEsb<Raw>(true, 'pnl_accounts', ctx, extra),
    loadCoverage(ctx),
  ]);

  const k = kpiR.ok ? kpiR.data[0] : undefined;
  const kpi: PnlKpiRaw = {
    net_sales: num(k?.net_sales),
    gross_profit: num(k?.gross_profit),
    opex: num(k?.opex),
    ebitda: num(k?.ebitda),
    net_profit: num(k?.net_profit),
    net_sales_ly: num(k?.net_sales_ly),
    gross_profit_ly: num(k?.gross_profit_ly),
    opex_ly: num(k?.opex_ly),
    ebitda_ly: num(k?.ebitda_ly),
    net_profit_ly: num(k?.net_profit_ly),
  };
  const { cur, ly } = pnlSides(kpi);
  const hasAny = num(k?.branches) > 0 || kpi.net_sales !== 0 || kpi.net_profit !== 0;
  const hint = coverageHint(cov, COVERAGE_KEYS.pnl);

  const trend = trendR.ok ? trendR.data : [];
  const cats = trend.map((r) => `${fmtMonth(String(r.period_month))}${num(r.partial_month) === 1 ? ' (sementara)' : ''}`);
  const series = (key: string) => trend.map((r) => num(r[key]));
  const trendSpec: ChartSpec = {
    id: 'pnl-trend',
    kind: 'line',
    fmt: 'idr',
    axisFmt: 'idr-short',
    categories: cats,
    series: [
      { name: 'Penjualan bersih', data: series('net_sales'), color: 'c1' },
      { name: 'Laba kotor', data: series('gross_profit'), color: 'c2', type: 'bar' },
      { name: 'EBITDA', data: series('ebitda'), color: 'c3', type: 'bar' },
      { name: 'Laba bersih', data: series('net_profit'), color: 'c4', type: 'bar' },
    ],
    summary: summarize('Penjualan bersih per bulan', cats, series('net_sales'), 'idr-short', true),
  };

  const stmtRaw: StatementRaw[] = stmtR.ok
    ? stmtR.data.map((r) => ({ period_month: String(r.period_month), scope: String(r.scope), section: String(r.section), amount_sum: num(r.amount_sum) }))
    : [];
  const stmtRows = buildStatement(stmtRaw, mr.months) as TableRow[];

  const rankRows: TableRow[] = rankR.ok
    ? rankR.data.map((r) => {
        const net = num(r.net_sales);
        return {
          branch_label: String(r.branch_label ?? ''),
          company_code: String(r.company_code ?? ''),
          net_sales: net,
          gp_pct: pctOf(num(r.gross_profit), net),
          ebitda: num(r.ebitda),
          ebitda_pct: pctOf(num(r.ebitda), net),
          np_pct: pctOf(num(r.net_profit), net),
        };
      })
    : [];

  const accRows: TableRow[] = accR.ok
    ? accR.data.map((r) => ({
        section: String(r.section ?? ''),
        account_no: String(r.account_no ?? ''),
        account_name: String(r.account_label ?? ''),
        amount: num(r.amount_sum),
        share: pctOf(num(r.amount_sum), cur.net),
      }))
    : [];

  const partialNote = mr.months.includes(mr.current)
    ? `${fmtMonth(mr.current)} masih berjalan (sementara). `
    : '';

  return (
    <>
      <PageHeader title="Laba Rugi Outlet" question="Berapa laba setiap outlet menurut laporan ESB, dan pos mana yang menggerakkannya?" actions={<span className="persona-chip">Eksekutif</span>} />
      <FilterNotice adjusted={adjusted || mr.adjusted} />

      <G67Form
        path="/pnl"
        label="Pilih rentang bulan"
        carry={{ pt: one(raw.pt), branch: one(raw.branch) }}
        summary={
          <>
            Periode: {fmtMonth(mr.from)} s.d. {fmtMonth(mr.to)} ({mr.months.length} bulan), dibanding {fmtRange(mr.ly.from, mr.ly.to)} tahun lalu.
            {' '}PT: {ptNames}.
          </>
        }
      >
        <label className="tt-select">
          <span>Dari bulan</span>
          <input className="input" type="month" name="mfrom" defaultValue={mr.mfrom} max={mr.current.slice(0, 7)} required />
        </label>
        <label className="tt-select">
          <span>Sampai bulan</span>
          <input className="input" type="month" name="mto" defaultValue={mr.mto} max={mr.current.slice(0, 7)} required />
        </label>
      </G67Form>

      <p className="muted small">
        Angka mengikuti laporan laba rugi ESB tanpa penyesuaian EFN. {partialNote}Bulan yang sudah tertutup dapat berubah karena jurnal susulan; data
        diperbarui harian. Maksimal 24 bulan.
      </p>

      {!kpiR.ok ? (
        fail(kpiR, 'Indikator utama')
      ) : !hasAny ? (
        <EmptyState
          title={`Data ESB untuk ${ptNames} belum tersedia pada rentang ini.`}
          reason={hint ?? `Rentang ${fmtDate(mr.from)} s.d. ${fmtDate(mr.to)}. Coba perluas rentang bulan atau pilih PT lain.`}
          icon="clock"
        />
      ) : (
        <>
          <Section title="Indikator utama" id="kpi">
            {tiles(cur, ly)}
            <p className="muted small">{num(k?.branches)} outlet berdata pada periode ini. {ly === null ? 'Data tahun lalu belum tersedia untuk pembanding.' : ''}</p>
          </Section>

          {fail(trendR, 'Tren bulanan') ?? <ChartCard title="Tren bulanan" subtitle="Penjualan bersih (garis), laba kotor, EBITDA, dan laba bersih (batang)" specs={[trendSpec]} />}
          {fail(kpiR, 'Pos laba rugi') ?? <ChartCard title="Pos laba rugi periode terpilih" subtitle="Penjumlahan seluruh outlet pada periode" specs={[posSpec(cur)]} />}

          <Section title="Laba rugi per pos dan bulan" id="statement">
            {fail(stmtR, 'Laba rugi per pos') ?? (
              <>
                <p className="muted small">
                  Nilai = jumlah outlet. Baris &quot;{UNALLOCATED_LABEL}&quot; = total brand dikurangi jumlah outlet (biaya pusat/alokasi yang tidak dibebankan ke
                  outlet); hanya tampil bila filter cabang kosong dan selisihnya berarti.
                </p>
                <DataTable caption="Laba rugi per pos dan bulan" columns={statementColumns(mr.months)} rows={stmtRows} pageSize={50} stickyFirstCol />
              </>
            )}
          </Section>

          <Section title="Peringkat outlet" id="rank">
            {fail(rankR, 'Peringkat outlet') ?? (
              <DataTable caption="Peringkat outlet menurut penjualan bersih" columns={RANK_COLS} rows={rankRows} defaultSort={{ key: 'net_sales', dir: 'desc' }} />
            )}
          </Section>

          <Section title="Akun per seksi" id="accounts">
            {fail(accR, 'Akun per seksi') ?? (
              <>
                <p className="muted small">
                  Hingga 50 akun terbesar per seksi (menurut nilai mutlak); gunakan filter Seksi di kolom pertama. Persen terhadap penjualan bersih
                  periode ({PL_LABEL.net_sales.toLowerCase()}).
                </p>
                <DataTable caption="Akun laba rugi per seksi" columns={ACCOUNT_COLS} rows={accRows} defaultSort={{ key: 'amount', dir: 'desc' }} />
              </>
            )}
          </Section>
        </>
      )}
    </>
  );
}
