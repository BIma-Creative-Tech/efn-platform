import type { Metadata } from 'next';
import Link from 'next/link';
import { redirect } from 'next/navigation';
import type { ReactNode } from 'react';
import { ChartCard } from '@/components/ChartCard';
import { FunnelBars } from '@/components/sm/FunnelBars';
import { SmCard, SmErrorInline } from '@/components/sm/SmCard';
import { SmFilterForm } from '@/components/sm/SmFilterForm';
import { SmTable } from '@/components/sm/SmTable';
import { EmptyState, KpiRow, KpiTile, PageHeader } from '@/components/ui';
import { requireSession } from '@/lib/auth';
import { codeColor, codeName, markerFor } from '@/lib/brands';
import { summarize, type ChartSpec } from '@/lib/chart-spec';
import { chQueryVis } from '@/lib/clickhouse';
import { eachDay, wibYesterday } from '@/lib/dates';
import { fmtDate, fmtInt, fmtPct, fmtRange } from '@/lib/format';
import { settle, type Result } from '@/lib/loaders';
import type { VisQueryName } from '@/lib/queries-visitor';
import { getConfig } from '@/lib/runtime';
import { K_ANON } from '@/lib/sm-defaults';
import type { SmFilters } from '@/lib/sm-filters';
import { pct, pctChange } from '@/lib/sm-kpi';
import { buildFunnel } from '@/lib/sm-vm';
import {
  dailySeries,
  pageLabel,
  parseVisitorFilters,
  toProductConversion,
  visitorCodes,
  visitorHref,
  type VisitorChannel,
} from '@/lib/visitor';

export const dynamic = 'force-dynamic';
export const metadata: Metadata = { title: 'Pengunjung' };

type Raw = Record<string, unknown>;
type SearchParams = Promise<Record<string, string | string[] | undefined>>;

const CHANNELS: { v: VisitorChannel; label: string }[] = [
  { v: '', label: 'Semua kanal' },
  { v: 'web', label: 'Web' },
  { v: 'app', label: 'Aplikasi' },
];

const num = (v: unknown): number => {
  const n = Number(v ?? 0);
  return Number.isFinite(n) ? n : 0;
};

function Card<T>({ id, title, result, wide, children }: { id: string; title: string; result: Result<T>; wide?: boolean; children: (d: T) => ReactNode }): ReactNode {
  return (
    <SmCard id={id} title={title} wide={wide}>
      {result.ok ? children(result.data) : <SmErrorInline refId={result.ref} reason={result.reason} />}
    </SmCard>
  );
}

function Empty({ text }: { text: string }): ReactNode {
  return <EmptyState icon="info" title={text} reason="Data terisi setelah log kunjungan aktif dan ETL perilaku berjalan (agregat dihitung ulang tiap jam)." />;
}

