import type { Metadata } from 'next';
import Link from 'next/link';
import { ErrorCard } from '@/components/blocks';
import { ChartCard } from '@/components/ChartCard';
import { DataTable, type TableRow } from '@/components/DataTable';
import { FilterNotice } from '@/components/FilterNotice';
import { MixedScopeNote, NotApplicableSmd, SideNote, SmdBasisNote } from '@/components/SmdNotice';
import { EmptyState, KpiRow, KpiTile, PageHeader, Section, StatusPill } from '@/components/ui';
import { codeColor, codeName } from '@/lib/brands';
import { summarize, type ChartSpec } from '@/lib/chart-spec';
import { eachDay } from '@/lib/dates';
import { fmtDayShort, fmtRange } from '@/lib/format';
import { num, type Result, type Row } from '@/lib/loaders';
import { pageCtx, type SearchParams } from '@/lib/page-ctx';
import { loadEsbTable, loadSmdNames, runEsb, runSm } from '@/lib/smd-data';
import {
  ZERO_PARTS,
  allOk,
  esbOnlyMode,
  esbParts,
  hppPct,
  mergeBranchRank,
  mergePtShare,
  mergeSides,
  smdActive,
  smdBranchRows,
  smdParts,
  smdTrendRows,
  sumParts,
  SMD_BASIS,
} from '@/lib/smd-merge';
import { TABLES } from '@/lib/table-defs';
import { freshnessStatus, sellThroughStatus, type Status } from '@/lib/thresholds';
import { pctDelta, ratio, ppDelta, trendSeries, type DailyRow } from '@/lib/vm';

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
  const { ctx, adjusted, sel } = await pageCtx(searchParams);
  const { from, to } = ctx.filters;
  const esbOn = sel.esb;
  const smdOn = smdActive(sel, ctx.filters, null);
  const mode = esbOnlyMode(sel);
  const mixed = esbOn && smdOn;
  const sm = (n: Parameters<typeof runSm>[0]) => (smdOn ? runSm(n, ctx, sel) : Promise.resolve(null));
  const showShare = smdOn || ctx.session.codes.length > 1;

  const [kpiR, trendR, rankR, shareR, stR, flowBrR, freshR, noSalesR, lastRunR, smDaily, smNet, smCogs, smOutlet, smMargin, names] = await Promise.all([
    runEsb<KpiRowRaw>(esbOn, 'exec_kpi', ctx),
    runEsb<DailyRow>(esbOn, 'exec_trend_daily', ctx),
    loadEsbTable(esbOn, 'branch-rank', ctx),
    showShare ? runEsb<{ company_code: string; net: number }>(esbOn, 'exec_pt_share', ctx) : Promise.resolve(null),
    runEsb<{ sold: number; supply: number }>(esbOn, 'exec_sell_through', ctx),
    runEsb<{ sell_through_pct: number | null }>(esbOn, 'flow_by_branch', ctx),
    runEsb<{ age_hours: number }>(esbOn, 'dq_freshness', ctx),
    loadEsbTable(esbOn, 'dq-no-sales', ctx),
    loadEsbTable(esbOn, 'dq-last-run', ctx),
    sm('sm_fb_daily'),
    sm('sm_sales_net'),
    sm('sm_cogs_sums'),
    sm('sm_fb_by_outlet'),
    sm('sm_ex_by_outlet_margin'),
    smdOn ? loadSmdNames(ctx, sel) : Promise.resolve(null),
  ]);

  // ---- KPI: ESB + SMD digabung per PT (SMD = termasuk PPN)
  const kpiSides = mergeSides(esbOn, kpiR, smdOn, smdOn ? allOk([smDaily, smNet, smCogs].map((r) => r as Result<Record<string, unknown>[]>)) : null);

  // ---- tren harian: baris ESB + baris seri SMD (brand `smd`)
  const trendSides = mergeSides(esbOn, trendR, smdOn, smDaily as Result<Record<string, unknown>[]> | null);
  const trendRows: DailyRow[] = [...(trendSides.esb ?? []), ...(trendSides.smd ? smdTrendRows(trendSides.smd) : [])];

  // Rata-rata per transaksi gabungan tidak dihitung bila basis campuran (docs/04 §28.14.2): tab "avg" hanya untuk satu basis.
  const metrics: readonly ('net' | 'trx' | 'avg')[] = mixed ? ['net', 'trx'] : ['net', 'trx', 'avg'];
  const trendSpecs = (data: DailyRow[]): ChartSpec[] =>
    metrics.map((m) => {
      const t = trendSeries(data.map((r) => ({ ...r, net: num(r.net), trx: num(r.trx) })), from, to, m);
      const cats = t.days.map(fmtDayShort);
      const fmt = m === 'trx' ? 'int' : 'idr';
      return {
        id: m,
        tabLabel: m === 'net' ? (mixed ? 'Penjualan (basis campuran)' : smdOn ? 'Omzet' : 'Penjualan bersih') : m === 'trx' ? 'Transaksi' : 'Rata-rata per transaksi',
        kind: 'line',
        fmt,
        axisFmt: m === 'trx' ? 'int' : 'idr-short',
        categories: cats,
        series: t.series,
        summary: summarize(
          m === 'net' ? 'Penjualan harian' : m === 'trx' ? 'Transaksi harian' : 'Rata-rata per transaksi harian',
          cats,
          t.series[0].data,
          m === 'trx' ? 'int' : 'idr-short',
          m !== 'avg',
        ),
      } satisfies ChartSpec;
    });

  const days = eachDay(from, to);
  const dailyNet = days.map(
    (d) =>
      trendRows.filter((r) => r.sales_date === d).reduce((s, r) => s + num(r.net), 0),
  );

  // ---- peringkat cabang: ESB + outlet SMD
  const rankSides = mergeSides(esbOn, rankR, smdOn, smdOn ? allOk([smOutlet, smMargin].map((r) => r as Result<Record<string, unknown>[]>)) : null);
  const rankRows: Row[] = mergeBranchRank(
    rankSides.esb ?? [],
    rankSides.smd ? smdBranchRows(rankSides.smd[0], rankSides.smd[1], names?.wh ?? new Map<number, string>()) : [],
  );

  // ---- kontribusi PT
  const shareSides = showShare && shareR ? mergeSides(esbOn, shareR, smdOn, smdOn ? (smDaily as Result<Record<string, unknown>[]>) : null) : null;
  const shareRows = shareSides
    ? mergePtShare(
        shareSides.esb ?? [],
        shareSides.smd ? kpiSmdRev(shareSides.smd, from) : null,
      )
    : [];

  // "Perlu perhatian" (khusus F&B)
  const attention: { key: string; status: Status; text: string; href: string }[] = [];
  if (flowBrR.ok) {
    const bad = flowBrR.data.filter((r) => sellThroughStatus(r.sell_through_pct === null ? null : Number(r.sell_through_pct)) === 'bad').length;
    if (bad > 0) attention.push({ key: 'st', status: 'bad', text: `${bad} cabang dengan sell-through di bawah 70%`, href: '/flow' });
  }
  if (esbOn && freshR.ok && freshR.data.length > 0) {
    const worst = Math.max(...freshR.data.map((r) => num(r.age_hours)));
    const st = freshnessStatus(worst);
    if (st !== 'good') attention.push({ key: 'fresh', status: st, text: 'Data belum diperbarui sesuai jadwal', href: '/data-quality' });
  } else if (esbOn && freshR.ok) {
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
      {smdOn ? <SmdBasisNote mixed={mixed} /> : null}
      <SideNote note={kpiSides.note} />

      <Section title="Indikator utama" id="kpi">
        {kpiSides.fail ? (
          <ErrorCard title="Indikator utama" refId={kpiSides.fail.ref} reason={kpiSides.fail.reason} />
        ) : (
          (() => {
            const k = kpiSides.esb?.[0];
            const e = esbParts(k);
            const s = kpiSides.smd ? smdParts(kpiSides.smd[0], from, kpiSides.smd[1][0], kpiSides.smd[2][0]) : ZERO_PARTS;
            const p = sumParts(e, s);
            if (!k && p.rev === 0 && p.revPrev === 0 && p.trx === 0) {
              return <EmptyState title="Tidak ada penjualan pada periode ini" reason="Filter mungkin terlalu sempit, atau data belum masuk." />;
            }
            const avgC = ratio(p.rev, p.trx);
            const avgP = ratio(p.revPrev, p.trxPrev);
            const avgE = ratio(e.rev, e.trx);
            const avgEP = ratio(e.revPrev, e.trxPrev);
            const avgS = ratio(s.rev, s.trx);
            const avgSP = ratio(s.revPrev, s.trxPrev);
            const hpp = hppPct(p);
            const stRow = stR.ok ? stR.data[0] : undefined;
            const st = stRow ? ratio(num(stRow.sold), num(stRow.supply)) : null;
            const stPct = st === null ? null : st * 100;
            const revLabel = mixed ? 'Penjualan (basis campuran)' : !esbOn ? `Omzet SMD (${SMD_BASIS})` : 'Penjualan bersih';
            return (
              <KpiRow>
                <KpiTile
                  label={revLabel}
                  value={p.rev}
                  fmt="idr-short"
                  delta={{ value: pctDelta(p.rev, p.revPrev), kind: 'pct', goodWhen: 'up' }}
                  spark={dailyNet}
                  note={mixed ? 'F&B tanpa pajak, SMD termasuk PPN' : !esbOn ? SMD_BASIS : 'Sebelum PB1/service'}
                  info={
                    mixed
                      ? 'Total brand F&B (penjualan bersih ESB dikurangi diskon, sebelum PB1/service) ditambah omzet SMD termasuk PPN. Basis berbeda; nilai per brand ada di grafik dan tabel di bawah.'
                      : !esbOn
                        ? 'Omzet SMD termasuk PPN (sama dengan dashboard supermarket).'
                        : 'Total ESB dikurangi diskon. Belum termasuk PB1/service charge, jadi tidak sama dengan total pembayaran.'
                  }
                />
                <KpiTile
                  label="Transaksi"
                  value={p.trx}
                  fmt="int"
                  delta={{ value: pctDelta(p.trx, p.trxPrev), kind: 'pct', goodWhen: 'up' }}
                  info={mixed ? 'Struk unik F&B ditambah order POS SMD.' : 'Jumlah struk unik per cabang per hari.'}
                />
                {mixed ? (
                  <>
                    <KpiTile label="Rata-rata per transaksi (F&B)" value={avgE} fmt="idr" delta={{ value: avgE !== null && avgEP !== null ? pctDelta(avgE, avgEP) : null, kind: 'pct', goodWhen: 'up' }} note="Tanpa pajak" />
                    <KpiTile label={`Rata-rata per transaksi (SMD, ${SMD_BASIS})`} value={avgS} fmt="idr" delta={{ value: avgS !== null && avgSP !== null ? pctDelta(avgS, avgSP) : null, kind: 'pct', goodWhen: 'up' }} note="Tidak digabung: basis berbeda" />
                  </>
                ) : (
                  <KpiTile
                    label={!esbOn ? `Rata-rata per transaksi (${SMD_BASIS})` : 'Rata-rata per transaksi'}
                    value={avgC}
                    fmt="idr"
                    delta={{ value: avgC !== null && avgP !== null ? pctDelta(avgC, avgP) : null, kind: 'pct', goodWhen: 'up' }}
                  />
                )}
                <KpiTile
                  label="HPP terhadap penjualan bersih"
                  value={hpp.cur}
                  fmt="pct"
                  delta={{ value: ppDelta(hpp.cur, hpp.prev), kind: 'pp', goodWhen: 'down' }}
                  note={smdOn ? 'Penyebut tanpa pajak' : undefined}
                  info="Total HPP dibagi penjualan bersih tanpa pajak (SMD: tanpa PPN)."
                />
                {esbOn ? (
                  <KpiTile
                    label="Sell-through"
                    value={stPct}
                    fmt="pct"
                    status={sellThroughStatus(stPct)}
                    note={mixed ? 'Hanya brand F&B; berdasarkan arus barang' : 'Berdasarkan arus barang, bukan stok'}
                    info="Terjual dibagi pasokan toko (diterima + produksi toko + transfer masuk - transfer keluar). Definisi proposal, menunggu konfirmasi planner CK."
                  />
                ) : null}
              </KpiRow>
            );
          })()
        )}
      </Section>

      <div className="grid-2">
        <div className="wide">
          {trendSides.fail ? (
            <ErrorCard title="Tren penjualan" refId={trendSides.fail.ref} reason={trendSides.fail.reason} />
          ) : (
            <ChartCard
              title="Tren"
              subtitle={`${fmtRange(from, to)}; garis putus = periode lalu${smdOn ? `; SMD ${SMD_BASIS}` : ''}`}
              specs={trendSpecs(trendRows)}
              wide
              emptyText="Tidak ada penjualan pada periode ini."
            />
          )}
        </div>
        {rankSides.fail ? (
          <ErrorCard title="Peringkat cabang" refId={rankSides.fail.ref} reason={rankSides.fail.reason} />
        ) : (
          <ChartCard
            title="Peringkat cabang"
            subtitle={`Klik batang untuk memfilter cabang${smdOn ? `; outlet SMD ${SMD_BASIS}` : ''}`}
            specs={[
              rankSpec('top', '10 teratas', rankRows.slice(0, 10), from, to),
              rankSpec('bottom', '10 terbawah', rankRows.slice(-10).reverse(), from, to),
            ]}
            height={320}
          />
        )}
        {shareSides ? (
          shareSides.fail ? (
            <ErrorCard title="Kontribusi PT" refId={shareSides.fail.ref} reason={shareSides.fail.reason} />
          ) : (
            <div>
              <ChartCard
                title="Kontribusi PT"
                subtitle={mixed ? 'Penjualan per PT (basis campuran)' : 'Penjualan bersih per PT'}
                specs={[
                  {
                    id: 'share',
                    kind: 'barH',
                    fmt: 'idr',
                    axisFmt: 'idr-short',
                    categories: shareRows.map((r) => (r.company_code === 'SMD' ? `${codeName('SMD')} (${SMD_BASIS})` : codeName(r.company_code))),
                    series: [{ name: mixed ? 'Penjualan (basis campuran)' : 'Penjualan bersih', data: shareRows.map((r) => num(r.net)), color: 'c1', itemColors: shareRows.map((r) => codeColor(r.company_code)) }],
                    summary: summarize('Penjualan per PT', shareRows.map((r) => codeName(r.company_code)), shareRows.map((r) => num(r.net)), 'idr-short', true),
                  },
                ]}
              />
              {mixed ? (
                <p className="muted small" role="note">
                  Basis campuran: F&amp;B tanpa PB1/service charge, SMD termasuk PPN.
                </p>
              ) : null}
            </div>
          )
        ) : null}
      </div>

      <Section title="Perlu perhatian" id="attn">
        {mode === 'only-smd' ? (
          <NotApplicableSmd reason="Pemeriksaan ini memakai data brand F&B (sell-through, kesegaran ESB). Untuk SMD buka dashboard Supermarket di sidebar." />
        ) : (
          <div className="card">
            {mode === 'mixed' ? <MixedScopeNote /> : null}
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
        )}
      </Section>

      <Section title="Cabang" id="rank">
        {rankSides.fail ? (
          <ErrorCard title="Tabel peringkat cabang" refId={rankSides.fail.ref} reason={rankSides.fail.reason} />
        ) : (
          <>
            <SideNote note={rankSides.note} />
            <DataTable
              caption={TABLES['branch-rank'].caption}
              columns={TABLES['branch-rank'].columns}
              rows={rankRows as TableRow[]}
              csvName={smdOn ? undefined : 'branch-rank'}
              defaultSort={{ key: 'net_sales', dir: 'desc' }}
            />
          </>
        )}
      </Section>
    </>
  );
}

/** Omzet SMD (termasuk PPN) periode ini dari baris `sm_fb_daily` (hari >= from). */
function kpiSmdRev(rows: readonly Record<string, unknown>[], from: string): number {
  return rows.reduce((s, r) => (String(r.day) >= from ? s + num(r.rev_sum) : s), 0);
}
