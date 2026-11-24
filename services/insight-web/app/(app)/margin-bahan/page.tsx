/**
 * Margin bahan (/margin-bahan, G67, docs/04 §31.5; ADR-040/041 Proposed). Hanya persona Eksekutif: dicek di server lewat
 * `fnbRangePage('exec')` (lapis kedua setelah proxy.ts; selain exec -> /tidak-berwenang). Semua hitungan di lib/margin-bahan.ts.
 */
import type { Metadata } from 'next';
import type { ReactNode } from 'react';
import { ErrorCard } from '@/components/blocks';
import { ChartCard } from '@/components/ChartCard';
import { DataTable, type TableRow } from '@/components/DataTable';
import { FilterNotice } from '@/components/FilterNotice';
import { EmptyState, KpiRow, KpiTile, PageHeader, Section } from '@/components/ui';
import { codeName } from '@/lib/brands';
import { summarize, type ChartSpec, type ColorToken } from '@/lib/chart-spec';
import { fnbRangePage, type FnbSearchParams } from '@/lib/fnb-data';
import { fmtMonth } from '@/lib/format';
import { num, numOrNull, type Result } from '@/lib/loaders';
import {
  groupContributions,
  groupSeries,
  ingredientImpacts,
  kpis,
  menuCostChanges,
  MB_TOP_GROUPS,
  MB_TOP_INGREDIENTS,
  MB_TOP_MENUS,
  type MbGroupRow,
  type MbMenuRow,
  type MbPriceRow,
} from '@/lib/margin-bahan';
import type { ColumnDef } from '@/lib/table-defs';

export const dynamic = 'force-dynamic';
export const metadata: Metadata = { title: 'Margin bahan' };

type Raw = Record<string, unknown>;
const L = 'left' as const;
const R = 'right' as const;
const str = (v: unknown): string => String(v ?? '');

const toGroup = (r: Raw): MbGroupRow => ({
  brand: str(r.brand),
  company_code: str(r.company_code),
  month: str(r.month),
  ing_group: str(r.ing_group),
  basket_value: num(r.basket_value),
  base_value: num(r.base_value),
});

const toPrice = (r: Raw): MbPriceRow => ({
  brand: str(r.brand),
  company_code: str(r.company_code),
  month: str(r.month),
  product_code: str(r.product_code),
  product_name: str(r.product_name),
  ing_group: str(r.ing_group),
  uom: str(r.uom),
  idx: numOrNull(r.idx),
  usage_value: num(r.usage_value),
});

const toMenu = (r: Raw): MbMenuRow => ({
  brand: str(r.brand),
  company_code: str(r.company_code),
  month: str(r.month),
  menu_name: str(r.menu_name),
  menu_category: str(r.menu_category),
  qty: num(r.qty),
  net: num(r.net),
  cogs: num(r.cogs),
  bom_matched: num(r.bom_matched),
  cost_idx: numOrNull(r.cost_idx),
  leaf_coverage: num(r.leaf_coverage),
});

const GROUP_COLS: ColumnDef[] = [
  { key: 'ing_group', header: 'Kelompok bahan', align: L },
  { key: 'base_value', header: 'Nilai keranjang harga dasar (per bulan)', fmt: 'idr', align: R },
  { key: 'basket_value', header: 'Nilai keranjang harga terkini (per bulan)', fmt: 'idr', align: R },
  { key: 'contribution', header: 'Kontribusi (Rp per bulan)', fmt: 'idr', align: R },
  { key: 'idx', header: 'Indeks (dasar = 100)', fmt: 'num', align: R },
  { key: 'share', header: 'Porsi dari total perubahan', fmt: 'pct', align: R },
];

const ING_COLS: ColumnDef[] = [
  { key: 'product_name', header: 'Bahan', align: L },
  { key: 'ing_group', header: 'Kelompok', align: L, filterable: true },
  { key: 'company', header: 'Brand / PT', align: L, filterable: true },
  { key: 'uom', header: 'Satuan beli', align: L },
  { key: 'last_month', header: 'Harga terakhir', align: L },
  { key: 'idx_chg', header: 'Perubahan harga vs dasar', fmt: 'pct', align: R },
  { key: 'usage_value', header: 'Nilai pakai di resep (per bulan)', fmt: 'idr', align: R },
  { key: 'impact', header: 'Dampak (Rp per bulan)', fmt: 'idr', align: R },
];

