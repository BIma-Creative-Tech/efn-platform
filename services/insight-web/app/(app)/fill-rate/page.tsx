import type { Metadata } from 'next';
import type { ReactNode } from 'react';
import { ErrorCard } from '@/components/blocks';
import { ChartCard } from '@/components/ChartCard';
import { DataTable, type TableRow } from '@/components/DataTable';
import { FilterNotice } from '@/components/FilterNotice';
import { CheckGroup, G67Form } from '@/components/g67/G67Form';
import { EmptyState, KpiRow, KpiTile, PageHeader, Section } from '@/components/ui';
import { brandName, codeName } from '@/lib/brands';
import { fmtInt, fmtRange } from '@/lib/format';
import {
  COVERAGE_KEYS,
  coverageHint,
  fillRateByOutletSpec,
  fillRateTrendSpec,
  parseSuppliers,
  type WeeklyFillRow,
} from '@/lib/g67-vm';
import { g67Page, type G67SearchParams } from '@/lib/g67-page';
import { loadCoverage, num, numOrNull, type Result } from '@/lib/loaders';
import { runEsb } from '@/lib/smd-data';
import type { ColumnDef } from '@/lib/table-defs';

export const dynamic = 'force-dynamic';
export const metadata: Metadata = { title: 'Fill Rate Outlet ke CK' };

type Raw = Record<string, unknown>;
const L = 'left' as const;
const R = 'right' as const;

// Label F&B memakai "Fill rate outlet (baris)" agar glosarium memilih entri `fill-rate-line`, bukan `fill-rate` (DC SMD; /distribution).
const OUTLET_COLS: ColumnDef[] = [
  { key: 'branch_label', header: 'Outlet', align: L },
  { key: 'company_code', header: 'PT', align: L, filterable: true },
  { key: 'requests', header: 'Permintaan', fmt: 'int', align: R },
  { key: 'lines', header: 'Baris permintaan', fmt: 'int', align: R },
  { key: 'fill_rate_line', header: 'Fill rate outlet (baris)', fmt: 'pct', align: R },
  { key: 'pct_lines_full', header: '% baris penuh', fmt: 'pct', align: R },
  { key: 'lines_zero', header: 'Baris tidak terkirim', fmt: 'int', align: R },
];

const SUPPLIER_COLS: ColumnDef[] = [
  { key: 'supplier_label', header: 'Pemasok (CK)', align: L },
  { key: 'lines', header: 'Baris permintaan', fmt: 'int', align: R },
  { key: 'fill_rate_line', header: 'Fill rate outlet (baris)', fmt: 'pct', align: R },
  { key: 'avg_lead_days', header: 'Waktu kirim rata-rata (hari)', fmt: 'num', align: R },
];

const PRODUCT_COLS: ColumnDef[] = [
  { key: 'product_label', header: 'Produk', align: L },
  { key: 'brand', header: 'Brand', align: L, filterable: true },
  { key: 'uom', header: 'Satuan', align: L },
  { key: 'request_qty', header: 'Qty diminta', fmt: 'num', align: R },
  { key: 'qty_sent', header: 'Qty dikirim', fmt: 'num', align: R },
  { key: 'fill_rate_qty', header: 'Pemenuhan qty (%)', fmt: 'pct', align: R },
  { key: 'short_qty', header: 'Qty kurang kirim', fmt: 'num', align: R },
];

const one = (v: string | string[] | undefined): string | undefined => (Array.isArray(v) ? v[0] : v);

function fail(r: Result<unknown>, title: string): ReactNode | null {
  return r.ok ? null : <ErrorCard title={title} refId={r.ref} reason={r.reason} />;
}

const pp = (a: number | null, b: number | null, hasPrev: boolean): number | null => (hasPrev && a !== null && b !== null ? a - b : null);

