/**
 * Halaman persona F&B (/fnb/*) hanya ESB: toolbar filter global tidak boleh menawarkan SMD (G52, QA 22 Nov 2026).
 * Server (`fnbRangePage` -> parseFilters tanpa opsi smd) menolak `pt=SMD` sebagai "filter tidak valid".
 */
import { describe, expect, it } from 'vitest';
import { selectableCodes } from '@/lib/page-filters';
import { parseFilters } from '@/lib/filters';

const CODES = ['PB', 'WTZ', 'SMD'];

describe('selectableCodes', () => {
  it('membuang SMD di /fnb/exec dan /fnb/marketing', () => {
    expect(selectableCodes('/fnb/exec', CODES)).toEqual(['PB', 'WTZ']);
    expect(selectableCodes('/fnb/marketing', CODES)).toEqual(['PB', 'WTZ']);
  });
  it('membuang SMD di /fnb/store', () => {
    expect(selectableCodes('/fnb/store', CODES)).toEqual(['PB', 'WTZ']);
  });
  it('mempertahankan SMD di halaman F&B bersama (/sales, /forecast, /)', () => {
    for (const p of ['/', '/sales', '/payments', '/forecast', '/distribution']) {
      expect(selectableCodes(p, CODES)).toEqual(CODES);
    }
  });
  it('tidak memutasi input', () => {
    const input = [...CODES];
    selectableCodes('/fnb/exec', input);
    expect(input).toEqual(CODES);
  });
  it('server tetap menolak pt=SMD tanpa opsi smd (kontrak sisi server)', () => {
    const r = parseFilters({ pt: 'SMD' }, ['PB', 'WTZ'], new Date('2026-11-22T03:00:00Z'));
    expect(r.adjusted).toBe(true);
    expect(r.filters.pt).toEqual(['PB', 'WTZ']);
    expect(parseFilters({ branch: 'sm:5' }, ['PB', 'WTZ'], new Date('2026-11-22T03:00:00Z')).adjusted).toBe(true);
  });
});