const MENU_COLS: ColumnDef[] = [
  { key: 'menu_name', header: 'Menu', align: L },
  { key: 'menu_category', header: 'Kategori', align: L, filterable: true },
  { key: 'company', header: 'Brand / PT', align: L, filterable: true },
  { key: 'last_month', header: 'Bulan terakhir', align: L },
  { key: 'cost_chg', header: 'Kenaikan indeks biaya vs dasar', fmt: 'pct', align: R },
  { key: 'gm_base', header: 'Margin kotor bulan dasar', fmt: 'pct', align: R },
  { key: 'gm_last', header: 'Margin kotor bulan terakhir', fmt: 'pct', align: R },
  { key: 'gm_pp', header: 'Perubahan margin (pp)', fmt: 'num', align: R },
  { key: 'coverage', header: 'Cakupan harga PO resep', fmt: 'pct', align: R },
];

function fail(r: Result<unknown>, title: string): ReactNode | null {
  return !r.ok ? <ErrorCard title={title} refId={r.ref} reason={r.reason} /> : null;
}

export default async function MarginBahanPage({ searchParams }: { searchParams: FnbSearchParams }) {
  const { adjusted, q } = await fnbRangePage('exec', searchParams);
  const [groupR, priceR, menuR] = await Promise.all([
    q<Raw>('mb_group_monthly'),
    q<Raw>('mb_price_monthly'),
    q<Raw>('mb_menu_monthly'),
  ]);

  const groups = groupR.ok ? groupR.data.map(toGroup) : [];
  const prices = priceR.ok ? priceR.data.map(toPrice) : [];
  const menus = menuR.ok ? menuR.data.map(toMenu) : [];
  const allOk = groupR.ok && priceR.ok && menuR.ok;
  const empty = allOk && groups.length === 0 && menus.length === 0;

  const header = (
    <>
      <PageHeader
        title="Margin bahan"
        question="Seberapa besar kenaikan harga bahan baku menggerus margin kotor, dan kelompok bahan serta menu mana yang paling terdampak?"
        actions={<span className="persona-chip">Direksi · F&B</span>}
      />
      <FilterNotice adjusted={adjusted} />
    </>
  );

  if (empty) {
    return (
      <>
        {header}
        <EmptyState
          title="Belum ada data margin bahan untuk pilihan ini."
          reason="Penyegaran mingguan belum berjalan atau brand ini belum diaktifkan untuk analisis margin bahan. Coba pilih Brand / PT lain."
        />
      </>
    );
  }

  const k = kpis(menus, groups);
  const series = groupSeries(groups, MB_TOP_GROUPS);
  const cats = series.months.map(fmtMonth);
  const chartSpecs = (): ChartSpec[] => {
    const palette: ColorToken[] = ['c2', 'c3', 'c4', 'c5', 'c6'];
    const total = series.total.idx;
    return [
      {
        id: 'idx',
        tabLabel: 'Indeks biaya bahan',
        kind: 'line',
        fmt: 'num',
        axisFmt: 'num',
        categories: cats,
        series: [
          { name: 'Total keranjang', data: total, color: 'c1' },
          ...series.groups.map((g, i) => ({ name: g.name, data: g.idx, color: palette[i % palette.length] })),
        ],
        summary: summarize('Indeks biaya bahan total (dasar = 100)', cats, total, 'num', false),
      },
    ];
  };

  const groupRows: TableRow[] = groupContributions(groups).map((g) => ({
    ing_group: g.ing_group,
    base_value: g.base_value,
    basket_value: g.basket_value,
    contribution: g.contribution,
    idx: g.idx,
    share: g.share,
  }));
  const ingRows: TableRow[] = ingredientImpacts(prices, MB_TOP_INGREDIENTS).map((r) => ({
    product_name: r.product_name,
    ing_group: r.ing_group,
    company: codeName(r.company_code),
    uom: r.uom,
    last_month: fmtMonth(r.last_month),
    idx_chg: r.idx_chg,
    usage_value: r.usage_value,
    impact: r.impact,
  }));
  const menuRows: TableRow[] = menuCostChanges(menus, MB_TOP_MENUS).map((r) => ({
    menu_name: r.menu_name,
    menu_category: r.menu_category,
    company: codeName(r.company_code),
    last_month: fmtMonth(r.last_month),
    cost_chg: r.cost_chg,
    gm_base: r.gm_base,
    gm_last: r.gm_last,
    gm_pp: r.gm_pp,
    coverage: r.coverage,
  }));

  const baseLabel = k.baseMonth ? fmtMonth(k.baseMonth) : 'bulan dasar';
  const costDelta = k.costIdx !== null ? k.costIdx - 100 : null;
  const gmDelta = k.gmBase !== null && k.gmLast !== null ? k.gmLast - k.gmBase : null;

  return (
    <>
      {header}
      <p className="muted small">
        Metode: indeks harga bahan dari harga terima PO ESB, dirata-rata berbobot 4 bulan bergulir (bulan ini dan 3 bulan sebelumnya),
        dibanding bulan dasar ({baseLabel} = 100). Keranjang tetap: jumlah pemakaian bahan menurut resep dan penjualan dikunci,
        hanya harga yang berubah, sehingga kontribusi kelompok = nilai keranjang pada harga terkini dikurangi pada harga dasar. Bahan
        tanpa harga PO dianggap tidak berubah; menu tanpa resep tidak ikut indeks. Satuan beli dominan per bahan, tanpa konversi satuan; PO impor diindeks
        dalam mata uang aslinya (mis. USD), jadi efek kurs tidak ikut terhitung. Tanpa PB1/service. Data terakhir: {k.lastMonth ? fmtMonth(k.lastMonth) : 'tidak ada'} (bulan berjalan tidak
        ikut). Resep disegarkan mingguan, harga PO harian. Filter tanggal dan cabang tidak berlaku di halaman ini.
      </p>

      <Section title="Indikator utama" id="kpi">
        {fail(groupR, 'Indikator biaya bahan') ?? fail(menuR, 'Indikator margin') ?? (
          <KpiRow>
            <KpiTile
              label="Indeks biaya bahan"
              value={k.costIdx}
              fmt="num"
              note={`Dasar ${baseLabel} = 100`}
              delta={{ value: costDelta, kind: 'pct', goodWhen: 'down', compareLabel: 'bulan dasar' }}
              info="Nilai keranjang bahan tetap pada harga PO terkini dibagi pada harga bulan dasar, dikali 100."
            />
            <KpiTile
              label="Margin kotor ESB"
              value={k.gmLast}
              fmt="pct"
              note={k.gmBase === null ? undefined : `Bulan dasar ${baseLabel}: ${k.gmBase.toLocaleString('id-ID', { maximumFractionDigits: 1 })}%`}
              delta={{ value: gmDelta, kind: 'pp', goodWhen: 'up', compareLabel: 'bulan dasar' }}
              info="(Penjualan bersih - COGS ESB) / penjualan bersih, semua menu, bulan terakhir."
            />
            <KpiTile
              label="Cakupan COGS menu ber-resep"
              value={k.bomCoverage}
              fmt="pct"
              info="Porsi COGS bulan terakhir dari menu yang resepnya (BOM) berhasil dicocokkan; sisanya tidak ikut indeks biaya."
            />
          </KpiRow>
        )}
      </Section>

      {fail(groupR, 'Indeks per kelompok bahan') ?? (
        <ChartCard
          title="Indeks biaya bahan per kelompok"
          subtitle={`Total dan ${MB_TOP_GROUPS} kelompok dengan kontribusi terbesar${series.hidden > 0 ? ` (${series.hidden} kelompok lain hanya masuk total)` : ''}; dasar = 100`}
          specs={chartSpecs()}
        />
      )}

      <Section title="Kontribusi kelompok bahan" id="groups">
        {fail(groupR, 'Kontribusi kelompok') ?? (
          <DataTable caption="Kontribusi kelompok bahan terhadap perubahan biaya (bulan terakhir vs dasar)" columns={GROUP_COLS} rows={groupRows} stickyFirstCol={false} defaultSort={{ key: 'contribution', dir: 'desc' }} />
        )}
      </Section>

      <Section title={`${MB_TOP_INGREDIENTS} bahan dengan dampak terbesar`} id="ingredients">
        {fail(priceR, 'Dampak per bahan') ?? (
          <>
            <p className="muted small">
              Dampak = perubahan harga vs bulan dasar x nilai pakai bahan itu di resep (Rupiah per bulan, keranjang tetap). Urut menurut besar dampak (naik maupun turun).
            </p>
            <DataTable caption="Bahan dengan dampak harga terbesar" columns={ING_COLS} rows={ingRows} defaultSort={{ key: 'impact', dir: 'desc' }} />
          </>
        )}
      </Section>

      <Section title={`${MB_TOP_MENUS} menu dengan kenaikan biaya terbesar`} id="menus">
        {fail(menuR, 'Biaya per menu') ?? (
          <>
            <p className="muted small">
              Hanya menu yang resepnya cocok. Margin kotor = (penjualan bersih - COGS ESB) / penjualan bersih pada bulan dasar dan bulan terakhir menu itu.
            </p>
            <DataTable caption="Menu dengan kenaikan indeks biaya terbesar" columns={MENU_COLS} rows={menuRows} defaultSort={{ key: 'cost_chg', dir: 'desc' }} />
          </>
        )}
      </Section>
    </>
  );
}
