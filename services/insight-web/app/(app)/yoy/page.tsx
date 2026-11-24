import type { Metadata } from 'next';
import type { ReactNode } from 'react';
import { ErrorCard } from '@/components/blocks';
import { ChartCard } from '@/components/ChartCard';
import { CoverageNotice } from '@/components/CoverageNotice';
import { DataTable, type TableRow } from '@/components/DataTable';
import { FilterNotice } from '@/components/FilterNotice';
import { EmptyState, KpiRow, KpiTile, PageHeader, Section } from '@/components/ui';
import { summarize, type ChartSpec } from '@/lib/chart-spec';
import { eachDay } from '@/lib/dates';
import { fmtDayShort, fmtIdrShort, fmtPct, fmtRange } from '@/lib/format';
import { loadCoverage, num, type Result } from '@/lib/loaders';
import { pageCtx, type SearchParams } from '@/lib/page-ctx';
import { runEsb } from '@/lib/smd-data';
import type { ColumnDef } from '@/lib/table-defs';
import {
  atv,
  branchStatus,
  bridge,
  dailySeries,
  discPct,
  gapNotes,
  lyRange,
  marginPct,
  menuView,
  pctChange,
  promo,
  spanDays,
  STATUS_LABEL,
  totals,
  YOY_MATURE_DAYS,
  YOY_MAX_DAYS,
  YOY_MENU_MAX_DAYS,
  type BranchRow,
  type DailyRow,
  type GapRow,
  type MenuRow,
  type Side,
} from '@/lib/yoy';

export const dynamic = 'force-dynamic';
export const metadata: Metadata = { title: 'Tahun lalu vs sekarang' };

type Raw = Record<string, unknown>;
const L = 'left' as const;
const R = 'right' as const;

const BRANCH_NUM = ['net_cur', 'net_ly', 'gross_cur', 'gross_ly', 'disc_cur', 'disc_ly', 'cogs_cur', 'cogs_ly', 'trx_cur', 'trx_ly', 'days_cur', 'days_ly'] as const;

function toBranch(r: Raw): BranchRow {
  const o: Record<string, unknown> = { ...r, branch_id: num(r.branch_id), first_sale: String(r.first_sale ?? '') };
  for (const k of BRANCH_NUM) o[k] = num(r[k]);
  // ClickHouse: LEFT JOIN tanpa pasangan -> tanggal default 1970-01-01 (bukan NULL).
  if (o.first_sale === '1970-01-01') o.first_sale = '';
  return o as unknown as BranchRow;
}

function toDaily(r: Raw): DailyRow {
  return { day: String(r.day), brand: String(r.brand), branch_id: num(r.branch_id), net_cur: num(r.net_cur), net_ly: num(r.net_ly), trx_cur: num(r.trx_cur), trx_ly: num(r.trx_ly) };
}

function toMenu(r: Raw): MenuRow {
  const o: Record<string, unknown> = { menu_name: String(r.menu_name ?? ''), category: String(r.category ?? '') };
  for (const [k, v] of Object.entries(r)) if (k !== 'menu_name' && k !== 'category') o[k] = num(v);
  return o as unknown as MenuRow;
}

const BRANCH_COLS: ColumnDef[] = [
  { key: 'branch_name', header: 'Cabang', align: L },
  { key: 'status', header: 'Status', align: L, filterable: true },
  { key: 'net_ly', header: 'Penjualan bersih tahun lalu', fmt: 'idr', align: R },
  { key: 'net_cur', header: 'Penjualan bersih sekarang', fmt: 'idr', align: R },
  { key: 'net_chg', header: 'Perubahan penjualan', fmt: 'pct', align: R },
  { key: 'trx_chg', header: 'Perubahan transaksi', fmt: 'pct', align: R },
  { key: 'atv_chg', header: 'Perubahan rata-rata per transaksi', fmt: 'pct', align: R },
  { key: 'gm_ly', header: 'Margin kotor tahun lalu', fmt: 'pct', align: R },
  { key: 'gm_cur', header: 'Margin kotor sekarang', fmt: 'pct', align: R },
  { key: 'days', header: 'Hari berjualan (lalu / kini)', align: R },
];

