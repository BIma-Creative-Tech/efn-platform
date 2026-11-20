/**
 * Tabel server sederhana untuk kartu supermarket: <table> asli, caption sr-only, <th scope>, wadah gulir berfokus
 * (tabindex=0, role=region, aria-label) agar bisa digulir dengan keyboard. Jumlah baris dibatasi LIMIT di katalog query,
 * jadi tanpa paginasi klien. Ekspor CSV kartu SM belum dibangun (laporan G47).
 */
import type { ReactNode } from 'react';

export interface SmColumn<T> {
  header: string;
  align?: 'left' | 'right';
  cell: (row: T) => ReactNode;
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
  return (
    <div className="table-wrap" tabIndex={0} role="region" aria-label={`Tabel: ${caption}`}>
      <table className="table">
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c.header} scope="col" className={c.align === 'right' ? 'num' : undefined}>
                {c.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={rowKey(r, i)}>
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
          ))}
        </tbody>
      </table>
    </div>
  );
}
