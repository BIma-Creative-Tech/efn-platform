import type { Metadata } from 'next';
import { Block } from '@/components/blocks';
import { ChartCard } from '@/components/ChartCard';
import { CkMovementCard, type GroupRow } from '@/components/CkMovementCard';
import { CoverageNotice } from '@/components/CoverageNotice';
import { DataTable, type TableRow } from '@/components/DataTable';
import { FilterNotice } from '@/components/FilterNotice';
import { SellThroughSection } from '@/components/oms/SellThroughSection';
import { MixedScopeNote, NotApplicableSmd } from '@/components/SmdNotice';
import { InfoTip, KpiRow, KpiTile, PageHeader, Section } from '@/components/ui';
import { summarize, type ChartSpec, type ColorToken } from '@/lib/chart-spec';
import { comparable, deltaGuards } from '@/lib/coverage';
import { fmtDayShort, fmtRange } from '@/lib/format';
import { loadCoverage, num, numOrNull, run, settle, TABLE_LOADERS } from '@/lib/loaders';
import type { FinHourlyRaw, FreshnessRaw, QualityRaw, SupplyRaw } from '@/lib/oms-vm';
import { pageCtx, type SearchParams } from '@/lib/page-ctx';
import { TABLES } from '@/lib/table-defs';
import { sellThroughStatus, STATUS_LABEL, T, type Status } from '@/lib/thresholds';
import { pctOf } from '@/lib/vm';

export const dynamic = 'force-dynamic';
export const metadata: Metadata = { title: 'Produksi vs Penjualan' };

interface Summary {
  ck_prod_cur: number; ck_prod_prev: number; ck_out_cur: number; ck_out_prev: number;
  received_cur: number; received_prev: number; store_prod_cur: number; st_in_cur: number; st_out_cur: number;
  sold_cur: number; sold_prev: number; supply_cur: number; supply_prev: number;
}
interface BranchRow { brand: string; branch_id: number; branch_label: string; supply: number; sold_qty: number; sell_through_pct: number | null }
interface TrendRow { period: string; ck_prod: number; store_prod: number; ck_out: number; sold: number }

const statusColor = (s: Status): ColorToken => (s === 'good' ? 'good' : s === 'warn' ? 'warn' : s === 'bad' ? 'bad' : 'muted');

