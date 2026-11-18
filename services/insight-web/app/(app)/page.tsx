import type { Metadata } from 'next';
import Link from 'next/link';
import { Block } from '@/components/blocks';
import { ChartCard } from '@/components/ChartCard';
import { DataTable, type TableRow } from '@/components/DataTable';
import { FilterNotice } from '@/components/FilterNotice';
import { EmptyState, KpiRow, KpiTile, PageHeader, Section, StatusPill } from '@/components/ui';
import { codeColor, codeName } from '@/lib/brands';
import { summarize, type ChartSpec } from '@/lib/chart-spec';
import { eachDay } from '@/lib/dates';
import { fmtDayShort, fmtRange } from '@/lib/format';
import { run, settle, TABLE_LOADERS, num, type Result, type Row } from '@/lib/loaders';
import { pageCtx, type SearchParams } from '@/lib/page-ctx';
import { TABLES } from '@/lib/table-defs';
import { freshnessStatus, sellThroughStatus, type Status } from '@/lib/thresholds';
import { pctDelta, pctOf, ppDelta, ratio, trendSeries, type DailyRow } from '@/lib/vm';

export const dynamic = 'force-dynamic';
export const metadata: Metadata = { title: 'Ringkasan' };

interface KpiRowRaw {
  net_cur: number;
  net_prev: number;
  trx_cur: number;
  trx_prev: number;
  cogs_cur: number;
  cogs_prev: number;
}

function rankSpec(id: string, tab: string, rows: Row[], from: string, to: string): ChartSpec {
  const labels = rows.map((r) => String(r.branch_label));
  const vals = rows.map((r) => Number(r.net_sales));
  return {
    id,
    tabLabel: tab,
    kind: 'barH',
    fmt: 'idr',
    axisFmt: 'idr-short',
    categories: labels,
    series: [{ name: 'Penjualan bersih', data: vals, color: 'c1', itemColors: rows.map((r) => codeColor(String(r.company_code))) }],
    drill: { param: 'branch', values: rows.map((r) => String(r.branch_key)), label: 'cabang' },
    summary: summarize(`Penjualan bersih per cabang ${fmtRange(from, to)}`, labels, vals, 'idr-short', true),
  };
}

