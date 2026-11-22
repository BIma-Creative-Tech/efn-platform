/**
 * Logika murni F&B persona (G52): tanggal (addYears, startOfMonthShift), hari acuan Toko, pemilihan cabang, pemisahan pekan,
 * ketersediaan pembanding tahun lalu, dan pengelompokan kanal pembayaran (Q-INS-F4 default).
 */
import { describe, expect, it } from 'vitest';
import { classifyChannel, groupChannels, CHANNEL_ORDER } from '@/lib/channels';
import { addYears, startOfMonthShift } from '@/lib/dates';
import { parseStoreBranches, pickStoreDay, splitWeeks } from '@/lib/fnb-store';
import { growthPct, pctOfSafe, yoyStatus } from '@/lib/fnb-vm';

describe('addYears / startOfMonthShift', () => {
  it('tahun lalu sejajar kalender; 29 Feb -> 28 Feb di tahun non-kabisat', () => {
    expect(addYears('2026-11-21', -1)).toBe('2025-11-21');
    expect(addYears('2024-02-29', -1)).toBe('2023-02-28');
    expect(addYears('2024-02-29', 1)).toBe('2025-02-28');
    expect(addYears('2028-02-29', -4)).toBe('2024-02-29');
    expect(addYears('2026-01-01', -1)).toBe('2025-01-01');
  });
  it('awal bulan digeser n bulan (lintas tahun)', () => {
    expect(startOfMonthShift('2026-11-21', -12)).toBe('2025-11-01');
    expect(startOfMonthShift('2026-02-10', -3)).toBe('2025-11-01');
    expect(startOfMonthShift('2026-11-21', 0)).toBe('2026-11-01');
    expect(startOfMonthShift('2026-12-31', 1)).toBe('2027-01-01');
  });
});

describe('pickStoreDay', () => {
  const Y = '2026-11-21';
  const row = (sales_date: string, net_sum = 100, trx = 3) => ({ sales_date, net_sum, trx });
  it('hari eksplisit yang sah dipakai apa adanya', () => {
    expect(pickStoreDay('2026-11-10', null, Y)).toEqual({ day: '2026-11-10', adjusted: false, explicit: true });
    expect(pickStoreDay(Y, [], Y)).toEqual({ day: Y, adjusted: false, explicit: true });
  });
  it('hari tidak sah (format, masa depan, terlalu lama) -> otomatis + adjusted', () => {
    expect(pickStoreDay('2026-11-22', [row('2026-11-19')], Y)).toEqual({ day: '2026-11-19', adjusted: true, explicit: false });
    expect(pickStoreDay('bukan-tanggal', null, Y)).toEqual({ day: Y, adjusted: true, explicit: false });
    expect(pickStoreDay('2024-01-01', null, Y)).toMatchObject({ day: Y, adjusted: true });
    expect(pickStoreDay("2026-11-10'; DROP", null, Y)).toMatchObject({ adjusted: true });
  });
  it('default = hari terakhir yang punya penjualan (<= kemarin); abaikan hari nol dan masa depan', () => {
    expect(pickStoreDay(undefined, [row('2026-11-17'), row('2026-11-19'), row('2026-11-20', 0, 0)], Y)).toEqual({ day: '2026-11-19', adjusted: false, explicit: false });
    expect(pickStoreDay(undefined, [row('2026-11-22')], Y).day).toBe(Y);
    expect(pickStoreDay('', [row('2026-11-18')], Y).day).toBe('2026-11-18');
  });
  it('tanpa data -> kemarin', () => {
    expect(pickStoreDay(undefined, [], Y).day).toBe(Y);
    expect(pickStoreDay(undefined, null, Y).day).toBe(Y);
  });
});

describe('parseStoreBranches', () => {
  it('tanpa batas (exec/all_brands): format sah, unik, urut; nilai tidak sah dibuang + adjusted', () => {
    expect(parseStoreBranches(['pb:12', 'PB:7', 'pb:12'], [])).toEqual({ branch: ['pb:12', 'pb:7'], adjusted: false });
    expect(parseStoreBranches(['pb:07', 'xx:1', 'pb:7;'], [])).toEqual({ branch: [], adjusted: true });
    expect(parseStoreBranches([''], [])).toEqual({ branch: [], adjusted: false });
  });
  it('sesi ber-cabang: dipotong ke brs (di luar dibuang); kosong = semua brs (query memakai policy BR)', () => {
    const brs = ['pb:12', 'pb:7'];
    expect(parseStoreBranches(['pb:7'], brs)).toEqual({ branch: ['pb:7'], adjusted: false });
    expect(parseStoreBranches(['pb:7', 'pb:99', 'wtz:7'], brs)).toEqual({ branch: ['pb:7'], adjusted: true });
    expect(parseStoreBranches(['pb:99'], brs)).toEqual({ branch: [], adjusted: true });
    expect(parseStoreBranches([], brs)).toEqual({ branch: [], adjusted: false });
  });
  it('maks 50 cabang', () => {
    const many = Array.from({ length: 60 }, (_, i) => `pb:${i + 1}`);
    const r = parseStoreBranches(many, []);
    expect(r.branch).toHaveLength(50);
    expect(r.adjusted).toBe(true);
  });
});

