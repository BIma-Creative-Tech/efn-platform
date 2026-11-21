import type { Metadata } from 'next';
import { ChartCard } from '@/components/ChartCard';
import { EXPIRY_PILL, markdownText } from '@/components/sm/ExpiryRows';
import { ActionLink, SmCardAuto, SmKpiTile } from '@/components/sm/SmCard';
import { SmFilterForm } from '@/components/sm/SmFilterForm';
import { SmFreshnessBadge } from '@/components/sm/SmFreshnessBadge';
import { ShelfLifeTable } from '@/components/sm/ShelfLifeTable';
import { SmTable } from '@/components/sm/SmTable';
import { KpiRow, PageHeader, StatusPill } from '@/components/ui';
import { fmtDate, fmtIdr, fmtIdrShort, fmtInt, fmtPct, fmtQty } from '@/lib/format';
import { odooRecordUrl } from '@/lib/odoo-links';
import { loadCategory } from '@/lib/sm-data';
import { MARTS, martOff } from '@/lib/sm-freshness';
import { fmtDaysLeft, fmtPctOrNa, fmtSuppressed } from '@/lib/sm-format';
import { oosRate, oosStatus, pct, ppChange, priceStatus } from '@/lib/sm-kpi';
import { smPage, type SmSearchParams } from '@/lib/sm-page';
import { toNum, toNumOrNull } from '@/lib/sm-parse';
import { expiryReason, priceReason } from '@/lib/status-reasons';

export const dynamic = 'force-dynamic';
export const metadata: Metadata = { title: 'Kategori' };

const ERR = 'Tidak dapat dimuat';

