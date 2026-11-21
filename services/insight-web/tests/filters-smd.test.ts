/**
 * Validasi `pt` / `branch` di server untuk SMD sebagai filter halaman F&B (G51 B1, docs/04 §28.14.1).
 */
import { describe, expect, it } from 'vitest';
import { parseFilters } from '@/lib/filters';

const NOW = new Date('2026-11-21T05:00:00Z');
const ESB = ['BCF', 'PB', 'WTZ'];
const EXEC = { smd: true, smWids: [20, 21, 22] };
const q = (s: string): URLSearchParams => new URLSearchParams(s);

describe('parseFilters: SMD hanya untuk sesi exec', () => {
  it('tanpa opsi smd (sesi non-exec): SMD dibuang seperti kode tak dikenal, sm: ditolak, query SM tidak pernah aktif', () => {
    const r = parseFilters(q('pt=PB,SMD&branch=sm:21'), ESB, NOW);
    expect(r.filters.pt).toEqual(['PB']);
    expect(r.filters.branch).toEqual([]);
    expect(r.adjusted).toBe(true);
    expect(r.sel).toEqual({ allowed: false, esb: true, smd: false, wh: [] });
  });

  it('pt=SMD saja tanpa hak: kembali ke semua ESB, bukan SMD', () => {
    const r = parseFilters(q('pt=SMD'), ESB, NOW);
    expect(r.filters.pt).toEqual(['BCF', 'PB', 'WTZ']);
    expect(r.sel.smd).toBe(false);
    expect(r.sel.esb).toBe(true);
    expect(r.adjusted).toBe(true);
  });

  it('exec tanpa param pt: semua PT termasuk SMD (keputusan 21 Nov 2026), tanpa penyesuaian', () => {
    const r = parseFilters(q(''), ESB, NOW, EXEC);
    expect(r.filters.pt).toEqual(['BCF', 'PB', 'WTZ']);
    expect(r.sel).toEqual({ allowed: true, esb: true, smd: true, wh: [] });
    expect(r.adjusted).toBe(false);
  });

  it('exec: pt=PB,SMD -> campuran; Filters.pt tetap hanya ESB', () => {
    const r = parseFilters(q('pt=PB,SMD'), ESB, NOW, EXEC);
    expect(r.filters.pt).toEqual(['PB']);
    expect(r.sel).toEqual({ allowed: true, esb: true, smd: true, wh: [] });
    expect(r.adjusted).toBe(false);
  });

  it('exec: pt=SMD saja -> query ESB mati, pt ESB jatuh ke semua kode sesi (tidak dipakai)', () => {
    const r = parseFilters(q('pt=smd'), ESB, NOW, EXEC);
    expect(r.sel.esb).toBe(false);
    expect(r.sel.smd).toBe(true);
  });

  it('exec tanpa PT F&B di sesi: SMD terpilih bawaan', () => {
    const r = parseFilters(q(''), [], NOW, EXEC);
    expect(r.sel).toEqual({ allowed: true, esb: false, smd: true, wh: [] });
    expect(r.filters.pt).toEqual([]);
  });
});

describe('parseFilters: cabang sm:<warehouse_id>', () => {
  it('menerima sm:id ⊆ wids sesi; id di luar wids dibuang (adjusted)', () => {
    const r = parseFilters(q('pt=SMD&branch=sm:22,sm:21,sm:99'), ESB, NOW, EXEC);
    expect(r.sel.wh).toEqual([21, 22]);
    expect(r.filters.branch).toEqual([]);
    expect(r.adjusted).toBe(true);
  });

  it('bentuk tidak sah dibuang: sm:0, sm:abc, sm:1.5, sm:4294967296', () => {
    const r = parseFilters(q('pt=SMD&branch=sm:0,sm:abc,sm:1.5,sm:4294967296,sm:20'), ESB, NOW, { smd: true });
    expect(r.sel.wh).toEqual([20]);
    expect(r.adjusted).toBe(true);
  });

  it('sm: tanpa SMD dipilih di pt dibuang', () => {
    const r = parseFilters(q('pt=PB&branch=sm:21'), ESB, NOW, EXEC);
    expect(r.sel.wh).toEqual([]);
    expect(r.sel.smd).toBe(false);
    expect(r.adjusted).toBe(true);
  });

  it('campuran: cabang hanya sm -> ESB dikecualikan; cabang hanya ESB -> SMD dikecualikan', () => {
    const a = parseFilters(q('pt=PB,SMD&branch=sm:21'), ESB, NOW, EXEC);
    expect(a.sel).toMatchObject({ esb: false, smd: true, wh: [21] });
    const b = parseFilters(q('pt=PB,SMD&branch=pb:5'), ESB, NOW, EXEC);
    expect(b.sel).toMatchObject({ esb: true, smd: false, wh: [] });
    expect(b.filters.branch).toEqual(['pb:5']);
    const c = parseFilters(q('pt=PB,SMD&branch=pb:5,sm:21'), ESB, NOW, EXEC);
    expect(c.sel).toMatchObject({ esb: true, smd: true, wh: [21] });
  });

  it('pt=SMD dengan cabang ESB: cabang ESB diabaikan (tidak ada irisan), SMD tetap', () => {
    const r = parseFilters(q('pt=SMD&branch=pb:5'), ESB, NOW, EXEC);
    expect(r.filters.branch).toEqual([]);
    expect(r.sel).toMatchObject({ esb: false, smd: true });
    expect(r.adjusted).toBe(true);
  });

  it('nilai ESB lama tidak berubah tanpa SMD', () => {
    const r = parseFilters(q('pt=PB&branch=pb:5,wtz:7'), ESB, NOW);
    expect(r.filters.branch).toEqual(['pb:5', 'wtz:7']);
    expect(r.adjusted).toBe(false);
  });
});
