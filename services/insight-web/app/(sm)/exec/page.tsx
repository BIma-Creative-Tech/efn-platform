import type { Metadata } from 'next';
import { ChartCard } from '@/components/ChartCard';
import { SmCardAuto, SmKpiTile } from '@/components/sm/SmCard';
import { SmFilterForm } from '@/components/sm/SmFilterForm';
import { SmFreshnessBadge } from '@/components/sm/SmFreshnessBadge';
import { SmTable, type SmColumn } from '@/components/sm/SmTable';
import { KpiRow, PageHeader, StatusPill } from '@/components/ui';
import { addDays, diffDays, eachDay } from '@/lib/dates';
import { fmtDayShort, fmtIdrShort, fmtMonth, fmtPct, fmtInt } from '@/lib/format';
import { summarize, type ChartSpec } from '@/lib/chart-spec';
import { loadExec } from '@/lib/sm-data';
import { MARTS, martOff } from '@/lib/sm-freshness';
import { fmtPctOrNa, fmtRatioX } from '@/lib/sm-format';
import { biasStatus, costCoveragePct, marginPct, pctChange, ppChange, wapeBias, wapeStatus, wasteStatus } from '@/lib/sm-kpi';
import { smPage, type SmSearchParams } from '@/lib/sm-page';
import { cfgStr } from '@/lib/sm-thresholds';
import { toNum } from '@/lib/sm-parse';
import { OUTLET_DROP_PCT, type OutletRow } from '@/lib/sm-vm';
import { biasReason, outletReason } from '@/lib/status-reasons';

export const dynamic = 'force-dynamic';
export const metadata: Metadata = { title: 'Direksi' };

const ERR = 'Tidak dapat dimuat';

