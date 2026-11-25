/**
 * G69 putaran QA: pilihan hari bagian OMS (/fnb/store `oms_day`), satuan resep non-EA, dan jam 23 (struk lewat tengah malam).
 * Fixture sintetis.
 */
import { describe, expect, it } from 'vitest';
import { buildSellThrough, buildStoreHourly, isEaUom, parseOmsDay, resolveOmsDay, type FinHourlyRaw, type SupplyRaw } from '@/lib/oms-vm';

describe('parseOmsDay: hanya today atau date', () => {
  it('nilai sah', () => {
    expect(parseOmsDay('today')).toBe('today');
    expect(parseOmsDay('date')).toBe('date');
    expect(parseOmsDay(['today', 'date'])).toBe('today');
  });
  it('selain itu = tidak ada (tanpa melempar, tanpa meneruskan nilai mentah)', () => {
    for (const v of [undefined, '', 'TODAY', '2026-11-25', "today'; DROP", ' today', [], ['x']] as const) {
      expect(parseOmsDay(v as string | string[] | undefined), String(v)).toBeNull();
    }
  });
});

describe('resolveOmsDay', () => {
  const base = { today: '2026-11-25', filterDay: '2026-11-24' };
  it('bawaan = hari ini bila ada tarikan hari ini', () => {
    expect(resolveOmsDay({ ...base, param: null, freshLabel: '14:30' })).toEqual({ mode: 'today', day: '2026-11-25', todayAvailable: true });
  });
  it('bawaan = tanggal filter bila tidak ada tarikan hari ini', () => {
    expect(resolveOmsDay({ ...base, param: null, freshLabel: null })).toEqual({ mode: 'date', day: '2026-11-24', todayAvailable: false });
  });
  it('pilihan eksplisit date menimpa bawaan hari ini', () => {
    expect(resolveOmsDay({ ...base, param: 'date', freshLabel: '14:30' })).toMatchObject({ mode: 'date', day: '2026-11-24' });
  });
  it('today tanpa tarikan hari ini jatuh ke tanggal filter (tidak buntu)', () => {
    expect(resolveOmsDay({ ...base, param: 'today', freshLabel: null })).toMatchObject({ mode: 'date', day: '2026-11-24' });
  });
  it('today eksplisit dengan tarikan = hari ini', () => {
    expect(resolveOmsDay({ ...base, param: 'today', freshLabel: '09:00' })).toMatchObject({ mode: 'today', day: '2026-11-25' });
  });
});

describe('satuan resep non-EA', () => {
  it('EA, PCS, PC (tanpa peka huruf besar-kecil) dan kosong setara EA; lainnya tidak', () => {
    for (const u of ['EA', 'ea', ' Pcs ', 'PC', '', null, undefined]) expect(isEaUom(u), String(u)).toBe(true);
    for (const u of ['KG', 'gr', 'LTR', 'BOX']) expect(isEaUom(u), u).toBe(false);
  });

  const supply: SupplyRaw[] = [
    { product_key: 'pb:A', product_label: 'A', supply_qty: 10 },
    { product_key: 'pb:B', product_label: 'B', supply_qty: 10 },
  ];
  const fin: FinHourlyRaw[] = [
    { brand: 'pb', product_key: 'A', product_label: 'A', hour: 10, qty_sum: 10, uom: 'EA' },
    { brand: 'pb', product_key: 'B', product_label: 'B', hour: 10, qty_sum: 10, uom: 'KG' },
  ];

  it('uom non-EA: tanpa persen dan tanpa jam habis, ditandai uomMismatch; EA tetap dihitung', () => {
    const rows = buildSellThrough(fin, supply);
    const a = rows.find((r) => r.key === 'pb:A')!;
    const b = rows.find((r) => r.key === 'pb:B')!;
    expect(a).toMatchObject({ uomMismatch: null, pctClose: 100, soldOutHour: 10 });
    expect(b).toMatchObject({ uomMismatch: 'KG', supply: null, pctClose: null, soldOutHour: null, sold: 10 });
    expect(b.pctAt[12]).toBeNull();
  });

  it('satu baris non-EA di antara baris EA menandai seluruh produk', () => {
    const rows = buildSellThrough(
      [
        { brand: 'pb', product_key: 'A', product_label: 'A', hour: 10, qty_sum: 5, uom: 'EA' },
        { brand: 'pb', product_key: 'A', product_label: 'A', hour: 11, qty_sum: 5, uom: 'KG' },
      ],
      supply,
    );
    expect(rows.find((r) => r.key === 'pb:A')).toMatchObject({ uomMismatch: 'KG', pctClose: null, soldOutHour: null });
  });

  it('uom tidak ada (kolom kosong) dianggap EA', () => {
    const rows = buildSellThrough([{ brand: 'pb', product_key: 'A', product_label: 'A', hour: 10, qty_sum: 4 }], supply);
    expect(rows[0].uomMismatch).toBeNull();
    expect(rows[0].pctClose).toBeCloseTo(40);
  });
});

describe('jam 23 untuk struk lewat tengah malam', () => {
  it('tidak ada asumsi jam 0: rentang jam berakhir di 23 dan jam 0 tidak muncul', () => {
    const vm = buildStoreHourly([
      { hour: 22, channel_name: 'A', bills_sum: 2, pax_sum: 2, gross_sum: 0, net_sum: 200, items_sum: 2 },
      { hour: 23, channel_name: 'A', bills_sum: 3, pax_sum: 3, gross_sum: 0, net_sum: 300, items_sum: 3 },
    ])!;
    expect(vm.hours).toEqual([22, 23]);
    expect(vm.bills).toEqual([2, 3]);
  });
});
