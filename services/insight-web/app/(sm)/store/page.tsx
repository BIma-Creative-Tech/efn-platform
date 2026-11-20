import type { Metadata } from 'next';
import Link from 'next/link';
import { CutoffCountdown } from '@/components/sm/CutoffCountdown';
import { CriticalRows, ExpiryRows } from '@/components/sm/ExpiryRows';
import { ActionLink, SmCardAuto } from '@/components/sm/SmCard';
import { SmFreshnessBadge } from '@/components/sm/SmFreshnessBadge';
import { DeltaBadge, PageHeader, StatusPill } from '@/components/ui';
import { fmtIdr, fmtPct } from '@/lib/format';
import { odooListUrl, odooRecordUrl } from '@/lib/odoo-links';
import { MARTS } from '@/lib/sm-freshness';
import { fmtClockWib, fmtDeliveryDate, fmtStampWib } from '@/lib/sm-format';
import { cutoffInfo, ppChange, wasteStatus } from '@/lib/sm-kpi';
import { loadStore } from '@/lib/sm-data';
import { smPage, type SmSearchParams } from '@/lib/sm-page';
import { cfgStr } from '@/lib/sm-thresholds';
import { pickProposals } from '@/lib/sm-vm';

export const dynamic = 'force-dynamic';
export const metadata: Metadata = { title: 'Toko saya' };

const SHOWN = 3;

