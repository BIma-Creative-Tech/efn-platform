/**
 * Umur simpan & sisa umur lot (G51 B4): penggabungan lot terdekat (v_sm_lot_stock) dengan master produk (shelf_life_days).
 */
import { describe, expect, it } from 'vitest';
import { buildShelfRows, fmtShelfLife, shelfProductIds, shelfReason, shelfStatus } from '@/lib/sm-shelf';

describe('shelfReason (kolom Keterangan)', () => {
  it('menyebut angka sisa hari, batas, dan dasar batas', () => {
    expect(shelfReason({ daysLeft: -3, shelfDays: 14 })).toContain('lewat tanggal kedaluwarsa 3 hari');
    expect(shelfReason({ daysLeft: 2, shelfDays: 14 })).toContain('Sisa umur 2 hari <= 4 hari (25% umur simpan 14 hari)');
    expect(shelfReason({ daysLeft: 2, shelfDays: 0 })).toContain('batas 3 hari (umur simpan produk tidak dikelola)');
    expect(shelfReason({ daysLeft: 20, shelfDays: 14 })).toContain('Sisa umur 20 hari > 4 hari');
  });
  it('konsisten dengan shelfStatus', () => {
    for (const r of [{ daysLeft: -1, shelfDays: 10 }, { daysLeft: 1, shelfDays: 10 }, { daysLeft: 9, shelfDays: 10 }]) {
      const s = shelfStatus(r).status;
      const reason = shelfReason(r);
      if (s === 'good') expect(reason).toContain('>');
      else expect(reason).not.toContain('Sisa umur 9');
    }
  });
});

const lots = [
  { product_id: 1, warehouse_id: 21, lot_nearest: 'L-002', exp_nearest: '2026-11-25', days_min: 4, qty_nearest: 6, qty_total: 20, value_nearest: 60000, lots_n: 3 },
  { product_id: 2, warehouse_id: 21, lot_nearest: 'L-009', exp_nearest: '2026-11-19', days_min: -2, qty_nearest: 3, qty_total: 3, value_nearest: 30000, lots_n: 1 },
  { product_id: 1, warehouse_id: 22, lot_nearest: 'L-010', exp_nearest: '1970-01-01', days_min: 9, qty_nearest: 1, qty_total: 1, value_nearest: 10000, lots_n: 1 },
  { product_id: 0, warehouse_id: 22, lot_nearest: 'x', days_min: 1 }, // tanpa kunci utama: dibuang
];
const products = [
  { product_id: 1, name_text: 'Susu UHT 1L', category_name: 'Susu', shelf_days: 10 },
  { product_id: 2, name_text: 'Bayam', category_name: 'Sayur', shelf_days: 0 },
];

describe('buildShelfRows', () => {
  const rows = buildShelfRows(lots, products);
  it('menggabungkan nama, kategori, umur simpan; membuang baris tanpa kunci', () => {
    expect(rows).toHaveLength(3);
    expect(rows.find((r) => r.productId === 1 && r.warehouseId === 21)).toMatchObject({ name: 'Susu UHT 1L', category: 'Susu', shelfDays: 10, lotName: 'L-002', qty: 6, qtyTotal: 20, valueCost: 60000, lotsN: 3 });
  });
  it('urut sisa umur naik (lewat ED dulu); tanggal nol ClickHouse -> null', () => {
    expect(rows.map((r) => r.daysLeft)).toEqual([-2, 4, 9]);
    expect(rows[2].expirationDate).toBeNull();
    expect(rows[1].expirationDate).toBe('2026-11-25');
  });
  it('master produk tidak ada: nama "Produk #id" dan umur simpan 0 (tidak dikelola)', () => {
    const r = buildShelfRows(lots, []);
    expect(r[0]).toMatchObject({ name: 'Produk #2', shelfDays: 0, category: '' });
  });
});

describe('fmtShelfLife / shelfProductIds / shelfStatus', () => {
  it('0 hari = tidak dikelola', () => {
    expect(fmtShelfLife(0)).toBe('tidak dikelola');
    expect(fmtShelfLife(14)).toBe('14 hari');
  });
  it('id produk unik dan dibatasi 300', () => {
    expect(shelfProductIds(lots)).toEqual([1, 2]);
    const many = Array.from({ length: 400 }, (_, i) => ({ product_id: i + 1 }));
    expect(shelfProductIds(many)).toHaveLength(300);
  });
  it('status: lewat ED = bad; <= 25% umur simpan = warn; sisanya good; tanpa umur simpan ambang 3 hari', () => {
    expect(shelfStatus({ daysLeft: -1, shelfDays: 10 }).status).toBe('bad');
    expect(shelfStatus({ daysLeft: 0, shelfDays: 10 }).status).toBe('warn');
    expect(shelfStatus({ daysLeft: 3, shelfDays: 10 }).status).toBe('warn');
    expect(shelfStatus({ daysLeft: 4, shelfDays: 10 }).status).toBe('good');
    expect(shelfStatus({ daysLeft: 3, shelfDays: 0 }).status).toBe('warn');
    expect(shelfStatus({ daysLeft: 4, shelfDays: 0 }).status).toBe('good');
    // label berupa kata, bukan warna saja
    expect(shelfStatus({ daysLeft: -1, shelfDays: 0 }).label).toBe('Lewat ED');
  });
});