const MENU_COLS: ColumnDef[] = [
  { key: 'menu_name', header: 'Menu', align: L },
  { key: 'category', header: 'Kategori', align: L, filterable: true },
  { key: 'qty_ly', header: 'Unit berbayar tahun lalu', fmt: 'int', align: R },
  { key: 'qty_cur', header: 'Unit berbayar sekarang', fmt: 'int', align: R },
  { key: 'price_ly', header: 'Harga jual tahun lalu', fmt: 'idr', align: R },
  { key: 'price_cur', header: 'Harga jual sekarang', fmt: 'idr', align: R },
  { key: 'price_chg', header: 'Perubahan harga', fmt: 'pct', align: R },
  { key: 'cogs_ly', header: 'COGS per unit tahun lalu', fmt: 'idr', align: R },
  { key: 'cogs_cur', header: 'COGS per unit sekarang', fmt: 'idr', align: R },
  { key: 'cogs_chg', header: 'Perubahan COGS', fmt: 'pct', align: R },
  { key: 'fc_ly', header: 'COGS % harga tahun lalu', fmt: 'pct', align: R },
  { key: 'fc_cur', header: 'COGS % harga sekarang', fmt: 'pct', align: R },
  { key: 'fc_pp', header: 'Perubahan COGS % (pp)', fmt: 'num', align: R },
  { key: 'free_share_cur', header: 'Porsi unit gratis sekarang', fmt: 'pct', align: R },
  { key: 'net_price_cur', header: 'Harga bersih setelah diskon sekarang', fmt: 'idr', align: R },
  { key: 'rev_cur', header: 'Omzet bersih sekarang', fmt: 'idr', align: R },
];

const BRIDGE_COLS: ColumnDef[] = [
  { key: 'label', header: 'Komponen', align: L },
  { key: 'value', header: 'Nilai', fmt: 'idr', align: R },
  { key: 'share', header: '% dari penjualan tahun lalu', fmt: 'pct', align: R },
];

function sideTiles(s: Side, title: string): ReactNode {
  const gmC = marginPct(s.cur);
  const gmL = marginPct(s.ly);
  const aC = atv(s.cur);
  const aL = atv(s.ly);
  const dC = discPct(s.cur);
  const dL = discPct(s.ly);
  return (
    <>
      <h3 className="small">{title}</h3>
      <KpiRow>
        <KpiTile label="Penjualan bersih" value={s.cur.net} fmt="idr-short" note={`Tahun lalu ${fmtIdrShort(s.ly.net)}`} delta={{ value: pctChange(s.cur.net, s.ly.net), kind: 'pct', goodWhen: 'up' }} />
        <KpiTile label="Transaksi" value={s.cur.trx} fmt="int" note={`Tahun lalu ${Math.round(s.ly.trx).toLocaleString('id-ID')}`} delta={{ value: pctChange(s.cur.trx, s.ly.trx), kind: 'pct', goodWhen: 'up' }} />
        <KpiTile label="Rata-rata per transaksi" value={aC} fmt="idr" note={aL === null ? undefined : `Tahun lalu ${fmtIdrShort(aL)}`} delta={{ value: aC !== null && aL !== null ? pctChange(aC, aL) : null, kind: 'pct', goodWhen: 'up' }} />
        <KpiTile label="Margin kotor" value={gmC} fmt="pct" note={gmL === null ? undefined : `Tahun lalu ${fmtPct(gmL)}`} delta={{ value: gmC !== null && gmL !== null ? gmC - gmL : null, kind: 'pp', goodWhen: 'up' }} info="(Penjualan bersih - COGS ESB) / penjualan bersih. Tanpa roti tidak terjual, komisi ojol, dan MDR." />
        <KpiTile label="Diskon tercatat" value={dC} fmt="pct" note={dL === null ? undefined : `Tahun lalu ${fmtPct(dL)}`} delta={{ value: dC !== null && dL !== null ? dC - dL : null, kind: 'pp', goodWhen: 'neutral' }} info="Kolom diskon ESB dibagi penjualan kotor. Belum termasuk potongan bundel dan item gratis (lihat Promo)." />
      </KpiRow>
      <p className="muted small">{s.branches} cabang.</p>
    </>
  );
}

function fail(r: Result<unknown> | null, title: string): ReactNode | null {
  return r && !r.ok ? <ErrorCard title={title} refId={r.ref} reason={r.reason} /> : null;
}

