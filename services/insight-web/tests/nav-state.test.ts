import { describe, expect, it } from 'vitest';
import { parseNavPrefs, serializeNavPrefs, toggleClosed } from '@/lib/nav-state';

describe('nav-state', () => {
  it('nilai kosong/rusak -> bawaan', () => {
    expect(parseNavPrefs(null)).toEqual({ collapsed: false, closed: [] });
    expect(parseNavPrefs('{bukan json')).toEqual({ collapsed: false, closed: [] });
    expect(parseNavPrefs('42')).toEqual({ collapsed: false, closed: [] });
  });
  it('round trip dan penyaringan id tidak sah', () => {
    const s = serializeNavPrefs({ collapsed: true, closed: ['fnb', 'sm'] });
    expect(parseNavPrefs(s)).toEqual({ collapsed: true, closed: ['fnb', 'sm'] });
    expect(parseNavPrefs('{"collapsed":true,"closed":["fnb","<x>",5,"fnb"]}')).toEqual({ collapsed: true, closed: ['fnb'] });
  });
  it('toggleClosed melipat dan membuka', () => {
    expect(toggleClosed([], 'sm')).toEqual(['sm']);
    expect(toggleClosed(['sm', 'fnb'], 'sm')).toEqual(['fnb']);
  });
});
