/**
 * G69: view-model ESB OMS (penjualan per jam & kanal, sell-through per jam). Fixture sintetis: tanpa angka atau nama cabang nyata.
 */
import { describe, expect, it } from 'vitest';
import {
  buildSellThrough,
  buildStoreHourly,
  freshnessLabel,
  hourLabel,
  hourRangeLabel,
  soldBefore,
  soldOutHour,
  unmappedSharePct,
  type FinHourlyRaw,
  type HourlyRaw,
  type SupplyRaw,
} from '@/lib/oms-vm';

const H = (hour: number | string, channel: string, bills: number, pax: number, net: number, items: number): HourlyRaw => ({
  hour,
  channel_name: channel,
  bills_sum: bills,
  pax_sum: pax,
  gross_sum: net * 1.1,
  net_sum: net,
  items_sum: items,
});

describe('buildStoreHourly', () => {
  const rows: HourlyRaw[] = [
    H(10, 'Dine In', 2, 4, 200, 6),
    H(10, 'Take Away', 1, 1, 50, 2),
    H('12', 'Dine In', 3, 6, 300, 9),
    H(13, 'Delivery', 1, 1, 150, 3),
  ];

  it('jam berurutan tanpa celah (jam kosong = 0) dan total benar', () => {
    const vm = buildStoreHourly(rows)!;
    expect(vm.hours).toEqual([10, 11, 12, 13]);
    expect(vm.bills).toEqual([3, 0, 3, 1]);
    expect(vm.net).toEqual([250, 0, 300, 150]);
    expect(vm.totals).toMatchObject({ bills: 7, pax: 12, net: 700, items: 20 });
  });

  it('rata-rata nilai struk dan item per struk', () => {
    const t = buildStoreHourly(rows)!.totals;
    expect(t.avgBillValue).toBeCloseTo(100);
    expect(t.itemsPerBill).toBeCloseTo(20 / 7);
  });

  it('bauran kanal diurutkan penjualan bersih turun, porsi berjumlah 100', () => {
    const ch = buildStoreHourly(rows)!.channels;
    expect(ch.map((c) => c.name)).toEqual(['Dine In', 'Delivery', 'Take Away']);
    expect(ch[0]).toMatchObject({ bills: 5, net: 500 });
    expect(ch.reduce((s, c) => s + (c.sharePct ?? 0), 0)).toBeCloseTo(100);
  });

  it('nama kanal kosong menjadi "Tidak diketahui"', () => {
    const vm = buildStoreHourly([H(9, '  ', 1, 1, 10, 1)])!;
    expect(vm.channels[0].name).toBe('Tidak diketahui');
  });

  it('kosong, jam tidak sah, atau tanpa struk -> null (bagian disembunyikan)', () => {
    expect(buildStoreHourly([])).toBeNull();
    expect(buildStoreHourly([H(24, 'A', 1, 1, 1, 1), H(-1, 'A', 1, 1, 1, 1), H('x', 'A', 1, 1, 1, 1)])).toBeNull();
    expect(buildStoreHourly([H(10, 'A', 0, 0, 0, 0)])).toBeNull();
  });

  it('angka berupa string (ClickHouse) dan rata-rata tidak NaN', () => {
    const vm = buildStoreHourly([{ hour: '9', channel_name: 'A', bills_sum: '2', pax_sum: '3', gross_sum: '0', net_sum: '90.5', items_sum: '4' }])!;
    expect(vm.totals.net).toBeCloseTo(90.5);
    expect(vm.totals.avgBillValue).toBeCloseTo(45.25);
  });

  it('porsi null bila total bersih <= 0 (tidak membagi nol)', () => {
    const vm = buildStoreHourly([H(9, 'A', 1, 1, 0, 1)])!;
    expect(vm.channels[0].sharePct).toBeNull();
  });
});

