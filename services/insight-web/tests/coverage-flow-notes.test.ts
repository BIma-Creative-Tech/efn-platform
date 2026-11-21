import { describe, expect, it } from 'vitest';
import { comparable, coverageNotices, deltaGuards, entitySpan, fmtSpan, type CoverageRow } from '@/lib/coverage';
import { ckSendDayNote, ckSendPeriodNote, groupCkLines, joinNotes } from '@/lib/flow-notes';

const row = (entity: string, first: string, last: string, days: number, pt = 'PB'): CoverageRow => ({ brand: 'pb', company_code: pt, entity, first_day: first, last_day: last, days });

describe('fmtSpan', () => {
  it('format rentang', () => {
    expect(fmtSpan('2026-11-10', '2026-11-16')).toBe('10-16 Nov 2026');
    expect(fmtSpan('2026-10-22', '2026-11-20')).toBe('22 Okt-20 Nov 2026');
    expect(fmtSpan('2025-12-30', '2026-01-02')).toBe('30 Des 2025-2 Jan 2026');
    expect(fmtSpan('2026-11-10', '2026-11-10')).toBe('10 Nov 2026');
  });
});

describe('coverageNotices', () => {
  const rows = [row('sales', '2025-11-01', '2026-11-20', 385), row('production', '2026-11-10', '2026-11-16', 7)];
  it('rentang melebihi data: sebut rentang tersedia dan rentang dipilih', () => {
    const n = coverageNotices(rows, ['production'], { from: '2026-10-22', to: '2026-11-20' });
    expect(n).toHaveLength(1);
    expect(n[0]).toContain('Data produksi tersedia 10-16 Nov 2026; rentang dipilih 22 Okt-20 Nov 2026');
    expect(n[0]).toContain('bukan karena tidak ada produksi');
  });
  it('rentang di dalam data: tidak ada banner', () => {
    expect(coverageNotices(rows, ['production', 'sales'], { from: '2026-11-11', to: '2026-11-15' })).toEqual([]);
  });
  it('entitas tanpa data sama sekali', () => {
    expect(coverageNotices(rows, ['delivery'], { from: '2026-11-11', to: '2026-11-15' })[0]).toContain('Belum ada data pengiriman');
  });
  it('hari bolong disebut', () => {
    const n = coverageNotices([row('sales', '2026-11-01', '2026-11-10', 8)], ['sales'], { from: '2026-11-02', to: '2026-11-09' });
    expect(n[0]).toContain('8 dari 10 hari');
  });
  it('entitySpan menggabungkan PT', () => {
    expect(entitySpan([row('sales', '2026-01-05', '2026-02-01', 20, 'PB'), row('sales', '2026-01-01', '2026-01-31', 30, 'WTZ')], 'sales')).toEqual({ first: '2026-01-01', last: '2026-02-01' });
    expect(entitySpan([], 'sales')).toBeNull();
  });
});

describe('comparable / deltaGuards', () => {
  const rows = [row('sales', '2026-10-01', '2026-11-20', 51)];
  const cur = { from: '2026-11-01', to: '2026-11-20' };
  it('periode lalu di luar data -> tidak bermakna -> delta null (bukan -100%)', () => {
    const prev = { from: '2026-09-12', to: '2026-10-31' };
    expect(comparable(rows, ['sales'], cur, prev)).toBe(false);
    const g = deltaGuards(false);
    expect(g.pctDelta(0, 100)).toBeNull();
    expect(g.ppDelta(10, 20)).toBeNull();
  });
  it('kedua periode tercakup -> delta normal', () => {
    const prev = { from: '2026-10-12', to: '2026-10-31' };
    expect(comparable(rows, ['sales'], cur, prev)).toBe(true);
    expect(deltaGuards(true).pctDelta(110, 100)).toBeCloseTo(10);
  });
  it('tanpa data cakupan (view gagal): perilaku lama', () => {
    expect(comparable(null, ['sales'], cur, cur)).toBe(true);
    expect(comparable([], ['sales'], cur, cur)).toBe(true);
  });
});

describe('flow-notes', () => {
  it('kirim > produksi periode: stok sebelum from', () => {
    const n = ckSendPeriodNote([{ ck: 'CK Cikarang', prod: 100, out: 150 }], '2026-10-22');
    expect(n).toContain('Kirim melebihi produksi periode ini sebesar 50');
    expect(n).toContain('stok CK sebelum 22 Okt 2026');
    expect(n).toContain('stok awal tidak tercatat di ESB');
  });
  it('produksi tercatat di CK lain disebut', () => {
    const n = ckSendPeriodNote(
      [
        { ck: 'CK A', prod: 0, out: 80 },
        { ck: 'CKWH Alam Sutera', prod: 200, out: 20 },
      ],
      '2026-11-01',
    );
    expect(n).toContain('CKWH Alam Sutera (produksi 200, kirim 20)');
  });
  it('kirim <= produksi: tidak ada keterangan', () => {
    expect(ckSendPeriodNote([{ ck: 'CK A', prod: 100, out: 100 }], '2026-11-01')).toBeNull();
    expect(ckSendDayNote(10, 10)).toBeNull();
    expect(ckSendDayNote(40, 10)).toContain('sebesar 30');
  });
  it('groupCkLines dan joinNotes', () => {
    const m = groupCkLines([
      { brand: 'pb', product_code: 'X1', ck_label: 'A', prod_qty: 1, out_qty: 2 },
      { brand: 'pb', product_code: 'X1', ck_label: 'B', prod_qty: 5, out_qty: 0 },
    ]);
    expect(m.get('pb:X1')).toHaveLength(2);
    expect(joinNotes('a', null, '', 'b')).toBe('a b');
  });
});