export default async function VisitorsPage({ searchParams }: { searchParams: SearchParams }) {
  const session = await requireSession();
  const cfg = getConfig();
  const allowed = visitorCodes(session, cfg.smEnabled);
  if (allowed.length === 0) redirect('/tidak-berwenang');
  const now = new Date();
  const { filters: f, adjusted } = parseVisitorFilters(await searchParams, allowed, now);
  const bag = { from: f.from, to: f.to, prev_from: f.prev.from, prev_to: f.prev.to, codes: f.codes, channel: f.channel };
  const load = (name: VisQueryName): Promise<Result<Raw[]>> => settle(chQueryVis<Raw>(name, session, bag));
  const [kpi, daily, pages, funnel, products, searchTop, searchNone] = await Promise.all([
    load('vis_kpi'),
    load('vis_daily'),
    load('vis_pages'),
    load('vis_funnel'),
    load('vis_products'),
    load('vis_search_top'),
    load('vis_search_none'),
  ]);

  const k = kpi.ok ? kpi.data[0] : undefined;
  const visitorsCur = num(k?.visitors_cur);
  const viewsCur = num(k?.views_cur);
  const lastDay = typeof k?.last_day === 'string' && k.last_day > '1970-01-01' ? k.last_day : null;
  const fn = funnel.ok ? funnel.data[0] : undefined;
  const steps = buildFunnel({ view: num(fn?.view_n), cart: num(fn?.cart_n), chk: num(fn?.chk_n), buy: num(fn?.buy_n) });
  const conv = pct(num(fn?.buy_n), num(fn?.view_n));

  const days = eachDay(f.from, f.to);
  const specs = (rows: Raw[]): ChartSpec[] =>
    (['visitors_n', 'views_n'] as const).map((field): ChartSpec => {
      const series = dailySeries(rows, days, field);
      const codes = Array.from(series.keys()).sort();
      const label = field === 'visitors_n' ? 'Pengunjung unik (perangkat)' : 'Tampilan halaman';
      return {
        id: field,
        tabLabel: field === 'visitors_n' ? 'Pengunjung' : 'Tampilan halaman',
        kind: 'line',
        fmt: 'int',
        categories: days,
        series: codes.map((c) => ({ name: codeName(c), data: series.get(c) ?? [], color: codeColor(c), marker: markerFor(codeColor(c)) })),
        summary: summarize(
          label,
          days,
          days.map((_, i) => codes.reduce((s, c) => s + (series.get(c)?.[i] ?? 0), 0)),
          'int',
          true,
          true,
        ),
      };
    });

  const ptLinks = (codes: string[], label: string, current: boolean): ReactNode => (
    <Link key={label} href={visitorHref(f, codes, f.channel)} className="btn" aria-current={current ? 'true' : undefined}>
      {label}
    </Link>
  );
  const allSelected = f.codes.length === allowed.length;
  // Toolbar periode milik halaman supermarket dipakai ulang (hanya rentang tanggal; pt/channel di URL dipertahankan).
  const periodFilters: SmFilters = { from: f.from, to: f.to, prev: f.prev, yoy: f.prev, wh: [], cat: [], catDefaulted: false };

  return (
    <>
      <PageHeader title="Pengunjung" question="Berapa yang berkunjung ke web & aplikasi, apa yang mereka lihat, dan di mana mereka berhenti?" />
      <SmFilterForm
        path="/visitors"
        filters={periodFilters}
        yesterday={wibYesterday(now)}
        outlets={[]}
        categories={[]}
        show={{ period: true, outlets: false, category: false }}
        adjusted={adjusted}
      />
      {allowed.length > 1 ? (
        <nav aria-label="PT" className="tabs-links">
          {ptLinks([], 'Semua PT', allSelected)}
          {allowed.map((c) => ptLinks([c], codeName(c), !allSelected && f.codes.length === 1 && f.codes[0] === c))}
        </nav>
      ) : null}
      <nav aria-label="Kanal" className="tabs-links">
        {CHANNELS.map((c) => (
          <Link key={c.v || 'all'} href={visitorHref(f, allSelected ? [] : f.codes, c.v)} className="btn" aria-current={f.channel === c.v ? 'true' : undefined}>
            {c.label}
          </Link>
        ))}
      </nav>
      <p className="note-block" role="note">
        Mencakup semua kunjungan web dan aplikasi, termasuk pengunjung anonim tanpa persetujuan pelacakan (keputusan pemilik proyek 23 Nov 2026).
        Tanpa nama, nomor HP, IP, atau URL lengkap. Satuan pengunjung = perangkat per hari (satu perangkat yang datang dua hari terhitung dua).
        Produk dan kata kunci hanya tampil bila dilihat/dicari minimal {K_ANON} perangkat. Angka pembelian hanya berasal dari member yang
        menyetujui pelacakan (pengunjung anonim tidak pernah tercatat membeli), sehingga konversi di sini batas bawah. Periode {fmtRange(f.from, f.to)}
        {lastDay ? `, data terakhir ${fmtDate(lastDay)}` : ''}.
      </p>

      {kpi.ok ? (
        <KpiRow>
          <KpiTile
            label="Pengunjung (perangkat-hari)"
            value={visitorsCur}
            fmt="int"
            delta={{ value: pctChange(visitorsCur, num(k?.visitors_prev)), kind: 'pct', goodWhen: 'up' }}
            info="Jumlah perangkat berbeda per hari, dijumlahkan sepanjang periode."
          />
          <KpiTile
            label="Tampilan halaman"
            value={viewsCur}
            fmt="int"
            delta={{ value: pctChange(viewsCur, num(k?.views_prev)), kind: 'pct', goodWhen: 'up' }}
          />
          <KpiTile
            label="Halaman per pengunjung"
            value={visitorsCur > 0 ? Math.round((viewsCur / visitorsCur) * 10) / 10 : null}
            fmt="num"
          />
          <KpiTile
            label="Konversi lihat produk ke beli"
            value={funnel.ok && num(fn?.view_n) >= K_ANON ? conv : null}
            fmt="pct"
            info="Perangkat yang membeli dibagi perangkat yang melihat produk (per hari, per outlet)."
          />
        </KpiRow>
      ) : (
        <SmErrorInline refId={kpi.ref} reason={kpi.reason} />
      )}

      <div className="sm-grid">
        {daily.ok ? (
          <ChartCard title="Kunjungan per hari" subtitle="Per PT" specs={specs(daily.data)} wide emptyText="Belum ada kunjungan pada periode ini." />
        ) : (
          <Card id="v1" title="Kunjungan per hari" result={daily} wide>
            {() => null}
          </Card>
        )}

        <Card id="v2" title="Halaman paling sering dikunjungi" result={pages}>
          {(rows) =>
            rows.length === 0 ? (
              <Empty text="Belum ada tampilan halaman." />
            ) : (
              <SmTable
                caption="Halaman paling sering dikunjungi"
                rows={rows}
                rowKey={(r) => String(r.page)}
                columns={[
                  { header: 'Halaman', cell: (r) => pageLabel(String(r.page)), text: (r) => pageLabel(String(r.page)) },
                  { header: 'Tampilan', align: 'right', cell: (r) => fmtInt(num(r.views_n)) },
                  { header: 'Pengunjung (perangkat-hari)', align: 'right', cell: (r) => fmtInt(num(r.visitors_n)) },
                ]}
              />
            )
          }
        </Card>

        <Card id="v3" title="Funnel lihat produk sampai beli" result={funnel}>
          {(rows) =>
            num(rows[0]?.view_n) === 0 ? (
              <Empty text="Belum ada event produk pada periode ini." />
            ) : (
              <>
                <FunnelBars steps={steps} />
                <p className="muted small">Satuan = perangkat-hari per outlet. Tahap di bawah {K_ANON} perangkat ditampilkan &quot;&lt; {K_ANON}&quot;.</p>
              </>
            )
          }
        </Card>

        <Card id="v4" title="Produk paling dilihat vs dibeli" result={products} wide>
          {(rows) =>
            rows.length === 0 ? (
              <Empty text={`Belum ada produk yang dilihat minimal ${K_ANON} perangkat.`} />
            ) : (
              <SmTable
                caption="Produk paling dilihat vs dibeli"
                rows={rows.map(toProductConversion)}
                rowKey={(r) => String(r.productId)}
                columns={[
                  { header: 'Produk', cell: (r) => r.name, text: (r) => r.name },
                  { header: 'PT', cell: (r) => codeName(r.code), text: (r) => codeName(r.code), filter: allowed.length > 1 },
                  { header: 'Dilihat', align: 'right', cell: (r) => fmtInt(r.views) },
                  { header: 'Perangkat melihat', align: 'right', cell: (r) => fmtInt(r.viewers) },
                  { header: 'Masuk keranjang', align: 'right', cell: (r) => fmtInt(r.carts) },
                  { header: 'Dibeli', align: 'right', cell: (r) => fmtInt(r.buys) },
                  { header: 'Konversi', align: 'right', cell: (r) => (r.conversionPct === null ? '-' : fmtPct(r.conversionPct)) },
                ]}
              />
            )
          }
        </Card>

        <Card id="v5" title="Pencarian teratas" result={searchTop}>
          {(rows) =>
            rows.length === 0 ? (
              <Empty text={`Belum ada kata kunci yang dicari minimal ${K_ANON} perangkat.`} />
            ) : (
              <SmTable
                caption="Pencarian teratas"
                rows={rows}
                rowKey={(r) => String(r.query)}
                columns={[
                  { header: 'Kata kunci', cell: (r) => String(r.query), text: (r) => String(r.query) },
                  { header: 'Pencarian', align: 'right', cell: (r) => fmtInt(num(r.searches_n)) },
                  { header: 'Tanpa hasil', align: 'right', cell: (r) => fmtInt(num(r.no_result_n)) },
                ]}
              />
            )
          }
        </Card>

        <Card id="v6" title="Pencarian tanpa hasil" result={searchNone}>
          {(rows) =>
            rows.length === 0 ? (
              <Empty text="Tidak ada kata kunci tanpa hasil yang memenuhi ambang anonimitas." />
            ) : (
              <>
                <p className="muted small">Kandidat produk yang dicari tetapi belum ada di katalog online.</p>
                <SmTable
                  caption="Pencarian tanpa hasil"
                  rows={rows}
                  rowKey={(r) => String(r.query)}
                  columns={[
                    { header: 'Kata kunci', cell: (r) => String(r.query), text: (r) => String(r.query) },
                    { header: 'Tanpa hasil', align: 'right', cell: (r) => fmtInt(num(r.no_result_n)) },
                    { header: 'Total pencarian', align: 'right', cell: (r) => fmtInt(num(r.searches_n)) },
                  ]}
                />
              </>
            )
          }
        </Card>
      </div>
    </>
  );
}
