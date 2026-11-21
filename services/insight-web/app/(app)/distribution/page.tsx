import type { Metadata } from 'next';
import { Block, ErrorCard } from '@/components/blocks';
import { ChartCard } from '@/components/ChartCard';
import { CkMovementCard, type GroupRow } from '@/components/CkMovementCard';
import { CoverageNotice } from '@/components/CoverageNotice';
import { DataTable, type TableRow } from '@/components/DataTable';
import { FilterNotice } from '@/components/FilterNotice';
import { MixedScopeNote, NotApplicableSmd } from '@/components/SmdNotice';
import { SmFreshnessBadge } from '@/components/sm/SmFreshnessBadge';
import { SmKpiTile } from '@/components/sm/SmCard';
import { SmTable } from '@/components/sm/SmTable';
import { EmptyState, KpiRow, KpiTile, PageHeader, Section, StatusPill } from '@/components/ui';
import { summarize, type ChartSpec } from '@/lib/chart-spec';
import { fmtDayShort, fmtInt, fmtPct, fmtQty, fmtRange } from '@/lib/format';
import { comparable, deltaGuards } from '@/lib/coverage';
import { loadCoverage, num } from '@/lib/loaders';
import { pageCtx, type SearchParams } from '@/lib/page-ctx';
import { loadEsbTable, loadSmdProductNames, runEsb, runSm } from '@/lib/smd-data';
import { esbOnlyMode, smdActive, smdExcludedNote } from '@/lib/smd-merge';
import { MARTS } from '@/lib/sm-freshness';
import { fmtPctOrNa } from '@/lib/sm-format';
import { fillRate, fillStatus, pct, ppChange, proposalStateLabel } from '@/lib/sm-kpi';
import { loadSmBase } from '@/lib/sm-data';
import { toNum, type ProposalState } from '@/lib/sm-parse';
import { cfgStr } from '@/lib/sm-thresholds';
import { TABLES } from '@/lib/table-defs';
import { fillReason } from '@/lib/status-reasons';
import { gapStatus } from '@/lib/thresholds';
import { pctOf } from '@/lib/vm';

export const dynamic = 'force-dynamic';
export const metadata: Metadata = { title: 'Distribusi' };

interface Kpi {
  ck_out_cur: number; ck_out_prev: number; in_ck_cur: number; in_ck_prev: number;
  st_out_cur: number; st_out_prev: number; in_other_cur: number; in_other_prev: number;
}
const PROPOSAL_STATES: readonly string[] = ['draft', 'proposed', 'approved', 'rejected', 'expired'];
const proposalLabel = (s: unknown): string => proposalStateLabel((typeof s === 'string' && PROPOSAL_STATES.includes(s) ? s : 'unknown') as ProposalState);

