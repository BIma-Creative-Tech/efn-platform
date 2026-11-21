/**
 * Tabel server sederhana untuk kartu supermarket: <table> asli, caption sr-only, <th scope>, wadah gulir berfokus
 * (tabindex=0, role=region, aria-label) agar bisa digulir dengan keyboard. Jumlah baris dibatasi LIMIT di katalog query,
 * jadi tanpa paginasi klien. Ekspor CSV kartu SM belum dibangun (laporan G47).
 *
 * Pencarian/filter (poin 4, 21 Nov 2026): kolom yang mendefinisikan `text` ikut dicari; `filter: true` menambah dropdown
 * nilai kolom itu. Penyaringan dilakukan di klien oleh <TableTools> lewat atribut data-* pada baris (server tetap
 * merender semua baris). Alat saring muncul bila ada kolom `text` dan baris > 8, atau ada kolom `filter`.
 */
import { useId, type ReactNode } from 'react';
import { distinct } from '@/lib/table-filter';
import { TermHints } from '../Term';
import { TableTools, type ToolFilter } from '../TableTools';

export interface SmColumn<T> {
  header: string;
  align?: 'left' | 'right';
  cell: (row: T) => ReactNode;
  /** Teks datar sel ini untuk pencarian dan filter (wajib agar kolom ikut dicari). */
  text?: (row: T) => string;
  /** Tampilkan dropdown filter nilai `text` kolom ini (kategori/status). */
  filter?: boolean;
}

export function SmTable<T>({
  caption,
  columns,
  rows,
  rowKey,
}: {
  caption: string;
  columns: SmColumn<T>[];
  rows: readonly T[];
  rowKey: (row: T, index: number) => string;
}): ReactNode {
  // useId juga sah di Server Component; id dipakai TableTools untuk menemukan <table> ini (bukan untuk logika data).
  const tableId = `smt${useId().replace(/[^A-Za-z0-9]/g, '')}`;
  const searchCols = columns.map((c, i) => ({ c, i })).filter((x) => x.c.text);
  const filterCols = searchCols.filter((x) => x.c.filter);
  const tools = searchCols.length > 0 && (rows.length > 8 || filterCols.length > 0);
  const filters: ToolFilter[] = filterCols.map(({ c, i }) => ({
    key: `c${i}`,
    label: c.header,
    options: distinct(rows.map((r) => c.text?.(r) ?? '')),
  }));
  return (
    <>
      {tools ? <TableTools targetId={tableId} caption={caption} filters={filters} /> : null}
      <div className="table-wrap" tabIndex={0} role="region" aria-label={`Tabel: ${caption}`}>
        <table className="table" id={tableId}>
          <caption className="sr-only">{caption}</caption>
          <thead>
            <tr>
              {columns.map((c) => (
                <th key={c.header} scope="col" className={c.align === 'right' ? 'num' : undefined}>
                  {c.header}
                  <TermHints text={c.header} />
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => {
              const attrs: Record<string, string> = {};
              if (tools) {
                attrs['data-q'] = searchCols.map((x) => x.c.text?.(r) ?? '').join(' ');
                for (const { c, i: ci } of filterCols) attrs[`data-fc${ci}`] = c.text?.(r) ?? '';
              }
              return (
                <tr key={rowKey(r, i)} {...attrs}>
                  {columns.map((c, ci) =>
                    ci === 0 ? (
                      <th key={c.header} scope="row" className={c.align === 'right' ? 'num' : undefined}>
                        {c.cell(r)}
                      </th>
                    ) : (
                      <td key={c.header} className={c.align === 'right' ? 'num' : undefined}>
                        {c.cell(r)}
                      </td>
                    ),
                  )}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </>
  );
}
