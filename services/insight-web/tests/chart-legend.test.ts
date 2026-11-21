import { describe, expect, it } from 'vitest';
import type { ChartSpec } from '@/lib/chart-spec';
import { legendItems, legendValue, seriesGlyph, toggleHidden } from '@/lib/chart-legend';

const spec = (over: Partial<ChartSpec>): ChartSpec => ({
  id: 's',
  kind: 'line',
  fmt: 'num',
  categories: ['a', 'b', 'c'],
  series: [
    { name: 'PB', data: [1, 2, 3], color: 'c1' },
    { name: 'Periode lalu (total)', data: [null, 4, 5], color: 'muted', dashed: true },
  ],
  summary: '',
  ...over,
});

describe('chart-legend', () => {
  it('seri tunggal dan heatmap tidak punya legend', () => {
    expect(legendItems(spec({ series: [{ name: 'A', data: [1], color: 'c1' }] }))).toEqual([]);
    expect(legendItems(spec({ kind: 'heat' }))).toEqual([]);
  });
  it('multi-seri: nama, glyph bentuk, total', () => {
    const items = legendItems(spec({}));
    expect(items.map((i) => i.name)).toEqual(['PB', 'Periode lalu (total)']);
    expect(items[0].glyph).toBe('●');
    expect(items[0].value).toBe('Total 6');
    expect(items[1].dashed).toBe(true);
    expect(items[1].value).toBe('Total 9');
  });
  it('persentase memakai nilai terakhir; seri kosong tanpa nilai', () => {
    expect(legendValue('pct', [10, null, 48.2])).toBe('Terakhir 48,2%');
    expect(legendValue('idr', [null, null])).toBe('');
    expect(legendValue('idr-short', [1_000_000, 2_000_000])).toBe('Total Rp 3.000.000');
  });
  it('penanda bentuk berbeda per warna', () => {
    expect(seriesGlyph({ color: 'c1' })).not.toBe(seriesGlyph({ color: 'c2' }));
    expect(seriesGlyph({ color: 'c1', marker: 'diamond' })).toBe('◆');
  });
  it('toggleHidden: menyembunyikan, memunculkan, dan menjaga minimal satu seri tampil', () => {
    const all = ['A', 'B'];
    expect(toggleHidden([], 'A', all)).toEqual(['A']);
    expect(toggleHidden(['A'], 'A', all)).toEqual([]);
    expect(toggleHidden(['A'], 'B', all)).toEqual(['A']);
  });
});
