import type { Metadata } from 'next';
import { ChartCard } from '@/components/ChartCard';
import { ActionLink, SmCardAuto, SmKpiTile } from '@/components/sm/SmCard';
import { SmFilterForm } from '@/components/sm/SmFilterForm';
import { SmFreshnessBadge } from '@/components/sm/SmFreshnessBadge';
import { SmTable } from '@/components/sm/SmTable';
import { KpiRow, PageHeader, StatusPill } from '@/components/ui';
import { summarize, type ChartSpec } from '@/lib/chart-spec';
import { fmtDate, fmtDayShort, fmtIdr, fmtIdrShort, fmtInt, fmtPct } from '@/lib/format';
import { odooRecordUrl } from '@/lib/odoo-links';
import { loadDc } from '@/lib/sm-data';
import { MARTS } from '@/lib/sm-freshness';
import { fmtClockWib, fmtDeliveryDate, fmtPctOrNa } from '@/lib/sm-format';
import { cutoffInfo, fillRate, fillStatus, pct, poLateStatus, ppChange } from '@/lib/sm-kpi';
import { smPage, type SmSearchParams } from '@/lib/sm-page';
import { cfgStr } from '@/lib/sm-thresholds';
import { toNum } from '@/lib/sm-parse';
import { nextDeliveryGroup } from '@/lib/sm-vm';

export const dynamic = 'force-dynamic';
export const metadata: Metadata = { title: 'Distribution Center' };

const ERR = 'Tidak dapat dimuat';

const PO_STATE: Record<string, string> = { draft: 'Draf RFQ', sent: 'RFQ terkirim', 'to approve': 'Menunggu persetujuan', purchase: 'Dikonfirmasi' };

