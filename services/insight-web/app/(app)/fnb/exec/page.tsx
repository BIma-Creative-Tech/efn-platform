import type { Metadata } from 'next';
import { Block } from '@/components/blocks';
import { ChartCard } from '@/components/ChartCard';
import { FilterNotice } from '@/components/FilterNotice';
import { SmTable, type SmColumn } from '@/components/sm/SmTable';
import { EmptyState, KpiRow, KpiTile, PageHeader, Section } from '@/components/ui';
import { codeColor, codeName, markerFor } from '@/lib/brands';
import { summarize, type ChartSpec } from '@/lib/chart-spec';
import { fnbRangePage, type FnbSearchParams } from '@/lib/fnb-data';
import { growthPct, pctOfSafe, yoyStatus, type CoverageFirstDay } from '@/lib/fnb-vm';
import { fmtDelta, fmtIdr, fmtInt, fmtMonth, fmtPct, fmtRange } from '@/lib/format';
import { num, numOrNull } from '@/lib/loaders';
import { ppDelta } from '@/lib/vm';

export const dynamic = 'force-dynamic';
export const metadata: Metadata = { title: 'Eksekutif F&B' };

interface KpiRaw {
  net_cur: number; net_yoy: number; cogs_cur: number; cogs_yoy: number; gm_cur: number; gm_yoy: number; trx_cur: number; trx_yoy: number;
}
interface MonthlyRow { month: string; company_code: string; net_sum: number; cogs_sum: number; gm: number }
interface PtRow { brand: string; company_code: string; net_sum: number; cogs_sum: number; gm: number; gm_pct: number | null; trx: number; net_yoy: number }
interface MdrRow { company_code: string; method: string; amount_sum: number; mdr_sum: number; mdr_pct: number | null }
interface RankRow { brand: string; company_code: string; branch_id: number; branch_label: string; net_sum: number; gm: number; gm_pct: number | null; net_yoy: number; growth_pct: number | null }

const pctText = (v: number | null): string => (v === null ? 'n/a' : fmtPct(v));
const growthText = (v: number | null): string => (v === null ? 'n/a' : fmtDelta(v, 'pct'));

