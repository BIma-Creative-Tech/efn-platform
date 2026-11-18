import { describe, expect, it } from 'vitest';
import { addDays, diffDays, isIsoDate, isoWeekday, wibYesterday } from '@/lib/dates';
import { parseFilters } from '@/lib/filters';
import { fmtAge, fmtDate, fmtDelta, fmtIdr, fmtIdrShort, fmtNumber, fmtPct, fmtRange } from '@/lib/format';
import { summarize } from '@/lib/chart-spec';
import { gapStatus, sellThroughStatus, freshnessStatus } from '@/lib/thresholds';
import { trendSeries, withOther } from '@/lib/vm';

const NOW = new Date('2026-11-18T05:00:00Z'); // 12:00 WIB 18 Nov -> kemarin = 17 Nov
const ALL = ['BCF', 'PB'];

describe('tanggal', () => {
  it('WIB: kemarin dihitung dari tanggal Jakarta', () => {
    expect(wibYesterday(NOW)).toBe('2026-11-17');
    expect(wibYesterday(new Date('2026-11-17T18:00:00Z'))).toBe('2026-11-17'); // 01:00 WIB 18 Nov
    expect(wibYesterday(new Date('2026-11-17T16:00:00Z'))).toBe('2026-11-16'); // 23:00 WIB 17 Nov
  });
  it('util', () => {
    expect(isIsoDate('2026-02-30')).toBe(false);
    expect(isIsoDate('2026-11-17')).toBe(true);
    expect(addDays('2026-03-01', -1)).toBe('2026-02-28');
    expect(diffDays('2026-11-01', '2026-11-17')).toBe(16);
    expect(isoWeekday('2026-11-16')).toBe(1); // Senin
    expect(isoWeekday('2026-11-22')).toBe(7);
  });
});

describe('parseFilters (server-side)', () => {
  it('default: 30 hari s.d. H-1, semua PT sesi, prev sama panjang', () => {
    const r = parseFilters({}, ALL, NOW);
    expect(r.filters).toEqual({ from: '2026-10-19', to: '2026-11-17', pt: ['BCF', 'PB'], branch: [], cat: [], product: [] });
    expect(r.prev).toEqual({ from: '2026-09-19', to: '2026-10-18' });
    expect(r.adjusted).toBe(false);
  });

  it('rentang valid dipakai', () => {
    const r = parseFilters({ from: '2026-11-01', to: '2026-11-10' }, ALL, NOW);
    expect(r.filters.from).toBe('2026-11-01');
    expect(r.prev).toEqual({ from: '2026-10-22', to: '2026-10-31' });
  });

  it.each([
    [{ from: '2026-11-01', to: '2026-11-18' }], // to = hari ini
    [{ from: '2026-11-10', to: '2026-11-01' }], // terbalik
    [{ from: '2025-01-01', to: '2026-11-17' }], // > 366 hari
    [{ from: 'x', to: 'y' }],
    [{ from: '2026-11-01' }],
  ])('rentang tidak valid -> default + adjusted (%j)', (p) => {
    const r = parseFilters(p, ALL, NOW);
    expect(r.filters.from).toBe('2026-10-19');
    expect(r.adjusted).toBe(true);
  });

  it('pt dipotong ke kode sesi; kode asing dibuang; kosong -> semua', () => {
    expect(parseFilters({ pt: 'pb,WTZ,XX' }, ALL, NOW).filters.pt).toEqual(['PB']);
    const r = parseFilters({ pt: 'WTZ' }, ALL, NOW);
    expect(r.filters.pt).toEqual(['BCF', 'PB']);
    expect(r.adjusted).toBe(true);
  });

  it('branch: format <brand>:<id> saja; injeksi dibuang', () => {
    const r = parseFilters({ branch: "pb:4,PB:5,bacha:12,xx:1,pb:abc,pb:1'--,pb:99999999999" }, ALL, NOW);
    expect(r.filters.branch).toEqual(['pb:4', 'pb:5', 'bacha:12']);
    expect(r.adjusted).toBe(true);
  });

  it('branch maks 50', () => {
    const many = Array.from({ length: 60 }, (_, i) => `pb:${i + 1}`).join(',');
    expect(parseFilters({ branch: many }, ALL, NOW).filters.branch).toHaveLength(50);
  });

  it('cat: parameter berulang (boleh memuat koma), kontrol/backslash dibuang, maks 20', () => {
    const p = new URLSearchParams();
    p.append('cat', 'Roti, Kue');
    p.append('cat', 'Kopi');
    p.append('cat', 'bad\\x');
    p.append('cat', 'a'.repeat(65));
    const r = parseFilters(p, ALL, NOW);
    expect(r.filters.cat).toEqual(['Roti, Kue', 'Kopi']);
    expect(r.adjusted).toBe(true);
  });

  it('product: wajib berawalan brand; mendukung NAME:<alias>', () => {
    const r = parseFilters({ product: 'pb:FG-001,PB:NAME:Roti Coklat,FG-002,pb:a;b' }, ALL, NOW);
    expect(r.filters.product).toEqual(['pb:FG-001', 'pb:NAME:Roti Coklat']);
    expect(r.adjusted).toBe(true);
  });

  it('menerima URLSearchParams', () => {
    expect(parseFilters(new URLSearchParams('branch=pb:4'), ALL, NOW).filters.branch).toEqual(['pb:4']);
  });
});

