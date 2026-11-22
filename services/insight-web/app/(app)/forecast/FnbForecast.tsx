/**
 * Forecast F&B (ESB): KPI, grafik p50 + p10/p90, tabel per cabang, top 20 produk. Sumber v_esb_forecast_daily (run OK terbaru per PT).
 * Gudang pusat (DC, env INSIGHT_FNB_DC_CODES) = agregat outlet: tidak ikut total/grafik/top produk, diberi label di tabel per cabang.
 */
import { Block } from '@/components/blocks';
import { ChartCard } from '@/components/ChartCard';
import { ActionLink, SmKpiTile } from '@/components/sm/SmCard';
import { SmTable } from '@/components/sm/SmTable';
import { EmptyState, KpiRow, Section } from '@/components/ui';
import { summarize, type ChartSpec } from '@/lib/chart-spec';
import { DC_LABEL, isDcCode, splitDc } from '@/lib/fnb-forecast';
import { fmtDayShort, fmtQty } from '@/lib/format';
import { odooListUrl } from '@/lib/odoo-links';
import type { Result } from '@/lib/loaders';
import { getConfig } from '@/lib/runtime';
import { FORECAST_DAYS } from '@/lib/smd-data';
import { fmtStampWib } from '@/lib/sm-format';
import { parseChDateTime, toNum, type Raw } from '@/lib/sm-parse';

const q = (v: unknown): string => fmtQty(Math.round(toNum(v)));

