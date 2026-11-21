/**
 * Definisi tabel (kolom) yang dipakai bersama oleh DataTable (halaman) dan ekspor CSV (route handler),
 * sehingga isi CSV = isi tabel dengan filter & cakupan yang sama. Murni dan dapat diserialisasi.
 */
export type CellFmt = 'text' | 'idr' | 'idr-short' | 'int' | 'num' | 'pct' | 'date' | 'status' | 'segment';

export interface ColumnDef {
  key: string;
  header: string;
  fmt?: CellFmt;
  align?: 'left' | 'right';
  sortable?: boolean;
  /** Sel menjadi tombol yang menambah filter `param` = nilai kolom `valueKey`. */
  drill?: { param: 'branch' | 'cat' | 'product'; valueKey: string };
  /** Kolom teks panjang (Keterangan): boleh membungkus baris dan tidak ikut sortir. */
  wrap?: boolean;
  /** Tampilkan dropdown filter nilai untuk kolom ini (kategori/status). Kolom `fmt: 'status'` otomatis. */
  filterable?: boolean;
}

export interface TableDef {
  caption: string;
  filename: string;
  columns: ColumnDef[];
}

export const TABLE_NAMES = [
  'branch-rank',
  'flow-outliers',
  'flow-products',
  'top-menu',
  'menu-mix-branch',
  'pay-methods',
  'pay-branches',
  'dist-matrix',
  'dist-gap',
  'mov-category',
  'dq-freshness',
  'dq-alias',
  'dq-no-sales',
  'dq-non-ea',
  'dq-last-run',
] as const;
export type TableName = (typeof TABLE_NAMES)[number];

export function isTableName(s: string): s is TableName {
  return (TABLE_NAMES as readonly string[]).includes(s);
}

const L = 'left' as const;
const R = 'right' as const;