export default async function FlowPage({ searchParams }: { searchParams: SearchParams }) {
  const { ctx, adjusted, sel } = await pageCtx(searchParams);
  const { from, to, product } = ctx.filters;
  const single = product.length === 1 ? product[0] : null;

  // G51 B1: halaman khusus F&B. Hanya SMD dipilih -> tanpa query ESB; campuran -> brand ESB saja + catatan.
  if (!sel.esb) {
    return (
      <>
        <PageHeader title="Produksi vs Penjualan" question="Apakah yang diproduksi dan dikirim sebanding dengan yang terjual? Di mana barang menumpuk atau kurang?" />
        <FilterNotice adjusted={adjusted} />
        <NotApplicableSmd reason="Produksi CK, produk FIN, dan sell-through adalah konsep brand F&B. Pilih brand F&B pada filter Brand / PT." />
      </>
    );
  }

  // G69: sell-through per jam hanya bermakna per outlet: query OMS hanya dijalankan bila tepat satu cabang terpilih; selain itu
  // hanya kesegaran (untuk memutuskan apakah petunjuk "pilih satu outlet" relevan, yaitu OMS aktif pada cakupan ini).
  const oneBranch = ctx.filters.branch.length === 1;
  const [sumR, brR, dailyR, weeklyR, outR, prodR, detailR, cov, groupR, catR, omsFinR, omsSupplyR, omsQualityR, omsFreshR] = await Promise.all([
    settle(run<Summary>('flow_summary', ctx)),
    settle(run<BranchRow>('flow_by_branch', ctx)),
    settle(run<TrendRow>('flow_daily_trend', ctx, { weekly: 0 })),
    settle(run<TrendRow>('flow_daily_trend', ctx, { weekly: 1 })),
    settle(TABLE_LOADERS['flow-outliers'](ctx)),
    settle(TABLE_LOADERS['flow-products'](ctx)),
    single ? settle(run<{ day: string; ck_prod: number; ck_out: number; received: number; sold: number }>('flow_product_daily', ctx, { product_key: single })) : Promise.resolve(null),
    loadCoverage(ctx),
    settle(run<GroupRow>('mov_by_group', ctx)),
    settle(TABLE_LOADERS['mov-category'](ctx)),
    // G69: sell-through per jam untuk hari terakhir periode (ESB OMS); gagal/kosong = bagian disembunyikan.
    oneBranch ? settle(run<FinHourlyRaw>('foms_fin_hourly', ctx, { ref_day: to })) : Promise.resolve(null),
    oneBranch ? settle(run<SupplyRaw>('foms_fin_supply', ctx, { ref_day: to })) : Promise.resolve(null),
    oneBranch ? settle(run<QualityRaw>('foms_fin_quality', ctx, { ref_day: to })) : Promise.resolve(null),
    oneBranch ? Promise.resolve(null) : settle(run<FreshnessRaw>('foms_freshness', ctx)),
  ]);
  // Sell-through membandingkan produksi/kirim dengan penjualan: periode lalu hanya bermakna bila produksi, pengiriman, dan penjualan FIN ada di data.
  const { pctDelta, ppDelta } = deltaGuards(comparable(cov, ['production', 'delivery', 'fin_sold'], ctx.filters, ctx.prev));

  const trendSpec = (id: string, tab: string, rows: TrendRow[]): ChartSpec => {
    const cats = rows.map((r) => fmtDayShort(String(r.period)));
    const prod = rows.map((r) => num(r.ck_prod));
    return {
      id,
      tabLabel: tab,
      kind: 'bar',
      fmt: 'num',
      categories: cats,
      series: [
        { name: 'Produksi CK', data: prod, color: 'c1' },
        { name: 'Terjual', data: rows.map((r) => num(r.sold)), color: 'c2' },
      ],
      summary: summarize('Produksi CK per periode', cats, prod, 'num', true),
    };
  };

  return (
    <>
      <PageHeader title="Produksi vs Penjualan" question="Apakah yang diproduksi dan dikirim sebanding dengan yang terjual? Di mana barang menumpuk atau kurang?" />
      <FilterNotice adjusted={adjusted} />
      {sel.smd ? <MixedScopeNote /> : null}
      <p className="notice">Berdasarkan arus barang, bukan stok: sell-through periode pendek dapat melebihi 100% atau menyesatkan karena stok awal/akhir toko tidak tersedia.</p>
      <CoverageNotice rows={cov} entities={['production', 'delivery', 'fin_sold']} range={{ from, to }} />
      <p className="muted small">
        Periode: {fmtRange(from, to)}. <strong>Hanya produk jadi (FIN, satuan EA)</strong>. CK juga mengirim barang setengah jadi (WIP); bahan baku, kemasan,
        dan supplies dikirim gudang. Lihat kartu &ldquo;Kiriman barang per asal dan kelompok&rdquo; di bawah. Kiriman FIN yang lebih besar dari produksi berasal dari stok CK sebelum periode (stok awal tidak
        tercatat di ESB).
      </p>

      <Section title="Indikator utama" id="kpi">
        <Block result={sumR} title="Indikator utama">
          {(rows) => {
            const s = rows[0];
            if (!s) return <p>Tidak ada data arus barang pada periode ini.</p>;
            const stCur = pctOf(num(s.sold_cur), num(s.supply_cur));
            const stPrev = pctOf(num(s.sold_prev), num(s.supply_prev));
            return (
              <KpiRow>
                <KpiTile label="Produksi CK (hanya FIN)" value={num(s.ck_prod_cur)} fmt="num" delta={{ value: pctDelta(num(s.ck_prod_cur), num(s.ck_prod_prev)), kind: 'pct', goodWhen: 'neutral' }} info="Jumlah (EA) hasil produksi produk jadi (FIN) di Central Kitchen." />
                <KpiTile label="Dikirim CK (hanya FIN)" value={num(s.ck_out_cur)} fmt="num" delta={{ value: pctDelta(num(s.ck_out_cur), num(s.ck_out_prev)), kind: 'pct', goodWhen: 'neutral' }} info="Hanya produk jadi (FIN). Bahan baku, WIP, kemasan, dan supplies yang dikirim CK tidak dihitung di sini." />
                <KpiTile label="Produksi toko (dari WIP/bahan baku)" value={num(s.store_prod_cur)} fmt="num" info="Produk jadi (FINS/FINK) yang dibuat toko sendiri dari WIP kiriman CK dan bahan baku kiriman gudang. Ikut pasokan toko pada sell-through." />
                <KpiTile label="Diterima toko" value={num(s.received_cur)} fmt="num" delta={{ value: pctDelta(num(s.received_cur), num(s.received_prev)), kind: 'pct', goodWhen: 'neutral' }} info="Diterima dari CK dan sumber lain." />
                <KpiTile label="Terjual" value={num(s.sold_cur)} fmt="num" delta={{ value: pctDelta(num(s.sold_cur), num(s.sold_prev)), kind: 'pct', goodWhen: 'up' }} />
                <KpiTile
                  label="Sell-through"
                  value={stCur}
                  fmt="pct"
                  status={sellThroughStatus(stCur)}
                  delta={{ value: ppDelta(stCur, stPrev), kind: 'pp', goodWhen: 'up' }}
                  info="Terjual / pasokan toko. Pasokan = diterima dari CK + sumber lain + produksi toko + transfer masuk - transfer keluar. Ambang 70% dan 85% adalah proposal (Q-INS1)."
                />
              </KpiRow>
            );
          }}
        </Block>
      </Section>

      <div className="grid-2">
        <Block result={sumR} title="Alur barang">
          {(rows) => {
            const s = rows[0];
            if (!s) return null;
            const cats = ['Produksi CK', 'Dikirim CK', 'Diterima toko', 'Produksi toko', 'Transfer masuk', 'Transfer keluar', 'Terjual'];
            const vals = [num(s.ck_prod_cur), num(s.ck_out_cur), num(s.received_cur), num(s.store_prod_cur), num(s.st_in_cur), num(s.st_out_cur), num(s.sold_cur)];
            return (
              <ChartCard
                title="Alur barang"
                subtitle="CK, kirim, produksi toko, transfer, terjual (bar bertahap)"
                specs={[{ id: 'flow', kind: 'barH', fmt: 'num', categories: cats, series: [{ name: 'Jumlah (EA)', data: vals, color: 'c1', itemColors: ['c1', 'c1', 'c1', 'c3', 'c6', 'c6', 'c2'] }], summary: summarize('Alur barang', cats, vals, 'num', false) }]}
              />
            );
          }}
        </Block>
        <Block result={brR} title="Sell-through per cabang">
          {(rows) => {
            const used = rows.filter((r) => r.sell_through_pct !== null).slice(0, 25);
            const sts = used.map((r) => sellThroughStatus(numOrNull(r.sell_through_pct)));
            const cats = used.map((r, i) => `${r.branch_label} (${STATUS_LABEL[sts[i]]})`);
            const vals = used.map((r) => num(r.sell_through_pct));
            return (
              <ChartCard
                title="Sell-through per cabang"
                subtitle="25 terendah; garis acuan 70% dan 85%"
                height={Math.max(280, used.length * 26 + 40)}
                specs={[
                  {
                    id: 'st',
                    kind: 'barH',
                    fmt: 'pct',
                    categories: cats,
                    series: [{ name: 'Sell-through', data: vals, color: 'muted', itemColors: sts.map(statusColor) }],
                    refLines: [{ value: T.sellThroughWarn, label: '70%' }, { value: T.sellThroughGood, label: '85%' }],
                    drill: { param: 'branch', values: used.map((r) => `${r.brand}:${r.branch_id}`), label: 'cabang' },
                    summary: summarize('Sell-through per cabang', cats, vals, 'pct', false),
                  },
                ]}
              />
            );
          }}
        </Block>
        <div className="wide">
          <Block result={dailyR} title="Produksi vs terjual">
            {(daily) => (
              <ChartCard
                title="Produksi CK vs terjual"
                specs={[trendSpec('d', 'Harian', daily), trendSpec('w', 'Mingguan', weeklyR.ok ? weeklyR.data : [])]}
                wide
              />
            )}
          </Block>
        </div>
      </div>

      {detailR ? (
        <Section title="Detail produk" id="detail">
          <Block result={detailR} title="Detail produk">
            {(rows) => {
              const cats = rows.map((r) => fmtDayShort(String(r.day)));
              const sold = rows.map((r) => num(r.sold));
              return (
                <ChartCard
                  title="Produksi, kirim, dan terjual harian"
                  subtitle={single ?? ''}
                  specs={[{ id: 'pd', kind: 'line', fmt: 'num', categories: cats, series: [{ name: 'Produksi CK', data: rows.map((r) => num(r.ck_prod)), color: 'c1' }, { name: 'Dikirim CK', data: rows.map((r) => num(r.ck_out)), color: 'c3' }, { name: 'Terjual', data: sold, color: 'c2' }], summary: summarize('Terjual harian', cats, sold, 'num', true) }]}
                />
              );
            }}
          </Block>
        </Section>
      ) : null}

      <SellThroughSection day={to} oneBranch={oneBranch} freshR={omsFreshR} finR={omsFinR} supplyR={omsSupplyR} qualityR={omsQualityR} />

      <Section title="Cabang dan produk" id="outliers">
        <Block result={outR} title="Tabel sell-through">
          {(rows) => (
            <DataTable
              caption={TABLES['flow-outliers'].caption}
              columns={TABLES['flow-outliers'].columns}
              rows={rows as TableRow[]}
              csvName="flow-outliers"
              defaultSort={{ key: 'sell_through_pct', dir: 'asc' }}
              quickFilter={{ label: 'Hanya yang bermasalah', statuses: ['bad', 'warn'] }}
            />
          )}
        </Block>
        {/* div, bukan p: InfoTip = <details> (konten blok) tidak sah di dalam <p> -> hydration error #418. */}
        <div className="muted small">
          Produk berlabel &ldquo;belum dipetakan&rdquo; berasal dari alias ESB tanpa pemetaan; lihat halaman Kualitas Data.{' '}
          <InfoTip label="Sell-through">Cabang CK (produksi/kirim tanpa penjualan) tampil n/a.</InfoTip>
        </div>
      </Section>

      <Section title="Per produk" id="products">
        <Block result={prodR} title="Tabel per produk">
          {(rows) => (
            <DataTable
              caption={TABLES['flow-products'].caption}
              columns={TABLES['flow-products'].columns}
              rows={rows as TableRow[]}
              csvName="flow-products"
              defaultSort={{ key: 'sold_qty', dir: 'desc' }}
            />
          )}
        </Block>
      </Section>

      <CkMovementCard groupR={groupR} catR={catR} />
    </>
  );
}
