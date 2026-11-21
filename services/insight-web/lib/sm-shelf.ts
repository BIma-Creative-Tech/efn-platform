/**
 * Tabel "Umur simpan & sisa umur lot" (G51 B4, docs/04 §28.14.5; keputusan lead 21 Nov: semua lot berstok dari
 * `v_sm_lot_stock`, bukan hanya lot beralert). Murni: menggabungkan lot terdekat kedaluwarsa per produk x outlet
 * (`sm_shelf_lots`) dengan master produk (`sm_shelf_products`: nama, kategori, `shelf_life_days`). Tanpa PII.
 */
import { cleanText, parseChDate, toNum, type Raw } from './sm-parse';

export interface ShelfRow {
  productId: number;
  name: string;
  category: string;
  /** 0 = umur simpan tidak dikelola (tampil "tidak dikelola"). */
  shelfDays: number;
  warehouseId: number;
  lotName: string;
  expirationDate: string | null;
  /** Sisa hari lot terdekat (negatif = sudah lewat). */
  daysLeft: number;
  qty: number;
  qtyTotal: number;
  valueCost: number;
  lotsN: number;
}

export function buildShelfRows(lots: readonly Raw[], products: readonly Raw[]): ShelfRow[] {
  const prod = new Map<number, { name: string; category: string; shelfDays: number }>();
  for (const p of products) {
    const id = toNum(p.product_id);
    if (id <= 0) continue;
    prod.set(id, {
      name: cleanText(p.name_text) || `Produk #${id}`,
      category: cleanText(p.category_name),
      shelfDays: Math.max(0, Math.trunc(toNum(p.shelf_days))),
    });
  }
  const rows: ShelfRow[] = [];
  for (const l of lots) {
    const productId = toNum(l.product_id);
    const warehouseId = toNum(l.warehouse_id);
    if (productId <= 0 || warehouseId <= 0) continue;
    const p = prod.get(productId);
    rows.push({
      productId,
      name: p?.name ?? `Produk #${productId}`,
      category: p?.category ?? '',
      shelfDays: p?.shelfDays ?? 0,
      warehouseId,
      lotName: cleanText(l.lot_nearest, 64),
      expirationDate: parseChDate(l.exp_nearest),
      daysLeft: Math.trunc(toNum(l.days_min)),
      qty: toNum(l.qty_nearest),
      qtyTotal: toNum(l.qty_total),
      valueCost: toNum(l.value_nearest),
      lotsN: toNum(l.lots_n),
    });
  }
  return rows.sort((a, b) => a.daysLeft - b.daysLeft || b.valueCost - a.valueCost);
}

/** Sisa umur terhadap umur simpan produk: lewat ED -> bad; <= 25% umur (atau <= 3 hari bila umur simpan tidak dikelola) -> warn; selain itu good. */
export function shelfStatus(r: Pick<ShelfRow, 'daysLeft' | 'shelfDays'>): { status: 'good' | 'warn' | 'bad'; label: string } {
  if (r.daysLeft < 0) return { status: 'bad', label: 'Lewat ED' };
  const limit = r.shelfDays > 0 ? Math.max(1, Math.round(r.shelfDays * 0.25)) : 3;
  if (r.daysLeft <= limit) return { status: 'warn', label: 'Segera' };
  return { status: 'good', label: 'Aman' };
}

/** "tidak dikelola" untuk 0, selain itu "14 hari". */
export const fmtShelfLife = (days: number): string => (days > 0 ? `${days} hari` : 'tidak dikelola');

/** Id produk unik (urutan muncul) untuk query master produk; dibatasi 300 (batas parameter array). */
export function shelfProductIds(lots: readonly Raw[]): number[] {
  return Array.from(new Set(lots.map((l) => toNum(l.product_id)).filter((n) => n > 0))).slice(0, 300);
}
