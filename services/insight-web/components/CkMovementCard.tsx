import { BRANCH_KIND_ORDER, ITEM_GROUP_LABEL, ITEM_GROUP_ORDER, kindLabel } from '@/lib/flow-notes';
import { fmtInt, fmtQty } from '@/lib/format';
import type { Result, Row } from '@/lib/loaders';
import { num } from '@/lib/loaders';
import { TABLES } from '@/lib/table-defs';
import { Block } from './blocks';
import { DataTable, type TableRow } from './DataTable';
import { SmTable } from './sm/SmTable';
import { Section } from './ui';

export interface GroupRow {
  origin_kind: string;
  dest_kind: string;
  item_group: string;
  uom: string;
  qty_sum: number;
  lines_n: number;
  skus: number;
}

const idx = (list: readonly string[], v: string): number => {
  const i = list.indexOf(v);
  return i < 0 ? 99 : i;
};

/**
 * Kartu "Kiriman barang per asal dan kelompok" (review 21 Nov 2026). `v_esb_flow_daily` hanya memuat produk jadi (FIN). Data nyata
 * PB: Central Kitchen mengirim FIN dan setengah jadi (WIP); bahan baku, kemasan, dan supplies dikirim gudang (WHProd, Warehouse
 * Palem) dan sebagian toko (KACS). Kartu ini memperlihatkan semua kiriman per asal -> tujuan: jumlah baris dan SKU per kelompok,
 * qty per satuan (TIDAK dijumlah lintas satuan), lalu tabel per kategori ESB yang bisa dicari dan disaring.
 */
export function CkMovementCard({ groupR, catR }: { groupR: Result<GroupRow[]>; catR: Result<Row[]> }): React.ReactNode {
  return (
    <Section title="Kiriman barang per asal dan kelompok (semua jenis)" id="ck-groups">
      <p className="notice" role="note">
        Angka alur barang di halaman ini hanya produk jadi (FIN). Selain FIN, Central Kitchen mengirim barang setengah jadi (WIP); bahan baku, kemasan, dan
        supplies dikirim dari gudang. Toko lalu membuat sebagian produk jadi sendiri (produksi toko) dari WIP dan bahan baku itu. Qty ditampilkan per satuan dan
        tidak dijumlah antar satuan.
      </p>
      <Block result={groupR} title="Kiriman per asal dan kelompok barang">
        {(rows) => {
          const sorted = [...rows].sort(
            (a, b) =>
              idx(BRANCH_KIND_ORDER, a.origin_kind) - idx(BRANCH_KIND_ORDER, b.origin_kind) ||
              idx(BRANCH_KIND_ORDER, a.dest_kind) - idx(BRANCH_KIND_ORDER, b.dest_kind) ||
              idx(ITEM_GROUP_ORDER, a.item_group) - idx(ITEM_GROUP_ORDER, b.item_group) ||
              String(a.uom).localeCompare(String(b.uom), 'id'),
          );
          return (
            <SmTable
              caption="Kiriman barang per asal, tujuan, kelompok barang, dan satuan"
              rows={sorted}
              rowKey={(r) => `${r.origin_kind}|${r.dest_kind}|${r.item_group}|${r.uom}`}
              columns={[
                { header: 'Asal', cell: (r) => kindLabel(r.origin_kind), text: (r) => kindLabel(r.origin_kind), filter: true },
                { header: 'Tujuan', cell: (r) => kindLabel(r.dest_kind), text: (r) => kindLabel(r.dest_kind), filter: true },
                { header: 'Kelompok barang', cell: (r) => ITEM_GROUP_LABEL[r.item_group] ?? r.item_group, text: (r) => ITEM_GROUP_LABEL[r.item_group] ?? r.item_group, filter: true },
                { header: 'Satuan', cell: (r) => r.uom, text: (r) => r.uom },
                { header: 'Baris kiriman', align: 'right', cell: (r) => fmtInt(num(r.lines_n)) },
                { header: 'SKU', align: 'right', cell: (r) => fmtInt(num(r.skus)) },
                { header: 'Qty (per satuan)', align: 'right', cell: (r) => fmtQty(num(r.qty_sum)) },
              ]}
            />
          );
        }}
      </Block>
      <Block result={catR} title="Kiriman per kategori ESB">
        {(rows) => (
          <DataTable
            caption={TABLES['mov-category'].caption}
            columns={TABLES['mov-category'].columns}
            rows={rows as TableRow[]}
            csvName="mov-category"
            pageSize={25}
            defaultSort={{ key: 'origin_kind', dir: 'asc' }}
            emptyTitle="Tidak ada kiriman pada periode ini."
          />
        )}
      </Block>
    </Section>
  );
}
