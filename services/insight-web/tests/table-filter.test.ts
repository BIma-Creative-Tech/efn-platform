import { describe, expect, it } from 'vitest';
import { countText, distinct, matchesQuery, matchesSelects } from '@/lib/table-filter';

describe('table-filter', () => {
  it('pencarian: semua kata harus ada, tanpa peka huruf besar dan aksen', () => {
    expect(matchesQuery('Croissant Almond - Roti', 'almond roti')).toBe(true);
    expect(matchesQuery('Croissant Almond', 'ALMOND')).toBe(true);
    expect(matchesQuery('Croissant Almond', 'almond kopi')).toBe(false);
    expect(matchesQuery('Café Latte', 'cafe')).toBe(true);
    expect(matchesQuery('apa saja', '   ')).toBe(true);
  });
  it('filter dropdown: kosong = semua; harus sama persis', () => {
    expect(matchesSelects({ category: 'Roti', status: 'bad' }, { category: '', status: '' })).toBe(true);
    expect(matchesSelects({ category: 'Roti', status: 'bad' }, { category: 'Roti' })).toBe(true);
    expect(matchesSelects({ category: 'Roti', status: 'bad' }, { category: 'Kue' })).toBe(false);
    expect(matchesSelects({ category: 'Roti' }, { status: 'bad' })).toBe(false);
  });
  it('distinct urut dan membuang kosong', () => {
    expect(distinct(['b', '', 'a', 'b'])).toEqual(['a', 'b']);
  });
  it('countText', () => {
    expect(countText(5, 5)).toBe('5 baris');
    expect(countText(2, 340)).toBe('2 dari 340 baris');
  });
});
