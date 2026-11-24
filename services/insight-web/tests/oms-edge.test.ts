/**
 * G69 (QA): kasus tepi view-model ESB OMS + kontrak kolom `foms_*` terhadap wrapper efn_insight (insight-access.sql).
 * Fixture sintetis: tanpa angka atau nama cabang nyata. Uji kontrak kolom dilewati bila berkas infra tidak ada di konteks
 * test (mis. build container yang hanya menyalin services/insight-web).
 */
import { existsSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { QUERIES, type QueryName } from '@/lib/queries';
import {
  buildSellThrough,
  buildStoreHourly,
  freshnessLabel,
  soldOutHour,
  unmappedSharePct,
  type FinHourlyRaw,
  type HourlyRaw,
} from '@/lib/oms-vm';

const H = (hour: unknown, channel: string, bills: number, pax: number, net: number, items: number): HourlyRaw => ({
  hour: hour as number,
  channel_name: channel,
  bills_sum: bills,
  pax_sum: pax,
  gross_sum: net * 1.1,
  net_sum: net,
  items_sum: items,
});

describe('buildStoreHourly: kasus tepi', () => {
  it('jam null atau kosong bukan jam 00:00 (baris diabaikan, tidak menarik rentang jam ke tengah malam)', () => {
    const vm = buildStoreHourly([H(null, 'A', 5, 5, 500, 5), H('', 'A', 5, 5, 500, 5), H(undefined, 'A', 5, 5, 500, 5), H(10, 'A', 1, 2, 100, 3)])!;
    expect(vm.hours).toEqual([10]);
    expect(vm.totals.bills).toBe(1);
    expect(vm.totals.net).toBe(100);
  });

  it('semua jam null -> null (bagian disembunyikan)', () => {
    expect(buildStoreHourly([H(null, 'A', 1, 1, 1, 1), H('  ', 'A', 1, 1, 1, 1)])).toBeNull();
  });

  it('batas jam 0 dan 23 sah, 24 dan pecahan tidak sah', () => {
    const vm = buildStoreHourly([H(0, 'A', 1, 1, 10, 1), H(23, 'A', 1, 1, 10, 1), H(24, 'A', 9, 9, 90, 9), H(3.5, 'A', 9, 9, 90, 9)])!;
    expect(vm.hours[0]).toBe(0);
    expect(vm.hours[vm.hours.length - 1]).toBe(23);
    expect(vm.totals.bills).toBe(2);
  });

  it('baris jam dan kanal yang sama dari beberapa cabang dijumlahkan (bukan ditimpa)', () => {
    const vm = buildStoreHourly([H(11, 'Dine In', 2, 4, 200, 6), H(11, 'Dine In', 3, 5, 300, 7)])!;
    expect(vm.bills).toEqual([5]);
    expect(vm.net).toEqual([500]);
    expect(vm.channels).toHaveLength(1);
    expect(vm.totals.avgBillValue).toBeCloseTo(100);
  });

  it('jam tanpa transaksi di tengah = 0 dan tidak mengubah rata-rata nilai struk', () => {
    const vm = buildStoreHourly([H(9, 'A', 1, 1, 100, 1), H(13, 'A', 1, 1, 300, 1)])!;
    expect(vm.hours).toEqual([9, 10, 11, 12, 13]);
    expect(vm.bills).toEqual([1, 0, 0, 0, 1]);
    expect(vm.totals.avgBillValue).toBeCloseTo(200);
  });

  it('nama kanal null tidak melempar error', () => {
    const vm = buildStoreHourly([{ ...H(9, 'x', 1, 1, 10, 1), channel_name: null as unknown as string }])!;
    expect(vm.channels[0].name).toBe('Tidak diketahui');
  });
});

describe('buildSellThrough: kasus tepi', () => {
  const perHour = (m: Record<number, number>): number[] => Array.from({ length: 24 }, (_, h) => m[h] ?? 0);

  it('pasokan negatif (transfer keluar > masuk) diperlakukan tidak diketahui: tanpa persen dan tanpa jam habis', () => {
    expect(soldOutHour(perHour({ 10: 5 }), -5)).toBeNull();
    const rows = buildSellThrough(
      [{ brand: 'pb', product_key: 'A', product_label: 'A', hour: 10, qty_sum: 5 }],
      [{ product_key: 'pb:A', product_label: 'A', supply_qty: -5 }],
    );
    expect(rows[0]).toMatchObject({ sold: 5, pctClose: null, soldOutHour: null });
    expect(rows[0].pctAt[12]).toBeNull();
  });

  it('beberapa baris produk-jam yang sama (multi-cabang) dijumlahkan', () => {
    const rows = buildSellThrough(
      [
        { brand: 'pb', product_key: 'A', product_label: 'A', hour: 10, qty_sum: 4 },
        { brand: 'pb', product_key: 'A', product_label: 'A', hour: '10', qty_sum: 3 },
      ],
      [{ product_key: 'pb:A', product_label: 'A', supply_qty: 7 }],
    );
    expect(rows[0].perHour[10]).toBe(7);
    expect(rows[0].soldOutHour).toBe(10);
    expect(rows[0].pctClose).toBeCloseTo(100);
  });

  it('jam null/kosong pada baris terjual tidak dihitung sebagai penjualan 00:00', () => {
    const bad: FinHourlyRaw[] = [
      { brand: 'pb', product_key: 'A', product_label: 'A', hour: null as unknown as number, qty_sum: 9 },
      { brand: 'pb', product_key: 'A', product_label: 'A', hour: '', qty_sum: 9 },
    ];
    expect(buildSellThrough(bad, [{ product_key: 'pb:A', product_label: 'A', supply_qty: 10 }])).toEqual([]);
  });

  it('% pada jam patok bersifat kumulatif dan tidak menurun; jam patok tepat = bucket jam itu belum termasuk', () => {
    const rows = buildSellThrough(
      [
        { brand: 'pb', product_key: 'A', product_label: 'A', hour: 11, qty_sum: 2 },
        { brand: 'pb', product_key: 'A', product_label: 'A', hour: 12, qty_sum: 3 },
        { brand: 'pb', product_key: 'A', product_label: 'A', hour: 15, qty_sum: 1 },
      ],
      [{ product_key: 'pb:A', product_label: 'A', supply_qty: 10 }],
    );
    const a = rows[0];
    expect(a.pctAt[12]).toBeCloseTo(20); // hanya 11:xx
    expect(a.pctAt[15]).toBeCloseTo(50); // 11:xx + 12:xx
    expect(a.pctAt[18]).toBeCloseTo(60);
    expect((a.pctAt[12] ?? 0) <= (a.pctAt[15] ?? 0) && (a.pctAt[15] ?? 0) <= (a.pctAt[18] ?? 0)).toBe(true);
    expect(a.pctClose).toBeCloseTo(60);
  });

  it('pasokan sama dengan terjual -> habis di jam terakhir penjualan, 100% saat tutup', () => {
    const rows = buildSellThrough(
      [
        { brand: 'pb', product_key: 'A', product_label: 'A', hour: 10, qty_sum: 6 },
        { brand: 'pb', product_key: 'A', product_label: 'A', hour: 14, qty_sum: 4 },
      ],
      [{ product_key: 'pb:A', product_label: 'A', supply_qty: 10 }],
    );
    expect(rows[0].soldOutHour).toBe(14);
    expect(rows[0].pctClose).toBeCloseTo(100);
  });

  it('limit 0 -> daftar kosong; label jatuh ke kunci bila tanpa label', () => {
    const fin: FinHourlyRaw[] = [{ brand: 'pb', product_key: 'A', product_label: '', hour: 10, qty_sum: 1 }];
    expect(buildSellThrough(fin, [], 0)).toEqual([]);
    expect(buildSellThrough(fin, [])[0].label).toBe('pb:A');
  });
});

describe('freshnessLabel dan unmappedSharePct: kasus tepi', () => {
  it('DateTime64(3) dari ClickHouse (dengan milidetik) -> HH:MM', () => {
    expect(freshnessLabel([{ finished_at: '2026-11-25 15:30:00.123' }])).toBe('15:30');
  });
  it('lewat tengah malam: stempel hari berikutnya dianggap lebih baru', () => {
    expect(freshnessLabel([{ finished_at: '2026-11-24 23:59:59' }, { finished_at: '2026-11-25 00:05:00' }])).toBe('00:05');
  });
  it('baris null dicampur baris sah -> memakai yang sah', () => {
    expect(freshnessLabel([{ finished_at: null }, { finished_at: '2026-11-25 08:00:00' }])).toBe('08:00');
  });
  it('unmappedSharePct: nilai bukan angka dihitung 0 (tanpa NaN), lebih dari total dijepit 100', () => {
    expect(unmappedSharePct([{ unmapped_qty_f: 'x', menu_qty_f: 10 }])).toBe(0);
    expect(unmappedSharePct([{ unmapped_qty_f: 50, menu_qty_f: 10 }])).toBe(100);
  });
});

// ---------- kontrak kolom foms_* <-> wrapper efn_insight ----------
const ACCESS_SQL = new URL('../../../infra/clickhouse/access/insight-access.sql', import.meta.url);
const haveSql = existsSync(fileURLToPath(ACCESS_SQL));

/** Daftar kolom wrapper `efn_insight.<view>` dari `CREATE OR REPLACE VIEW ... AS SELECT <kolom> FROM`. */
function wrapperColumns(sql: string, view: string): string[] {
  const m = new RegExp(`CREATE OR REPLACE VIEW efn_insight\\.${view}\\b[^;]*?AS\\s+SELECT([\\s\\S]*?)\\bFROM\\b`).exec(sql);
  if (!m) return [];
  return m[1].split(',').map((c) => c.trim().split(/\s+/).pop() as string).filter(Boolean);
}

const USED: Record<string, { view: string; cols: string[] }> = {
  foms_freshness: { view: 'v_esb_oms_freshness', cols: ['company_code', 'brand', 'branch_id', 'last_finished_at'] },
  foms_hourly: {
    view: 'v_esb_oms_hourly',
    cols: ['sales_date', 'company_code', 'brand', 'branch_id', 'hour', 'visit_purpose_name', 'bills', 'pax', 'gross_sales', 'net_sales', 'items'],
  },
  foms_fin_hourly: {
    view: 'v_esb_oms_fin_hourly',
    cols: ['sales_date', 'company_code', 'brand', 'branch_id', 'hour', 'product_key', 'product_name', 'uom', 'qty_fin'],
  },
  foms_fin_quality: {
    view: 'v_esb_oms_fin_mapping_quality',
    cols: ['sales_date', 'company_code', 'brand', 'branch_id', 'unmapped_qty', 'menu_qty'],
  },
  foms_fin_supply: {
    view: 'v_esb_flow_daily',
    cols: ['day', 'company_code', 'brand', 'branch_id', 'product_code', 'product_name', 'in_ck', 'in_other', 'store_prod', 'st_in', 'st_out'],
  },
};

describe.skipIf(!haveSql)('kontrak kolom foms_* terhadap wrapper efn_insight (insight-access.sql)', () => {
  const sql = haveSql ? readFileSync(fileURLToPath(ACCESS_SQL), 'utf8') : '';

  it.each(Object.keys(USED))('%s: setiap kolom yang dipakai ada di wrapper, dan query membaca view itu', (name) => {
    const { view, cols } = USED[name];
    const have = wrapperColumns(sql, view);
    expect(have.length, `wrapper ${view} tidak ditemukan`).toBeGreaterThan(0);
    for (const c of cols) expect(have, `${view}.${c}`).toContain(c);
    expect(QUERIES[name as QueryName].sql).toContain(`efn_insight.${view}`);
  });

  it('kunci pasokan = concat(brand, ":", product_code): format sama dengan kunci terjual `<brand>:<product_key>` di VM', () => {
    expect(QUERIES.foms_fin_supply.sql).toContain("concat(brand, ':', product_code) AS product_key");
  });
});
