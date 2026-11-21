import type { Metadata } from 'next';
import Link from 'next/link';
import { Block } from '@/components/blocks';
import { ChartCard } from '@/components/ChartCard';
import { FilterNotice } from '@/components/FilterNotice';
import { MixedScopeNote } from '@/components/SmdNotice';
import { ActionLink, SmKpiTile } from '@/components/sm/SmCard';
import { SmFreshnessBadge } from '@/components/sm/SmFreshnessBadge';
import { SmTable } from '@/components/sm/SmTable';
import { EmptyState, KpiRow, PageHeader, Section, StatusPill } from '@/components/ui';
import { summarize, type ChartSpec } from '@/lib/chart-spec';
import { addDays, wibToday } from '@/lib/dates';
import { fmtDayShort, fmtPct, fmtQty } from '@/lib/format';
import { odooListUrl } from '@/lib/odoo-links';
import { pageCtx, type SearchParams } from '@/lib/page-ctx';
import { getConfig } from '@/lib/runtime';
import { FORECAST_DAYS, runSm } from '@/lib/smd-data';
import { esbOnlyMode, smdActive } from '@/lib/smd-merge';
import { loadSmBase } from '@/lib/sm-data';
import { MARTS, martOff } from '@/lib/sm-freshness';
import { fmtPctOrNa, fmtStampWib } from '@/lib/sm-format';
import { biasStatus, wapeBias, wapeStatus } from '@/lib/sm-kpi';
import { parseChDateTime, toNum } from '@/lib/sm-parse';
import { joinNotes } from '@/lib/flow-notes';
import type { SmThresholds } from '@/lib/sm-thresholds';
import { biasReason, wapeReason } from '@/lib/status-reasons';

export const dynamic = 'force-dynamic';
export const metadata: Metadata = { title: 'Forecast' };

/** Keterangan status WAPE (+ bias bila bukan Baik) untuk satu baris jumlah mentah (abs_sum, actual_sum, fc_sum). */
function wapeNote(r: Record<string, unknown>, t: SmThresholds): string {
  const wb = wapeBias(toNum(r.abs_sum), toNum(r.actual_sum), toNum(r.fc_sum));
  const ws = wapeStatus(wb.wape, t);
  const bs = biasStatus(wb.bias, t);
  return joinNotes(wapeReason(wb.wape, ws, t), bs === 'warn' || bs === 'bad' ? biasReason(wb.bias, bs, t) : null);
}

const fmtBias = (b: number | null): string => (b === null ? 'n/a' : `${b > 0 ? '+' : ''}${fmtPct(b)} (${b > 0 ? 'kelebihan' : b < 0 ? 'kekurangan' : 'pas'})`);

/**
 * Forecast. ESB tetap "segera hadir" (G44). SMD (G51 B3, docs/04 §28.14.4): 8 minggu forecast vs aktual, WAPE dan bias per outlet
 * dan kategori (agregasi ulang dari jumlah mentah), forecast 14 hari dengan pita p10-p90, tautan usulan min/max di Odoo.
 * Forecast adalah usulan; di luar jalur transaksi.
 */