describe('splitWeeks', () => {
  it('memisahkan 14 hari menjadi pekan ini (day-6..day) dan pekan lalu sejajar, menjumlahkan lintas cabang, hari kosong = null', () => {
    const day = '2026-11-21';
    const rows = [
      { sales_date: '2026-11-21', net_sum: 100, trx: 4 },
      { sales_date: '2026-11-21', net_sum: 50, trx: 1 },
      { sales_date: '2026-11-14', net_sum: 80, trx: 2 },
      { sales_date: '2026-11-18', net_sum: 70, trx: 3 },
    ];
    const w = splitWeeks(rows, day);
    expect(w.days).toHaveLength(7);
    expect(w.days[0]).toBe('2026-11-15');
    expect(w.days[6]).toBe(day);
    expect(w.lastWeekDays[0]).toBe('2026-11-08');
    expect(w.lastWeekDays[6]).toBe('2026-11-14');
    expect(w.net[6]).toBe(150);
    expect(w.trx[6]).toBe(5);
    expect(w.netLastWeek[6]).toBe(80);
    expect(w.net[3]).toBe(70);
    expect(w.net[0]).toBeNull();
    expect(w.netLastWeek[0]).toBeNull();
  });
});

describe('yoyStatus', () => {
  const YF = '2025-11-01';
  it('tersedia bila semua PT punya data sejak awal rentang tahun lalu', () => {
    const s = yoyStatus([{ company_code: 'PB', first_day: '2025-01-01' }, { company_code: 'WTZ', first_day: '2025-11-01' }], YF);
    expect(s.available).toBe(true);
    expect(s.note).toBeNull();
    expect(s.forCode('PB')).toBe(true);
  });
  it('PT yang datanya baru mulai setelah awal rentang -> tidak tersedia dengan catatan, per-PT berbeda', () => {
    const s = yoyStatus([{ company_code: 'PB', first_day: '2025-01-01' }, { company_code: 'WTZ', first_day: '2026-10-05' }], YF);
    expect(s.available).toBe(false);
    expect(s.missing).toEqual(['WTZ']);
    expect(s.note).toContain('belum tersedia');
    expect(s.note).toContain('WTZ');
    expect(s.forCode('PB')).toBe(true);
    expect(s.forCode('WTZ')).toBe(false);
    expect(s.forCode('XXX')).toBe(false);
  });
  it('cakupan kosong atau gagal dimuat -> tidak tersedia (n/a, bukan angka menyesatkan)', () => {
    expect(yoyStatus([], YF)).toMatchObject({ available: false, note: 'Data tahun lalu belum tersedia.' });
    const failed = yoyStatus(null, YF);
    expect(failed.available).toBe(false);
    expect(failed.note).toContain('belum dapat diverifikasi');
    expect(failed.forCode('PB')).toBe(false);
  });
  it('satu PT dengan beberapa baris: cukup satu yang terlambat', () => {
    const s = yoyStatus([{ company_code: 'BCF', first_day: '2025-01-01' }, { company_code: 'BCF', first_day: '2026-01-01' }], YF);
    expect(s.forCode('BCF')).toBe(false);
  });
});

describe('persentase aman', () => {
  it('pctOfSafe dan growthPct: null untuk penyebut nol (bukan 0 atau Infinity)', () => {
    expect(pctOfSafe(25, 100)).toBe(25);
    expect(pctOfSafe(1, 0)).toBeNull();
    expect(pctOfSafe(1, -5)).toBeNull();
    expect(growthPct(120, 100)).toBeCloseTo(20);
    expect(growthPct(80, 100)).toBeCloseTo(-20);
    expect(growthPct(5, 0)).toBeNull();
    expect(growthPct(Number.NaN, 100)).toBeNull();
  });
});

describe('kanal pembayaran (Q-INS-F4 default)', () => {
  it('pesan-antar online dikenali dari nama metode', () => {
    for (const m of ['GoFood', 'GRABFOOD', 'Grab Food', 'ShopeeFood', 'shopee-food', 'Gojek']) expect(classifyChannel('Online', m), m).toBe('delivery');
  });
  it('QRIS/dompet, kartu, tunai; selain itu Lainnya', () => {
    expect(classifyChannel('Digital', 'QRIS')).toBe('qris');
    expect(classifyChannel('Digital', 'OVO')).toBe('qris');
    expect(classifyChannel('Card', 'Debit BCA')).toBe('card');
    expect(classifyChannel('Credit Card', 'Visa')).toBe('card');
    expect(classifyChannel('Cash', 'Tunai')).toBe('cash');
    expect(classifyChannel('Other', 'Voucher')).toBe('other');
    expect(classifyChannel('', 'Tidak diketahui')).toBe('other');
  });
  it('groupChannels menjumlahkan per kanal, semua kanal selalu ada, porsi total 100', () => {
    const g = groupChannels([
      { payment_type: 'Online', method: 'GoFood', cnt: 10, amount_sum: 300 },
      { payment_type: 'Online', method: 'GrabFood', cnt: 5, amount_sum: 100 },
      { payment_type: 'Cash', method: 'Tunai', cnt: 20, amount_sum: 600 },
    ]);
    expect(g.map((x) => x.id)).toEqual([...CHANNEL_ORDER]);
    expect(g.find((x) => x.id === 'delivery')).toMatchObject({ cnt: 15, amount: 400 });
    expect(g.find((x) => x.id === 'cash')).toMatchObject({ cnt: 20, amount: 600 });
    expect(g.find((x) => x.id === 'card')).toMatchObject({ cnt: 0, amount: 0, sharePct: 0 });
    expect(g.reduce((s, x) => s + (x.sharePct ?? 0), 0)).toBeCloseTo(100);
  });
  it('tanpa data: porsi null', () => {
    expect(groupChannels([]).every((x) => x.sharePct === null)).toBe(true);
  });
});
