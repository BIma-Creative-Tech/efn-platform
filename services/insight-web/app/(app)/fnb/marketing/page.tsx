import type { Metadata } from 'next';
import { Block } from '@/components/blocks';
import { ChartCard } from '@/components/ChartCard';
import { FilterNotice } from '@/components/FilterNotice';
import { SmTable, type SmColumn } from '@/components/sm/SmTable';
import { EmptyState, KpiRow, KpiTile, PageHeader, Section } from '@/components/ui';
import { brandName, codeName } from '@/lib/brands';
import { CHANNEL_LABEL, classifyChannel, groupChannels } from '@/lib/channels';
import { summarize, type ChartSpec } from '@/lib/chart-spec';
import { eachDay } from '@/lib/dates';
import { fnbRangePage, type FnbSearchParams } from '@/lib/fnb-data';
import { pctOfSafe } from '@/lib/fnb-vm';
import { fmtDayShort, fmtIdr, fmtInt, fmtPct, fmtQty, fmtRange } from '@/lib/format';
import { num, numOrNull } from '@/lib/loaders';
import { discountStatus, T } from '@/lib/thresholds';
import { pctDelta, ppDelta } from '@/lib/vm';

export const dynamic = 'force-dynamic';
export const metadata: Metadata = { title: 'Marketing F&B' };

interface KpiRaw {
  gross_cur: number; gross_prev: number; discount_cur: number; discount_prev: number; net_cur: number; net_prev: number;
  disc_pct: number | null; disc_pct_prev: number | null;
}
interface TrendRow { sales_date: string; gross_sum: number; discount_sum: number }
interface MenuRow { brand: string; menu_code: string; menu_label: string; category: string; qty_sum: number; gross_sum: number; discount_sum: number; disc_pct: number | null }
interface BranchRow { brand: string; company_code: string; branch_id: number; branch_label: string; gross_sum: number; discount_sum: number; disc_pct: number | null }
interface ChannelRaw { payment_type: string; method: string; cnt: number; amount_sum: number }
interface TakeawayRow { is_takeaway: number; qty_sum: number; net_sum: number }

const pctText = (v: number | null): string => (v === null ? 'n/a' : fmtPct(v));