describe('sell-through per jam', () => {
  const perHour = (m: Record<number, number>): number[] => Array.from({ length: 24 }, (_, h) => m[h] ?? 0);

  it('soldBefore menjumlah bucket jam < batas', () => {
    const a = perHour({ 8: 5, 12: 10, 20: 1 });
    expect(soldBefore(a, 12)).toBe(5);
    expect(soldBefore(a, 13)).toBe(15);
    expect(soldBefore(a, 0)).toBe(0);
    expect(soldBefore(a, 24)).toBe(16);
  });

  it('soldOutHour: jam pertama kumulatif >= pasokan; null bila tidak habis atau pasokan tak sah', () => {
    const a = perHour({ 10: 4, 14: 6, 19: 5 });
    expect(soldOutHour(a, 10)).toBe(14);
    expect(soldOutHour(a, 4)).toBe(10);
    expect(soldOutHour(a, 15)).toBe(19);
    expect(soldOutHour(a, 16)).toBeNull();
    expect(soldOutHour(a, 0)).toBeNull();
    expect(soldOutHour(a, null)).toBeNull();
  });

  const fin: FinHourlyRaw[] = [
    // product_key view OMS = kode produk polos; kunci gabungan = `<brand>:<product_key>` (sama dengan pasokan).
    { brand: 'pb', product_key: 'A', product_label: 'Produk A', hour: 10, qty_sum: 4 },
    { brand: 'pb', product_key: 'A', product_label: 'Produk A', hour: '13', qty_sum: 6 },
    { brand: 'pb', product_key: 'A', product_label: 'Produk A', hour: 16, qty_sum: 2 },
    { brand: 'pb', product_key: 'B', product_label: 'NAME:Produk B', hour: 11, qty_sum: 3 },
    { brand: 'pb', product_key: 'C', product_label: 'Produk C', hour: 25, qty_sum: 9 },
    { brand: '', product_key: 'Z', product_label: 'Tanpa brand', hour: 9, qty_sum: 9 },
  ];
  const supply: SupplyRaw[] = [
    { product_key: 'pb:A', product_label: 'Produk A', supply_qty: 20 },
    { product_key: 'pb:D', product_label: 'Produk D', supply_qty: '5' },
    { product_key: 'pb:E', product_label: 'Produk E', supply_qty: 0 },
  ];

  it('persen pada jam patok, tutup, dan jam habis per produk', () => {
    const rows = buildSellThrough(fin, supply);
    const a = rows.find((r) => r.key === 'pb:A')!;
    expect(a.sold).toBe(12);
    expect(a.supply).toBe(20);
    expect(a.pctAt[12]).toBeCloseTo(20); // 4 dari 20 sebelum 12:00
    expect(a.pctAt[15]).toBeCloseTo(50); // 10 dari 20 sebelum 15:00
    expect(a.pctAt[18]).toBeCloseTo(60);
    expect(a.pctClose).toBeCloseTo(60);
    expect(a.soldOutHour).toBeNull();
  });

  it('jam habis terisi bila kumulatif mencapai pasokan', () => {
    const rows = buildSellThrough(fin, [{ product_key: 'pb:A', product_label: 'Produk A', supply_qty: 10 }]);
    const a = rows.find((r) => r.key === 'pb:A')!;
    expect(a.soldOutHour).toBe(13);
    expect(a.pctClose).toBeCloseTo(120);
  });

  it('produk tanpa pasokan: persen null (bukan nol/NaN); label NAME: dibersihkan', () => {
    const b = buildSellThrough(fin, supply).find((r) => r.key === 'pb:B')!;
    expect(b.supply).toBeNull();
    expect(b.pctAt[12]).toBeNull();
    expect(b.pctClose).toBeNull();
    expect(b.soldOutHour).toBeNull();
    expect(b.label).toBe('Produk B');
  });

  it('pasokan tanpa penjualan ikut (0% terjual); pasokan 0 tanpa penjualan dibuang; jam tak sah diabaikan', () => {
    const rows = buildSellThrough(fin, supply);
    const d = rows.find((r) => r.key === 'pb:D')!;
    expect(d.sold).toBe(0);
    expect(d.pctClose).toBe(0);
    expect(rows.find((r) => r.key === 'pb:E')).toBeUndefined();
    expect(rows.find((r) => r.key === 'pb:C')).toBeUndefined();
    expect(rows.find((r) => r.key.endsWith(':Z'))).toBeUndefined(); // brand kosong dibuang
  });

  it('kunci gabungan = brand:product_key: kode yang sama di brand lain tidak tercampur', () => {
    const rows = buildSellThrough(
      [
        { brand: 'pb', product_key: 'A', product_label: 'A', hour: 10, qty_sum: 4 },
        { brand: 'wtz', product_key: 'A', product_label: 'A', hour: 10, qty_sum: 7 },
      ],
      [{ product_key: 'wtz:A', product_label: 'A', supply_qty: 14 }],
    );
    expect(rows.find((r) => r.key === 'wtz:A')).toMatchObject({ sold: 7, supply: 14 });
    expect(rows.find((r) => r.key === 'pb:A')).toMatchObject({ sold: 4, supply: null });
  });

  it('urut terjual terbanyak dan limit dihormati', () => {
    const rows = buildSellThrough(fin, supply);
    expect(rows[0].key).toBe('pb:A');
    expect(buildSellThrough(fin, supply, 1)).toHaveLength(1);
  });

  it('OMS kosong -> [] (bagian disembunyikan) walau pasokan ada', () => {
    expect(buildSellThrough([], supply)).toEqual([]);
    expect(buildSellThrough([{ brand: 'pb', product_key: 'A', product_label: 'A', hour: 10, qty_sum: 0 }], supply)).toEqual([]);
  });
});

describe('kesegaran dan porsi tak terpetakan', () => {
  it('freshnessLabel: HH:MM dari finished_at terbaru', () => {
    expect(freshnessLabel([{ finished_at: '2026-11-25 14:05:09' }, { finished_at: '2026-11-25 15:30:00' }])).toBe('15:30');
    expect(freshnessLabel([{ finished_at: '2026-11-25T09:07:00' }])).toBe('09:07');
  });
  it('freshnessLabel: kosong / null / bukan stempel waktu -> null', () => {
    expect(freshnessLabel([])).toBeNull();
    expect(freshnessLabel([{ finished_at: null }])).toBeNull();
    expect(freshnessLabel([{ finished_at: 'belum' }])).toBeNull();
  });

  it('unmappedSharePct = Σunmapped_qty / Σmenu_qty x 100 (berbobot, bukan rata-rata persen)', () => {
    expect(unmappedSharePct([{ unmapped_qty_f: 1, menu_qty_f: 10 }, { unmapped_qty_f: '3', menu_qty_f: '90' }])).toBeCloseTo(4);
    expect(unmappedSharePct([{ unmapped_qty_f: 5, menu_qty_f: 5 }])).toBe(100);
  });
  it('unmappedSharePct: kosong atau total menu 0 -> null', () => {
    expect(unmappedSharePct([])).toBeNull();
    expect(unmappedSharePct([{ unmapped_qty_f: 0, menu_qty_f: 0 }])).toBeNull();
  });

  it('label jam', () => {
    expect(hourLabel(7)).toBe('07:00');
    expect(hourRangeLabel(14)).toBe('14:00-14:59');
  });
});
