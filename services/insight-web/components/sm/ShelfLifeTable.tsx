/**
 * Tabel "Umur simpan & sisa umur lot" (G51 B4): produk, kategori, umur simpan, lot terdekat kedaluwarsa per outlet, sisa hari,
 * qty, nilai HPP. Server component; status sisa umur ditulis sebagai teks + kata (tidak hanya warna).
 */
import type { ReactNode } from 'react';
import { fmtDate, fmtIdr, fmtQty } from '@/lib/format';
import { fmtDaysLeft } from '@/lib/sm-format';
import { fmtShelfLife, shelfStatus, type ShelfRow } from '@/lib/sm-shelf';
import { StatusPill } from '../ui';
import { SmTable } from './SmTable';

export function ShelfLifeTable({
  rows,
  whNames,
  showOutlet,
}: {
  rows: readonly ShelfRow[];
  whNames: ReadonlyMap<number, string>;
  showOutlet: boolean;
}): ReactNode {
  return (
    <>
      <p className="muted small">
        Lot terdekat kedaluwarsa per produk dan outlet, dari semua lot berstok. Umur simpan dikelola per produk di Odoo; &quot;tidak dikelola&quot; berarti belum diisi.
        Nilai dalam Rp (HPP). Maksimal 300 baris, terdekat kedaluwarsa lebih dulu.
      </p>
      <SmTable
        caption="Umur simpan dan sisa umur lot terdekat per produk dan outlet"
        rows={rows}
        rowKey={(r) => `${r.productId}-${r.warehouseId}`}
        columns={[
          { header: 'Produk', cell: (r) => r.name },
          { header: 'Kategori', cell: (r) => r.category || '-' },
          { header: 'Umur simpan', align: 'right', cell: (r) => fmtShelfLife(r.shelfDays) },
          ...(showOutlet ? [{ header: 'Outlet', cell: (r: ShelfRow) => whNames.get(r.warehouseId) ?? `Outlet #${r.warehouseId}` }] : []),
          { header: 'Lot terdekat', cell: (r) => r.lotName || '-' },
          { header: 'Kedaluwarsa', cell: (r) => (r.expirationDate ? fmtDate(r.expirationDate) : '-') },
          { header: 'Sisa umur', align: 'right', cell: (r) => fmtDaysLeft(r.daysLeft) },
          { header: 'Qty lot', align: 'right', cell: (r) => fmtQty(r.qty) },
          { header: 'Nilai Rp (HPP)', align: 'right', cell: (r) => fmtIdr(r.valueCost) },
          {
            header: 'Status',
            cell: (r) => {
              const s = shelfStatus(r);
              return <StatusPill status={s.status} label={s.label} />;
            },
          },
        ]}
      />
    </>
  );
}