export const TABLES: Record<TableName, TableDef> = {
  'branch-rank': {
    caption: 'Peringkat cabang menurut penjualan bersih',
    filename: 'peringkat-cabang',
    columns: [
      { key: 'branch_label', header: 'Cabang', align: L, drill: { param: 'branch', valueKey: 'branch_key' } },
      { key: 'company_code', header: 'PT', align: L, filterable: true },
      { key: 'net_sales', header: 'Penjualan bersih', fmt: 'idr', align: R },
      { key: 'trx', header: 'Transaksi', fmt: 'int', align: R },
      { key: 'avg_trx', header: 'Rata-rata per transaksi', fmt: 'idr', align: R },
      { key: 'hpp_pct', header: 'HPP %', fmt: 'pct', align: R },
    ],
  },
  'flow-outliers': {
    caption: 'Sell-through per cabang dan produk (terendah lebih dulu)',
    filename: 'sell-through-cabang-produk',
    columns: [
      { key: 'branch_label', header: 'Cabang', align: L, drill: { param: 'branch', valueKey: 'branch_key' } },
      { key: 'product_label', header: 'Produk', align: L, drill: { param: 'product', valueKey: 'product_key' } },
      { key: 'category', header: 'Kategori produk', align: L, filterable: true },
      { key: 'received', header: 'Diterima (bersih transfer)', fmt: 'num', align: R },
      { key: 'store_prod_qty', header: 'Produksi toko', fmt: 'num', align: R },
      { key: 'sold_qty', header: 'Terjual', fmt: 'num', align: R },
      { key: 'sell_through_pct', header: 'Sell-through', fmt: 'pct', align: R },
      { key: 'status', header: 'Status', fmt: 'status', align: L },
      { key: 'reason', header: 'Keterangan', align: L, wrap: true, sortable: false },
    ],
  },
  'flow-products': {
    caption: 'Arus barang per produk',
    filename: 'arus-barang-produk',
    columns: [
      { key: 'product_label', header: 'Produk', align: L, drill: { param: 'product', valueKey: 'product_key' } },
      { key: 'category', header: 'Kategori produk', align: L, filterable: true },
      { key: 'ck_prod_qty', header: 'Produksi CK', fmt: 'num', align: R },
      { key: 'ck_out_qty', header: 'Dikirim CK', fmt: 'num', align: R },
      { key: 'received', header: 'Diterima toko', fmt: 'num', align: R },
      { key: 'store_prod_qty', header: 'Produksi toko', fmt: 'num', align: R },
      { key: 'sold_qty', header: 'Terjual', fmt: 'num', align: R },
      { key: 'sell_through_pct', header: 'Sell-through', fmt: 'pct', align: R },
      { key: 'status', header: 'Status', fmt: 'status', align: L },
      { key: 'reason', header: 'Keterangan', align: L, wrap: true, sortable: false },
    ],
  },
  'top-menu': {
    caption: 'Menu terlaris',
    filename: 'menu',
    columns: [
      { key: 'menu_label', header: 'Menu', align: L },
      { key: 'category', header: 'Kategori', align: L, filterable: true, drill: { param: 'cat', valueKey: 'category' } },
      { key: 'brand_name', header: 'Brand', align: L, filterable: true },
      { key: 'net_sales', header: 'Penjualan bersih', fmt: 'idr', align: R },
      { key: 'qty_sold', header: 'Qty', fmt: 'num', align: R },
      { key: 'margin_pct', header: 'Margin kotor %', fmt: 'pct', align: R },
    ],
  },
  'menu-mix-branch': {
    caption: 'Bauran kategori per cabang (bulan penuh)',
    filename: 'bauran-kategori-cabang',
    columns: [
      { key: 'branch_label', header: 'Cabang', align: L, drill: { param: 'branch', valueKey: 'branch_key' } },
      { key: 'menu_category', header: 'Kategori', align: L, filterable: true },
      { key: 'net_sales', header: 'Penjualan bersih', fmt: 'idr', align: R },
      { key: 'share_pct', header: 'Porsi', fmt: 'pct', align: R },
    ],
  },
  'pay-methods': {
    caption: 'Metode pembayaran dan MDR',
    filename: 'metode-pembayaran',
    columns: [
      { key: 'payment_type', header: 'Tipe', align: L, filterable: true },
      { key: 'method', header: 'Metode', align: L },
      { key: 'cnt', header: 'Jumlah', fmt: 'int', align: R },
      { key: 'amount_total', header: 'Nilai', fmt: 'idr', align: R },
      { key: 'mdr_total', header: 'MDR', fmt: 'idr', align: R },
      { key: 'mdr_pct', header: 'MDR %', fmt: 'pct', align: R },
      { key: 'avg_amount', header: 'Rata-rata per pembayaran', fmt: 'idr', align: R },
    ],
  },
  'pay-branches': {
    caption: 'Pembayaran per cabang',
    filename: 'pembayaran-cabang',
    columns: [
      { key: 'branch_name', header: 'Cabang', align: L, drill: { param: 'branch', valueKey: 'branch_key' } },
      { key: 'amount', header: 'Total pembayaran', fmt: 'idr', align: R },
      { key: 'mdr', header: 'MDR', fmt: 'idr', align: R },
      { key: 'top_method', header: 'Metode dominan', align: L },
    ],
  },
  'dist-matrix': {
    caption: 'Matriks distribusi per cabang',
    filename: 'matriks-distribusi',
    columns: [
      { key: 'branch_label', header: 'Cabang', align: L, drill: { param: 'branch', valueKey: 'branch_key' } },
      { key: 'ck_out_qty', header: 'Dikirim CK', fmt: 'num', align: R },
      { key: 'in_ck_qty', header: 'Diterima dari CK', fmt: 'num', align: R },
      { key: 'in_other_qty', header: 'Diterima non-CK', fmt: 'num', align: R },
      { key: 'st_in_qty', header: 'Transfer masuk', fmt: 'num', align: R },
      { key: 'st_out_qty', header: 'Transfer keluar', fmt: 'num', align: R },
      { key: 'st_net', header: 'Neto transfer', fmt: 'num', align: R },
      { key: 'note', header: 'Keterangan', align: L, wrap: true, sortable: false },
    ],
  },
  'dist-gap': {
    caption: 'Selisih kirim CK vs terima toko per produk-hari (hanya produk jadi FIN)',
    filename: 'selisih-kirim-terima',
    columns: [
      { key: 'day', header: 'Tanggal', fmt: 'date', align: L },
      { key: 'product_label', header: 'Produk', align: L, drill: { param: 'product', valueKey: 'product_key' } },
      { key: 'category', header: 'Kategori produk', align: L, filterable: true },
      { key: 'ck_prod_qty', header: 'Produksi CK', fmt: 'num', align: R },
      { key: 'ck_out_qty', header: 'Dikirim CK', fmt: 'num', align: R },
      { key: 'in_ck_qty', header: 'Diterima dari CK', fmt: 'num', align: R },
      { key: 'gap', header: 'Selisih', fmt: 'num', align: R },
      { key: 'status', header: 'Status', fmt: 'status', align: L },
      { key: 'reason', header: 'Keterangan', align: L, wrap: true, sortable: false },
    ],
  },
  'mov-category': {
    caption: 'Kiriman barang per asal, kategori ESB, dan kelompok barang',
    filename: 'kiriman-kategori',
    columns: [
      { key: 'origin_kind', header: 'Asal', align: L, filterable: true },
      { key: 'item_group', header: 'Kelompok barang', align: L, filterable: true },
      { key: 'category', header: 'Kategori ESB', align: L, filterable: true },
      { key: 'uom', header: 'Satuan', align: L, filterable: true },
      { key: 'qty_sum', header: 'Qty (per satuan)', fmt: 'num', align: R },
      { key: 'skus', header: 'SKU', fmt: 'int', align: R },
      { key: 'lines_n', header: 'Baris', fmt: 'int', align: R },
    ],
  },
  'dq-freshness': {
    caption: 'Kesegaran data per brand dan entitas',
    filename: 'kesegaran-data',
    columns: [
      { key: 'brand_name', header: 'Brand', align: L },
      { key: 'entity_label', header: 'Entitas', align: L },
      { key: 'age_hours', header: 'Umur (jam)', fmt: 'num', align: R },
      { key: 'last_day', header: 'Hari terakhir', fmt: 'date', align: L },
      { key: 'status', header: 'Status', fmt: 'status', align: L },
      { key: 'reason', header: 'Keterangan', align: L, wrap: true, sortable: false },
    ],
  },
  'dq-alias': {
    caption: 'Alias produk belum dipetakan',
    filename: 'alias-belum-dipetakan',
    columns: [
      { key: 'name', header: 'Nama di ESB', align: L },
      { key: 'brand_name', header: 'Brand', align: L },
      { key: 'qty', header: 'Qty 30 hari', fmt: 'num', align: R },
      { key: 'last_seen', header: 'Terakhir terlihat', fmt: 'date', align: L },
    ],
  },
  'dq-no-sales': {
    caption: 'Cabang tanpa penjualan kemarin',
    filename: 'cabang-tanpa-penjualan',
    columns: [
      { key: 'branch', header: 'Cabang', align: L },
      { key: 'brand_name', header: 'Brand', align: L },
      { key: 'last_seen', header: 'Hari acuan', fmt: 'date', align: L },
    ],
  },
  'dq-non-ea': {
    caption: 'Baris dengan satuan bukan EA',
    filename: 'baris-non-ea',
    columns: [
      { key: 'item', header: 'Tabel : satuan', align: L },
      { key: 'brand_name', header: 'Brand', align: L },
      { key: 'rows', header: 'Jumlah baris', fmt: 'int', align: R },
    ],
  },
  'dq-last-run': {
    caption: 'Hasil ekstraksi terakhir yang tidak OK',
    filename: 'ekstraksi-tidak-ok',
    columns: [
      { key: 'brand_name', header: 'Brand', align: L },
      { key: 'item', header: 'Mode : status', align: L },
      { key: 'errors', header: 'Jumlah error', fmt: 'int', align: R },
    ],
  },
};
