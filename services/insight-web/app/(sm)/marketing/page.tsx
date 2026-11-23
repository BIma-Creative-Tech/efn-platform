import type { Metadata } from 'next';
import Link from 'next/link';
import { FunnelBars } from '@/components/sm/FunnelBars';
import { ActionLink, SmCardAuto, SmKpiTile } from '@/components/sm/SmCard';
import { SmFilterForm } from '@/components/sm/SmFilterForm';
import { SmFreshnessBadge } from '@/components/sm/SmFreshnessBadge';
import { SmTable } from '@/components/sm/SmTable';
import { EmptyState, KpiRow, PageHeader, StatusPill } from '@/components/ui';
import { fmtDate, fmtIdr, fmtInt, fmtPct } from '@/lib/format';
import { odooRecordUrl } from '@/lib/odoo-links';
import { loadMarketing } from '@/lib/sm-data';
import { MARTS, martOff } from '@/lib/sm-freshness';
import { fmtSuppressed, isSuppressed } from '@/lib/sm-format';
import { pct, ppChange } from '@/lib/sm-kpi';
import { smPage, type SmSearchParams } from '@/lib/sm-page';
import { SM_FIXED } from '@/lib/sm-defaults';
import { toNum } from '@/lib/sm-parse';
import { buildFunnel, campaignTotals } from '@/lib/sm-vm';

export const dynamic = 'force-dynamic';
export const metadata: Metadata = { title: 'Marketing' };

const ERR = 'Tidak dapat dimuat';
const CHANNELS = [
  { v: '', label: 'Semua kanal' },
  { v: 'web', label: 'Web' },
  { v: 'app', label: 'Aplikasi' },
];