export default async function SummaryPage({ searchParams }: { searchParams: SearchParams }) {
  const { ctx, adjusted } = await pageCtx(searchParams);
  const { from, to } = ctx.filters;

  const [kpiR, trendR, rankR, shareR, stR, flowBrR, freshR, noSalesR, lastRunR] = await Promise.all([
    settle(run<KpiRowRaw>('exec_kpi', ctx)),
    settle(run<DailyRow>('exec_trend_daily', ctx)),
    settle(TABLE_LOADERS['branch-rank'](ctx)),
    ctx.session.codes.length > 1 ? settle(run<{ company_code: string; net: number }>('exec_pt_share', ctx)) : Promise.resolve(null),
    settle(run<{ sold: number; supply: number }>('exec_sell_through', ctx)),
    settle(run<{ sell_through_pct: number | null }>('flow_by_branch', ctx)),
    settle(run<{ age_hours: number }>('dq_freshness', ctx)),
    settle(TABLE_LOADERS['dq-no-sales'](ctx)),
    settle(TABLE_LOADERS['dq-last-run'](ctx)),
  ]);

  const trendSpecs = (data: DailyRow[]): ChartSpec[] =>
    (['net', 'trx', 'avg'] as const).map((m) => {
      const t = trendSeries(data.map((r) => ({ ...r, net: num(r.net), trx: num(r.trx) })), from, to, m);
      const cats = t.days.map(fmtDayShort);
      const fmt = m === 'trx' ? 'int' : 'idr';
      return {
        id: m,
        tabLabel: m === 'net' ? 'Penjualan bersih' : m === 'trx' ? 'Transaksi' : 'Rata-rata per transaksi',
        kind: 'line',
        fmt,
        axisFmt: m === 'trx' ? 'int' : 'idr-short',
        categories: cats,
        series: t.series,
        summary: summarize(
          m === 'net' ? 'Penjualan bersih harian' : m === 'trx' ? 'Transaksi harian' : 'Rata-rata per transaksi harian',
          cats,
          t.series[0].data,
          m === 'trx' ? 'int' : 'idr-short',
          m !== 'avg',
        ),
      } satisfies ChartSpec;
    });

  const days = eachDay(from, to);
  const dailyNet = trendR.ok
    ? days.map((d) => trendR.data.filter((r) => r.sales_date === d).reduce((s, r) => s + num(r.net), 0))
    : [];

  // "Perlu perhatian"
  const attention: { key: string; status: Status; text: string; href: string }[] = [];
  if (flowBrR.ok) {
    const bad = flowBrR.data.filter((r) => sellThroughStatus(r.sell_through_pct === null ? null : Number(r.sell_through_pct)) === 'bad').length;
    if (bad > 0) attention.push({ key: 'st', status: 'bad', text: `${bad} cabang dengan sell-through di bawah 70%`, href: '/flow' });
  }
  if (freshR.ok && freshR.data.length > 0) {
    const worst = Math.max(...freshR.data.map((r) => num(r.age_hours)));
    const st = freshnessStatus(worst);
    if (st !== 'good') attention.push({ key: 'fresh', status: st, text: 'Data belum diperbarui sesuai jadwal', href: '/data-quality' });
  } else if (freshR.ok) {
    attention.push({ key: 'fresh', status: 'unknown', text: 'Kesegaran data tidak diketahui', href: '/data-quality' });
  }
  if (noSalesR.ok && noSalesR.data.length > 0) {
    attention.push({ key: 'nosales', status: 'bad', text: `${noSalesR.data.length} cabang tanpa penjualan kemarin`, href: '/data-quality' });
  }
  if (lastRunR.ok && lastRunR.data.length > 0) {
    attention.push({ key: 'run', status: 'bad', text: 'Ekstraksi data terakhir tidak OK', href: '/data-quality' });
  }

  return (
    <>
      <PageHeader title="Ringkasan" question="Bagaimana kinerja bisnis periode ini dibanding sebelumnya, dan di mana yang perlu perhatian?" />
      <FilterNotice adjusted={adjusted} />
      <p className="muted small">Periode: {fmtRange(from, to)}</p>

      <Section title="Indikator utama" id="kpi">
        <Block result={kpiR} title="Indikator utama">
          {(rows) => {
            const k = rows[0];
            if (!k) return <EmptyState title="Tidak ada penjualan pada periode ini" reason="Filter mungkin terlalu sempit, atau data belum masuk." />;
            const netC = num(k.net_cur);
            const netP = num(k.net_prev);
            const trxC = num(k.trx_cur);
            const trxP = num(k.trx_prev);
            const avgC = ratio(netC, trxC);
            const avgP = ratio(netP, trxP);
            const hppC = pctOf(num(k.cogs_cur), netC);
            const hppP = pctOf(num(k.cogs_prev), netP);
            const st = stR.ok && stR.data[0] ? ratio(num(stR.data[0].sold), num(stR.data[0].supply)) : null;
            const stPct = st === null ? null : st * 100;
            return (
              <KpiRow>
                <KpiTile
                  label="Penjualan bersih"
                  value={netC}
                  fmt="idr-short"
                  delta={{ value: pctDelta(netC, netP), kind: 'pct', goodWhen: 'up' }}
                  spark={dailyNet}
                  note="Sebelum PB1/service"
                  info="Total ESB dikurangi diskon. Belum termasuk PB1/service charge, jadi tidak sama dengan total pembayaran."
                />
                <KpiTile
                  label="Transaksi"
                  value={trxC}
                  fmt="int"
                  delta={{ value: pctDelta(trxC, trxP), kind: 'pct', goodWhen: 'up' }}
                  info="Jumlah struk unik per cabang per hari."
                />
                <KpiTile label="Rata-rata per transaksi" value={avgC} fmt="idr" delta={{ value: avgC !== null && avgP !== null ? pctDelta(avgC, avgP) : null, kind: 'pct', goodWhen: 'up' }} />
                <KpiTile
                  label="HPP terhadap penjualan bersih"
                  value={hppC}
                  fmt="pct"
                  delta={{ value: ppDelta(hppC, hppP), kind: 'pp', goodWhen: 'down' }}
                  info="Total HPP dibagi penjualan bersih."
                />
                <KpiTile
                  label="Sell-through"
                  value={stPct}
                  fmt="pct"
                  status={sellThroughStatus(stPct)}
                  note="Berdasarkan arus barang, bukan stok"
                  info="Terjual dibagi pasokan toko (diterima + produksi toko + transfer masuk - transfer keluar). Definisi proposal, menunggu konfirmasi planner CK."
                />
              </KpiRow>
            );
          }}
        </Block>
      </Section>

      <div className="grid-2">
        <div className="wide">
          <Block result={trendR} title="Tren penjualan">
            {(data) => (
              <ChartCard
                title="Tren"
                subtitle={`${fmtRange(from, to)}; garis putus = periode lalu`}
                specs={trendSpecs(data)}
                wide
                emptyText="Tidak ada penjualan pada periode ini."
              />
            )}
          </Block>
        </div>
        <Block result={rankR} title="Peringkat cabang">
          {(rows) => (
            <ChartCard
              title="Peringkat cabang"
              subtitle="Klik batang untuk memfilter cabang"
              specs={[
                rankSpec('top', '10 teratas', rows.slice(0, 10), from, to),
                rankSpec('bottom', '10 terbawah', rows.slice(-10).reverse(), from, to),
              ]}
              height={320}
            />
          )}
        </Block>
        {shareR ? (
          <Block result={shareR as Result<{ company_code: string; net: number }[]>} title="Kontribusi PT">
            {(rows) => (
              <ChartCard
                title="Kontribusi PT"
                subtitle="Penjualan bersih per PT"
                specs={[
                  {
                    id: 'share',
                    kind: 'barH',
                    fmt: 'idr',
                    axisFmt: 'idr-short',
                    categories: rows.map((r) => codeName(r.company_code)),
                    series: [{ name: 'Penjualan bersih', data: rows.map((r) => num(r.net)), color: 'c1', itemColors: rows.map((r) => codeColor(r.company_code)) }],
                    summary: summarize('Penjualan bersih per PT', rows.map((r) => codeName(r.company_code)), rows.map((r) => num(r.net)), 'idr-short', true),
                  },
                ]}
              />
            )}
          </Block>
        ) : null}
      </div>

      <Section title="Perlu perhatian" id="attn">
        <div className="card">
          {attention.length === 0 ? (
            <EmptyState tone="positive" title="Tidak ada yang perlu perhatian" reason="Semua pemeriksaan utama lolos." />
          ) : (
            <ul className="attn">
              {attention.slice(0, 5).map((a) => (
                <li key={a.key}>
                  <StatusPill status={a.status} />
                  <span>{a.text}</span>
                  <Link href={a.href}>Lihat</Link>
                </li>
              ))}
            </ul>
          )}
        </div>
      </Section>

      <Section title="Cabang" id="rank">
        <Block result={rankR} title="Tabel peringkat cabang">
          {(rows) => (
            <DataTable
              caption={TABLES['branch-rank'].caption}
              columns={TABLES['branch-rank'].columns}
              rows={rows as TableRow[]}
              csvName="branch-rank"
              defaultSort={{ key: 'net_sales', dir: 'desc' }}
            />
          )}
        </Block>
      </Section>
    </>
  );
}