export default async function FnbMarketingPage({ searchParams }: { searchParams: FnbSearchParams }) {
  const { ctx, adjusted, q } = await fnbRangePage('marketing', searchParams);
  const { from, to } = ctx.filters;

  const [kpiR, trendR, menuR, branchR, chanR, takeR] = await Promise.all([
    q<KpiRaw>('fmkt_discount_kpi'),
    q<TrendRow>('fmkt_discount_trend'),
    q<MenuRow>('fmkt_discount_menu'),
    q<BranchRow>('fmkt_discount_branch'),
    q<ChannelRaw>('fmkt_channel_mix'),
    q<TakeawayRow>('fmkt_takeaway_share'),
  ]);

  const menuCols: SmColumn<MenuRow>[] = [
    { header: 'Menu', cell: (r) => r.menu_label, text: (r) => r.menu_label },
    { header: 'Brand', cell: (r) => brandName(r.brand), text: (r) => brandName(r.brand), filter: true },
    { header: 'Kategori', cell: (r) => (r.category === '' ? 'Tanpa kategori' : r.category), text: (r) => (r.category === '' ? 'Tanpa kategori' : r.category), filter: true },
    { header: 'Qty', align: 'right', cell: (r) => fmtQty(num(r.qty_sum)) },
    { header: 'Penjualan kotor', align: 'right', cell: (r) => fmtIdr(num(r.gross_sum)) },
    { header: 'Diskon', align: 'right', cell: (r) => fmtIdr(num(r.discount_sum)) },
    { header: 'Diskon (% dari kotor)', align: 'right', cell: (r) => pctText(numOrNull(r.disc_pct)) },
  ];
  const branchCols: SmColumn<BranchRow>[] = [
    { header: 'Cabang', cell: (r) => r.branch_label, text: (r) => r.branch_label },
    { header: 'Brand / PT', cell: (r) => codeName(r.company_code), text: (r) => codeName(r.company_code), filter: true },
    { header: 'Penjualan kotor', align: 'right', cell: (r) => fmtIdr(num(r.gross_sum)) },
    { header: 'Diskon', align: 'right', cell: (r) => fmtIdr(num(r.discount_sum)) },
    { header: 'Diskon (% dari kotor)', align: 'right', cell: (r) => pctText(numOrNull(r.disc_pct)) },
  ];

  return (
    <>
      <PageHeader
        title="Marketing F&B"
        question="Seberapa besar diskon yang diberikan, lewat kanal pembayaran apa pelanggan membeli, dan berapa porsi takeaway?"
        actions={<span className="persona-chip">Marketing · F&B</span>}
      />
      <FilterNotice adjusted={adjusted} />
      <p className="muted small">
        Periode: {fmtRange(from, to)}. Basis sebelum pajak (PB1/service). Data ESB tidak memuat data member atau segmen pelanggan, jadi analisis di sini
        berbasis transaksi.
      </p>

      <Section title="Diskon" id="kpi">
        <Block result={kpiR} title="Diskon">
          {(rows) => {
            const k = rows[0];
            if (!k || (num(k.gross_cur) === 0 && num(k.net_cur) === 0)) {
              return <EmptyState title="Tidak ada penjualan pada periode ini." reason="Ubah periode atau pilihan brand/cabang di filter." />;
            }
            const dp = numOrNull(k.disc_pct);
            return (
              <KpiRow>
                <KpiTile label="Diskon" value={num(k.discount_cur)} fmt="idr-short" delta={{ value: pctDelta(num(k.discount_cur), num(k.discount_prev)), kind: 'pct', goodWhen: 'neutral' }} />
                <KpiTile
                  label="Diskon (% dari penjualan kotor)"
                  value={dp}
                  fmt="pct"
                  status={discountStatus(dp)}
                  delta={{ value: ppDelta(dp, numOrNull(k.disc_pct_prev)), kind: 'pp', goodWhen: 'neutral' }}
                  info={`Peringatan bila diskon > ${T.discountWarnPct}% dari penjualan kotor (ambang konfigurasi).`}
                />
                <KpiTile label="Penjualan kotor" value={num(k.gross_cur)} fmt="idr-short" delta={{ value: pctDelta(num(k.gross_cur), num(k.gross_prev)), kind: 'pct', goodWhen: 'up' }} />
                <KpiTile label="Penjualan bersih" value={num(k.net_cur)} fmt="idr-short" note="Setelah diskon" delta={{ value: pctDelta(num(k.net_cur), num(k.net_prev)), kind: 'pct', goodWhen: 'up' }} />
              </KpiRow>
            );
          }}
        </Block>
      </Section>

      <Section title="Tren diskon harian" id="trend">
        <Block result={trendR} title="Tren diskon harian">
          {(rows) => {
            if (rows.length === 0) return <EmptyState title="Tidak ada penjualan pada periode ini." />;
            const days = eachDay(from, to);
            const by = new Map(rows.map((r) => [String(r.sales_date), r]));
            const cats = days.map(fmtDayShort);
            const disc = days.map((d) => (by.has(d) ? num(by.get(d)?.discount_sum) : null));
            const pct = days.map((d) => {
              const r = by.get(d);
              return r ? pctOfSafe(num(r.discount_sum), num(r.gross_sum)) : null;
            });
            const specs: ChartSpec[] = [
              {
                id: 'disc',
                tabLabel: 'Diskon (Rp)',
                kind: 'bar',
                fmt: 'idr',
                axisFmt: 'idr-short',
                categories: cats,
                series: [{ name: 'Diskon', data: disc, color: 'c1' }],
                summary: summarize('Diskon harian', days, disc, 'idr-short', true, true),
              },
              {
                id: 'pct',
                tabLabel: 'Diskon (% dari kotor)',
                kind: 'line',
                fmt: 'pct',
                categories: cats,
                series: [{ name: 'Diskon % dari penjualan kotor', data: pct, color: 'c3' }],
                summary: summarize('Diskon persen harian', days, pct, 'pct', false, true),
              },
            ];
            return <ChartCard title="Diskon per hari" subtitle="Hari tanpa data ditampilkan kosong, bukan nol" specs={specs} wide />;
          }}
        </Block>
      </Section>

      <Section title="Diskon per menu" id="menu">
        <p className="muted small">Hanya menu yang menerima diskon; 100 teratas menurut nilai diskon.</p>
        <Block result={menuR} title="Diskon per menu">
          {(rows) =>
            rows.length === 0 ? (
              <EmptyState title="Tidak ada diskon pada periode ini." tone="positive" />
            ) : (
              <SmTable caption="Diskon per menu (terbesar di atas)" columns={menuCols} rows={rows} rowKey={(r) => `${r.brand}:${r.menu_code}`} />
            )
          }
        </Block>
      </Section>

      <Section title="Diskon per cabang" id="branch">
        <Block result={branchR} title="Diskon per cabang">
          {(rows) =>
            rows.length === 0 ? (
              <EmptyState title="Tidak ada penjualan cabang pada periode ini." />
            ) : (
              <SmTable caption="Diskon per cabang (terbesar di atas)" columns={branchCols} rows={rows} rowKey={(r) => `${r.brand}:${r.branch_id}`} />
            )
          }
        </Block>
      </Section>

      <Section title="Kanal pembayaran" id="channel">
        <p className="muted small">
          Kanal adalah perkiraan: ESB tidak punya kolom kanal, jadi dikelompokkan dari nama metode bayar (mis. GoFood, GrabFood, QRIS). Nama yang tidak
          dikenali masuk &quot;Lainnya&quot;.
        </p>
        <Block result={chanR} title="Kanal pembayaran">
          {(rows) => {
            if (rows.length === 0) return <EmptyState title="Tidak ada data pembayaran pada periode ini." />;
            const groups = groupChannels(rows);
            const cats = groups.map((g) => g.label);
            const vals = groups.map((g) => g.amount);
            const detailCols: SmColumn<ChannelRaw>[] = [
              { header: 'Metode bayar', cell: (r) => r.method, text: (r) => r.method },
              { header: 'Kanal (perkiraan)', cell: (r) => CHANNEL_LABEL[classifyChannel(r.payment_type, r.method)], text: (r) => CHANNEL_LABEL[classifyChannel(r.payment_type, r.method)], filter: true },
              { header: 'Jumlah pembayaran', align: 'right', cell: (r) => fmtInt(num(r.cnt)) },
              { header: 'Nilai', align: 'right', cell: (r) => fmtIdr(num(r.amount_sum)) },
            ];
            return (
              <>
                <ChartCard
                  title="Nilai pembayaran per kanal (perkiraan)"
                  specs={[{ id: 'channel', kind: 'barH', fmt: 'idr', axisFmt: 'idr-short', categories: cats, series: [{ name: 'Nilai pembayaran', data: vals, color: 'c1' }], summary: summarize('Nilai pembayaran per kanal', cats, vals, 'idr-short', true) }]}
                />
                <SmTable
                  caption="Ringkasan per kanal (perkiraan)"
                  columns={[
                    { header: 'Kanal (perkiraan)', cell: (g) => g.label },
                    { header: 'Jumlah pembayaran', align: 'right', cell: (g) => fmtInt(g.cnt) },
                    { header: 'Nilai', align: 'right', cell: (g) => fmtIdr(g.amount) },
                    { header: 'Porsi', align: 'right', cell: (g) => pctText(g.sharePct) },
                  ]}
                  rows={groups}
                  rowKey={(g) => g.id}
                />
                <SmTable caption="Rincian per metode bayar" columns={detailCols} rows={rows} rowKey={(r) => `${r.payment_type}:${r.method}`} />
              </>
            );
          }}
        </Block>
      </Section>

      <Section title="Takeaway vs dine-in (perkiraan)" id="takeaway">
        <p className="muted small">Perkiraan: ditebak dari nama menu ESB, bukan dari jenis pesanan sebenarnya. Gunakan sebagai arah, bukan angka pasti.</p>
        <Block result={takeR} title="Takeaway vs dine-in">
          {(rows) => {
            const tk = rows.find((r) => num(r.is_takeaway) === 1);
            const di = rows.find((r) => num(r.is_takeaway) === 0);
            const tkNet = num(tk?.net_sum);
            const diNet = num(di?.net_sum);
            const total = tkNet + diNet;
            if (rows.length === 0 || total <= 0) return <EmptyState title="Tidak ada penjualan pada periode ini." />;
            const cats = ['Takeaway (perkiraan)', 'Dine-in (perkiraan)'];
            const vals = [pctOfSafe(tkNet, total), pctOfSafe(diNet, total)];
            return (
              <>
                <ChartCard
                  title="Porsi penjualan bersih (perkiraan)"
                  subtitle="Takeaway = perkiraan dari nama menu"
                  specs={[{ id: 'tk', kind: 'bar', fmt: 'pct', axisFmt: 'pct', categories: cats, series: [{ name: 'Porsi penjualan bersih', data: vals, color: 'c2' }], summary: summarize('Porsi takeaway vs dine-in (perkiraan)', cats, vals, 'pct', false) }]}
                />
                <SmTable
                  caption="Takeaway vs dine-in (perkiraan)"
                  columns={[
                    { header: 'Jenis (perkiraan)', cell: (r) => r.label },
                    { header: 'Qty', align: 'right', cell: (r) => fmtQty(r.qty) },
                    { header: 'Penjualan bersih', align: 'right', cell: (r) => fmtIdr(r.net) },
                    { header: 'Porsi', align: 'right', cell: (r) => pctText(r.share) },
                  ]}
                  rows={[
                    { key: 'tk', label: 'Takeaway (perkiraan)', qty: num(tk?.qty_sum), net: tkNet, share: vals[0] },
                    { key: 'di', label: 'Dine-in (perkiraan)', qty: num(di?.qty_sum), net: diNet, share: vals[1] },
                  ]}
                  rowKey={(r) => r.key}
                />
              </>
            );
          }}
        </Block>
      </Section>
    </>
  );
}
