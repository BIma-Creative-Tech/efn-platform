import type { Metadata } from 'next';
import type { ReactNode } from 'react';
import { ErrorCard } from '@/components/blocks';
import { ChartCard } from '@/components/ChartCard';
import { DataTable, type TableRow } from '@/components/DataTable';
import { FilterNotice } from '@/components/FilterNotice';
import { CheckGroup, G68Form } from '@/components/g68/G68Form';
import { EmptyState, KpiRow, KpiTile, PageHeader, Section } from '@/components/ui';
import { codeName } from '@/lib/brands';
import { fmtIdrShort, fmtRange } from '@/lib/format';
import {
  COVERAGE_KEYS,
  coverageHint,
  isWeekly,
  parseUsageClasses,
  pctSafe,
  USAGE_CLASSES,
  USAGE_LABEL,
  usageLabel,
  wasteTrendSpec,
  type WasteTrendRow,
} from '@/lib/g68-vm';
import { g68Page, type G68SearchParams } from '@/lib/g68-page';
import { loadCoverage, num, numOrNull, type Result } from '@/lib/loaders';
import { changePct } from '@/lib/pnl';
import { runEsb } from '@/lib/smd-data';
import type { ColumnDef } from '@/lib/table-defs';

export const dynamic = 'force-dynamic';
export const metadata: Metadata = { title: 'Waste & Pemakaian Internal' };

type Raw = Record<string, unknown>;
const L = 'left' as const;
const R = 'right' as const;

const PURPOSE_COLS: ColumnDef[] = [
  { key: 'usage_class', header: 'Kelas', align: L, filterable: true },
  { key: 'purpose_name', header: 'Purpose jurnal', align: L },
  { key: 'value_sum', header: 'Nilai (HPP)', fmt: 'idr', align: R },
  { key: 'docs_sum', header: 'Jumlah dokumen', fmt: 'int', align: R },
];

const BRANCH_COLS: ColumnDef[] = [
  { key: 'branch_label', header: 'Outlet', align: L },
  { key: 'company_code', header: 'PT', align: L, filterable: true },
  { key: 'waste_value', header: 'Waste (HPP)', fmt: 'idr', align: R },
  { key: 'waste_pct_net', header: 'Waste % penjualan bersih', fmt: 'pct', align: R },
  { key: 'internal_value', header: 'Pemakaian internal (HPP)', fmt: 'idr', align: R },
];

const PRODUCT_COLS: ColumnDef[] = [
  { key: 'product_label', header: 'Produk', align: L },
  { key: 'brand', header: 'Brand', align: L, filterable: true },
  { key: 'uom', header: 'Satuan', align: L },
  { key: 'qty_sum', header: 'Qty keluar (per satuan)', fmt: 'num', align: R },
  { key: 'value_sum', header: 'Nilai (HPP)', fmt: 'idr', align: R },
];

const one = (v: string | string[] | undefined): string | undefined => (Array.isArray(v) ? v[0] : v);

function fail(r: Result<unknown>, title: string): ReactNode | null {
  return r.ok ? null : <ErrorCard title={title} refId={r.ref} reason={r.reason} />;
}

