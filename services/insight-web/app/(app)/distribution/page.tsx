import type { Metadata } from 'next';
import { Block } from '@/components/blocks';
import { ChartCard } from '@/components/ChartCard';
import { DataTable, type TableRow } from '@/components/DataTable';
import { FilterNotice } from '@/components/FilterNotice';
import { KpiRow, KpiTile, PageHeader, Section } from '@/components/ui';
import { summarize, type ChartSpec } from '@/lib/chart-spec';
import { fmtDayShort, fmtRange } from '@/lib/format';
import { num, run, settle, TABLE_LOADERS } from '@/lib/loaders';
import { pageCtx, type SearchParams } from '@/lib/page-ctx';
import { TABLES } from '@/lib/table-defs';
import { gapStatus } from '@/lib/thresholds';
import { pctDelta, pctOf } from '@/lib/vm';

export const dynamic = 'force-dynamic';
export const metadata: Metadata = { title: 'Distribusi' };

interface Kpi {
  ck_out_cur: number; ck_out_prev: number; in_ck_cur: number; in_ck_prev: number;
  st_out_cur: number; st_out_prev: number; in_other_cur: number; in_other_prev: number;
}

export default async function DistributionPage({ searchParams }: { searchParams: SearchParams }) {
  const { ctx, adjusted } = await pageCtx(searchParams);
  const { from, to } = ctx.filters;

  const [kpiR, delR, matR, topR, gapR] = await Promise.all([
    settle(run<Kpi>('dist_kpi', ctx)),
    settle(run<{ day: string; ck_out_qty: number; in_ck_qty: number; in_other_qty: number }>('dist_ck_delivery', ctx)),
    settle(TABLE_LOADERS['dist-matrix'](ctx)),
    settle(run<{ product_label: string; brand: string; product_code: string; moved: number }>('dist_transfer_top_products', ctx)),
    settle(TABLE_LOADERS['dist-gap'](ctx)),
  ]);

  return (
    <>
      <PageHeader title="Distribusi" question="Barang bergerak dari CK ke toko dan antar toko seperti seharusnya?" />
      <FilterNotice adjusted={adjusted} />
      <p className="muted small">
        Periode: {fmtRange(from, to)}. Hanya produk FIN. Pengiriman dicatat di cabang CK dan penerimaan di toko tujuan; selisih bermakna per produk dan hari,
        bukan per cabang. Peta asal-tujuan belum tersedia.
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
                <KpiTile label="Dikirim CK" value={num(k.ck_out_cur)} fmt="num" delta={{ value: pctDelta(num(k.ck_out_cur), num(k.ck_out_prev)), kind: 'pct', goodWhen: 'neutral' }} />
                <KpiTile label="Diterima dari CK" value={num(k.in_ck_cur)} fmt="num" delta={{ value: pctDelta(num(k.in_ck_cur), num(k.in_ck_prev)), kind: 'pct', goodWhen: 'neutral' }} />
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
                    drill: { param: 'branch', values: used.map((r) => String(r.branch_key)), label: 'cabang' },
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
    </>
  );
}