/** Fill Rate Outlet ke CK (docs/04 §31.6.1, §31.8-§31.10): kohort tanggal permintaan, hanya permintaan matang; tanpa target resmi (Q-ESB31). */
export default async function FillRatePage({ searchParams }: { searchParams: G67SearchParams }) {
  const { ctx, adjusted, raw } = await g67Page('/fill-rate', searchParams);
  const { from, to } = ctx.filters;
  const sup = parseSuppliers(raw);
  const extra = { supplier: sup.supplier };
  const ptNames = ctx.filters.pt.map(codeName).join(', ');

  const [kpiR, trendR, outletR, supplierR, productR, optR, cov] = await Promise.all([
    runEsb<Raw>(true, 'fr_kpi', ctx, extra),
    runEsb<Raw>(true, 'fr_trend_weekly', ctx, extra),
    runEsb<Raw>(true, 'fr_by_outlet', ctx, extra),
    runEsb<Raw>(true, 'fr_by_supplier', ctx, extra),
    runEsb<Raw>(true, 'fr_top_short_products', ctx, extra),
    runEsb<Raw>(true, 'fr_suppliers', ctx, extra),
    loadCoverage(ctx),
  ]);

  const k = kpiR.ok ? kpiR.data[0] : undefined;
  const linesCur = num(k?.lines_cur);
  const hasPrev = num(k?.lines_prev) > 0;
  const frCur = numOrNull(k?.fill_rate_line_cur);
  const fullCur = numOrNull(k?.pct_lines_full_cur);
  const reqFullCur = numOrNull(k?.pct_requests_full_cur);
  const leadCur = numOrNull(k?.avg_lead_days_cur);
  const leadPrev = numOrNull(k?.avg_lead_days_prev);
  const open = num(k?.lines_open_cur);
  const mismatch = num(k?.lines_uom_mismatch_cur);

  const trend: WeeklyFillRow[] = trendR.ok
    ? trendR.data.map((r) => ({ week_start: String(r.week_start), fill_rate_line: numOrNull(r.fill_rate_line), pct_lines_full: numOrNull(r.pct_lines_full) }))
    : [];

  const outletRows: TableRow[] = outletR.ok
    ? outletR.data.map((r) => ({
        branch_label: String(r.branch_label ?? ''),
        company_code: String(r.company_code ?? ''),
        requests: num(r.requests_sum),
        lines: num(r.lines_sum),
        fill_rate_line: numOrNull(r.fill_rate_line),
        pct_lines_full: numOrNull(r.pct_lines_full),
        lines_zero: num(r.lines_zero_sum),
      }))
    : [];
  const supplierRows: TableRow[] = supplierR.ok
    ? supplierR.data.map((r) => ({
        supplier_label: String(r.supplier_label ?? ''),
        lines: num(r.lines_sum),
        fill_rate_line: numOrNull(r.fill_rate_line),
        avg_lead_days: numOrNull(r.avg_lead_days),
      }))
    : [];
  const productRows: TableRow[] = productR.ok
    ? productR.data.map((r) => ({
        product_label: String(r.name_text ?? '') || String(r.product_code ?? ''),
        brand: codeName(String(r.brand ?? '').toUpperCase()),
        uom: String(r.uom ?? ''),
        request_qty: num(r.request_sum),
        qty_sent: num(r.sent_sum),
        fill_rate_qty: numOrNull(r.fill_rate_qty),
        short_qty: num(r.short_qty),
      }))
    : [];

  const supplierOptions = optR.ok
    ? optR.data.map((r) => ({ value: `${String(r.brand)}:${num(r.supplier_branch_id)}`, label: `${String(r.supplier_label ?? '')} (${brandName(String(r.brand))})` }))
    : [];
  // Pilihan yang sudah dipilih tetap tampil walau tidak lagi ada pada rentang (agar bisa dicabut).
  const shownOptions = [...supplierOptions, ...sup.supplier.filter((s) => !supplierOptions.some((o) => o.value === s)).map((s) => ({ value: s, label: s }))];

  const empty = kpiR.ok && linesCur === 0 && !hasPrev && outletRows.length === 0;
  const hint = coverageHint(cov, COVERAGE_KEYS.fill);

  return (
    <>
      <PageHeader title="Fill Rate Outlet ke CK" question="Seberapa penuh permintaan barang outlet ke CK terpenuhi, dan outlet, pemasok, atau produk mana yang paling sering kurang kirim?" />
      <FilterNotice adjusted={adjusted || sup.adjusted} />

      <G67Form
        path="/fill-rate"
        label="Pilih pemasok"
        carry={{ pt: one(raw.pt), branch: one(raw.branch), from: one(raw.from), to: one(raw.to) }}
        summary={
          <>
            Tanggal permintaan: {fmtRange(from, to)}. PT: {ptNames}. Filter Cabang = outlet peminta; Pemasok = CK pengirim (maksimal 10).
          </>
        }
      >
        {shownOptions.length > 0 ? (
          <CheckGroup legend="Pemasok (CK)" name="supplier" options={shownOptions} checked={sup.supplier} hint="Kosong = semua pemasok." />
        ) : null}
      </G67Form>

      <p className="muted small">
        Dihitung per tanggal permintaan. Hanya permintaan matang (selesai, atau berumur lebih dari 7 hari) yang masuk; sisanya masih berjalan. Fill rate baris =
        rata-rata qty dikirim dibanding diminta per baris, dibatasi 100%. Belum ada target resmi. Produk yang sama dengan satuan berbeda tidak dihitung.
      </p>

      {!kpiR.ok ? (
        fail(kpiR, 'Indikator utama')
      ) : empty ? (
        <EmptyState
          title={`Data ESB untuk ${ptNames} belum tersedia pada rentang ini.`}
          reason={hint ?? 'Tidak ada permintaan matang pada rentang, cabang, dan pemasok terpilih. Coba perluas rentang tanggal.'}
          icon="clock"
        />
      ) : (
        <>
          <Section title="Indikator utama" id="kpi">
            <KpiRow>
              <KpiTile label="Fill rate outlet (baris)" value={frCur} fmt="pct" delta={{ value: pp(frCur, numOrNull(k?.fill_rate_line_prev), hasPrev), kind: 'pp', goodWhen: 'up' }} info="Rata-rata pemenuhan per baris permintaan (dikirim dibanding diminta, maksimum 100% per baris)." />
              <KpiTile label="% baris penuh" value={fullCur} fmt="pct" delta={{ value: pp(fullCur, numOrNull(k?.pct_lines_full_prev), hasPrev), kind: 'pp', goodWhen: 'up' }} />
              <KpiTile label="% permintaan penuh" value={reqFullCur} fmt="pct" delta={{ value: pp(reqFullCur, numOrNull(k?.pct_requests_full_prev), hasPrev), kind: 'pp', goodWhen: 'up' }} />
              <KpiTile
                label="Waktu kirim rata-rata (hari)"
                value={leadCur}
                fmt="num"
                note={leadPrev === null ? undefined : `Periode lalu ${leadPrev.toFixed(1).replace('.', ',')} hari`}
                delta={{ value: leadCur !== null && leadPrev !== null && hasPrev ? leadCur - leadPrev : null, kind: 'pp', goodWhen: 'down' }}
              />
            </KpiRow>
            <p className="muted small">
              {fmtInt(linesCur)} baris permintaan matang dihitung.
              {open > 0 ? ` ${fmtInt(open)} baris masih berjalan (belum matang) tidak dihitung.` : ''}
              {mismatch > 0 ? ` ${fmtInt(mismatch)} baris tidak dihitung karena satuan berbeda antara permintaan dan pengiriman.` : ''}
            </p>
          </Section>

          {fail(trendR, 'Tren mingguan') ?? <ChartCard title="Tren Fill rate outlet (baris) per minggu" subtitle="Menurut tanggal permintaan; minggu mulai Senin" specs={[fillRateTrendSpec(trend)]} />}

          {fail(outletR, 'Fill rate outlet (baris)') ?? (
            <ChartCard
              title="Fill rate outlet (baris), 15 terendah"
              subtitle="Batang horizontal; tanpa target resmi"
              specs={[fillRateByOutletSpec(outletRows.map((r) => ({ label: String(r.branch_label), value: r.fill_rate_line as number | null })))]}
              height={Math.max(220, Math.min(15, outletRows.length) * 26 + 60)}
            />
          )}

          <Section title="Per outlet peminta" id="outlet">
            {fail(outletR, 'Per outlet') ?? (
              <DataTable caption="Fill rate baris per outlet (terendah lebih dulu)" columns={OUTLET_COLS} rows={outletRows} defaultSort={{ key: 'fill_rate_line', dir: 'asc' }} />
            )}
          </Section>

          <Section title="Per pemasok (CK)" id="supplier">
            {fail(supplierR, 'Per pemasok') ?? (
              <DataTable caption="Fill rate baris dan waktu kirim per pemasok" columns={SUPPLIER_COLS} rows={supplierRows} defaultSort={{ key: 'fill_rate_line', dir: 'asc' }} />
            )}
          </Section>

          <Section title="Produk paling sering kurang kirim" id="products">
            {fail(productR, 'Produk kurang kirim') ?? (
              <>
                <p className="muted small">
                  Hingga 50 produk menurut qty kurang kirim. Data bulanan: mencakup bulan penuh yang tersentuh rentang. Qty per satuan (tidak dijumlah lintas
                  satuan); pemenuhan qty = qty dikirim (dibatasi qty diminta) dibanding qty diminta.
                </p>
                <DataTable caption="Produk dengan qty kurang kirim terbesar" columns={PRODUCT_COLS} rows={productRows} defaultSort={{ key: 'short_qty', dir: 'desc' }} />
              </>
            )}
          </Section>
        </>
      )}
    </>
  );
}
