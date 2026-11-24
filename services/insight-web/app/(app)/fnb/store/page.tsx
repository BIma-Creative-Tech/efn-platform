import type { Metadata } from 'next';
import Link from 'next/link';
import { Block, ErrorCard } from '@/components/blocks';
import { ChartCard } from '@/components/ChartCard';
import { FilterNotice } from '@/components/FilterNotice';
import { StoreHourlySection } from '@/components/oms/StoreHourlySection';
import { SmTable, type SmColumn } from '@/components/sm/SmTable';
import { EmptyState, KpiRow, KpiTile, PageHeader, Section } from '@/components/ui';
import { brandName } from '@/lib/brands';
import { summarize, type ChartSpec } from '@/lib/chart-spec';
import { addDays, isoWeekday, wibToday } from '@/lib/dates';
import { fnbStorePage, type FnbSearchParams } from '@/lib/fnb-data';
import { fnbStoreChip, isBranchScoped } from '@/lib/fnb-personas';
import { splitWeeks } from '@/lib/fnb-store';
import { pctOfSafe } from '@/lib/fnb-vm';
import { freshnessLabel, parseOmsDay, resolveOmsDay, type FreshnessRaw, type HourlyRaw } from '@/lib/oms-vm';
import { ITEM_GROUP_LABEL } from '@/lib/flow-notes';
import { fmtDate, fmtDayShort, fmtIdr, fmtInt, fmtPct, fmtQty, WEEKDAYS } from '@/lib/format';
import { num, numOrNull, run, settle } from '@/lib/loaders';
import { pctDelta } from '@/lib/vm';

export const dynamic = 'force-dynamic';
export const metadata: Metadata = { title: 'Toko F&B' };

interface KpiRaw {
  net_day: number; net_day_lw: number; trx_day: number; trx_day_lw: number; avg_trx_day: number | null; avg_trx_lw: number | null;
}
interface MenuRow { brand: string; menu_code: string; menu_label: string; category: string; qty_sold: number; net_sales: number }
interface PayRow { payment_type: string; method: string; cnt: number; amount_sum: number }
interface ReceiptRow {
  day: string; branch_id: number; branch_label: string; origin_branch_name: string; item_group: string; product_code: string;
  product_label: string; uom: string; qty_sum: number; lines_sum: number;
}

/**
 * Toko F&B (docs/04 §28.15.9, G52): tanpa HPP/margin/MDR (view toko tidak memuatnya, L-244), tanpa waste. Data per jam hanya dari
 * ESB OMS (G69, bagian "Penjualan per jam & kanal"; tersembunyi bila OMS belum aktif).
 * Hari dan cabang dipilih di halaman ini (GET); kepala toko ber-cabang hanya melihat cabang yang ditugaskan (dipotong di
 * lib/fnb-data.ts dan ditegakkan row policy kelas BR di ClickHouse).
 */