export default async function ForecastPage({ searchParams }: { searchParams: SearchParams }) {
  const { ctx, adjusted, sel } = await pageCtx(searchParams);
  const smdOn = smdActive(sel, ctx.filters, null);
  const mode = esbOnlyMode(sel);
  const now = new Date();
  const today = wibToday(now);

  // QA G51: week_start = Senin; -55 membuang minggu tertua (8 minggu = Senin minggu ini - 56 s.d. - 7) -> -62.
  const range = { from: addDays(today, -62), to: today };
  const [base, weekly, outlets, cats, next] = smdOn
    ? await Promise.all([
        loadSmBase(ctx.session, now),
        runSm('sm_ex_wape_weekly', ctx, sel, range),
        runSm('sm_ex_wape_outlets', ctx, sel, range),
        runSm('sm_fc_by_cat', ctx, sel, range),
        runSm('sm_fc_next', ctx, sel),
      ])
    : [null, null, null, null, null];

  return (
    <>
      <PageHeader
        title="Forecast"
        question="Seberapa akurat prakiraan penjualan, dan berapa kebutuhan 14 hari ke depan?"
        actions={base ? <SmFreshnessBadge marts={[MARTS.forecastAccuracy]} freshness={base.freshness} t={base.t} now={now} /> : undefined}
      />
      <FilterNotice adjusted={adjusted} />

      {mode !== 'only-smd' ? (
        <div>
          <p className="muted small" role="note">
            Forecast brand F&amp;B (ESB) belum tersedia dan sedang disiapkan (G44): prakiraan penjualan dan kebutuhan produksi per cabang dan produk.
          </p>
          {mode === 'mixed' ? <MixedScopeNote /> : null}
        </div>
      ) : null}

      {!smdOn && sel.allowed ? (
        <p className="muted small">Pilih SMD pada filter Brand / PT untuk melihat forecast supermarket.</p>
      ) : null}
      {!smdOn && !sel.allowed ? (
        <Link className="btn btn-primary" href="/">
          Kembali ke Ringkasan
        </Link>
      ) : null}

      {smdOn && base ? (
        <>
          <p className="muted small">
            Forecast supermarket adalah usulan; kepala toko menyetujui qty. Angka dalam satuan produk (qty), bukan Rupiah. Pembanding aktual: 8 minggu terakhir.
          </p>
          {(() => {
            const wRows = weekly && weekly.ok ? weekly.data : [];
            const tot = wRows.reduce<{ abs: number; act: number; fc: number }>((a, r) => ({ abs: a.abs + toNum(r.abs_sum), act: a.act + toNum(r.actual_sum), fc: a.fc + toNum(r.fc_sum) }), { abs: 0, act: 0, fc: 0 });
            const wb = wapeBias(tot.abs, tot.act, tot.fc);
            const nRows = next && next.ok ? next.data : [];
            const nextTotal = nRows.reduce((s, r) => s + toNum(r.yhat_sum), 0);
            const off = martOff(base.freshness.get(MARTS.forecastAccuracy));
            const t = base.t;
            return (
              <KpiRow>
                <SmKpiTile
                  label="Galat forecast (WAPE), 8 minggu"
                  value={weekly && !weekly.ok ? 'Tidak dapat dimuat' : off ? 'Forecast belum aktif' : wb.wape !== null ? fmtPct(wb.wape) : 'Belum ada data'}
                  muted={wb.wape === null}
                  status={wb.wape !== null ? wapeStatus(wb.wape, t) : undefined}
                  info="Jumlah selisih mutlak aktual vs forecast dibagi jumlah aktual, per SKU x outlet x minggu. Akurasi kira-kira 100% dikurangi galat."
                />
                <SmKpiTile
                  label="Bias forecast"
                  value={wb.bias === null ? 'Belum ada data' : fmtBias(wb.bias)}
                  muted={wb.bias === null}
                  status={wb.bias !== null ? biasStatus(wb.bias, t) : undefined}
                  info="(Jumlah forecast - jumlah aktual) / jumlah aktual. Positif = forecast terlalu tinggi (risiko kelebihan stok)."
                />
                <SmKpiTile
                  label={`Total forecast ${FORECAST_DAYS} hari`}
                  value={next && !next.ok ? 'Tidak dapat dimuat' : nRows.length === 0 ? 'Belum ada forecast' : `${fmtQty(Math.round(nextTotal))} unit`}
                  muted={nRows.length === 0}
                  note="Jumlah qty semua SKU; satuan campuran"
                />
              </KpiRow>
            );
          })()}

          <Section title="Forecast vs aktual per minggu" id="weekly">
            <Block result={weekly!} title="Forecast vs aktual per minggu">
              {(rows) => {
                if (rows.length === 0) return <EmptyState title="Belum ada hasil evaluasi forecast" reason="Butuh minimal satu minggu data aktual setelah forecast berjalan." icon="info" />;
                const cats8 = rows.map((r) => fmtDayShort(String(r.week_start)));
                const act = rows.map((r) => toNum(r.actual_sum));
                const fc = rows.map((r) => toNum(r.fc_sum));
                const wp = rows.map((r) => wapeBias(toNum(r.abs_sum), toNum(r.actual_sum), toNum(r.fc_sum)).wape);
                const specs: ChartSpec[] = [
                  {
                    id: 'fc-act',
                    tabLabel: 'Aktual vs forecast',
                    kind: 'line',
                    fmt: 'num',
                    categories: cats8,
                    series: [
                      { name: 'Aktual', data: act, color: 'c1' },
                      { name: 'Forecast', data: fc, color: 'c2', dashed: true },
                    ],
                    summary: summarize('Aktual per minggu', cats8, act, 'num', true),
                  },
                  {
                    id: 'fc-wape',
                    tabLabel: 'WAPE',
                    kind: 'line',
                    fmt: 'pct',
                    categories: cats8,
                    series: [{ name: 'Galat forecast (WAPE)', data: wp, color: 'c1' }],
                    refLines: [{ value: base.t.wapeWarn, label: `Batas perlu dicek ${base.t.wapeWarn}%` }],
                    summary: summarize('Galat forecast mingguan', cats8, wp, 'pct', false),
                  },
                ];
                return <ChartCard title="Forecast vs aktual (8 minggu, qty)" subtitle="Garis putus = forecast; tab WAPE menunjukkan galat per minggu" specs={specs} />;
              }}
            </Block>
          </Section>

          <div className="grid-2">
            <Block result={outlets!} title="WAPE dan bias per outlet">
              {(rows) =>
                rows.length === 0 ? (
                  <EmptyState title="Belum ada hasil evaluasi per outlet" icon="info" />
                ) : (
                  <SmTable
                    caption="WAPE dan bias forecast per outlet (8 minggu)"
                    rows={[...rows].sort((a, b) => (wapeBias(toNum(b.abs_sum), toNum(b.actual_sum), toNum(b.fc_sum)).wape ?? -1) - (wapeBias(toNum(a.abs_sum), toNum(a.actual_sum), toNum(a.fc_sum)).wape ?? -1))}
                    rowKey={(r) => String(toNum(r.warehouse_id))}
                    columns={[
                      { header: 'Outlet', cell: (r) => base.whNames.get(toNum(r.warehouse_id)) ?? `Outlet #${toNum(r.warehouse_id)}` },
                      { header: 'WAPE', align: 'right', cell: (r) => fmtPctOrNa(wapeBias(toNum(r.abs_sum), toNum(r.actual_sum), toNum(r.fc_sum)).wape) },
                      { header: 'Bias', align: 'right', cell: (r) => fmtBias(wapeBias(toNum(r.abs_sum), toNum(r.actual_sum), toNum(r.fc_sum)).bias) },
                      { header: 'Status WAPE', cell: (r) => <StatusPill status={wapeStatus(wapeBias(toNum(r.abs_sum), toNum(r.actual_sum), toNum(r.fc_sum)).wape, base.t)} /> },
                      { header: 'Keterangan', cell: (r) => wapeNote(r, base.t), text: (r) => wapeNote(r, base.t) },
                      { header: 'SKU dievaluasi', align: 'right', cell: (r) => fmtQty(toNum(r.sku_n)) },
                    ]}
                  />
                )
              }
            </Block>
            <Block result={cats!} title="WAPE dan bias per kategori">
              {(rows) =>
                rows.length === 0 ? (
                  <EmptyState title="Belum ada hasil evaluasi per kategori" icon="info" />
                ) : (
                  <SmTable
                    caption="WAPE dan bias forecast per kategori (8 minggu)"
                    rows={[...rows].sort((a, b) => (wapeBias(toNum(b.abs_sum), toNum(b.actual_sum), toNum(b.fc_sum)).wape ?? -1) - (wapeBias(toNum(a.abs_sum), toNum(a.actual_sum), toNum(a.fc_sum)).wape ?? -1))}
                    rowKey={(r) => String(toNum(r.categ_id))}
                    columns={[
                      { header: 'Kategori', cell: (r) => base.catNames.get(toNum(r.categ_id)) ?? `Kategori #${toNum(r.categ_id)}` },
                      { header: 'WAPE', align: 'right', cell: (r) => fmtPctOrNa(wapeBias(toNum(r.abs_sum), toNum(r.actual_sum), toNum(r.fc_sum)).wape) },
                      { header: 'Bias', align: 'right', cell: (r) => fmtBias(wapeBias(toNum(r.abs_sum), toNum(r.actual_sum), toNum(r.fc_sum)).bias) },
                      { header: 'Status WAPE', cell: (r) => <StatusPill status={wapeStatus(wapeBias(toNum(r.abs_sum), toNum(r.actual_sum), toNum(r.fc_sum)).wape, base.t)} /> },
                      { header: 'Keterangan', cell: (r) => wapeNote(r, base.t), text: (r) => wapeNote(r, base.t) },
                      { header: 'SKU dievaluasi', align: 'right', cell: (r) => fmtQty(toNum(r.sku_n)) },
                    ]}
                  />
                )
              }
            </Block>
          </div>

          <Section title={`Forecast ${FORECAST_DAYS} hari ke depan`} id="next">
            <Block result={next!} title="Forecast 14 hari">
              {(rows) => {
                if (rows.length === 0) return <EmptyState title="Belum ada forecast" reason="Belum ada run forecast berstatus OK untuk PT ini." icon="info" />;
                const cats14 = rows.map((r) => fmtDayShort(String(r.forecast_date)));
                const y = rows.map((r) => toNum(r.yhat_sum));
                const lo = rows.map((r) => toNum(r.p10_sum));
                const hi = rows.map((r) => toNum(r.p90_sum));
                const gens = rows.map((r) => parseChDateTime(r.gen_at)).filter((d): d is Date => d !== null);
                const gen = gens.length > 0 ? new Date(Math.max(...gens.map((d) => d.getTime()))) : null;
                const spec: ChartSpec = {
                  id: 'fc-next',
                  kind: 'line',
                  fmt: 'num',
                  categories: cats14,
                  series: [
                    { name: 'Forecast (p50)', data: y, color: 'c1' },
                    { name: 'Batas bawah (p10)', data: lo, color: 'muted', dashed: true },
                    { name: 'Batas atas (p90)', data: hi, color: 'c2', dashed: true },
                  ],
                  summary: summarize('Forecast harian', cats14, y, 'num', true),
                };
                return (
                  <>
                    <p className="muted small">
                      Pita p10-p90 = jumlah batas per SKU, jadi lebih lebar dari rentang ketidakpastian total sebenarnya.
                      {gen ? ` Forecast dibuat ${fmtStampWib(gen, now)}.` : ''}
                    </p>
                    <ChartCard title="Forecast harian semua SKU (qty)" subtitle="Garis tegas = forecast; garis putus = batas p10 dan p90" specs={[spec]} />
                    <SmTable
                      caption="Forecast harian dengan batas p10 dan p90"
                      rows={rows}
                      rowKey={(r) => String(r.forecast_date)}
                      columns={[
                        { header: 'Tanggal', cell: (r) => fmtDayShort(String(r.forecast_date)) },
                        { header: 'Forecast', align: 'right', cell: (r) => fmtQty(Math.round(toNum(r.yhat_sum))) },
                        { header: 'Batas bawah (p10)', align: 'right', cell: (r) => fmtQty(Math.round(toNum(r.p10_sum))) },
                        { header: 'Batas atas (p90)', align: 'right', cell: (r) => fmtQty(Math.round(toNum(r.p90_sum))) },
                      ]}
                    />
                  </>
                );
              }}
            </Block>
            <div className="card-tools">
              <ActionLink label="Buka usulan min/max forecast di Odoo" href={odooListUrl(getConfig().odooOrigin, 'efn.demand.forecast')} />
            </div>
          </Section>
        </>
      ) : null}
    </>
  );
}