export default async function CategoryPage({ searchParams }: { searchParams: SmSearchParams }) {
  const { ctx, adjusted, chip } = await smPage('category', searchParams, { defaultDays: 7 });
  const data = await loadCategory(ctx);
  const { base, now, filters, odooOrigin: origin } = ctx;
  const t = base.t;
  const f = base.freshness;
  const outlets = base.warehouses.filter((w) => w.type !== 'dc' && ctx.session.wids.includes(w.id));

  const o = data.oos.ok ? data.oos.data : undefined;
  const oosCur = o ? oosRate(toNum(o.oos_cur), toNum(o.uni_cur)) : null;
  const oosPrev = o ? oosRate(toNum(o.oos_prev), toNum(o.uni_prev)) : null;
  const ex = data.expiryTotals.ok ? data.expiryTotals.data : null;
  const expiryOff = martOff(f.get(MARTS.expiry));
  const visitorOff = martOff(f.get(MARTS.visitor));
  const sigCount = data.signal.ok ? data.signal.data.rows.length : null;

  return (
    <>
      <PageHeader
        title="Kategori"
        question="Apa yang habis, mendekati kedaluwarsa, atau mahal?"
        actions={
          <>
            <span className="persona-chip">{chip}</span>
            <SmFreshnessBadge
              marts={[MARTS.stock, MARTS.expiry, MARTS.priceHunter, MARTS.visitor]}
              freshness={f}
              t={t}
              now={now}
            />
          </>
        }
      />
      <SmFilterForm
        path="/category"
        filters={filters}
        yesterday={ctx.yesterday}
        outlets={outlets.map((w) => ({ id: w.id, name: w.name }))}
        categories={base.categories}
        show={{ period: true, outlets: true, category: true }}
        adjusted={adjusted}
      />
      {filters.catDefaulted ? <p className="muted small">Kategori awal = kategori yang Anda kelola. Ubah di filter bila perlu.</p> : null}

      <KpiRow>
        <SmKpiTile
          label="OOS rate"
          value={!data.oos.ok ? ERR : oosCur === null ? 'Belum ada data' : fmtPct(oosCur)}
          muted={oosCur === null}
          status={oosCur !== null ? oosStatus(oosCur, base.t) : undefined}
          delta={oosCur !== null ? { value: ppChange(oosCur, oosPrev), kind: 'pp', goodWhen: 'down' } : undefined}
          info="Porsi SKU x outlet x hari yang stoknya habis pada snapshot akhir hari, dari SKU yang punya orderpoint di outlet itu. Definisi diatur per PT di Odoo."
        />
        <SmKpiTile
          label="Near-expiry (nilai HPP)"
          value={!data.expiryTotals.ok ? ERR : expiryOff ? 'Pemantauan belum aktif' : `${ex?.total ?? 0} lot`}
          muted={!data.expiryTotals.ok || expiryOff}
          note={!expiryOff && ex ? `Rp (HPP) ${fmtIdrShort(ex.valueCost)}` : undefined}
        />
        <SmKpiTile
          label="Markdown aktif"
          value={!data.expiryTotals.ok ? ERR : expiryOff ? 'Pemantauan belum aktif' : `${ex?.counts.markdown_aktif ?? 0} lot`}
          muted={!data.expiryTotals.ok || expiryOff}
        />
        <SmKpiTile
          label="Harga di atas pasar, banyak dilihat"
          value={sigCount === null ? ERR : visitorOff ? 'Pelacakan perilaku belum aktif' : `${sigCount >= 100 ? '100+' : sigCount} SKU`}
          muted={sigCount === null || visitorOff}
          note={!visitorOff && sigCount !== null ? 'Hanya pengunjung yang menyetujui pelacakan' : undefined}
          info="SKU dengan harga di atas batas 'di atas pasar' (median Price Hunter 30 hari) yang dilihat oleh sedikitnya 10 pengunjung. Definisi sinyal final menunggu ADR-047."
        />
      </KpiRow>

      <div className="sm-grid">
        <SmCardAuto
          id="cm2"
          title="OOS: kategori x outlet"
          mart={MARTS.stock}
          base={base}
          now={now}
          result={data.heat}
          hasRows={(d) => d.cells.length > 0}
          zeroMeans="no-data"
          texts={{ zero: 'Tidak ada SKU yang habis.', off: 'Data stok belum tersedia.' }}
        >
          {(h) => (
            <ChartCard
              title="OOS rate"
              subtitle="Sel kosong = tidak ada SKU di kategori itu pada outlet itu (n/a)"
              height={Math.max(220, h.rows.length * 30 + 80)}
              specs={[
                {
                  id: 'oosheat',
                  kind: 'heat',
                  fmt: 'pct',
                  categories: h.cols,
                  yCategories: h.rows,
                  heat: h.cells,
                  series: [{ name: 'Kategori', data: [], color: 'c1' }],
                  summary: `Peta panas OOS rate ${h.rows.length} kategori x ${h.cols.length} outlet; lihat tabel untuk nilai tiap sel.`,
                },
              ]}
            />
          )}
        </SmCardAuto>

        <SmCardAuto
          id="cm3"
          title="SKU paling sering habis"
          mart={MARTS.stock}
          base={base}
          now={now}
          result={data.products}
          hasRows={(d) => d.length > 0}
          zeroMeans="zero"
          texts={{ zero: 'Tidak ada SKU yang habis pada periode ini.', off: 'Data stok belum tersedia.' }}
        >
          {(rows) => (
            <SmTable
              caption="SKU paling sering habis"
              rows={rows}
              rowKey={(r) => String(r.id)}
              columns={[
                { header: 'Produk', cell: (r) => r.name },
                { header: 'OOS rate', align: 'right', cell: (r) => fmtPctOrNa(pct(r.oosDays, r.universeDays)) },
                { header: 'Hari habis', align: 'right', cell: (r) => fmtInt(r.oosDays) },
                { header: 'Outlet terdampak', align: 'right', cell: (r) => fmtInt(r.outlets) },
                { header: 'Odoo', cell: (r) => <ActionLink label="Buka produk di Odoo" href={odooRecordUrl(origin, 'product.template', r.tmplId)} /> },
              ]}
            />
          )}
        </SmCardAuto>

        <div className="wide">
          <SmCardAuto
            id="cm4"
            title="Near-expiry lintas outlet"
            mart={MARTS.expiry}
            base={base}
            now={now}
            result={data.expiry}
            hasRows={(d) => d.length > 0}
            zeroMeans="zero"
            texts={{ zero: 'Tidak ada lot mendekati kedaluwarsa pada kategori Anda.', off: 'Pemantauan kedaluwarsa belum diaktifkan.' }}
            wide
          >
            {(lots) => (
              <>
                {ex ? (
                  <p>
                    {ex.counts.kedaluwarsa} kedaluwarsa · {ex.counts.markdown_sekarang} perlu markdown · {ex.counts.markdown_aktif} markdown aktif · {ex.counts.pantau} dipantau. Total nilai Rp (HPP) {fmtIdr(ex.valueCost)}.
                  </p>
                ) : null}
                <SmTable
                  caption="Lot mendekati kedaluwarsa menurut sisa umur"
                  rows={lots}
                  rowKey={(l) => String(l.alertId)}
                  columns={[
                    { header: 'Produk', cell: (l) => l.productName, text: (l) => l.productName },
                    { header: 'Lot', cell: (l) => l.lotName || '-', text: (l) => l.lotName },
                    { header: 'Outlet', cell: (l) => base.whNames.get(l.warehouseId) ?? `Outlet #${l.warehouseId}` },
                    { header: 'Qty', align: 'right', cell: (l) => fmtQty(l.qty) },
                    { header: 'Sisa umur', align: 'right', cell: (l) => fmtDaysLeft(l.daysLeft) },
                    { header: 'Nilai Rp (HPP)', align: 'right', cell: (l) => fmtIdr(l.valueCost) },
                    {
                      header: 'Status',
                      cell: (l) => <StatusPill status={EXPIRY_PILL[l.display].status} label={EXPIRY_PILL[l.display].label} />,
                      text: (l) => EXPIRY_PILL[l.display].label,
                      filter: true,
                    },
                    { header: 'Markdown', cell: (l) => markdownText(l) },
                    {
                      header: 'Keterangan',
                      cell: (l) => expiryReason(l.display, l.daysLeft, l.valueCost),
                      text: (l) => `${l.productName} ${l.lotName} ${expiryReason(l.display, l.daysLeft, l.valueCost)}`,
                    },
                    { header: 'Odoo', cell: (l) => <ActionLink label="Buka lot di Odoo" href={odooRecordUrl(origin, 'stock.lot', l.lotId)} /> },
                  ]}
                />
              </>
            )}
          </SmCardAuto>
        </div>

        <div className="wide">
          <SmCardAuto
            id="cm7"
            title="Umur simpan & sisa umur lot"
            mart={MARTS.stock}
            base={base}
            now={now}
            result={data.shelf}
            hasRows={(d) => d.length > 0}
            zeroMeans="no-data"
            texts={{ zero: 'Tidak ada lot berstok.', off: 'Data stok belum tersedia.', noData: 'Belum ada lot berstok pada kategori dan outlet ini.' }}
            wide
          >
            {(rows) => <ShelfLifeTable rows={rows} whNames={base.whNames} showOutlet />}
          </SmCardAuto>
        </div>

        <div className="wide">
          <SmCardAuto
            id="cm5"
            title="Harga vs Price Hunter"
            mart={MARTS.priceHunter}
            base={base}
            now={now}
            result={data.price}
            hasRows={(d) => d.length > 0}
            zeroMeans="no-data"
            texts={{ zero: 'Tidak ada selisih harga.', off: 'Data harga pasar belum aktif.', noData: 'Belum ada data harga pasar untuk kategori ini.' }}
            wide
          >
            {(rows) => {
              const cov = data.coverage.ok ? data.coverage.data : undefined;
              return (
                <>
                  <p className="muted small">
                    Harga pasar = sampel komunitas (Price Hunter), bukan survei lengkap. Harga rak termasuk PPN.
                    {cov ? ` Tersedia untuk ${fmtInt(toNum(cov.with_n))} dari ${fmtInt(toNum(cov.active_n))} SKU (minimal 3 sampel).` : ''}
                  </p>
                  <SmTable
                    caption="Harga rak dibanding median pasar 30 hari"
                    rows={rows}
                    rowKey={(r) => `${r.productTmplId ?? r.name}`}
                    columns={[
                      { header: 'Produk', cell: (r) => r.name },
                      { header: 'Harga rak (termasuk PPN)', align: 'right', cell: (r) => (r.ourPrice === null ? 'n/a' : fmtIdr(r.ourPrice)) },
                      { header: 'Median pasar', align: 'right', cell: (r) => (r.marketMedian === null ? 'n/a' : fmtIdr(r.marketMedian)) },
                      { header: 'Selisih', align: 'right', cell: (r) => fmtPctOrNa(r.diffPct) },
                      { header: 'Sampel (n)', align: 'right', cell: (r) => fmtInt(r.sampleN) },
                      { header: 'Sampel terakhir', cell: (r) => (r.lastSample ? fmtDate(r.lastSample) : '-') },
                      {
                        header: 'Status',
                        cell: (r) => {
                          const s = priceStatus(r.diffPct, r.sampleN, t);
                          return <StatusPill status={s.status} label={s.label} />;
                        },
                      },
                      {
                        header: 'Keterangan',
                        cell: (r) => priceReason(r.diffPct, r.sampleN, priceStatus(r.diffPct, r.sampleN, t).status, t),
                      },
                      { header: 'Odoo', cell: (r) => <ActionLink label="Buka produk di Odoo" href={odooRecordUrl(origin, 'product.template', r.productTmplId)} /> },
                    ]}
                  />
                </>
              );
            }}
          </SmCardAuto>
        </div>

        <div className="wide">
          <SmCardAuto
            id="cm6"
            title="Sinyal terlalu mahal"
            mart={MARTS.visitor}
            base={base}
            now={now}
            result={data.signal}
            hasRows={(d) => d.rows.length > 0}
            zeroMeans="zero"
            texts={{
              zero: 'Tidak ada SKU yang memenuhi sinyal pada periode ini.',
              off: 'Pelacakan perilaku belum aktif; kartu ini terisi setelah ada persetujuan DPO.',
            }}
            wide
          >
            {({ rows, names }) => (
              <>
                <p className="muted small">Hanya mencakup pengunjung yang menyetujui pelacakan. Angka di bawah 10 pengunjung ditekan.</p>
                <SmTable
                  caption="SKU banyak dilihat dengan harga di atas pasar"
                  rows={rows}
                  rowKey={(r) => String(toNum(r.product_id))}
                  columns={[
                    { header: 'Produk', cell: (r) => names.get(toNum(r.product_id))?.name ?? `Produk #${toNum(r.product_id)}` },
                    { header: 'Pengunjung', align: 'right', cell: (r) => fmtSuppressed(toNumOrNull(r.viewers_30d)) },
                    { header: 'Beli', align: 'right', cell: (r) => fmtSuppressed(toNumOrNull(r.purchases_30d)) },
                    { header: 'Harga rak', align: 'right', cell: (r) => (toNumOrNull(r.our_price) === null ? 'n/a' : fmtIdr(toNum(r.our_price))) },
                    { header: 'Median pasar', align: 'right', cell: (r) => (toNumOrNull(r.market_median_30d) === null ? 'n/a' : fmtIdr(toNum(r.market_median_30d))) },
                    { header: 'Selisih', align: 'right', cell: (r) => fmtPctOrNa(toNumOrNull(r.diff_pct)) },
                    {
                      header: 'Odoo',
                      cell: (r) => <ActionLink label="Buka produk di Odoo" href={odooRecordUrl(origin, 'product.template', names.get(toNum(r.product_id))?.tmplId ?? null)} />,
                    },
                  ]}
                />
              </>
            )}
          </SmCardAuto>
        </div>
      </div>
    </>
  );
}