export default async function FnbStorePage({ searchParams }: { searchParams: FnbSearchParams }) {
  const st = await fnbStorePage(searchParams);
  const { ctx, day, yesterday, adjusted, selected, options, daily, q } = st;
  const lastWeek = addDays(day, -7);
  const branched = isBranchScoped(ctx.session);

  const omsParam = parseOmsDay((await searchParams).oms_day);
  const [kpiR, menuR, payR, ckR, omsFreshR] = await Promise.all([
    q<KpiRaw>('fstore_kpi'),
    q<MenuRow>('fstore_top_menu'),
    q<PayRow>('fstore_payment_mix'),
    q<ReceiptRow>('fstore_ck_receipts'),
    // G69: ESB OMS (PB). Gagal/kosong = bagian disembunyikan oleh StoreHourlySection (bukan galat halaman).
    q<FreshnessRaw>('foms_freshness'),
  ]);
  // Bagian OMS punya pilihan hari sendiri (oms_day=today|date): bawaan = hari ini (WIB, dari server) bila ada tarikan hari ini.
  const omsFresh = omsFreshR.ok ? freshnessLabel(omsFreshR.data) : null;
  const omsPick = resolveOmsDay({ param: omsParam, freshLabel: omsFresh, today: wibToday(new Date()), filterDay: day });
  const omsR = await settle(run<HourlyRaw>('foms_hourly', ctx, { ref_day: omsPick.day }));
  const omsLink = (m: 'today' | 'date'): string => {
    const sp = new URLSearchParams({ day, oms_day: m });
    if (selected.length > 0) sp.set('branch', selected.join(','));
    return `/fnb/store?${sp.toString()}#oms`;
  };

  const opts = options.ok ? options.data : [];
  const names = new Map(opts.map((o) => [o.key, o.label]));
  const chip = branched ? fnbStoreChip(ctx.session.brs, names) : selected.length > 0 ? `Kepala toko · ${selected.length} cabang` : 'Kepala toko · semua cabang';
  const groups = Array.from(new Set(opts.map((o) => o.companyCode))).sort();

  const menuCols: SmColumn<MenuRow>[] = [
    { header: 'Menu', cell: (r) => r.menu_label, text: (r) => r.menu_label },
    { header: 'Kategori', cell: (r) => (r.category === '' ? 'Tanpa kategori' : r.category), text: (r) => (r.category === '' ? 'Tanpa kategori' : r.category) },
    { header: 'Qty terjual', align: 'right', cell: (r) => fmtQty(num(r.qty_sold)) },
    { header: 'Penjualan bersih', align: 'right', cell: (r) => fmtIdr(num(r.net_sales)) },
  ];
  const ckCols: SmColumn<ReceiptRow>[] = [
    { header: 'Tanggal', cell: (r) => fmtDate(String(r.day)), text: (r) => fmtDate(String(r.day)) },
    ...(opts.length > 1 || !options.ok
      ? [{ header: 'Cabang penerima', cell: (r: ReceiptRow) => r.branch_label, text: (r: ReceiptRow) => r.branch_label, filter: true } satisfies SmColumn<ReceiptRow>]
      : []),
    { header: 'Asal kiriman', cell: (r) => r.origin_branch_name, text: (r) => r.origin_branch_name },
    { header: 'Kelompok', cell: (r) => ITEM_GROUP_LABEL[r.item_group] ?? r.item_group, text: (r) => ITEM_GROUP_LABEL[r.item_group] ?? r.item_group, filter: true },
    { header: 'Produk', cell: (r) => r.product_label || r.product_code, text: (r) => r.product_label || r.product_code },
    { header: 'Qty diterima', align: 'right', cell: (r) => `${fmtQty(num(r.qty_sum))} ${r.uom}` },
  ];

  return (
    <>
      <PageHeader
        title="Toko F&B"
        question="Bagaimana penjualan toko hari ini dibanding minggu lalu, apa menu terlaris, dan kiriman CK apa yang sudah diterima?"
        actions={<span className="persona-chip">{chip}</span>}
      />
      <FilterNotice adjusted={adjusted} />

      <form method="get" className="filterbar filterbar-inline" aria-label="Pilih hari dan cabang">
        <div className="fb-row">
          <label className="tt-select">
            <span>Hari</span>
            <input className="input" type="date" name="day" defaultValue={day} max={yesterday} min={addDays(yesterday, -366)} required />
          </label>
          {opts.length > 1 ? (
            <label className="tt-select">
              <span>Cabang</span>
              <select className="input" name="branch" defaultValue={selected[0] ?? ''}>
                <option value="">{branched ? 'Semua cabang saya' : 'Semua cabang'}</option>
                {groups.map((g) => (
                  <optgroup key={g} label={g}>
                    {opts
                      .filter((o) => o.companyCode === g)
                      .map((o) => (
                        <option key={o.key} value={o.key}>
                          {o.label} ({brandName(o.brand)})
                        </option>
                      ))}
                  </optgroup>
                ))}
              </select>
            </label>
          ) : null}
          <button type="submit" className="btn btn-primary">
            Terapkan
          </button>
          <Link href="/fnb/store" className="btn">
            Reset
          </Link>
        </div>
        <p className="muted small fb-summary">
          Hari: {fmtDate(day)} dibanding {fmtDate(lastWeek)} (hari yang sama minggu lalu). Data per jam hanya dari ESB OMS (bila tersedia untuk cabang ini). Basis penjualan bersih sebelum PB1/service.
        </p>
      </form>
      {!options.ok ? <ErrorCard title="Daftar cabang" refId={options.ref} reason={options.reason} /> : null}

      <Section title="Indikator hari ini" id="kpi">
        <Block result={kpiR} title="Indikator hari ini">
          {(rows) => {
            const k = rows[0];
            if (!k || (num(k.net_day) === 0 && num(k.trx_day) === 0)) {
              return (
                <EmptyState
                  title={`Tidak ada penjualan pada ${fmtDate(day)}.`}
                  reason="Data hari itu mungkin belum masuk atau toko tutup. Pilih hari lain, atau cek kesegaran data."
                />
              );
            }
            const d = (cur: number | null, prev: number | null) => ({
              value: cur !== null && prev !== null ? pctDelta(cur, prev) : null,
              kind: 'pct' as const,
              goodWhen: 'up' as const,
              compareLabel: 'minggu lalu',
            });
            return (
              <KpiRow>
                <KpiTile label="Penjualan bersih" value={num(k.net_day)} fmt="idr-short" note={`vs ${fmtDayShort(lastWeek)}`} delta={d(num(k.net_day), num(k.net_day_lw))} />
                <KpiTile label="Transaksi" value={num(k.trx_day)} fmt="int" delta={d(num(k.trx_day), num(k.trx_day_lw))} />
                <KpiTile label="Rata-rata per transaksi" value={numOrNull(k.avg_trx_day)} fmt="idr" delta={d(numOrNull(k.avg_trx_day), numOrNull(k.avg_trx_lw))} />
              </KpiRow>
            );
          }}
        </Block>
      </Section>

      <StoreHourlySection day={omsPick.day} mode={omsPick.mode} fresh={omsFresh} links={{ today: omsLink('today'), date: omsLink('date') }} hourlyR={omsR} />

      <Section title="Penjualan 7 hari terakhir vs pekan lalu" id="trend">
        <Block result={daily} title="Penjualan 7 hari terakhir">
          {(rows) => {
            if (rows.length === 0) return <EmptyState title="Belum ada penjualan dalam 14 hari terakhir." />;
            const w = splitWeeks(rows, day);
            const cats = w.days.map((d) => `${WEEKDAYS[isoWeekday(d) - 1]} ${fmtDayShort(d)}`);
            const mk = (id: string, tab: string, fmt: 'idr' | 'int', cur: (number | null)[], prev: (number | null)[]): ChartSpec => ({
              id,
              tabLabel: tab,
              kind: 'line',
              fmt,
              axisFmt: fmt === 'idr' ? 'idr-short' : 'int',
              categories: cats,
              series: [
                { name: 'Pekan ini', data: cur, color: 'c1' },
                { name: 'Pekan lalu (hari yang sama)', data: prev, color: 'muted', dashed: true },
              ],
              summary: summarize(`${tab}, pekan ini`, cats, cur, fmt === 'idr' ? 'idr-short' : 'int', false),
            });
            return (
              <ChartCard
                title="Pekan ini vs pekan lalu"
                subtitle="Hari tanpa data ditampilkan kosong, bukan nol"
                specs={[mk('net', 'Penjualan bersih', 'idr', w.net, w.netLastWeek), mk('trx', 'Transaksi', 'int', w.trx, w.trxLastWeek)]}
                wide
              />
            );
          }}
        </Block>
      </Section>

      <Section title="Menu terlaris" id="menu">
        <Block result={menuR} title="Menu terlaris">
          {(rows) =>
            rows.length === 0 ? (
              <EmptyState title={`Tidak ada penjualan menu pada ${fmtDate(day)}.`} />
            ) : (
              <SmTable caption={`20 menu terlaris menurut qty pada ${fmtDate(day)}`} columns={menuCols} rows={rows} rowKey={(r) => `${r.brand}:${r.menu_code}`} />
            )
          }
        </Block>
      </Section>

      <Section title="Metode bayar" id="pay">
        <Block result={payR} title="Metode bayar">
          {(rows) => {
            if (rows.length === 0) return <EmptyState title={`Tidak ada pembayaran pada ${fmtDate(day)}.`} />;
            const total = rows.reduce((s, r) => s + num(r.amount_sum), 0);
            return (
              <SmTable
                caption={`Nilai pembayaran per metode pada ${fmtDate(day)}`}
                columns={[
                  { header: 'Metode bayar', cell: (r) => r.method, text: (r) => r.method },
                  { header: 'Jumlah pembayaran', align: 'right', cell: (r) => fmtInt(num(r.cnt)) },
                  { header: 'Nilai', align: 'right', cell: (r) => fmtIdr(num(r.amount_sum)) },
                  { header: 'Porsi', align: 'right', cell: (r) => { const p = pctOfSafe(num(r.amount_sum), total); return p === null ? 'n/a' : fmtPct(p); } },
                ]}
                rows={rows}
                rowKey={(r) => `${r.payment_type}:${r.method}`}
              />
            );
          }}
        </Block>
      </Section>

      <Section title="Kiriman CK yang diterima (7 hari terakhir)" id="ck">
        <p className="muted small">
          Kiriman yang tercatat diterima toko sampai {fmtDate(day)}. Jumlah tidak dijumlahkan lintas satuan; tiap satuan ditampilkan terpisah.
        </p>
        <Block result={ckR} title="Kiriman CK yang diterima">
          {(rows) =>
            rows.length === 0 ? (
              <EmptyState title="Tidak ada kiriman diterima dalam 7 hari terakhir." />
            ) : (
              <SmTable
                caption="Kiriman CK yang diterima, terbaru di atas"
                columns={ckCols}
                rows={rows}
                rowKey={(r) => `${r.day}:${r.branch_id}:${r.origin_branch_name}:${r.product_code}:${r.uom}`}
              />
            )
          }
        </Block>
      </Section>
    </>
  );
}