export default async function StorePage({ searchParams }: { searchParams: SmSearchParams }) {
  const { ctx, chip } = await smPage('store', searchParams, { defaultDays: 7 });
  const data = await loadStore(ctx);
  const { base, now, session, odooOrigin: origin } = ctx;
  const t = base.t;
  const outlets = session.wids.filter((w) => w > 0);
  const selected = ctx.filters.wh.length > 0 ? ctx.filters.wh : outlets;
  const single = selected.length === 1 ? selected[0] : null;
  const title = 'Toko saya';
  const sub = single !== null ? (base.whNames.get(single) ?? 'Outlet Anda') : `${selected.length} outlet`;
  const showNames = single === null;
  const wasteBasis = cfgStr(base.config, 'waste.basis');

  return (
    <div className="store-page">
      <PageHeader
        title={title}
        question={`${sub}. Apa yang perlu saya lakukan sekarang?`}
        actions={
          <>
            <span className="persona-chip">{chip}</span>
            <SmFreshnessBadge marts={[MARTS.replenishment, MARTS.expiry, MARTS.stock, MARTS.waste]} freshness={base.freshness} t={t} now={now} />
          </>
        }
      />
      {outlets.length > 1 && outlets.length <= 5 ? (
        <nav aria-label="Pilih outlet" className="tabs-links">
          <Link href="/store" className="btn" aria-current={ctx.filters.wh.length === 0 ? 'true' : undefined}>
            Semua outlet
          </Link>
          {outlets.map((id) => (
            <Link key={id} href={`/store?wh=${id}`} className="btn" aria-current={single === id && ctx.filters.wh.length > 0 ? 'true' : undefined}>
              {base.whNames.get(id) ?? `Outlet #${id}`}
            </Link>
          ))}
        </nav>
      ) : null}
      <nav aria-label="Lompat ke kartu" className="anchors">
        <a href="#st1-h">Usulan</a>
        <a href="#st2-h">Kedaluwarsa</a>
        <a href="#st3-h">Stok</a>
        <a href="#st4-h">Susut</a>
      </nav>

      <div className="stack">
        <SmCardAuto
          id="st1"
          title="Usulan order"
          mart={MARTS.replenishment}
          base={base}
          now={now}
          result={data.proposals}
          hasRows={(d) => d.length > 0}
          zeroMeans="no-data"
          texts={{
            zero: 'Tidak ada usulan pengiriman.',
            off: 'Usulan order belum diaktifkan untuk outlet ini.',
            noData: 'Tidak ada usulan pengiriman dalam 7 hari. Bisa jadi forecast belum berjalan atau jadwal kirim belum diatur.',
          }}
        >
          {(rows, level) => {
            const { primary, others } = pickProposals(rows);
            if (!primary) return null;
            const stale = level === 'bad';
            const info = cutoffInfo(primary.state, primary.cutoffAt, now, t, stale);
            const open = primary.state === 'draft' || primary.state === 'proposed';
            const url = odooRecordUrl(origin, 'efn.replenishment.proposal', primary.proposalId);
            return (
              <>
                <div className="big-status">
                  <span>Pengiriman {fmtDeliveryDate(primary.deliveryDate)}</span>
                  {primary.cutoffAt ? (
                    <CutoffCountdown cutoffAtIso={primary.cutoffAt.toISOString()} nowMs={now.getTime()} showCountdown={open && !info.uncertain} />
                  ) : (
                    <span>Batas persetujuan tidak diketahui</span>
                  )}
                  <span>
                    <StatusPill status={info.status} label={info.label} />
                  </span>
                  {showNames ? <span className="muted small">{base.whNames.get(primary.warehouseId) ?? `Outlet #${primary.warehouseId}`}</span> : null}
                </div>
                <p>
                  {primary.lineCount} baris · {primary.linesChanged} baris berubah dari sistem
                </p>
                <ActionLink label="Buka usulan di Odoo" href={url} big />
                <p className="muted small">Status per {fmtStampWib(base.freshness.get(MARTS.replenishment)?.dataAsOf ?? now, now)}. Cek di Odoo sebelum bertindak.</p>
                {others.length > 0 ? (
                  <details>
                    <summary>Pengiriman berikutnya lainnya ({others.length})</summary>
                    <ul className="rows">
                      {others.map((o) => (
                        <li key={o.proposalId}>
                          {fmtDeliveryDate(o.deliveryDate)}
                          {o.cutoffAt ? `, batas ${fmtClockWib(o.cutoffAt)}` : ''} ·{' '}
                          <StatusPill status={cutoffInfo(o.state, o.cutoffAt, now, t, stale).status} label={cutoffInfo(o.state, o.cutoffAt, now, t, stale).label} />
                        </li>
                      ))}
                    </ul>
                  </details>
                ) : null}
              </>
            );
          }}
        </SmCardAuto>

        <SmCardAuto
          id="st2"
          title="Kedaluwarsa hari ini"
          mart={MARTS.expiry}
          base={base}
          now={now}
          result={data.expiry}
          hasRows={(d) => d.lots.length > 0}
          zeroMeans="zero"
          texts={{ zero: 'Tidak ada barang mendekati kedaluwarsa. Bagus.', off: 'Pemantauan kedaluwarsa belum diaktifkan untuk outlet ini.' }}
        >
          {({ lots, delta }) => {
            const counts = { kedaluwarsa: 0, markdown_sekarang: 0, markdown_aktif: 0, pantau: 0 };
            for (const l of lots) counts[l.display] += 1;
            const more = lots.length > SHOWN ? lots.slice(SHOWN) : [];
            return (
              <>
                <p>
                  {lots.length >= 200 ? '200+' : lots.length} lot · {counts.kedaluwarsa} kedaluwarsa · {counts.markdown_sekarang} perlu markdown · {counts.markdown_aktif} markdown aktif
                  {delta.delta !== null ? ` · ${delta.delta >= 0 ? '+' : ''}${delta.delta} dari kemarin` : ''}
                </p>
                <ExpiryRows lots={lots.slice(0, SHOWN)} origin={origin} whNames={showNames ? base.whNames : undefined} label="Lot mendekati kedaluwarsa" />
                {more.length > 0 ? (
                  <details>
                    <summary>Lihat semua {lots.length >= 200 ? '200+' : lots.length} lot</summary>
                    <ExpiryRows lots={more} origin={origin} whNames={showNames ? base.whNames : undefined} label="Lot lainnya" />
                  </details>
                ) : null}
                <p className="muted small">Dihitung harian oleh sistem (cron), bukan per jam.</p>
              </>
            );
          }}
        </SmCardAuto>

        <SmCardAuto
          id="st3"
          title="Stok kritis"
          mart={MARTS.stock}
          base={base}
          now={now}
          result={data.critical}
          hasRows={(d) => d.length > 0}
          zeroMeans="zero"
          texts={{ zero: 'Tidak ada SKU kritis.', off: 'Data stok belum tersedia untuk outlet ini.' }}
        >
          {(rows) => {
            const out = rows.filter((r) => r.isOut).length;
            return (
              <>
                <p>
                  {rows.length >= 200 ? '200+' : rows.length} SKU · {out} habis · {rows.length - out} kritis
                </p>
                <CriticalRows rows={rows.slice(0, SHOWN)} origin={origin} whNames={showNames ? base.whNames : undefined} label="SKU habis atau kritis" />
                {rows.length > SHOWN ? (
                  <details>
                    <summary>Lihat semua {rows.length >= 200 ? '200+' : rows.length} SKU</summary>
                    <CriticalRows rows={rows.slice(SHOWN)} origin={origin} whNames={showNames ? base.whNames : undefined} label="SKU lainnya" />
                  </details>
                ) : null}
              </>
            );
          }}
        </SmCardAuto>

        <SmCardAuto
          id="st4"
          title="Susut (waste)"
          mart={MARTS.waste}
          base={base}
          now={now}
          result={data.waste}
          hasRows={(d) => d.wasteCur > 0 || d.wastePrev > 0}
          zeroMeans="no-data"
          texts={{
            zero: 'Tidak ada susut tercatat.',
            off: 'Pencatatan susut belum diaktifkan.',
            noData: 'Belum ada pencatatan susut minggu ini. Pastikan scrap dicatat di Odoo.',
          }}
          actions={<ActionLink label="Buka daftar scrap di Odoo" href={odooListUrl(origin, 'stock.scrap')} />}
        >
          {(w) => {
            const status = w.pctCur === null ? 'neutral' : wasteStatus(w.pctCur, t);
            const delta = ppChange(w.pctCur, w.pctPrev);
            return (
              <>
                <p>
                  <span className="kpi-value">{fmtIdr(w.wasteCur)}</span> <span className="muted small">Rp (HPP), minggu berjalan</span>
                </p>
                <p>
                  {w.pctCur !== null ? (
                    <>
                      <strong>{fmtPct(w.pctCur)}</strong> {wasteBasis === 'sales_excl_tax' ? 'dari omzet tanpa PPN' : 'dari HPP terjual'}{' '}
                      <StatusPill status={status} /> <DeltaBadge value={delta} kind="pp" goodWhen="down" compareLabel="minggu lalu" />
                    </>
                  ) : (
                    <span className="muted small">Persentase tidak tersedia untuk peran ini.</span>
                  )}
                </p>
                {w.reasons.length > 0 ? (
                  <details open>
                    <summary>Alasan susut</summary>
                    <ul className="rows" aria-label="Alasan susut">
                      {w.reasons.map((r) => (
                        <li key={r.name}>
                          {r.name}: {fmtIdr(r.value)}
                          {r.sharePct !== null ? ` (${fmtPct(r.sharePct)})` : ''}
                        </li>
                      ))}
                    </ul>
                  </details>
                ) : null}
              </>
            );
          }}
        </SmCardAuto>
      </div>
    </div>
  );
}