export function FnbForecast({
  next,
  wh,
  top,
  dcCodes,
  now,
}: {
  next: Result<Raw[]>;
  wh: Result<Raw[]>;
  top: Result<Raw[]>;
  dcCodes: readonly string[];
  now: Date;
}) {
  const nRows = next.ok ? next.data : [];
  const total = nRows.reduce((s, r) => s + toNum(r.yhat_sum), 0);
  const gens = nRows.map((r) => parseChDateTime(r.gen_at)).filter((d): d is Date => d !== null);
  const gen = gens.length > 0 ? new Date(Math.max(...gens.map((d) => d.getTime()))) : null;
  const whAll = wh.ok ? wh.data : [];
  const whParts = splitDc(whAll, dcCodes);
  const branchN = whParts.outlets.length;

  return (
    <Section title="Forecast F&B (ESB)" id="fnb-next">
      <p className="muted small" role="note">
        Forecast adalah usulan; akurasi backtest dan evaluasi aktual vs forecast menyusul (data penjualan ESB harian belum berjalan setelah cutover). Angka dalam
        satuan produk (qty), bukan Rupiah; jendela {FORECAST_DAYS} hari ke depan. Gudang pusat (agregat outlet) tidak dijumlahkan ke total.
      </p>
      <KpiRow>
        <SmKpiTile
          label={`Total forecast ${FORECAST_DAYS} hari`}
          value={!next.ok ? 'Tidak dapat dimuat' : nRows.length === 0 ? 'Belum ada forecast' : `${fmtQty(Math.round(total))} unit`}
          muted={nRows.length === 0}
          note="Jumlah qty semua produk non-gudang pusat; satuan campuran"
        />
        <SmKpiTile
          label="Jumlah cabang"
          value={!wh.ok ? 'Tidak dapat dimuat' : whAll.length === 0 ? 'Belum ada forecast' : fmtQty(branchN)}
          muted={whAll.length === 0}
          note="Cabang/gudang dengan forecast, tanpa gudang pusat"
        />
        <SmKpiTile
          label="Forecast dibuat"
          value={!next.ok ? 'Tidak dapat dimuat' : gen ? fmtStampWib(gen, now) : 'Belum ada forecast'}
          muted={!gen}
          note="Run forecast berstatus OK terbaru"
        />
      </KpiRow>

      <Block result={next} title="Forecast 14 hari">
        {(rows) => {
          if (rows.length === 0) return <EmptyState title="Belum ada forecast untuk PT ini" reason="Belum ada run forecast berstatus OK untuk PT yang dipilih." icon="info" />;
          const cats = rows.map((r) => fmtDayShort(String(r.forecast_date)));
          const y = rows.map((r) => toNum(r.yhat_sum));
          const lo = rows.map((r) => toNum(r.p10_sum));
          const hi = rows.map((r) => toNum(r.p90_sum));
          const spec: ChartSpec = {
            id: 'fnb-fc-next',
            kind: 'line',
            fmt: 'num',
            categories: cats,
            series: [
              { name: 'Forecast (p50)', data: y, color: 'c1' },
              { name: 'Batas bawah (p10)', data: lo, color: 'muted', dashed: true },
              { name: 'Batas atas (p90)', data: hi, color: 'c2', dashed: true },
            ],
            summary: summarize('Forecast harian', cats, y, 'num', true),
          };
          return (
            <>
              <p className="muted small">Pita p10-p90 = jumlah batas per produk dan cabang, jadi lebih lebar dari rentang ketidakpastian total sebenarnya.</p>
              <ChartCard title="Forecast harian semua produk (qty)" subtitle="Garis tegas = forecast; garis putus = batas p10 dan p90" specs={[spec]} />
              <SmTable
                caption="Forecast harian F&B dengan batas p10 dan p90"
                rows={rows}
                rowKey={(r) => String(r.forecast_date)}
                columns={[
                  { header: 'Tanggal', cell: (r) => fmtDayShort(String(r.forecast_date)) },
                  { header: 'Forecast', align: 'right', cell: (r) => q(r.yhat_sum) },
                  { header: 'Batas bawah (p10)', align: 'right', cell: (r) => q(r.p10_sum) },
                  { header: 'Batas atas (p90)', align: 'right', cell: (r) => q(r.p90_sum) },
                ]}
              />
            </>
          );
        }}
      </Block>

      <Block result={wh} title="Forecast per cabang">
        {(rows) =>
          rows.length === 0 ? (
            <EmptyState title="Belum ada forecast untuk PT ini" icon="info" />
          ) : (
            <SmTable
              caption={`Forecast ${FORECAST_DAYS} hari per cabang (qty)`}
              rows={rows}
              rowKey={(r) => `${String(r.company_code)}:${String(toNum(r.warehouse_id))}`}
              columns={[
                {
                  header: 'Cabang',
                  cell: (r) => {
                    const name = String(r.wh_label ?? '') || String(r.wh_code ?? '') || `Gudang #${toNum(r.warehouse_id)}`;
                    return isDcCode(r.wh_code, dcCodes) ? `${name} - ${DC_LABEL}` : name;
                  },
                  text: (r) => `${String(r.wh_label ?? '')} ${String(r.wh_code ?? '')}`,
                },
                { header: 'Forecast', align: 'right', cell: (r) => q(r.yhat_sum) },
                { header: 'Batas bawah (p10)', align: 'right', cell: (r) => q(r.p10_sum) },
                { header: 'Batas atas (p90)', align: 'right', cell: (r) => q(r.p90_sum) },
                { header: 'Produk', align: 'right', cell: (r) => fmtQty(toNum(r.product_n)) },
                { header: 'Keterangan', cell: (r) => (isDcCode(r.wh_code, dcCodes) ? 'Tidak dijumlah ke total' : ''), text: (r) => (isDcCode(r.wh_code, dcCodes) ? DC_LABEL : '') },
              ]}
            />
          )
        }
      </Block>

      <Block result={top} title="Top 20 produk">
        {(rows) =>
          rows.length === 0 ? (
            <EmptyState title="Belum ada forecast untuk PT ini" icon="info" />
          ) : (
            <SmTable
              caption={`20 produk dengan forecast ${FORECAST_DAYS} hari terbesar (qty, tanpa gudang pusat)`}
              rows={rows}
              rowKey={(r) => `${String(r.company_code)}:${String(r.product_code)}`}
              columns={[
                { header: 'Produk', cell: (r) => String(r.product_label ?? '') || String(r.product_code), text: (r) => `${String(r.product_label ?? '')} ${String(r.product_code ?? '')}` },
                { header: 'Kategori', cell: (r) => String(r.category_label ?? '') || '-', text: (r) => String(r.category_label ?? ''), filter: true },
                { header: 'Forecast', align: 'right', cell: (r) => q(r.yhat_sum) },
                { header: 'Batas bawah (p10)', align: 'right', cell: (r) => q(r.p10_sum) },
                { header: 'Batas atas (p90)', align: 'right', cell: (r) => q(r.p90_sum) },
              ]}
            />
          )
        }
      </Block>

      <div className="card-tools">
        <ActionLink label="Buka forecast harian di Odoo" href={odooListUrl(getConfig().odooOrigin, 'efn.forecast.daily')} />
      </div>
    </Section>
  );
}
