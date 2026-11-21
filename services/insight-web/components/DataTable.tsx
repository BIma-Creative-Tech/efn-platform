'use client';
/**
 * Tabel data (spec §6): <table> asli, <th scope>, aria-sort, sortir kolom, paginasi, filter cepat "hanya bermasalah",
 * tombol drill-down di sel, tautan ekspor CSV (route handler server-side dengan filter & cakupan yang sama).
 */
import { useMemo, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import { useDrill } from '@/lib/client-nav';
import { fmtDate, fmtIdr, fmtIdrShort, fmtInt, fmtPct, fmtQty } from '@/lib/format';
import { segBadgeClass } from '@/lib/smd-merge';
import type { ColumnDef, TableName } from '@/lib/table-defs';
import type { Status } from '@/lib/thresholds';
import { EmptyState, StatusPill } from './ui';

export type CellValue = string | number | boolean | null;
export type TableRow = Record<string, CellValue>;

function formatCell(col: ColumnDef, v: CellValue): string {
  if (v === null || v === undefined || v === '') return col.fmt && col.fmt !== 'text' ? 'n/a' : '-';
  switch (col.fmt) {
    case 'idr':
      return fmtIdr(Number(v));
    case 'idr-short':
      return fmtIdrShort(Number(v));
    case 'int':
      return fmtInt(Number(v));
    case 'num':
      return fmtQty(Number(v));
    case 'pct':
      return fmtPct(Number(v));
    case 'date':
      return fmtDate(String(v));
    default:
      return String(v);
  }
}

/** Sel non-interaktif (footer) dan sel segmen; segmen = lencana teks (tidak hanya warna). */
function cellContent(col: ColumnDef, v: CellValue): React.ReactNode {
  if (col.fmt === 'segment') {
    return typeof v === 'string' && v !== '' ? <span className={segBadgeClass(v)}>{v}</span> : '-';
  }
  return formatCell(col, v);
}

function cmp(a: CellValue, b: CellValue): number {
  if (a === null && b === null) return 0;
  if (a === null) return 1; // null selalu di akhir
  if (b === null) return -1;
  if (typeof a === 'number' && typeof b === 'number') return a - b;
  return String(a).localeCompare(String(b), 'id');
}

export function DataTable({
  caption,
  columns,
  rows,
  csvName,
  pageSize = 25,
  defaultSort,
  quickFilter,
  emptyTitle = 'Tidak ada data pada periode ini.',
  stickyFirstCol = true,
  footerRows,
}: {
  caption: string;
  columns: ColumnDef[];
  rows: TableRow[];
  csvName?: TableName;
  pageSize?: 10 | 25 | 50;
  defaultSort?: { key: string; dir: 'asc' | 'desc' };
  /** Filter cepat: tombol toggle yang hanya menampilkan baris dengan `status` tertentu. */
  quickFilter?: { label: string; statuses: Status[] };
  emptyTitle?: string;
  stickyFirstCol?: boolean;
  /** Baris subtotal/total: selalu tampil di <tfoot>, tidak ikut sortir/paginasi, tidak ikut CSV. */
  footerRows?: TableRow[];
}): React.ReactNode {
  const sp = useSearchParams();
  const drill = useDrill();
  const [sort, setSort] = useState(defaultSort ?? null);
  const [page, setPage] = useState(0);
  const [onlyBad, setOnlyBad] = useState(false);

  const view = useMemo(() => {
    let r = rows;
    if (quickFilter && onlyBad) r = r.filter((x) => quickFilter.statuses.includes(x.status as Status));
    if (sort) {
      const dir = sort.dir === 'asc' ? 1 : -1;
      r = [...r].sort((a, b) => {
        const av = a[sort.key] ?? null;
        const bv = b[sort.key] ?? null;
        // Nilai kosong tetap di akhir untuk kedua arah.
        if (av === null || bv === null) return cmp(av, bv);
        return dir * cmp(av, bv);
      });
    }
    return r;
  }, [rows, sort, quickFilter, onlyBad]);

  const pages = Math.max(1, Math.ceil(view.length / pageSize));
  const cur = Math.min(page, pages - 1);
  const slice = view.slice(cur * pageSize, cur * pageSize + pageSize);
  const csvHref = csvName ? `/api/export/${csvName}?${sp.toString()}` : null;

  const toggleSort = (key: string): void => {
    setPage(0);
    setSort((s) => (s && s.key === key ? { key, dir: s.dir === 'asc' ? 'desc' : 'asc' } : { key, dir: 'desc' }));
  };

  return (
    <div className="card table-card">
      <div className="card-head">
        <span className="card-title">{caption}</span>
        <div className="card-tools">
          {quickFilter ? (
            <button type="button" className="btn btn-sm" aria-pressed={onlyBad} onClick={() => { setOnlyBad((p) => !p); setPage(0); }}>
              {quickFilter.label}
            </button>
          ) : null}
          {csvHref ? (
            <a className="btn btn-sm" href={csvHref} download>
              Ekspor CSV
            </a>
          ) : null}
        </div>
      </div>
      {rows.length === 0 ? (
        <EmptyState title={emptyTitle} reason="Filter mungkin terlalu sempit, atau data belum masuk." />
      ) : (
        <>
          <div className="table-wrap" tabIndex={0} role="region" aria-label={`Tabel: ${caption}`}>
            <table className={`table${stickyFirstCol ? ' sticky-first' : ''}`}>
              <caption className="sr-only">{caption}</caption>
              <thead>
                <tr>
                  {columns.map((c) => {
                    const active = sort?.key === c.key;
                    const sortable = c.sortable !== false;
                    return (
                      <th
                        key={c.key}
                        scope="col"
                        className={c.align === 'right' ? 'num' : undefined}
                        aria-sort={active ? (sort?.dir === 'asc' ? 'ascending' : 'descending') : sortable ? 'none' : undefined}
                      >
                        {sortable ? (
                          <button type="button" className="th-btn" onClick={() => toggleSort(c.key)}>
                            {c.header}
                            <span aria-hidden="true">{active ? (sort?.dir === 'asc' ? ' ▲' : ' ▼') : ''}</span>
                          </button>
                        ) : (
                          c.header
                        )}
                      </th>
                    );
                  })}
                </tr>
              </thead>
              <tbody>
                {slice.map((r, ri) => (
                  <tr key={`${cur}-${ri}`}>
                    {columns.map((c, ci) => {
                      const v = r[c.key] ?? null;
                      const key = c.drill ? r[c.drill.valueKey] : null;
                      const content =
                        c.fmt === 'status' ? (
                          <StatusPill status={(typeof v === 'string' ? v : 'unknown') as Status} />
                        ) : c.drill && typeof key === 'string' && key !== '' ? (
                          <button type="button" className="link-btn" onClick={() => drill(c.drill!.param, key, String(v))}>
                            {formatCell(c, v)}
                          </button>
                        ) : (
                          cellContent(c, v)
                        );
                      return ci === 0 ? (
                        <th key={c.key} scope="row" className={c.align === 'right' ? 'num' : undefined}>
                          {content}
                        </th>
                      ) : (
                        <td key={c.key} className={c.align === 'right' ? 'num' : undefined}>
                          {content}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
              {footerRows && footerRows.length > 0 ? (
                <tfoot>
                  {footerRows.map((r, ri) => (
                    <tr key={`f-${ri}`} className="row-subtotal">
                      {columns.map((c, ci) => {
                        const content = cellContent(c, r[c.key] ?? null);
                        return ci === 0 ? (
                          <th key={c.key} scope="row" className={c.align === 'right' ? 'num' : undefined}>
                            {content}
                          </th>
                        ) : (
                          <td key={c.key} className={c.align === 'right' ? 'num' : undefined}>
                            {content}
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tfoot>
              ) : null}
            </table>
          </div>
          {pages > 1 ? (
            <nav className="pager" aria-label={`Halaman tabel ${caption}`}>
              <button type="button" className="btn btn-sm" disabled={cur === 0} onClick={() => setPage(cur - 1)}>
                Sebelumnya
              </button>
              <span aria-live="polite">
                Halaman {cur + 1} dari {pages} ({view.length} baris)
              </span>
              <button type="button" className="btn btn-sm" disabled={cur >= pages - 1} onClick={() => setPage(cur + 1)}>
                Berikutnya
              </button>
            </nav>
          ) : (
            <p className="muted small pager-note">{view.length} baris</p>
          )}
        </>
      )}
    </div>
  );
}
