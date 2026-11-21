import { describe, expect, it } from 'vitest';
import { detectPreset, isValidRange, presetRange } from '@/lib/date-presets';
import { buildEsbQuery, buildSmQuery, nextPt, type FilterDraft } from '@/lib/filter-url';

const Y = '2026-11-20';
const defaults = { from: '2026-10-22', to: Y };
const empty: FilterDraft = { from: defaults.from, to: defaults.to, pt: [], branch: [], cat: [], product: [] };
const all = ['PB', 'WTZ', 'SMD'];

describe('date-presets', () => {
  it('rentang preset terhadap H-1', () => {
    expect(presetRange('7', Y)).toEqual({ from: '2026-11-14', to: Y });
    expect(presetRange('30', Y)).toEqual(defaults);
    expect(presetRange('mtd', Y)).toEqual({ from: '2026-11-01', to: Y });
    expect(presetRange('prev-month', Y)).toEqual({ from: '2026-10-01', to: '2026-10-31' });
    expect(presetRange('ytd', Y)).toEqual({ from: '2026-01-01', to: Y });
  });
  it('detectPreset dan validasi rentang kustom', () => {
    expect(detectPreset('2026-10-22', Y, Y)).toBe('30');
    expect(detectPreset('2026-10-23', Y, Y)).toBeNull();
    expect(isValidRange('2026-11-01', '2026-11-10', Y)).toBe(true);
    expect(isValidRange('2026-11-10', '2026-11-01', Y)).toBe(false);
    expect(isValidRange('2026-11-01', '2026-11-21', Y)).toBe(false);
    expect(isValidRange('', '2026-11-01', Y)).toBe(false);
    expect(isValidRange('2024-01-01', '2026-01-01', Y)).toBe(false);
  });
});

describe('nextPt (chip brand)', () => {
  it('dari Semua memilih satu brand; toggle; kembali ke Semua', () => {
    expect(nextPt([], 'PB', all)).toEqual(['PB']);
    expect(nextPt(['PB'], 'WTZ', all)).toEqual(['PB', 'WTZ']);
    expect(nextPt(['PB', 'WTZ'], 'PB', all)).toEqual(['WTZ']);
    expect(nextPt(['PB'], 'PB', all)).toEqual([]);
    expect(nextPt(['PB', 'WTZ'], 'SMD', all)).toEqual([]); // semua = bawaan
  });
});

describe('buildEsbQuery', () => {
  const o = { defaults, allCodes: all };
  it('bawaan: tanpa param', () => {
    expect(buildEsbQuery(new URLSearchParams(), empty, o)).toBe('');
  });
  it('brand langsung masuk URL; SMD sebagai pilihan sungguhan', () => {
    expect(buildEsbQuery(new URLSearchParams(), { ...empty, pt: ['PB'] }, o)).toBe('pt=PB');
    expect(buildEsbQuery(new URLSearchParams(), { ...empty, pt: ['SMD', 'PB'] }, o)).toBe('pt=PB%2CSMD');
  });
  it('tanggal berbeda dari bawaan ditulis; forceDate menulis walau sama dengan bawaan', () => {
    expect(buildEsbQuery(new URLSearchParams(), { ...empty, from: '2026-11-01' }, o)).toBe('from=2026-11-01&to=2026-11-20');
    expect(buildEsbQuery(new URLSearchParams(), empty, { ...o, forceDate: true })).toBe('from=2026-10-22&to=2026-11-20');
  });
  it('URL yang sudah memuat tanggal mempertahankannya; param lain (sort) dipertahankan; filter lama diganti', () => {
    const cur = new URLSearchParams('from=2026-10-22&to=2026-11-20&sort=qty&pt=WTZ&cat=A');
    expect(buildEsbQuery(cur, { ...empty, pt: ['PB'], cat: ['B', 'C'] }, o)).toBe('sort=qty&from=2026-10-22&to=2026-11-20&pt=PB&cat=B&cat=C');
  });
  it('cabang dan produk', () => {
    expect(buildEsbQuery(new URLSearchParams(), { ...empty, branch: ['pb:1', 'sm:21'], product: ['pb:X'] }, o)).toBe('branch=pb%3A1%2Csm%3A21&product=pb%3AX');
  });
});

describe('buildSmQuery', () => {
  it('tanggal, outlet, kategori; cat undefined tidak menyentuh bawaan', () => {
    expect(buildSmQuery(new URLSearchParams(), { from: '2026-11-01', to: '2026-11-10', wh: [22, 21] })).toBe('from=2026-11-01&to=2026-11-10&wh=21%2C22');
    expect(buildSmQuery(new URLSearchParams('cat=3'), { wh: [] })).toBe('cat=3');
    expect(buildSmQuery(new URLSearchParams('cat=3'), { wh: [], cat: [] })).toBe('cat=');
    expect(buildSmQuery(new URLSearchParams('wh=21'), { wh: [], cat: [7, 5] })).toBe('cat=5%2C7');
  });
});