export default async function FnbExecPage({ searchParams }: { searchParams: FnbSearchParams }) {
  const { ctx, adjusted, yoy, q } = await fnbRangePage('exec', searchParams);
  const { from, to } = ctx.filters;

  const [kpiR, monthlyR, ptR, mdrR, rankR, covR] = await Promise.all([
    q<KpiRaw>('fexec_kpi'),
    q<MonthlyRow>('fexec_margin_monthly'),
    q<PtRow>('fexec_pt_compare'),
    q<MdrRow>('fexec_mdr_cost'),
    q<RankRow>('fexec_branch_rank'),
    q<CoverageFirstDay>('fexec_yoy_coverage'),
  ]);
  const yoyS = yoyStatus(covR.ok ? covR.data : null, yoy.from);

  const ptCols: SmColumn<PtRow>[] = [
    { header: 'Brand / PT', cell: (r) => codeName(r.company_code), text: (r) => codeName(r.company_code) },
    { header: 'Penjualan bersih', align: 'right', cell: (r) => fmtIdr(num(r.net_sum)) },
    { header: 'HPP', align: 'right', cell: (r) => fmtIdr(num(r.cogs_sum)) },
    { header: 'Margin kotor', align: 'right', cell: (r) => fmtIdr(num(r.gm)) },
    { header: 'Margin kotor (%)', align: 'right', cell: (r) => pctText(numOrNull(r.gm_pct)) },
    { header: 'Transaksi', align: 'right', cell: (r) => fmtInt(num(r.trx)) },
    { header: 'Penjualan vs tahun lalu', align: 'right', cell: (r) => growthText(yoyS.forCode(r.company_code) ? growthPct(num(r.net_sum), num(r.net_yoy)) : null) },
  ];

  const rankCols: SmColumn<RankRow>[] = [
    { header: 'Cabang', cell: (r) => r.branch_label, text: (r) => r.branch_label },
    { header: 'Brand / PT', cell: (r) => codeName(r.company_code), text: (r) => codeName(r.company_code), filter: true },
    { header: 'Penjualan bersih', align: 'right', cell: (r) => fmtIdr(num(r.net_sum)) },
    { header: 'Margin kotor', align: 'right', cell: (r) => fmtIdr(num(r.gm)) },
    { header: 'Margin kotor (%)', align: 'right', cell: (r) => pctText(numOrNull(r.gm_pct)) },
    { header: 'Penjualan vs tahun lalu', align: 'right', cell: (r) => growthText(yoyS.forCode(r.company_code) ? numOrNull(r.growth_pct) : null) },
  ];

  const mdrCols: SmColumn<MdrRow>[] = [
    { header: 'Brand / PT', cell: (r) => codeName(r.company_code), text: (r) => codeName(r.company_code), filter: true },
    { header: 'Metode bayar', cell: (r) => r.method, text: (r) => r.method },
    { header: 'Nilai transaksi', align: 'right', cell: (r) => fmtIdr(num(r.amount_sum)) },
    { header: 'Biaya MDR', align: 'right', cell: (r) => fmtIdr(num(r.mdr_sum)) },
    { header: 'MDR (% dari nilai)', align: 'right', cell: (r) => pctText(numOrNull(r.mdr_pct)) },
  ];

  return (
    <>
      <PageHeader
        title="Eksekutif F&B"
        question="Bagaimana margin kotor, pertumbuhan dibanding tahun lalu, dan biaya pembayaran tiap brand dan cabang?"
        actions={<span className="persona-chip">Direksi · F&B</span>}
      />
      <FilterNotice adjusted={adjusted} />
      <p className="muted small">
        Periode: {fmtRange(from, to)}. Pembanding tahun lalu: {fmtRange(yoy.from, yoy.to)}. Basis tanpa pajak (sebelum PB1/service). Hanya brand F&B;
        supermarket ada di menu Supermarket.
      </p>
      {yoyS.note ? (
        <p className="notice" role="status">
          {yoyS.note}
        </p>
      ) : null}

      <Section title="Indikator utama" id="kpi">
        <Block result={kpiR} title="Indikator utama">
          {(rows) => {
            const k = rows[0];
            if (!k || (num(k.net_cur) === 0 && num(k.trx_cur) === 0)) {
              return <EmptyState title="Tidak ada penjualan pada periode ini." reason="Ubah periode atau pilihan brand/cabang di filter." />;
            }
            const net = num(k.net_cur);
            const gm = num(k.gm_cur);
            const mC = pctOfSafe(gm, net);
            const mY = pctOfSafe(num(k.gm_yoy), num(k.net_yoy));
            const d = (cur: number, prev: number, goodWhen: 'up' | 'down' | 'neutral') => ({
              value: yoyS.available ? growthPct(cur, prev) : null,
              kind: 'pct' as const,
              goodWhen,
              compareLabel: 'tahun lalu',
            });
            return (
              <KpiRow>
                <KpiTile label="Penjualan bersih" value={net} fmt="idr-short" note="Sebelum PB1/service" delta={d(net, num(k.net_yoy), 'up')} />
                <KpiTile label="HPP" value={num(k.cogs_cur)} fmt="idr-short" delta={d(num(k.cogs_cur), num(k.cogs_yoy), 'neutral')} />
                <KpiTile
                  label="Margin kotor"
                  value={gm}
                  fmt="idr-short"
                  delta={d(gm, num(k.gm_yoy), 'up')}
                  info="Margin kotor = penjualan bersih tanpa pajak dikurangi HPP dari ESB."
                />
                <KpiTile
                  label="Margin kotor (%)"
                  value={mC}
                  fmt="pct"
                  delta={{ value: yoyS.available ? ppDelta(mC, mY) : null, kind: 'pp', goodWhen: 'up', compareLabel: 'tahun lalu' }}
                  info="Margin kotor dibagi penjualan bersih."
                />
                <KpiTile label="Transaksi" value={num(k.trx_cur)} fmt="int" delta={d(num(k.trx_cur), num(k.trx_yoy), 'up')} />
              </KpiRow>
            );
          }}
        </Block>
      </Section>

      <Section title="Margin kotor per bulan" id="margin">
        <Block result={monthlyR} title="Margin kotor per bulan">
          {(rows) => {
            if (rows.length === 0) return <EmptyState title="Belum ada data penjualan 13 bulan terakhir." />;
            const months = Array.from(new Set(rows.map((r) => String(r.month)))).sort();
            const codes = Array.from(new Set(rows.map((r) => r.company_code))).sort();
            const by = new Map(rows.map((r) => [`${String(r.month)}|${r.company_code}`, r]));
            const cats = months.map(fmtMonth);
            const series = (kind: 'gm' | 'pct') =>
              codes.map((c) => {
                const color = codeColor(c);
                return {
                  name: codeName(c),
                  color,
                  marker: markerFor(color),
                  data: months.map((m) => {
                    const r = by.get(`${m}|${c}`);
                    if (!r) return null;
                    return kind === 'gm' ? num(r.gm) : pctOfSafe(num(r.gm), num(r.net_sum));
                  }),
                };
              });
            const totals = months.map((m) => codes.reduce((s, c) => s + num(by.get(`${m}|${c}`)?.gm), 0));
            const specs: ChartSpec[] = [
              {
                id: 'gm',
                tabLabel: 'Margin kotor (Rp)',
                kind: 'line',
                fmt: 'idr',
                axisFmt: 'idr-short',
                categories: cats,
                series: series('gm'),
                summary: summarize('Margin kotor bulanan semua brand', cats, totals, 'idr-short', false),
              },
              {
                id: 'pct',
                tabLabel: 'Margin kotor (%)',
                kind: 'line',
                fmt: 'pct',
                categories: cats,
                series: series('pct'),
                summary: `Margin kotor persen per bulan untuk ${codes.length} brand/PT; lihat tabel untuk nilai tiap bulan.`,
              },
            ];
            return <ChartCard title="Margin kotor 13 bulan terakhir" subtitle="Per brand/PT; bulan berjalan belum penuh" specs={specs} wide />;
          }}
        </Block>
      </Section>

      <Section title="Perbandingan brand / PT" id="pt">
        <Block result={ptR} title="Perbandingan brand / PT">
          {(rows) =>
            rows.length === 0 ? (
              <EmptyState title="Tidak ada penjualan pada periode ini." />
            ) : (
              <SmTable caption="Perbandingan brand dan PT menurut margin kotor" columns={ptCols} rows={rows} rowKey={(r) => `${r.brand}:${r.company_code}`} />
            )
          }
        </Block>
      </Section>

      <Section title="Biaya MDR per metode bayar" id="mdr">
        <p className="muted small">MDR = biaya yang dipotong penyedia pembayaran (kartu, QRIS, dompet digital); angka dari data pembayaran ESB.</p>
        <Block result={mdrR} title="Biaya MDR">
          {(rows) => {
            if (rows.length === 0) return <EmptyState title="Tidak ada data pembayaran pada periode ini." />;
            const total = rows.reduce((s, r) => s + num(r.mdr_sum), 0);
            return (
              <>
                <p>
                  Total biaya MDR periode ini: <strong>{fmtIdr(total)}</strong>
                </p>
                <SmTable caption="Biaya MDR per brand dan metode bayar" columns={mdrCols} rows={rows} rowKey={(r) => `${r.company_code}:${r.method}`} />
              </>
            );
          }}
        </Block>
      </Section>

      <Section title="Peringkat cabang menurut margin kotor" id="rank">
        <Block result={rankR} title="Peringkat cabang">
          {(rows) =>
            rows.length === 0 ? (
              <EmptyState title="Tidak ada penjualan cabang pada periode ini." />
            ) : (
              <SmTable caption="Peringkat cabang menurut margin kotor (tertinggi di atas)" columns={rankCols} rows={rows} rowKey={(r) => `${r.brand}:${r.branch_id}`} />
            )
          }
        </Block>
      </Section>
    </>
  );
}
