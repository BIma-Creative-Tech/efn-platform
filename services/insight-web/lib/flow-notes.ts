/**
 * Keterangan arus CK (review 21 Nov 2026). Murni, dapat diuji.
 *
 * Fakta data: `v_esb_flow_daily` hanya menghitung produk FIN. CK juga mengirim WIP; bahan baku, kemasan, supplies dikirim gudang (WHProd/Palem); toko
 * membuat FINS/FINK sendiri (produksi toko) dari WIP/bahan baku; kiriman FIN yang lebih besar dari produksi pada hari/produk
 * yang sama berasal dari stok CK sebelum periode, dan stok awal TIDAK tersedia di ESB. Kalimat di bawah menjelaskan itu
 * dengan angka, bukan membiarkan pembaca menyimpulkan ada barang "hilang".
 */
import { fmtDate, fmtQty } from './format';

export const NON_STORE_LABEL = 'Non-toko (HO/gudang/damage)';
export const NON_STORE_NOTE = 'Gabungan transfer ke/dari HEAD OFFICE, gudang, dan cabang damage/expired (tidak menjual); bukan perpindahan antar toko.';

const EPS = 0.5;

export interface CkLine {
  /** Nama cabang CK asal. */
  ck: string;
  /** Produksi FIN tercatat di CK itu pada periode. */
  prod: number;
  /** Kiriman FIN dari CK itu pada periode. */
  out: number;
}

/**
 * Catatan per produk untuk periode: `lines` = produksi dan kiriman per CK asal (mov_ck_product). null = kirim tidak
 * melebihi produksi (tidak perlu keterangan).
 */
export function ckSendPeriodNote(lines: readonly CkLine[], from: string): string | null {
  const deficit = lines.filter((l) => l.out - l.prod > EPS);
  if (deficit.length === 0) return null;
  const excess = deficit.reduce((s, l) => s + (l.out - l.prod), 0);
  const surplus = lines.filter((l) => l.prod - l.out > EPS);
  let note = `Kirim melebihi produksi periode ini sebesar ${fmtQty(excess)}: berasal dari stok CK sebelum ${fmtDate(from)} (stok awal tidak tercatat di ESB).`;
  if (surplus.length > 0) {
    const names = surplus.map((l) => `${l.ck} (produksi ${fmtQty(l.prod)}, kirim ${fmtQty(l.out)})`).join('; ');
    note += ` Sebagian dapat berasal dari produksi yang dicatat di CK lain: ${names}.`;
  }
  return note;
}

/** Catatan per produk-hari (tabel selisih): `out` dan `prod` = jumlah hari itu di seluruh CK dalam cakupan. */
export function ckSendDayNote(out: number, prod: number): string | null {
  const excess = out - prod;
  if (excess <= EPS) return null;
  return `Kirim melebihi produksi hari itu sebesar ${fmtQty(excess)}: berasal dari stok CK atau produksi hari sebelumnya (stok awal tidak tercatat di ESB).`;
}

/** Gabungkan kalimat keterangan yang tidak kosong. */
export function joinNotes(...parts: (string | null | undefined)[]): string {
  return parts.filter((p): p is string => typeof p === 'string' && p !== '').join(' ');
}

/** Kelompokkan baris mov_ck_product per `<brand>:<product_code>`. */
export function groupCkLines(
  rows: readonly { brand: string; product_code: string; ck_label: string; prod_qty: number; out_qty: number }[],
): Map<string, CkLine[]> {
  const m = new Map<string, CkLine[]>();
  for (const r of rows) {
    const k = `${r.brand}:${r.product_code}`;
    const list = m.get(k) ?? [];
    list.push({ ck: r.ck_label, prod: Number(r.prod_qty) || 0, out: Number(r.out_qty) || 0 });
    m.set(k, list);
  }
  return m;
}

export const ITEM_GROUP_LABEL: Readonly<Record<string, string>> = {
  fin: 'Produk jadi (FIN)',
  wip: 'Setengah jadi (WIP)',
  raw: 'Bahan baku',
  packaging: 'Kemasan',
  supplies: 'Supplies',
  other: 'Lainnya',
};
export const ITEM_GROUP_ORDER: readonly string[] = ['fin', 'wip', 'raw', 'packaging', 'supplies', 'other'];

/** Jenis cabang asal/tujuan kiriman (v_esb_movement_daily origin_kind/dest_kind). */
export const BRANCH_KIND_LABEL: Readonly<Record<string, string>> = {
  ck: 'Central Kitchen',
  non_sales: 'Gudang / non-toko',
  store: 'Toko',
};
export const BRANCH_KIND_ORDER: readonly string[] = ['ck', 'non_sales', 'store'];
export const kindLabel = (k: string): string => BRANCH_KIND_LABEL[k] ?? (k === '' ? '-' : k);
