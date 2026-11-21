import type { Metadata } from 'next';
import { Block } from '@/components/blocks';
import { DataTable, type TableRow } from '@/components/DataTable';
import { FilterNotice } from '@/components/FilterNotice';
import { MixedScopeNote, NotApplicableSmd } from '@/components/SmdNotice';
import { EmptyState, PageHeader, Section, StatusPill } from '@/components/ui';
import { settle, TABLE_LOADERS, type Result, type Row } from '@/lib/loaders';
import { pageCtx, type SearchParams } from '@/lib/page-ctx';
import { TABLES, type TableName } from '@/lib/table-defs';
import { unmappedStatus, worst, type Status } from '@/lib/thresholds';

export const dynamic = 'force-dynamic';
export const metadata: Metadata = { title: 'Kualitas Data' };

function Tile({ title, status, text }: { title: string; status: Status; text: string }) {
  return (
    <div className="kpi">
      <dt>{title}</dt>
      <dd>
        <StatusPill status={status} />
        <span className="kpi-note">{text}</span>
      </dd>
    </div>
  );
}

function Findings({ name, result }: { name: TableName; result: Result<Row[]> }) {
  const def = TABLES[name];
  return (
    <Block result={result} title={def.caption}>
      {(rows) => (
        <DataTable
          caption={def.caption}
          columns={def.columns}
          rows={rows as TableRow[]}
          csvName={name}
          emptyTitle="Tidak ada temuan."
        />
      )}
    </Block>
  );
}

export default async function DataQualityPage({ searchParams }: { searchParams: SearchParams }) {
  const { ctx, adjusted, sel } = await pageCtx(searchParams);

  // G51 B1: kualitas data ESB. Hanya SMD dipilih -> tanpa query ESB; campuran -> brand ESB saja + catatan.
  if (!sel.esb) {
    return (
      <>
        <PageHeader title="Kualitas Data" question="Apakah angka yang saya lihat bisa dipercaya?" />
        <FilterNotice adjusted={adjusted} />
        <NotApplicableSmd reason="Pemeriksaan ini khusus ekstraksi data ESB (brand F&B). Kesegaran data supermarket ada di lencana 'Data' pada halaman Supermarket." />
      </>
    );
  }

  const [fresh, run, alias, nonEa, noSales] = await Promise.all([
    settle(TABLE_LOADERS['dq-freshness'](ctx)),
    settle(TABLE_LOADERS['dq-last-run'](ctx)),
    settle(TABLE_LOADERS['dq-alias'](ctx)),
    settle(TABLE_LOADERS['dq-non-ea'](ctx)),
    settle(TABLE_LOADERS['dq-no-sales'](ctx)),
  ]);

  const freshStatus: Status = fresh.ok
    ? fresh.data.length === 0
      ? 'unknown'
      : worst(fresh.data.map((r) => r.status as Status))
    : 'unknown';
  const count = (r: Result<Row[]>): number | null => (r.ok ? r.data.length : null);
  const allClear = [run, alias, nonEa, noSales].every((r) => r.ok && r.data.length === 0) && freshStatus === 'good';

  return (
    <>
      <PageHeader title="Kualitas Data" question="Apakah angka yang saya lihat bisa dipercaya?" />
      <FilterNotice adjusted={adjusted} />
      {sel.smd ? <MixedScopeNote /> : null}
      <p className="muted small">Jendela pemeriksaan tetap (30 hari / kemarin / terakhir); filter tanggal dan cabang tidak berlaku di halaman ini.</p>

      <Section title="Papan status" id="board">
        <dl className="kpi-row">
          <Tile title="Kesegaran ekstraksi" status={freshStatus} text={freshStatus === 'unknown' ? 'Kesegaran tidak diketahui' : 'Umur data per brand dan entitas'} />
          <Tile title="Run terakhir" status={count(run) === null ? 'unknown' : count(run) === 0 ? 'good' : 'bad'} text={`${count(run) ?? '-'} temuan`} />
          <Tile title="Alias tak terpetakan" status={count(alias) === null ? 'unknown' : unmappedStatus(count(alias) ?? 0)} text={`${count(alias) ?? '-'} alias`} />
          <Tile title="Baris non-EA" status={count(nonEa) === null ? 'unknown' : count(nonEa) === 0 ? 'good' : 'warn'} text={`${count(nonEa) ?? '-'} temuan`} />
          <Tile title="Cabang tanpa penjualan kemarin" status={count(noSales) === null ? 'unknown' : count(noSales) === 0 ? 'good' : 'bad'} text={`${count(noSales) ?? '-'} cabang`} />
        </dl>
        {allClear ? <EmptyState tone="positive" title="Tidak ada temuan. Semua pemeriksaan lolos." /> : null}
      </Section>

      <div className="stack">
        <Findings name="dq-freshness" result={fresh} />
        <Findings name="dq-alias" result={alias} />
        <Findings name="dq-no-sales" result={noSales} />
        <Findings name="dq-non-ea" result={nonEa} />
        <Findings name="dq-last-run" result={run} />
      </div>
      <p className="muted small">Pemetaan alias dilakukan admin data di luar Insight.</p>
    </>
  );
}