describe('format id-ID', () => {
  it('angka', () => {
    expect(fmtNumber(1234567)).toBe('1.234.567');
    expect(fmtNumber(3.5, 1)).toBe('3,5');
    expect(fmtNumber(12.0, 1)).toBe('12');
    expect(fmtNumber(-1234.56, 2)).toBe('-1.234,56');
    expect(fmtNumber(-0.0001, 0)).toBe('0');
  });
  it('rupiah penuh dan ringkas (contoh spec)', () => {
    expect(fmtIdr(1234567)).toBe('Rp 1.234.567');
    expect(fmtIdrShort(950_000)).toBe('Rp 950.000');
    expect(fmtIdrShort(1_200_000)).toBe('Rp 1,2 jt');
    expect(fmtIdrShort(12_000_000)).toBe('Rp 12 jt');
    expect(fmtIdrShort(3_400_000_000)).toBe('Rp 3,4 M');
    expect(fmtIdrShort(2_500_000_000_000)).toBe('Rp 2,5 T');
    expect(fmtIdrShort(999_960_000)).toBe('Rp 1 M');
    expect(fmtIdrShort(-12_000_000)).toBe('-Rp 12 jt');
  });
  it('persen, delta, tanggal, umur', () => {
    expect(fmtPct(71.43)).toBe('71,4%');
    expect(fmtDelta(4.2, 'pct')).toBe('+4,2%');
    expect(fmtDelta(-1.5, 'pp')).toBe('-1,5 pp');
    expect(fmtDate('2026-11-18')).toBe('18 Nov 2026');
    expect(fmtRange('2026-11-01', '2026-11-17')).toBe('1 Nov - 17 Nov 2026');
    expect(fmtRange('2025-12-20', '2026-01-05')).toBe('20 Des 2025 - 5 Jan 2026');
    expect(fmtAge(3)).toBe('3 jam lalu');
    expect(fmtAge(26)).toBe('1 hari 2 jam lalu');
  });
});

describe('ambang & ringkasan', () => {
  it('status', () => {
    expect(sellThroughStatus(90)).toBe('good');
    expect(sellThroughStatus(75)).toBe('warn');
    expect(sellThroughStatus(60)).toBe('bad');
    expect(sellThroughStatus(null)).toBe('neutral');
    expect(freshnessStatus(null)).toBe('unknown'); // tidak diketahui BUKAN baik
    expect(freshnessStatus(26)).toBe('good');
    expect(freshnessStatus(30)).toBe('warn');
    expect(freshnessStatus(49)).toBe('bad');
    expect(gapStatus(25)).toBe('bad');
    expect(gapStatus(-12)).toBe('warn');
  });
  it('summarize', () => {
    expect(summarize('Tes', ['a', 'b', 'c'], [5, 1, 9], 'int', true)).toBe('Tes: tertinggi 9 pada c, terendah 1 pada b, total 15');
    expect(summarize('Tes', ['a'], [null], 'int', true)).toBe('Tes: tidak ada data');
  });
  it('withOther', () => {
    const items = Array.from({ length: 10 }, (_, i) => ({ label: `k${i}`, value: 10 - i }));
    const r = withOther(items, 8);
    expect(r).toHaveLength(8);
    expect(r[7]).toEqual({ label: 'Lainnya', value: 3 + 2 + 1 });
  });
  it('trendSeries: periode lalu digeser ke sumbu periode ini', () => {
    const rows = [
      { sales_date: '2026-11-01', brand: 'pb', net: 10, trx: 2 },
      { sales_date: '2026-11-02', brand: 'pb', net: 20, trx: 4 },
      { sales_date: '2026-10-31', brand: 'pb', net: 5, trx: 1 }, // prev, len=2 -> digeser ke 11-02
    ];
    const t = trendSeries(rows, '2026-11-01', '2026-11-02', 'net');
    expect(t.days).toEqual(['2026-11-01', '2026-11-02']);
    expect(t.series[0].data).toEqual([10, 20]);
    expect(t.series[1].data).toEqual([0, 5]);
    expect(trendSeries(rows, '2026-11-01', '2026-11-02', 'avg').series[0].data).toEqual([5, 5]);
  });
});