export default async function DistributionPage({ searchParams }: { searchParams: SearchParams }) {
  const { ctx, adjusted, sel } = await pageCtx(searchParams);
  const { from, to } = ctx.filters;
  const esbOn = sel.esb;
  // Filter "Produk FIN" = produk ESB; tidak punya padanan di SMD -> SMD tidak ikut bila aktif.
  const smdOn = smdActive(sel, ctx.filters, 'product');
  const mode = esbOnlyMode({ ...sel, smd: smdOn });
  const excluded = smdExcludedNote(sel, ctx.filters, 'product');
  const sm = (n: Parameters<typeof runSm>[0]) => (smdOn ? runSm(n, ctx, sel) : Promise.resolve(null));

  const [kpiR, delR, matR, topR, gapR, base, fillSums, fillDaily, fillOutlets, lateOutlets, shortProducts, proposals, reduced, cov, groupR, catR] = await Promise.all([
    runEsb<Kpi>(esbOn, 'dist_kpi', ctx),
    runEsb<{ day: string; ck_out_qty: number; in_ck_qty: number; in_other_qty: number }>(esbOn, 'dist_ck_delivery', ctx),
    loadEsbTable(esbOn, 'dist-matrix', ctx),
    runEsb<{ product_label: string; brand: string; product_code: string; moved: number }>(esbOn, 'dist_transfer_top_products', ctx),
    loadEsbTable(esbOn, 'dist-gap', ctx),
    smdOn ? loadSmBase(ctx.session, new Date()) : Promise.resolve(null),
    sm('sm_dc_fill_sums'),
    sm('sm_dc_fill_daily'),
    sm('sm_dc_fill_outlets'),
    sm('sm_dist_late_outlets'),
    sm('sm_dist_short_products'),
    sm('sm_dist_proposals'),
    sm('sm_dist_reduced_products'),
    esbOn ? loadCoverage(ctx) : Promise.resolve(null),
    runEsb<GroupRow>(esbOn, 'mov_by_group', ctx),
    loadEsbTable(esbOn, 'mov-category', ctx),
  ]);
  // Perbandingan periode lalu hanya bermakna bila kedua periode ada di data pengiriman dan transfer.
  const { pctDelta } = deltaGuards(comparable(cov, ['delivery', 'transfer'], ctx.filters, ctx.prev));
  const prodIds = [
    ...(shortProducts && shortProducts.ok ? shortProducts.data : []),
    ...(reduced && reduced.ok ? reduced.data : []),
  ].map((r) => toNum(r.product_id));
  const prodNames = smdOn ? await loadSmdProductNames(ctx, sel, prodIds) : new Map<number, string>();

  return (
    <>
      <PageHeader
        title="Distribusi"
        question="Barang bergerak dari CK ke toko dan antar toko seperti seharusnya?"
        actions={base ? <SmFreshnessBadge marts={[MARTS.dcFillRate, MARTS.replenishment]} freshness={base.freshness} t={base.t} now={new Date()} /> : undefined}
      />
      <FilterNotice adjusted={adjusted} />
      {excluded ? <p className="notice" role="status">{excluded}</p> : null}

      {mode === 'only-smd' ? (
        <NotApplicableSmd title="Distribusi CK ke toko (F&B)" reason="Pengiriman CK dan transfer antar toko memakai produk FIN brand F&B. Untuk SMD lihat bagian Distribution Center di bawah." />
      ) : (
        <>
          {mode === 'mixed' ? <MixedScopeNote /> : null}
          <CoverageNotice rows={cov} entities={['delivery', 'transfer']} range={{ from, to }} />
          <p className="muted small">
            Periode: {fmtRange(from, to)}. <strong>Hanya produk jadi (FIN)</strong>; bahan baku, WIP, kemasan, dan supplies ada di kartu &ldquo;Kiriman CK per
            kelompok barang&rdquo; di bawah. Pengiriman dicatat di cabang CK dan penerimaan di toko tujuan; selisih bermakna per produk dan hari, bukan per
            cabang. Transfer ke/dari HEAD OFFICE, gudang, dan cabang damage/expired digabung sebagai &ldquo;Non-toko&rdquo; (bukan toko). Peta asal-tujuan belum tersedia.
          </p>

          <Section title="Indikator utama" id="kpi">
            <Block result={kpiR} title="Indikator utama">
              {(rows) => {
                const k = rows[0];
                if (!k) return <p>Tidak ada data distribusi pada periode ini.</p>;
                const gap = num(k.ck_out_cur) - num(k.in_ck_cur);
                const gapPct = pctOf(Math.abs(gap), num(k.ck_out_cur));
                return (
                  <KpiRow>
                    <KpiTile label="Dikirim CK (hanya FIN)" value={num(k.ck_out_cur)} fmt="num" info="Hanya produk jadi (FIN). Bahan baku, WIP, kemasan, dan supplies yang juga dikirim CK tidak termasuk; lihat kartu Kiriman CK per kelompok barang." delta={{ value: pctDelta(num(k.ck_out_cur), num(k.ck_out_prev)), kind: 'pct', goodWhen: 'neutral' }} />
                    <KpiTile label="Diterima dari CK (hanya FIN)" value={num(k.in_ck_cur)} fmt="num" delta={{ value: pctDelta(num(k.in_ck_cur), num(k.in_ck_prev)), kind: 'pct', goodWhen: 'neutral' }} />
                    <KpiTile
                      label="Selisih kirim vs terima"
                      value={gap}
                      fmt="num"
                      status={gapStatus(gapPct)}
                      note="Selisih lintas hari bisa karena lead time"
                      info="Dikirim CK dikurangi diterima dari CK. Bila CK dan toko beda PT, pengguna satu PT hanya melihat satu sisi sehingga selisih tampak besar. Ambang 10% dan 20% adalah proposal."
                    />
                    <KpiTile label="Transfer antar toko" value={num(k.st_out_cur)} fmt="num" delta={{ value: pctDelta(num(k.st_out_cur), num(k.st_out_prev)), kind: 'pct', goodWhen: 'neutral' }} />
                    <KpiTile label="Diterima dari non-CK" value={num(k.in_other_cur)} fmt="num" delta={{ value: pctDelta(num(k.in_other_cur), num(k.in_other_prev)), kind: 'pct', goodWhen: 'neutral' }} />
                  </KpiRow>
                );
              }}
            </Block>
          </Section>

          <div className="grid-2">
            <div className="wide">
              <Block result={delR} title="Pengiriman CK per hari">
                {(rows) => {
                  const cats = rows.map((r) => fmtDayShort(String(r.day)));
                  const out = rows.map((r) => num(r.ck_out_qty));
                  return (
                    <ChartCard
                      title="Pengiriman CK per hari"
                      subtitle="Batang = dikirim CK; garis = diterima dari CK"
                      wide
                      specs={[
                        {
                          id: 'del',
                          kind: 'bar',
                          fmt: 'num',
                          categories: cats,
                          series: [
                            { name: 'Dikirim CK', data: out, color: 'c1', type: 'bar' },
                            { name: 'Diterima dari CK', data: rows.map((r) => num(r.in_ck_qty)), color: 'c3', type: 'line' },
                          ],
                          summary: summarize('Pengiriman CK harian', cats, out, 'num', true),
                        } satisfies ChartSpec,
                      ]}
                    />
                  );
                }}
              </Block>
            </div>
            <Block result={matR} title="Neto transfer per cabang">
              {(rows) => {
                const used = [...rows].sort((a, b) => Math.abs(num(b.st_net)) - Math.abs(num(a.st_net))).slice(0, 15);
                const cats = used.map((r) => String(r.branch_label));
                const vals = used.map((r) => num(r.st_net));
                return (
                  <ChartCard
                    title="Neto transfer per cabang"
                    subtitle="Transfer masuk dikurangi keluar; 15 terbesar. Negatif = lebih banyak keluar"
                    height={Math.max(280, used.length * 26 + 40)}
                    specs={[
                      {
                        id: 'net',
                        kind: 'barH',
                        fmt: 'num',
                        categories: cats,
                        series: [{ name: 'Neto transfer', data: vals, color: 'c1', itemColors: vals.map((v) => (v < 0 ? 'c5' : 'c1')) }],
                        // Baris gabungan non-toko (branch_key null) tidak bisa dijadikan filter cabang.
                        drill: { param: 'branch', values: used.map((r) => (typeof r.branch_key === 'string' ? r.branch_key : '')), label: 'cabang' },
                        summary: summarize('Neto transfer per cabang', cats, vals, 'num', false),
                      },
                    ]}
                  />
                );
              }}
            </Block>
            <Block result={topR} title="Produk paling banyak ditransfer">
              {(rows) => {
                const cats = rows.map((r) => r.product_label);
                const vals = rows.map((r) => num(r.moved));
                return (
                  <ChartCard
                    title="Produk paling banyak ditransfer"
                    subtitle="Indikasi salah alokasi (10 teratas)"
                    specs={[{ id: 'top', kind: 'barH', fmt: 'num', categories: cats, series: [{ name: 'Masuk + keluar', data: vals, color: 'c2' }], drill: { param: 'product', values: rows.map((r) => `${r.brand}:${r.product_code}`), label: 'produk' }, summary: summarize('Produk paling banyak ditransfer', cats, vals, 'num', false) }]}
                  />
                );
              }}
            </Block>
          </div>

          <Section title="Matriks cabang" id="matrix">
            <Block result={matR} title="Matriks cabang">
              {(rows) => (
                <DataTable
                  caption={TABLES['dist-matrix'].caption}
                  columns={TABLES['dist-matrix'].columns}
                  rows={rows as TableRow[]}
                  csvName="dist-matrix"
                  defaultSort={{ key: 'st_net', dir: 'asc' }}
                />
              )}
            </Block>
          </Section>

          <Section title="Selisih kirim vs terima" id="gap">
            <Block result={gapR} title="Selisih kirim vs terima">
              {(rows) => (
                <DataTable
                  caption={TABLES['dist-gap'].caption}
                  columns={TABLES['dist-gap'].columns}
                  rows={rows as TableRow[]}
                  csvName="dist-gap"
                  quickFilter={{ label: 'Hanya selisih besar', statuses: ['bad', 'warn'] }}
                  emptyTitle="Tidak ada selisih pada periode ini."
                />
              )}
            </Block>
          </Section>

          <CkMovementCard groupR={groupR} catR={catR} />
        </>
      )}

      {smdOn && base ? (
        <Section title="Supermarket (SMD): Distribution Center ke outlet" id="smd">
          <p className="muted small">
            Periode: {fmtRange(from, to)}. Fill rate = qty yang benar-benar dikirim/diterima dibanding qty usulan yang disetujui (basis dan tingkat
            mengikuti konfigurasi PT). Angka qty tidak dijumlahkan dengan F&amp;B karena satuannya berbeda.
          </p>
          {(() => {
            const failed = [fillSums, fillDaily, fillOutlets, lateOutlets, shortProducts, proposals, reduced].find((r) => r !== null && !r.ok);
            const level = cfgStr(base.config, 'fill.level') === 'line' ? 'line' : 'qty';
            const t = base.t;
            const fs = fillSums && fillSums.ok ? fillSums.data[0] : undefined;
            const sums = (sfx: 'cur' | 'prev') => ({
              sumLeast: toNum(fs?.[`lq_${sfx}`]),
              sumApproved: toNum(fs?.[`ap_${sfx}`]),
              linesFull: toNum(fs?.[`full_${sfx}`]),
              lines: toNum(fs?.[`lines_${sfx}`]),
            });
            const fillCur = fs ? fillRate(sums('cur'), level) : null;
            const fillPrev = fs ? fillRate(sums('prev'), level) : null;
            const fillAlt = fs ? fillRate(sums('cur'), level === 'qty' ? 'line' : 'qty') : null;
            const lateRows = lateOutlets && lateOutlets.ok ? lateOutlets.data : [];
            const lateN = lateRows.reduce((s, r) => s + toNum(r.late_n), 0);
            const delivN = lateRows.reduce((s, r) => s + toNum(r.deliveries_n), 0);
            const latePct = pct(lateN, delivN);
            const outRows = (fillOutlets && fillOutlets.ok ? fillOutlets.data : []).map((r) => {
              const id = toNum(r.warehouse_id);
              const s = { sumLeast: toNum(r.lq_sum), sumApproved: toNum(r.ap_sum), linesFull: toNum(r.full_n), lines: toNum(r.lines_n) };
              const lr = lateRows.find((x) => toNum(x.warehouse_id) === id);
              return {
                id,
                name: base.whNames.get(id) ?? `Outlet #${id}`,
                qty: fillRate(s, 'qty'),
                line: fillRate(s, 'line'),
                main: fillRate(s, level),
                deliveries: toNum(lr?.deliveries_n),
                late: toNum(lr?.late_n),
              };
            }).sort((a, b) => (a.main ?? 101) - (b.main ?? 101));
            const daily = fillDaily && fillDaily.ok ? fillDaily.data : [];
            const dailyPct = daily.map((r) => pct(toNum(r.lq_sum), toNum(r.ap_sum)));
            const dailySpec: ChartSpec = {
              id: 'smd-fill',
              kind: 'line',
              fmt: 'pct',
              categories: daily.map((r) => fmtDayShort(String(r.delivery_date))),
              series: [{ name: 'Fill rate (qty)', data: dailyPct, color: 'c1' }],
              refLines: [
                { value: t.fillGood, label: `${t.fillGood}%` },
                { value: t.fillWarn, label: `${t.fillWarn}%` },
              ],
              summary: summarize('Fill rate harian SMD', daily.map((r) => String(r.delivery_date)), dailyPct, 'pct', false, true),
            };
            const outSpec: ChartSpec = {
              id: 'smd-fill-out',
              kind: 'barH',
              fmt: 'pct',
              categories: outRows.map((r) => r.name),
              series: [
                {
                  name: 'Fill rate',
                  data: outRows.map((r) => r.main),
                  color: 'c1',
                  itemColors: outRows.map((r) => {
                    const s = fillStatus(r.main, t);
                    return s === 'good' ? 'good' : s === 'warn' ? 'warn' : s === 'bad' ? 'bad' : 'muted';
                  }),
                },
              ],
              summary: summarize('Fill rate per outlet', outRows.map((r) => r.name), outRows.map((r) => r.main), 'pct', false),
            };
            const approvedN = (proposals && proposals.ok ? proposals.data : []).find((r) => r.state === 'approved');
            if (failed && !failed.ok) return <ErrorCard title="Distribusi SMD" refId={failed.ref} reason={failed.reason} />;
            if (!fs || sums('cur').sumApproved === 0) {
              return <EmptyState title="Belum ada pengiriman DC ke outlet pada periode ini" reason="Data muncul setelah usulan replenishment disetujui dan dikirim." icon="info" />;
            }
            return (
              <>
                <KpiRow>
                  <SmKpiTile
                    label={`Fill rate DC ke outlet (${level === 'qty' ? 'qty' : 'baris'})`}
                    value={fmtPctOrNa(fillCur)}
                    status={fillStatus(fillCur, t)}
                    delta={{ value: ppChange(fillCur, fillPrev), kind: 'pp', goodWhen: 'up' }}
                    note={`${level === 'qty' ? 'Baris terpenuhi penuh' : 'Qty'}: ${fmtPctOrNa(fillAlt)}`}
                  />
                  <SmKpiTile
                    label="Pengiriman terlambat"
                    value={latePct === null ? 'n/a' : fmtPct(latePct)}
                    note={`${fmtInt(lateN)} dari ${fmtInt(delivN)} pengiriman`}
                    muted={latePct === null}
                  />
                  <SmKpiTile
                    label="Usulan disetujui"
                    value={approvedN ? `${fmtInt(toNum(approvedN.proposals_n))} usulan` : '0 usulan'}
                    note={approvedN ? `${fmtInt(toNum(approvedN.changed_n))} baris diubah, ${fmtInt(toNum(approvedN.excluded_n))} dikecualikan` : undefined}
                  />
                </KpiRow>

                <div className="grid-2">
                  <ChartCard title="Fill rate DC per hari kirim" specs={[dailySpec]} />
                  <ChartCard title="Fill rate per outlet" subtitle="Terendah di atas" height={Math.max(220, outRows.length * 30 + 60)} specs={[outSpec]} />
                </div>

                <SmTable
                  caption="Fill rate dan keterlambatan per outlet SMD"
                  rows={outRows}
                  rowKey={(r) => String(r.id)}
                  columns={[
                    { header: 'Outlet', cell: (r) => r.name },
                    { header: 'Fill rate qty', align: 'right', cell: (r) => fmtPctOrNa(r.qty) },
                    { header: 'Fill rate baris', align: 'right', cell: (r) => fmtPctOrNa(r.line) },
                    { header: 'Pengiriman', align: 'right', cell: (r) => fmtInt(r.deliveries) },
                    { header: 'Terlambat', align: 'right', cell: (r) => fmtInt(r.late) },
                    { header: 'Status', cell: (r) => <StatusPill status={fillStatus(r.main, t)} /> },
                    {
                      header: 'Keterangan',
                      cell: (r) => fillReason(r.main, fillStatus(r.main, t), t) + (r.late > 0 ? ` ${fmtInt(r.late)} dari ${fmtInt(r.deliveries)} pengiriman terlambat.` : ''),
                    },
                  ]}
                />

                {shortProducts && shortProducts.ok && shortProducts.data.length > 0 ? (
                  <SmTable
                    caption="10 produk paling sering kurang kirim"
                    rows={shortProducts.data}
                    rowKey={(r) => String(toNum(r.product_id))}
                    columns={[
                      { header: 'Produk', cell: (r) => prodNames.get(toNum(r.product_id)) ?? `Produk #${toNum(r.product_id)}` },
                      { header: 'Qty disetujui', align: 'right', cell: (r) => fmtQty(toNum(r.approved_sum)) },
                      { header: 'Kurang kirim', align: 'right', cell: (r) => fmtQty(toNum(r.short_sum)) },
                      { header: 'Persen kurang', align: 'right', cell: (r) => fmtPctOrNa(pct(toNum(r.short_sum), toNum(r.approved_sum))) },
                      { header: 'Baris kurang', align: 'right', cell: (r) => fmtInt(toNum(r.short_lines)) },
                    ]}
                  />
                ) : (
                  <p className="muted small">Tidak ada produk kurang kirim pada periode ini.</p>
                )}

                {proposals && proposals.ok && proposals.data.length > 0 ? (
                  <SmTable
                    caption="Status usulan replenishment menurut tanggal kirim"
                    rows={proposals.data}
                    rowKey={(r) => String(r.state)}
                    columns={[
                      { header: 'Status usulan', cell: (r) => proposalLabel(r.state) },
                      { header: 'Jumlah usulan', align: 'right', cell: (r) => fmtInt(toNum(r.proposals_n)) },
                      { header: 'Qty diusulkan', align: 'right', cell: (r) => fmtQty(toNum(r.proposed_sum)) },
                      { header: 'Qty disetujui', align: 'right', cell: (r) => fmtQty(toNum(r.approved_sum)) },
                      { header: 'Baris dikecualikan', align: 'right', cell: (r) => fmtInt(toNum(r.excluded_n)) },
                      { header: 'Baris diubah', align: 'right', cell: (r) => fmtInt(toNum(r.changed_n)) },
                    ]}
                  />
                ) : null}

                {reduced && reduced.ok && reduced.data.length > 0 ? (
                  <SmTable
                    caption="Produk yang paling sering dikecualikan atau dikurangi dari usulan"
                    rows={reduced.data}
                    rowKey={(r) => String(toNum(r.product_id))}
                    columns={[
                      { header: 'Produk', cell: (r) => prodNames.get(toNum(r.product_id)) ?? `Produk #${toNum(r.product_id)}` },
                      { header: 'Dikecualikan (baris)', align: 'right', cell: (r) => fmtInt(toNum(r.excluded_n)) },
                      { header: 'Qty diusulkan', align: 'right', cell: (r) => fmtQty(toNum(r.proposed_sum)) },
                      { header: 'Qty disetujui', align: 'right', cell: (r) => fmtQty(toNum(r.approved_sum)) },
                    ]}
                  />
                ) : null}
              </>
            );
          })()}
        </Section>
      ) : null}
    </>
  );
}