export default async function DcPage({ searchParams }: { searchParams: SmSearchParams }) {
  const { ctx, adjusted, chip } = await smPage('dc', searchParams, { defaultDays: 30 });
  const data = await loadDc(ctx);
  const { base, now, filters, odooOrigin: origin } = ctx;
  const t = base.t;
  const level = cfgStr(base.config, 'fill.level') === 'line' ? 'line' : 'qty';
  const outlets = base.warehouses.filter((w) => w.type !== 'dc' && ctx.session.wids.includes(w.id));

  const fs = data.fill.ok ? data.fill.data : undefined;
  const sums = (suffix: 'cur' | 'prev') => ({
    sumLeast: toNum(fs?.[`lq_${suffix}`]),
    sumApproved: toNum(fs?.[`ap_${suffix}`]),
    linesFull: toNum(fs?.[`full_${suffix}`]),
    lines: toNum(fs?.[`lines_${suffix}`]),
  });
  const fillCur = fs ? fillRate(sums('cur'), level) : null;
  const fillPrev = fs ? fillRate(sums('prev'), level) : null;
  const secondary = fs ? fillRate(sums('cur'), level === 'qty' ? 'line' : 'qty') : null;

  const next = data.orders.ok ? nextDeliveryGroup(data.orders.data, ctx.today) : { date: null, rows: [] };
  const approved = next.rows.filter((r) => r.state === 'approved').length;

  const age = data.stockAge.ok ? data.stockAge.data : undefined;
  const ageTotal = toNum(age?.total_sum);
  const bucket = t.stockAgeDays < 45 ? 30 : t.stockAgeDays < 75 ? 60 : 90;
  const old = age ? (bucket === 30 ? toNum(age.a31_sum) + toNum(age.a61_sum) + toNum(age.a91_sum) : bucket === 60 ? toNum(age.a61_sum) + toNum(age.a91_sum) : toNum(age.a91_sum)) : 0;

  const lateN = data.poTotals.ok && data.poTotals.data ? toNum(data.poTotals.data.late_n) : null;

  const daily = data.fillDaily.ok ? data.fillDaily.data : [];
  const dailyPct = daily.map((r) => pct(toNum(r.lq_sum), toNum(r.ap_sum)));
  const fillSpec: ChartSpec = {
    id: 'fill',
    kind: 'line',
    fmt: 'pct',
    categories: daily.map((r) => fmtDayShort(String(r.delivery_date))),
    series: [{ name: 'Fill rate (qty)', data: dailyPct, color: 'c1' }],
    refLines: [
      { value: t.fillGood, label: `${t.fillGood}%` },
      { value: t.fillWarn, label: `${t.fillWarn}%` },
    ],
    summary: summarize('Fill rate harian', daily.map((r) => String(r.delivery_date)), dailyPct, 'pct', false, true),
  };
  const outRows = (data.fillOutlets.ok ? data.fillOutlets.data : [])
    .map((r) => ({ id: toNum(r.warehouse_id), p: fillRate({ sumLeast: toNum(r.lq_sum), sumApproved: toNum(r.ap_sum), linesFull: toNum(r.full_n), lines: toNum(r.lines_n) }, level) }))
    .sort((a, b) => (a.p ?? 101) - (b.p ?? 101));
  const outSpec: ChartSpec = {
    id: 'fill-out',
    kind: 'barH',
    fmt: 'pct',
    categories: outRows.map((r) => base.whNames.get(r.id) ?? `Outlet #${r.id}`),
    series: [
      {
        name: 'Fill rate',
        data: outRows.map((r) => r.p),
        color: 'c1',
        itemColors: outRows.map((r) => {
          const s = fillStatus(r.p, t);
          return s === 'good' ? 'good' : s === 'warn' ? 'warn' : s === 'bad' ? 'bad' : 'muted';
        }),
      },
    ],
    summary: summarize('Fill rate per outlet', outRows.map((r) => String(r.id)), outRows.map((r) => r.p), 'pct', false),
  };

  return (
    <>
      <PageHeader
        title="Distribution Center"
        question="Apakah pesanan outlet terpenuhi dan stok kita sehat?"
        actions={
          <>
            <span className="persona-chip">{chip}</span>
            <SmFreshnessBadge marts={[MARTS.replenishment, MARTS.dcFillRate, MARTS.stockValue, MARTS.dcPurchase]} freshness={base.freshness} t={t} now={now} />
          </>
        }
      />
      <SmFilterForm
        path="/dc"
        filters={filters}
        yesterday={ctx.yesterday}
        outlets={outlets.map((w) => ({ id: w.id, name: w.name }))}
        categories={[]}
        show={{ period: true, outlets: true, category: false }}
        adjusted={adjusted}
      />

      <KpiRow>
        <SmKpiTile
          label={level === 'qty' ? 'Fill rate (qty)' : 'Fill rate (baris)'}
          value={!data.fill.ok ? ERR : fillCur === null ? 'Belum ada pengiriman DC ke outlet' : fmtPct(fillCur)}
          muted={fillCur === null}
          status={fillCur !== null ? fillStatus(fillCur, t) : undefined}
          delta={fillCur !== null ? { value: ppChange(fillCur, fillPrev), kind: 'pp', goodWhen: 'up' } : undefined}
          note={secondary !== null ? `${level === 'qty' ? 'Baris terpenuhi' : 'Qty terpenuhi'}: ${fmtPct(secondary)}` : undefined}
          info="Qty yang dikirim DC dibagi qty yang disetujui outlet, per baris usulan. Basis (dikirim/diterima) dan tingkat (qty/baris) diatur per PT di Odoo."
        />
        <SmKpiTile
          label="Order outlet"
          value={!data.orders.ok ? ERR : next.date === null ? 'Tidak ada usulan' : `${next.rows.length} usulan`}
          muted={next.date === null}
          note={next.date !== null ? `${fmtDeliveryDate(next.date)}; ${approved} disetujui` : undefined}
        />
        <SmKpiTile
          label={`Stok > ${bucket} hari (Rp, HPP)`}
          value={!data.stockAge.ok ? ERR : age && ageTotal > 0 ? fmtIdrShort(old) : 'Belum ada data'}
          muted={!(age && ageTotal > 0)}
          note={age && ageTotal > 0 ? `${fmtPctOrNa(pct(old, ageTotal))} dari nilai stok DC` : undefined}
        />
        <SmKpiTile
          label="PO terlambat"
          value={lateN === null ? (data.poTotals.ok ? 'Belum ada data' : ERR) : `${lateN} PO`}
          muted={lateN === null}
          status={lateN !== null ? poLateStatus(lateN, t) : undefined}
          info="PO dikonfirmasi yang melewati tanggal terima terjanji dan belum diterima penuh."
        />
      </KpiRow>

      <div className="sm-grid">
        <SmCardAuto
          id="dc2"
          title="Order outlet"
          mart={MARTS.replenishment}
          base={base}
          now={now}
          result={data.orders}
          hasRows={() => next.rows.length > 0}
          zeroMeans="zero"
          texts={{ zero: 'Tidak ada usulan untuk hari kirim ini.', off: 'Usulan order belum diaktifkan.' }}
        >
          {(_all, lvl) => (
            <>
              <p>Pengiriman {next.date ? fmtDeliveryDate(next.date) : '-'}</p>
              <SmTable
                caption="Usulan order outlet untuk hari kirim terdekat"
                rows={next.rows}
                rowKey={(r) => String(r.proposalId)}
                columns={[
                  { header: 'Outlet', cell: (r) => base.whNames.get(r.warehouseId) ?? `Outlet #${r.warehouseId}` },
                  {
                    header: 'Status',
                    cell: (r) => {
                      const i = cutoffInfo(r.state, r.cutoffAt, now, t, lvl === 'bad');
                      return <StatusPill status={i.status} label={i.label} />;
                    },
                  },
                  { header: 'Batas', cell: (r) => (r.cutoffAt ? fmtClockWib(r.cutoffAt) : '-') },
                  { header: 'Baris', align: 'right', cell: (r) => fmtInt(r.lineCount) },
                  { header: 'Berubah', align: 'right', cell: (r) => fmtInt(r.linesChanged) },
                  { header: 'Odoo', cell: (r) => <ActionLink label="Buka usulan di Odoo" href={odooRecordUrl(origin, 'efn.replenishment.proposal', r.proposalId)} /> },
                ]}
              />
            </>
          )}
        </SmCardAuto>

        <SmCardAuto
          id="dc3"
          title="Fill rate"
          mart={MARTS.dcFillRate}
          base={base}
          now={now}
          result={data.fillDaily}
          hasRows={(d) => d.length > 0}
          zeroMeans="no-data"
          texts={{ zero: 'Tidak ada pengiriman.', off: 'Fill rate belum aktif.', noData: 'Belum ada pengiriman DC ke outlet pada periode ini.' }}
        >
          {() => (
            <>
              <p className="muted small">Fill rate dihitung terhadap qty yang disetujui outlet.</p>
              <ChartCard title="Tren harian" specs={[fillSpec]} />
              {outRows.length > 1 ? <ChartCard title="Per outlet" specs={[outSpec]} /> : null}
            </>
          )}
        </SmCardAuto>

        <SmCardAuto
          id="dc4"
          title="Umur stok DC"
          mart={MARTS.stockValue}
          base={base}
          now={now}
          result={data.stockAge}
          hasRows={(d) => d !== undefined && toNum(d.total_sum) > 0}
          zeroMeans="zero"
          texts={{ zero: 'Tidak ada stok DC tercatat.', off: 'Nilai stok belum tersedia.' }}
        >
          {(d) => {
            const parts = d
              ? [
                  { label: '0-30 hari', v: toNum(d.a0_sum), cls: 'var(--seq-1)' },
                  { label: '31-60 hari', v: toNum(d.a31_sum), cls: 'var(--seq-2)' },
                  { label: '61-90 hari', v: toNum(d.a61_sum), cls: 'var(--seq-4)' },
                  { label: '> 90 hari', v: toNum(d.a91_sum), cls: 'var(--seq-5)' },
                ]
              : [];
            return (
              <>
                <div className="stack-bar" role="img" aria-label={`Umur stok DC: ${parts.map((p) => `${p.label} ${fmtPctOrNa(pct(p.v, ageTotal))}`).join(', ')}`}>
                  {parts.map((p) => (
                    <span key={p.label} style={{ width: `${pct(p.v, ageTotal) ?? 0}%`, background: p.cls }} />
                  ))}
                </div>
                <SmTable
                  caption="Nilai stok DC menurut lama di gudang (Rp, HPP)"
                  rows={parts}
                  rowKey={(p) => p.label}
                  columns={[
                    { header: 'Lama di gudang', cell: (p) => p.label },
                    { header: 'Nilai Rp (HPP)', align: 'right', cell: (p) => fmtIdr(p.v) },
                    { header: 'Porsi', align: 'right', cell: (p) => fmtPctOrNa(pct(p.v, ageTotal)) },
                  ]}
                />
                <p className="muted small">Bucket menurut lama di gudang; bucket sisa umur lot tersedia di data tetapi tampilannya menunggu keputusan DC (Q-INS-S10).</p>
              </>
            );
          }}
        </SmCardAuto>

        <SmCardAuto
          id="dc5"
          title="PO supplier"
          mart={MARTS.dcPurchase}
          base={base}
          now={now}
          result={data.po}
          hasRows={(d) => d.length > 0}
          zeroMeans="zero"
          texts={{ zero: 'Tidak ada PO terbuka.', off: 'Data PO belum aktif.' }}
        >
          {(rows) => (
            <SmTable
              caption="PO dan RFQ terbuka, yang terlambat di atas"
              rows={rows}
              rowKey={(r) => String(r.orderId)}
              columns={[
                { header: 'Supplier', cell: (r) => r.supplier },
                { header: 'Status', cell: (r) => (r.late ? <StatusPill status="bad" label="Terlambat" /> : (PO_STATE[r.state] ?? r.state) + (r.fromOrderpoint && r.state === 'draft' ? ' (menunggu buyer)' : '')) },
                { header: 'Tanggal terjanji', cell: (r) => (r.plannedAt ? fmtDate(r.plannedAt.toISOString().slice(0, 10)) : '-') },
                { header: 'Nilai Rp', align: 'right', cell: (r) => fmtIdr(r.value) },
                { header: 'Baris', align: 'right', cell: (r) => fmtInt(r.lines) },
                { header: 'Odoo', cell: (r) => <ActionLink label="Buka RFQ/PO di Odoo" href={odooRecordUrl(origin, 'purchase.order', r.orderId)} /> },
              ]}
            />
          )}
        </SmCardAuto>
      </div>
    </>
  );
}