/** Waste & Pemakaian Internal (docs/04 §32.8-§32.10): nilai = HPP jurnal item per outlet; kelas purpose = pemetaan sementara (Q-ESB30). */
export default async function WastePage({ searchParams }: { searchParams: G68SearchParams }) {
  const { ctx, adjusted, raw } = await g68Page('/waste', searchParams);
  const { from, to } = ctx.filters;
  const cls = parseUsageClasses(raw);
  const weekly = isWeekly(from, to);
  const extra = { weekly: weekly ? 1 : 0, uclass: cls.uclass };
  const ptNames = ctx.filters.pt.map(codeName).join(', ');

  const [kpiR, trendR, purposeR, rankR, prodR, cov] = await Promise.all([
    runEsb<Raw>(true, 'waste_kpi', ctx, extra),
    runEsb<Raw>(true, 'waste_trend', ctx, extra),
    runEsb<Raw>(true, 'waste_by_purpose', ctx, extra),
    runEsb<Raw>(true, 'waste_branch_rank', ctx, extra),
    runEsb<Raw>(true, 'waste_top_products', ctx, extra),
    loadCoverage(ctx),
  ]);

  const k = kpiR.ok ? kpiR.data[0] : undefined;
  const w = num(k?.waste_cur);
  const wp = num(k?.waste_prev);
  const i = num(k?.internal_cur);
  const ip = num(k?.internal_prev);
  const nc = num(k?.net_cur);
  const np = num(k?.net_prev);
  const wPct = pctSafe(w, nc);
  const iPct = pctSafe(i, nc);
  const wPctPrev = pctSafe(wp, np);
  const iPctPrev = pctSafe(ip, np);
  const pp = (a: number | null, b: number | null): number | null => (a !== null && b !== null ? a - b : null);

  const trend: WasteTrendRow[] = trendR.ok
    ? trendR.data.map((r) => ({ bucket: String(r.bucket), usage_class: String(r.usage_class), value_sum: num(r.value_sum) }))
    : [];

  const purposeRows: TableRow[] = purposeR.ok
    ? purposeR.data.map((r) => ({ usage_class: usageLabel(String(r.usage_class)), purpose_name: String(r.purpose_name ?? ''), value_sum: num(r.value_sum), docs_sum: num(r.docs_sum) }))
    : [];
  const rankRows: TableRow[] = rankR.ok
    ? rankR.data.map((r) => ({
        branch_label: String(r.branch_label ?? ''),
        company_code: String(r.company_code ?? ''),
        waste_value: num(r.waste_value),
        waste_pct_net: numOrNull(r.waste_pct_net),
        internal_value: num(r.internal_value),
      }))
    : [];
  const prodRows: TableRow[] = prodR.ok
    ? prodR.data.map((r) => ({
        product_label: String(r.name_text ?? '') || String(r.product_code ?? ''),
        brand: codeName(String(r.brand ?? '').toUpperCase()),
        uom: String(r.uom ?? ''),
        qty_sum: num(r.qty_sum),
        value_sum: num(r.value_sum),
      }))
    : [];

  const empty = kpiR.ok && w === 0 && i === 0 && wp === 0 && ip === 0 && trend.length === 0 && purposeRows.length === 0;
  const hint = coverageHint(cov, COVERAGE_KEYS.usage);

  return (
    <>
      <PageHeader title="Waste & Pemakaian Internal" question="Berapa nilai barang yang terbuang atau dipakai internal di tiap outlet, dan seberapa besar dibanding penjualan?" />
      <FilterNotice adjusted={adjusted || cls.adjusted} />

      <G68Form
        path="/waste"
        label="Pilih kelas pemakaian"
        carry={{ pt: one(raw.pt), branch: one(raw.branch), from: one(raw.from), to: one(raw.to) }}
        summary={
          <>
            Periode: {fmtRange(from, to)} ({weekly ? 'tren per minggu' : 'tren per hari'}). PT: {ptNames}. Kelas terpilih memengaruhi tren, rincian purpose, dan
            produk; indikator utama selalu Waste dan Pemakaian internal.
          </>
        }
      >
        <CheckGroup
          legend="Kelas pemakaian"
          name="uclass"
          options={USAGE_CLASSES.map((c) => ({ value: c, label: USAGE_LABEL[c] }))}
          checked={cls.uclass}
          hint="Bawaan: Waste dan Pemakaian internal."
        />
      </G68Form>

      <p className="muted small">
        Nilai = HPP pada jurnal item (positif = biaya keluar). Kelas dipetakan dari purpose jurnal ESB: purpose &quot;WASTE&quot; dan
        &quot;PRODUCTION SUPPLIES&quot; dihitung sebagai waste; &quot;OTHER RECEIVABLE&quot; bukan biaya outlet.
      </p>

      {!kpiR.ok ? (
        fail(kpiR, 'Indikator utama')
      ) : empty ? (
        <EmptyState
          title={`Data ESB untuk ${ptNames} belum tersedia pada rentang ini.`}
          reason={hint ?? 'Tidak ada jurnal waste atau pemakaian internal pada rentang dan cabang terpilih.'}
          icon="clock"
        />
      ) : (
        <>
          <Section title="Indikator utama" id="kpi">
            <KpiRow>
              <KpiTile label="Waste" value={w} fmt="idr-short" note={`Periode lalu ${fmtIdrShort(wp)}`} delta={{ value: changePct(w, wp === 0 ? null : wp), kind: 'pct', goodWhen: 'down' }} info="Nilai HPP barang yang dibuang (purpose WASTE)." />
              <KpiTile label="Waste % penjualan bersih" value={wPct} fmt="pct" delta={{ value: pp(wPct, wPctPrev), kind: 'pp', goodWhen: 'down' }} info="Waste dibagi penjualan bersih outlet pada periode yang sama." />
              <KpiTile label="Pemakaian internal" value={i} fmt="idr-short" note={`Periode lalu ${fmtIdrShort(ip)}`} delta={{ value: changePct(i, ip === 0 ? null : ip), kind: 'pct', goodWhen: 'neutral' }} info="Perlengkapan produksi, kemasan, kebersihan, seragam, dan sejenisnya (kelas pemakaian internal)." />
              <KpiTile label="Pemakaian internal % penjualan bersih" value={iPct} fmt="pct" delta={{ value: pp(iPct, iPctPrev), kind: 'pp', goodWhen: 'neutral' }} />
            </KpiRow>
            {nc === 0 ? <p className="muted small">Penjualan bersih periode ini nol atau belum masuk: persentase n/a.</p> : null}
          </Section>

          {fail(trendR, 'Tren pemakaian') ?? (
            <ChartCard
              title={weekly ? 'Tren nilai per minggu' : 'Tren nilai per hari'}
              subtitle="Batang bertumpuk menurut kelas pemakaian"
              specs={[wasteTrendSpec(trend, weekly)]}
            />
          )}

          <Section title="Rincian purpose jurnal" id="purpose">
            {fail(purposeR, 'Rincian purpose') ?? (
              <DataTable caption="Nilai menurut kelas dan purpose jurnal" columns={PURPOSE_COLS} rows={purposeRows} defaultSort={{ key: 'value_sum', dir: 'desc' }} />
            )}
          </Section>

          <Section title="Per outlet" id="branch">
            {fail(rankR, 'Per outlet') ?? (
              <DataTable caption="Waste dan pemakaian internal per outlet" columns={BRANCH_COLS} rows={rankRows} defaultSort={{ key: 'waste_value', dir: 'desc' }} />
            )}
          </Section>

          <Section title="Produk terbesar" id="products">
            {fail(prodR, 'Produk terbesar') ?? (
              <>
                <p className="muted small">
                  Hingga 50 produk menurut nilai. Data bulanan: mencakup bulan penuh yang tersentuh rentang. Qty per satuan (tidak dijumlah lintas satuan).
                </p>
                <DataTable caption="Produk dengan nilai pemakaian terbesar" columns={PRODUCT_COLS} rows={prodRows} defaultSort={{ key: 'value_sum', dir: 'desc' }} />
              </>
            )}
          </Section>
        </>
      )}
    </>
  );
}