export default async function MarketingPage({ searchParams }: { searchParams: SmSearchParams }) {
  const { ctx, adjusted, chip } = await smPage('marketing', searchParams, { defaultDays: 30 });
  const rawCh = (await searchParams).channel;
  const chIn = Array.isArray(rawCh) ? rawCh[0] : rawCh;
  const channel = chIn === 'web' || chIn === 'app' ? chIn : '';
  const data = await loadMarketing(ctx, channel);
  const { base, now, filters, odooOrigin: origin } = ctx;
  const t = base.t;
  const visitorOff = martOff(base.freshness.get(MARTS.visitor));
  const outlets = base.warehouses.filter((w) => w.type !== 'dc' && ctx.session.wids.includes(w.id));

  const fn = data.funnel.ok ? data.funnel.data : undefined;
  const steps = buildFunnel({ view: toNum(fn?.view_cur), cart: toNum(fn?.cart_cur), chk: toNum(fn?.chk_cur), buy: toNum(fn?.buy_cur) });
  const convCur = pct(toNum(fn?.buy_cur), toNum(fn?.view_cur));
  const convPrev = pct(toNum(fn?.buy_prev), toNum(fn?.view_prev));
  const viewers = toNum(fn?.view_cur);
  const suppressedViewers = isSuppressed(viewers);
  const camp = data.campaigns.ok ? campaignTotals(data.campaigns.data) : null;
  const cost = camp?.costVsBudgetPct ?? null;
  const costStatus = cost === null ? undefined : cost > SM_FIXED.costBadPct ? 'bad' : cost > SM_FIXED.costWarnPct ? 'warn' : 'good';

  const qs = (v: string): string => {
    const p = new URLSearchParams();
    p.set('from', filters.from);
    p.set('to', filters.to);
    if (v) p.set('channel', v);
    return `/marketing?${p.toString()}`;
  };

  return (
    <>
      <PageHeader
        title="Marketing"
        question="Pengunjung berhenti di mana dan kampanye apa yang bekerja?"
        actions={
          <>
            <span className="persona-chip">{chip}</span>
            <SmFreshnessBadge marts={[MARTS.visitor, MARTS.campaign]} freshness={base.freshness} t={t} now={now} />
          </>
        }
      />
      <SmFilterForm
        path="/marketing"
        filters={filters}
        yesterday={ctx.yesterday}
        outlets={outlets.map((w) => ({ id: w.id, name: w.name }))}
        categories={[]}
        show={{ period: true, outlets: true, category: false }}
        adjusted={adjusted}
      />

      {visitorOff ? (
        <div className="card">
          <EmptyState
            icon="info"
            title="Pelacakan perilaku belum aktif."
            reason="Angka pengunjung dan funnel terisi setelah pelacakan diaktifkan dengan persetujuan DPO. Tidak ada angka nol yang ditampilkan sebelum itu."
          />
        </div>
      ) : (
        <>
          <p className="note-block" role="note">
            Angka funnel mencakup semua pengunjung, termasuk kunjungan anonim tanpa persetujuan pelacakan (V-01, 23 Nov 2026). Satuan = jumlah perangkat-hari (satu perangkat yang datang dua hari terhitung dua).
          </p>
          <nav aria-label="Kanal" className="tabs-links">
            {CHANNELS.map((c) => (
              <Link key={c.v || 'all'} href={qs(c.v)} className="btn" aria-current={channel === c.v ? 'true' : undefined}>
                {c.label}
              </Link>
            ))}
          </nav>
          <KpiRow>
            <SmKpiTile
              label="Pengunjung (perangkat-hari)"
              value={!data.funnel.ok ? ERR : viewers === 0 ? 'Belum ada data' : fmtSuppressed(viewers)}
              muted={!data.funnel.ok || viewers === 0}
              info="Σ perangkat per hari (anonim + ber-consent). Unik per periode belum tersedia (Q-SM41)."
            />
            <SmKpiTile
              label="Konversi"
              value={!data.funnel.ok ? ERR : suppressedViewers || convCur === null ? 'Belum cukup data' : fmtPct(convCur)}
              muted={!data.funnel.ok || suppressedViewers || convCur === null}
              delta={!suppressedViewers && convCur !== null ? { value: ppChange(convCur, convPrev), kind: 'pp', goodWhen: 'up' } : undefined}
              info="Pembeli dibagi pengunjung (perangkat-hari)."
            />
            <SmKpiTile
              label="Voucher dipakai"
              value={!data.campaigns.ok ? ERR : camp ? fmtInt(camp.used) : '-'}
              muted={!data.campaigns.ok}
            />
            <SmKpiTile
              label="Biaya voucher vs anggaran"
              value={!data.campaigns.ok ? ERR : cost === null ? 'Tidak ada anggaran' : fmtPct(cost)}
              muted={cost === null}
              status={costStatus}
              info="Diskon voucher dibagi anggaran, hanya untuk kampanye yang punya anggaran."
            />
          </KpiRow>
          <div className="sm-grid">
            <SmCardAuto
              id="mk2"
              title="Funnel pengunjung"
              mart={MARTS.visitor}
              base={base}
              now={now}
              result={data.funnel}
              hasRows={(d) => d !== undefined && toNum(d.view_cur) > 0}
              zeroMeans="no-data"
              texts={{ zero: 'Tidak ada pengunjung.', off: 'Pelacakan perilaku belum aktif.', noData: 'Belum ada event perilaku pada periode ini.' }}
              wide
            >
              {() => (
                <>
                  <FunnelBars steps={steps} />
                  <p className="muted small">Tahap wishlist belum tersedia di data; tahap kedua = memasukkan ke keranjang.</p>
                </>
              )}
            </SmCardAuto>
          </div>
        </>
      )}

      <div className="sm-grid">
        <SmCardAuto
          id="mk4"
          title="Hasil kampanye"
          mart={MARTS.campaign}
          base={base}
          now={now}
          result={data.campaigns}
          hasRows={(d) => d.length > 0}
          zeroMeans="no-data"
          texts={{ zero: 'Belum ada kampanye.', off: 'Fitur kampanye belum aktif.', noData: 'Belum ada kampanye pada periode ini.' }}
          wide
        >
          {(rows) => (
            <>
              <p className="muted small">Angka kampanye = korelasi, bukan dampak: omzet inkremental tidak dihitung karena belum ada kelompok kontrol.</p>
              <SmTable
                caption="Hasil kampanye pada periode"
                rows={rows}
                rowKey={(r) => String(r.campaignId)}
                columns={[
                  { header: 'Kampanye', cell: (r) => r.name },
                  { header: 'Kanal', cell: (r) => (r.channels.length > 0 ? r.channels.join(', ') : '-') },
                  { header: 'Status', cell: (r) => <StatusPill status="neutral" label={r.state || 'Tidak diketahui'} /> },
                  { header: 'Periode', cell: (r) => (r.dateFrom && r.dateTo ? `${fmtDate(r.dateFrom)} - ${fmtDate(r.dateTo)}` : '-') },
                  { header: 'Terkirim', align: 'right', cell: (r) => fmtSuppressed(r.delivered) },
                  { header: 'Voucher dipakai', align: 'right', cell: (r) => `${fmtInt(r.vouchersUsed)} / ${fmtInt(r.vouchersIssued)}` },
                  { header: 'Omzet inkremental', align: 'right', cell: () => 'Tidak dihitung' },
                  { header: 'Biaya voucher', align: 'right', cell: (r) => fmtIdr(r.discountIdr) },
                  { header: 'Anggaran', align: 'right', cell: (r) => (r.budgetIdr === null ? '-' : fmtIdr(r.budgetIdr)) },
                  { header: 'Odoo', cell: (r) => <ActionLink label="Buka kampanye di Odoo" href={odooRecordUrl(origin, 'efn.campaign', r.campaignId)} /> },
                ]}
              />
            </>
          )}
        </SmCardAuto>
      </div>
    </>
  );
}