export default async function YoyPage({ searchParams }: { searchParams: SearchParams }) {
  const { ctx, adjusted, sel } = await pageCtx(searchParams);
  const { from, to } = ctx.filters;
  const ly = lyRange(from, to);
  const span = spanDays(from, to);
  const header = (
    <>
      <PageHeader title="Tahun lalu vs sekarang" question="Apakah penjualan, harga jual, dan COGS benar-benar berubah dibanding periode yang sama tahun lalu?" />
      <FilterNotice adjusted={adjusted} />
    </>
  );
  if (!sel.esb) {
    return (
      <>
        {header}
        <EmptyState title="Halaman ini hanya untuk brand F&B (data ESB)." reason="Pilih Brand / PT F&B di filter." />
      </>
    );
  }
  if (span > YOY_MAX_DAYS) {
    return (
      <>
        {header}
        <EmptyState title={`Rentang maksimal ${YOY_MAX_DAYS} hari.`} reason="Rentang yang lebih panjang membuat periode ini dan periode pembanding tumpang tindih." />
      </>
    );
  }

  const extra = { yoy_from: ly.from, yoy_to: ly.to };
  const menuOn = span <= YOY_MENU_MAX_DAYS;
  const [branchR, dailyR, gapR, menuR, cov] = await Promise.all([
    runEsb<Raw>(true, 'yoy_branch', ctx, extra),
    runEsb<Raw>(true, 'yoy_daily', ctx, extra),
    runEsb<Raw>(true, 'yoy_gaps', ctx, extra),
    menuOn ? runEsb<Raw>(true, 'yoy_menu', ctx, extra) : Promise.resolve(null),
    loadCoverage(ctx),
  ]);

  const branches = branchR.ok ? branchR.data.map(toBranch) : [];
  const statusOf = new Map(branches.map((b) => [`${b.brand}:${b.branch_id}`, branchStatus(b, ly.from)]));
  const same = branches.filter((b) => statusOf.get(`${b.brand}:${b.branch_id}`) === 'same');
  const gaps: GapRow[] = gapR.ok ? gapR.data.map((g) => ({ day: String(g.day), miss_ly: num(g.miss_ly), miss_cur: num(g.miss_cur) })) : [];
  const menus = menuR && menuR.ok ? menuR.data.map(toMenu) : [];

  const days = eachDay(from, to);
  const cats = days.map(fmtDayShort);
  const daily = dailyR.ok ? dailyR.data.map(toDaily) : [];
  const dailySpecs = (): ChartSpec[] => {
    const mk = (id: string, tab: string, keep: (r: DailyRow) => boolean, metric: 'net' | 'trx'): ChartSpec => {
      const s = dailySeries(daily, days, keep);
      const cur = metric === 'net' ? s.netCur : s.trxCur;
      const lys = metric === 'net' ? s.netLy : s.trxLy;
      const fmt = metric === 'net' ? ('idr' as const) : ('int' as const);
      return {
        id,
        tabLabel: tab,
        kind: 'line',
        fmt,
        axisFmt: metric === 'net' ? 'idr-short' : 'int',
        categories: cats,
        series: [
          { name: 'Sekarang', data: cur, color: 'c1' },
          { name: 'Tahun lalu (hari yang sama)', data: lys, color: 'c2', dashed: true },
        ],
        summary: summarize(`${tab}: sekarang`, cats, cur.map((v) => v ?? 0), metric === 'net' ? 'idr-short' : 'int', true),
      };
    };
    const isSame = (r: DailyRow) => statusOf.get(`${r.brand}:${r.branch_id}`) === 'same';
    return [
      mk('all', 'Semua cabang', () => true, 'net'),
      mk('same', 'Cabang sebanding', isSame, 'net'),
      mk('trx', 'Transaksi (cabang sebanding)', isSame, 'trx'),
    ];
  };

  const b = bridge(branches, ly.from);
  const bridgeRows: TableRow[] = [
    { label: 'Penjualan bersih tahun lalu', value: b.ly, share: 100 },
    { label: 'Cabang sebanding: jumlah transaksi', value: b.trxEffect, share: b.ly > 0 ? (b.trxEffect / b.ly) * 100 : null },
    { label: 'Cabang sebanding: nilai per transaksi', value: b.atvEffect, share: b.ly > 0 ? (b.atvEffect / b.ly) * 100 : null },
    { label: 'Cabang baru', value: b.newStores, share: b.ly > 0 ? (b.newStores / b.ly) * 100 : null },
    { label: 'Cabang tidak berjualan sekarang', value: b.closedStores, share: b.ly > 0 ? (b.closedStores / b.ly) * 100 : null },
    { label: 'Cabang belum sebanding', value: b.youngStores, share: b.ly > 0 ? (b.youngStores / b.ly) * 100 : null },
    { label: 'Penjualan bersih sekarang', value: b.cur, share: b.ly > 0 ? (b.cur / b.ly) * 100 : null },
  ];

  const branchRows: TableRow[] = branches.map((r) => {
    const st = statusOf.get(`${r.brand}:${r.branch_id}`) ?? 'same';
    const aC = r.trx_cur > 0 ? r.net_cur / r.trx_cur : null;
    const aL = r.trx_ly > 0 ? r.net_ly / r.trx_ly : null;
    return {
      branch_name: r.branch_name,
      status: STATUS_LABEL[st],
      net_ly: r.net_ly,
      net_cur: r.net_cur,
      net_chg: pctChange(r.net_cur, r.net_ly),
      trx_chg: pctChange(r.trx_cur, r.trx_ly),
      atv_chg: aC !== null && aL !== null ? pctChange(aC, aL) : null,
      gm_ly: r.net_ly > 0 ? (1 - r.cogs_ly / r.net_ly) * 100 : null,
      gm_cur: r.net_cur > 0 ? (1 - r.cogs_cur / r.net_cur) * 100 : null,
      days: `${r.days_ly} / ${r.days_cur}`,
    };
  });

  const pCur = promo(menus, 'cur');
  const pLy = promo(menus, 'ly');
  const ppDelta = (a: number | null, c: number | null) => (a !== null && c !== null ? c - a : null);

  return (
    <>
      {header}
      <CoverageNotice rows={cov} entities={['sales']} range={{ from, to }} />
      <p className="muted small">
        Periode ini: {fmtRange(from, to)}. Pembanding: {fmtRange(ly.from, ly.to)} (digeser 364 hari, hari dalam minggu sama). Hanya
        cabang-hari yang data penjualannya sudah lengkap di kedua tahun. Cabang sebanding = berjualan di kedua periode dan sudah
        berjualan minimal {YOY_MATURE_DAYS} hari sebelum periode tahun lalu. Angka tanpa PB1/service charge.
      </p>
      {gaps.length > 0 ? (
        <div className="notice" role="status">
          <p>Dikeluarkan dari kedua sisi karena data belum lengkap:</p>
          <ul>
            {gapNotes(gaps, fmtDayShort).map((n) => (
              <li key={n}>{n}</li>
            ))}
          </ul>
        </div>
      ) : null}
      {fail(gapR, 'Celah data')}

      <Section title="Indikator utama" id="kpi">
        {fail(branchR, 'Indikator utama') ??
          (branches.length === 0 ? (
            <EmptyState title="Tidak ada penjualan sebanding pada periode ini." />
          ) : (
            <>
              {sideTiles(totals(branches), 'Semua cabang')}
              {sideTiles(totals(same), 'Cabang sebanding (like-for-like)')}
            </>
          ))}
      </Section>

      {fail(dailyR, 'Tren harian') ?? <ChartCard title="Penjualan harian: sekarang vs tahun lalu" subtitle="Tahun lalu dipetakan ke hari yang sama dalam minggu" specs={dailySpecs()} />}

      <Section title="Dari mana perubahannya" id="bridge">
        {fail(branchR, 'Jembatan perubahan') ?? (
          <>
            <p className="muted small">
              Efek jumlah transaksi = selisih transaksi x rata-rata per transaksi tahun lalu; efek nilai per transaksi = sisanya.
              Jumlah semua komponen = selisih total.
            </p>
            <DataTable caption="Jembatan perubahan penjualan bersih" columns={BRIDGE_COLS} rows={bridgeRows} stickyFirstCol={false} />
          </>
        )}
      </Section>

      <Section title="Per cabang" id="branch">
        {fail(branchR, 'Per cabang') ?? (
          <DataTable caption="Penjualan sebanding per cabang" columns={BRANCH_COLS} rows={branchRows} defaultSort={{ key: 'net_cur', dir: 'desc' }} />
        )}
      </Section>

      <Section title="Promo: diskon, bundel, item gratis" id="promo">
        {!menuOn ? (
          <EmptyState title={`Bagian ini hanya untuk rentang maksimal ${YOY_MENU_MAX_DAYS} hari.`} reason="Bagian promo dan menu membaca baris penjualan; persempit rentang tanggal." />
        ) : (
          fail(menuR, 'Promo') ?? (
            <>
              <KpiRow>
                <KpiTile label="Kedalaman promo" value={pCur.depth} fmt="pct" note={pLy.depth === null ? undefined : `Tahun lalu ${fmtPct(pLy.depth)}`} delta={{ value: ppDelta(pLy.depth, pCur.depth), kind: 'pp', goodWhen: 'neutral' }} info="(Diskon tercatat + potongan bundel + nilai item gratis) / nilai pada harga jual normal." />
                <KpiTile label="Diskon tercatat" value={pCur.discount} fmt="idr-short" note={`Tahun lalu ${fmtIdrShort(pLy.discount)}`} delta={{ value: pctChange(pCur.discount, pLy.discount), kind: 'pct', goodWhen: 'neutral' }} />
                <KpiTile label="Potongan bundel" value={pCur.bundle} fmt="idr-short" note={`Tahun lalu ${fmtIdrShort(pLy.bundle)}`} delta={{ value: pctChange(pCur.bundle, pLy.bundle), kind: 'pct', goodWhen: 'neutral' }} info="Nilai komponen paket pada harga jual normal dikurangi harga paket. Tidak tercatat di kolom diskon ESB." />
                <KpiTile label="Item gratis (harga normal)" value={pCur.free} fmt="idr-short" note={`Tahun lalu ${fmtIdrShort(pLy.free)}; COGS ${fmtIdrShort(pCur.freeCogs)}`} delta={{ value: pctChange(pCur.free, pLy.free), kind: 'pct', goodWhen: 'neutral' }} info="Baris dengan harga 0 (mis. beli 1 gratis 1). COGS-nya tetap dihitung, omzetnya 0." />
                <KpiTile label="Porsi omzet paket" value={pCur.pkgShare} fmt="pct" note={pLy.pkgShare === null ? undefined : `Tahun lalu ${fmtPct(pLy.pkgShare)}`} delta={{ value: ppDelta(pLy.pkgShare, pCur.pkgShare), kind: 'pp', goodWhen: 'neutral' }} />
              </KpiRow>
              {pCur.unpriced + pLy.unpriced > 0 ? (
                <p className="muted small">
                  {Math.round(pCur.unpriced + pLy.unpriced).toLocaleString('id-ID')} unit komponen paket/item gratis tidak punya harga
                  jual pembanding (menu tidak pernah dijual terpisah) dan tidak dinilai.
                </p>
              ) : null}
            </>
          )
        )}
      </Section>

      <Section title="Harga jual vs COGS per menu" id="menu">
        {!menuOn ? (
          <EmptyState title={`Bagian ini hanya untuk rentang maksimal ${YOY_MENU_MAX_DAYS} hari.`} />
        ) : (
          fail(menuR, 'Harga jual vs COGS') ?? (
            <>
              <p className="muted small">
                Harga jual = rata-rata harga baris berbayar sebelum diskon, tanpa pajak. COGS per unit = COGS ESB baris berbayar
                (resep x harga beli). Item gratis dan komponen paket tidak ikut dirata-rata; porsinya ditampilkan terpisah. Menu
                digabung per nama (kode lama DB/TB dan kode baru SC000xx). Takeaway (akhiran T) terpisah karena COGS-nya termasuk
                kemasan.
              </p>
              <DataTable caption="Harga jual dan COGS per unit, sekarang vs tahun lalu" columns={MENU_COLS} rows={menuView(menus) as unknown as TableRow[]} defaultSort={{ key: 'rev_cur', dir: 'desc' }} />
            </>
          )
        )}
      </Section>
    </>
  );
}