export default async function ExecPage({ searchParams }: { searchParams: SmSearchParams }) {
  const { ctx, adjusted, chip } = await smPage('exec', searchParams, { defaultDays: 30 });
  const data = await loadExec(ctx);
  const { base, now, filters } = ctx;
  const t = base.t;
  const f = base.freshness;
  const outlets = base.warehouses.filter((w) => w.type !== 'dc' && ctx.session.wids.includes(w.id));
  const wasteBasisSales = cfgStr(base.config, 'waste.basis') === 'sales_excl_tax';

  // ---- ubin EX-1 ----
  const s = data.sales.ok ? data.sales.data : undefined;
  const m = data.margin.ok ? data.margin.data : undefined;
  const revCur = toNum(s?.rev_cur);
  const hasSales = data.sales.ok && revCur > 0;
  const mCur = m ? marginPct(toNum(m.gm_cur), toNum(m.net_cur)) : null;
  const mPrev = m ? marginPct(toNum(m.gm_prev), toNum(m.net_prev)) : null;
  const cover = m ? costCoveragePct(toNum(m.cov_cur), toNum(m.net_cur)) : null;
  const yoy = s && toNum(s.rev_yoy) > 0 ? pctChange(revCur, toNum(s.rev_yoy)) : null;

  const weeklyRows = data.weekly.ok ? data.weekly.data : [];
  const wk = weeklyRows.reduce<{ abs: number; act: number; fc: number }>((a, r) => ({ abs: a.abs + toNum(r.abs_sum), act: a.act + toNum(r.actual_sum), fc: a.fc + toNum(r.fc_sum) }), { abs: 0, act: 0, fc: 0 });
  const wb = wapeBias(wk.abs, wk.act, wk.fc);

  // ---- tren EX-2 ----
  const len = diffDays(filters.from, filters.to) + 1;
  const days = eachDay(filters.from, filters.to);
  const trendRows = data.trend.ok ? data.trend.data : [];
  const byDay = new Map(trendRows.map((r) => [String(r.day), toNum(r.rev_sum)]));
  const trendSpec: ChartSpec = {
    id: 'trend',
    kind: 'line',
    fmt: 'idr',
    axisFmt: 'idr-short',
    categories: days.map(fmtDayShort),
    series: [
      { name: 'Omzet (termasuk PPN)', data: days.map((d) => byDay.get(d) ?? null), color: 'accent' },
      { name: 'Periode lalu', data: days.map((d) => byDay.get(addDays(d, -len)) ?? null), color: 'muted', dashed: true },
    ],
    summary: summarize('Omzet harian (termasuk PPN)', days, days.map((d) => byDay.get(d) ?? null), 'idr-short', true, true),
  };

  // ---- WAPE mingguan EX-6 ----
  const weekCats = weeklyRows.map((r) => fmtDayShort(String(r.week_start)));
  const weekWape = weeklyRows.map((r) => wapeBias(toNum(r.abs_sum), toNum(r.actual_sum), toNum(r.fc_sum)).wape);
  const wapeSpec: ChartSpec = {
    id: 'wape',
    kind: 'line',
    fmt: 'pct',
    categories: weekCats,
    series: [{ name: 'Galat forecast (WAPE)', data: weekWape, color: 'c1' }],
    refLines: [{ value: t.wapeWarn, label: `Batas perlu dicek ${t.wapeWarn}%` }],
    summary: summarize('Galat forecast mingguan', weekCats, weekWape, 'pct', false),
  };

  const outletCols: SmColumn<OutletRow>[] = [
    { header: 'Outlet', cell: (r) => r.name },
    { header: 'Omzet (termasuk PPN)', align: 'right', cell: (r) => (<>{fmtIdrShort(r.revCur)} <span className="muted small">({fmtPctOrNa(r.sharePct)})</span></>) },
    { header: 'vs periode lalu', align: 'right', cell: (r) => fmtPctOrNa(r.revDeltaPct) },
    { header: 'Margin kotor (tanpa PPN)', align: 'right', cell: (r) => fmtPctOrNa(r.marginPct) },
    { header: 'Waste %', align: 'right', cell: (r) => fmtPctOrNa(r.wastePct) },
    { header: 'YoY', align: 'right', cell: (r) => fmtPctOrNa(r.yoyPct) },
    { header: 'Status', cell: (r) => (r.status === 'neutral' ? <span className="muted">-</span> : <StatusPill status={r.status} />) },
    {
      header: 'Keterangan',
      cell: (r) => outletReason(r, t, r.wastePct !== null ? wasteStatus(r.wastePct, t) : 'neutral', OUTLET_DROP_PCT),
      text: (r) => outletReason(r, t, r.wastePct !== null ? wasteStatus(r.wastePct, t) : 'neutral', OUTLET_DROP_PCT),
    },
  ];

  const w = data.waste.ok ? data.waste.data : null;
  const gm = data.gmroi.ok ? data.gmroi.data : null;

  return (
    <>
      <PageHeader
        title="Direksi"
        question="Bagaimana kinerja PT Supermarket Demo periode ini?"
        actions={
          <>
            <span className="persona-chip">{chip}</span>
            <SmFreshnessBadge
              marts={[MARTS.sales, MARTS.waste, MARTS.stock, MARTS.forecastAccuracy]}
              freshness={f}
              t={t}
              now={now}
            />
          </>
        }
      />
      <SmFilterForm
        path="/exec"
        filters={filters}
        yesterday={ctx.yesterday}
        outlets={outlets.map((o) => ({ id: o.id, name: o.name }))}
        categories={base.categories}
        show={{ period: true, outlets: true, category: true }}
        adjusted={adjusted}
      />

      <KpiRow>
        <SmKpiTile
          label="Omzet (termasuk PPN)"
          value={!data.sales.ok ? ERR : hasSales ? fmtIdrShort(revCur) : 'Belum ada data'}
          muted={!hasSales}
          delta={hasSales ? { value: pctChange(revCur, toNum(s?.rev_prev)), kind: 'pct', goodWhen: 'up' } : undefined}
          note="POS dan kiosk; online menyusul"
          info="Penjualan bersih setelah diskon, termasuk PPN (keputusan 20 Nov 2026). Margin dan Waste % memakai omzet tanpa PPN."
        />
        <SmKpiTile
          label="Margin kotor (tanpa PPN)"
          value={!data.margin.ok ? ERR : mCur === null ? 'Belum ada data' : fmtPct(mCur)}
          muted={mCur === null}
          delta={mCur !== null ? { value: ppChange(mCur, mPrev), kind: 'pp', goodWhen: 'up' } : undefined}
          note={cover !== null ? `HPP tersedia untuk ${fmtPct(cover)} dari omzet` : undefined}
          info="(Omzet tanpa PPN - HPP) / omzet tanpa PPN. PPN bukan pendapatan, jadi tidak dihitung dari omzet termasuk PPN."
        />
        <SmKpiTile
          label="GMROI (dasar tanpa PPN)"
          value={!data.gmroi.ok ? ERR : gm?.value != null ? fmtRatioX(gm.value) : 'Menunggu data persediaan'}
          muted={!(gm?.value != null)}
          note={gm?.lastMonth ? `Bulan ${fmtMonth(gm.lastMonth)}` : undefined}
          info="Margin kotor dibagi rata-rata nilai persediaan pada HPP. Definisi (periode, rata-rata harian/akhir bulan, DC ikut) diatur per PT di Odoo."
        />
        <SmKpiTile
          label="YoY (omzet termasuk PPN)"
          value={!data.sales.ok ? ERR : yoy === null ? 'Belum ada pembanding tahun lalu' : fmtPct(yoy)}
          muted={yoy === null}
          info="Perubahan omzet dibanding periode yang sama tahun lalu. Kosong sampai ada 12 bulan histori PT ini."
        />
        <SmKpiTile
          label={wasteBasisSales ? 'Waste % (dari omzet tanpa PPN)' : 'Waste % (dari HPP terjual)'}
          value={
            !data.waste.ok ? ERR : martOff(f.get(MARTS.waste)) ? 'Pencatatan susut belum aktif' : w?.pctCur != null ? fmtPct(w.pctCur) : 'Belum ada data'
          }
          muted={!(w?.pctCur != null)}
          status={w?.pctCur != null ? wasteStatus(w.pctCur, t) : undefined}
          delta={w?.pctCur != null ? { value: ppChange(w.pctCur, w.pctPrev), kind: 'pp', goodWhen: 'down' } : undefined}
          info="Nilai susut pada HPP dibagi omzet tanpa PPN (penyebut dapat diubah ke HPP terjual per PT)."
        />
        <SmKpiTile
          label="Galat forecast (WAPE)"
          value={
            !data.weekly.ok ? ERR : martOff(f.get(MARTS.forecastAccuracy)) ? 'Forecast belum aktif' : wb.wape !== null ? fmtPct(wb.wape) : 'Belum ada data'
          }
          muted={wb.wape === null}
          status={wb.wape !== null ? wapeStatus(wb.wape, t) : undefined}
          note="8 minggu terakhir; forecast adalah usulan"
          info="Jumlah selisih mutlak aktual vs forecast dibagi jumlah aktual, per SKU x outlet. Akurasi kira-kira 100% dikurangi galat."
        />
      </KpiRow>

      <div className="sm-grid">
        <div className="wide">
          <SmCardAuto
            id="ex2"
            title="Tren omzet (termasuk PPN)"
            mart={MARTS.sales}
            base={base}
            now={now}
            result={data.trend}
            hasRows={(d) => d.length > 0}
            zeroMeans="no-data"
            texts={{ zero: 'Tidak ada penjualan.', off: 'Data penjualan belum aktif.' }}
            wide
          >
            {() => <ChartCard title="Omzet harian" subtitle="Garis putus = periode lalu" specs={[trendSpec]} wide />}
          </SmCardAuto>
        </div>

        {data.outlets.ok && data.outlets.data.length <= 1 ? null : (
          <div className="wide">
            <SmCardAuto
              id="ex3"
              title="Perbandingan outlet"
              mart={MARTS.sales}
              base={base}
              now={now}
              result={data.outlets}
              hasRows={(d) => d.length > 0}
              zeroMeans="no-data"
              texts={{ zero: 'Tidak ada penjualan.', off: 'Data penjualan belum aktif.' }}
              wide
            >
              {(rows) => <SmTable caption="Perbandingan outlet menurut omzet" columns={outletCols} rows={rows} rowKey={(r) => String(r.id)} />}
            </SmCardAuto>
          </div>
        )}

        <SmCardAuto
          id="ex5"
          title="Waste % per kategori"
          mart={MARTS.waste}
          base={base}
          now={now}
          result={data.wasteCats}
          hasRows={(d) => d.length > 0}
          zeroMeans="no-data"
          texts={{ zero: 'Tidak ada susut tercatat.', off: 'Pencatatan susut belum aktif.', noData: 'Belum ada pencatatan susut pada periode ini.' }}
        >
          {(rows) => (
            <ChartCard
              title="Susut dari omzet tanpa PPN"
              specs={[
                {
                  id: 'wastecat',
                  kind: 'barH',
                  fmt: 'pct',
                  categories: rows.map((r) => r.name),
                  series: [
                    {
                      name: 'Waste %',
                      data: rows.map((r) => r.wastePct),
                      color: 'c1',
                      itemColors: rows.map((r) => (r.wastePct === null ? 'muted' : wasteStatus(r.wastePct, t) === 'bad' ? 'bad' : wasteStatus(r.wastePct, t) === 'warn' ? 'warn' : 'good')),
                    },
                  ],
                  summary: summarize('Waste % per kategori', rows.map((r) => r.name), rows.map((r) => r.wastePct), 'pct', false),
                },
              ]}
            />
          )}
        </SmCardAuto>

        <SmCardAuto
          id="ex6"
          title="Akurasi forecast"
          mart={MARTS.forecastAccuracy}
          base={base}
          now={now}
          result={data.weekly}
          hasRows={(d) => d.length > 0}
          zeroMeans="no-data"
          texts={{
            zero: 'Belum ada hasil evaluasi.',
            off: 'Forecast belum aktif.',
            noData: 'Belum ada hasil evaluasi forecast (butuh minimal 4 minggu data setelah forecast aktif).',
          }}
        >
          {() => (
            <>
              <p className="muted small">Forecast adalah usulan; kepala toko menyetujui qty.</p>
              <ChartCard title="Galat forecast mingguan" specs={[wapeSpec]} />
              {data.wapeOutlets.ok && data.wapeOutlets.data.length > 0 ? (
                <SmTable
                  caption="Galat forecast dan bias per outlet"
                  rows={data.wapeOutlets.data}
                  rowKey={(r) => String(r.warehouse_id)}
                  columns={[
                    { header: 'Outlet', cell: (r) => base.whNames.get(toNum(r.warehouse_id)) ?? `Outlet #${toNum(r.warehouse_id)}` },
                    { header: 'WAPE', align: 'right', cell: (r) => fmtPctOrNa(wapeBias(toNum(r.abs_sum), toNum(r.actual_sum), toNum(r.fc_sum)).wape) },
                    {
                      header: 'Bias',
                      align: 'right',
                      cell: (r) => {
                        const b = wapeBias(toNum(r.abs_sum), toNum(r.actual_sum), toNum(r.fc_sum)).bias;
                        return b === null ? 'n/a' : `${b > 0 ? '+' : ''}${fmtPct(b)} (${b > 0 ? 'kelebihan' : b < 0 ? 'kekurangan' : 'pas'})`;
                      },
                    },
                    { header: 'Status bias', cell: (r) => <StatusPill status={biasStatus(wapeBias(toNum(r.abs_sum), toNum(r.actual_sum), toNum(r.fc_sum)).bias, t)} /> },
                    {
                      header: 'Keterangan',
                      cell: (r) => {
                        const b = wapeBias(toNum(r.abs_sum), toNum(r.actual_sum), toNum(r.fc_sum)).bias;
                        return biasReason(b, biasStatus(b, t), t);
                      },
                    },
                    { header: 'SKU dievaluasi', align: 'right', cell: (r) => fmtInt(toNum(r.sku_n)) },
                  ]}
                />
              ) : null}
            </>
          )}
        </SmCardAuto>
      </div>
    </>
  );
}
