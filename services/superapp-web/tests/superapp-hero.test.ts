import { describe, expect, it } from 'vitest';
import { BRAND_PANEL_COPY, HERO_MAX_ITEMS, interleave, SUPERAPP_BRANDS } from '@/lib/superapp';

describe('landing Super-App: hero 3D & etalase', () => {
  it('interleave: bergiliran antar-brand, buang duplikat, berhenti di max', () => {
    const pb = [{ id: 1 }, { id: 2 }, { id: 3 }];
    const bacha = [{ id: 10 }, { id: 11 }];
    const gl: { id: number }[] = [];
    expect(interleave([pb, bacha, gl], 4, (v) => v.id).map((v) => v.id)).toEqual([1, 10, 2, 11]);
    expect(interleave([pb, bacha], 99, (v) => v.id).map((v) => v.id)).toEqual([1, 10, 2, 11, 3]);
    expect(interleave([pb, [{ id: 1 }, { id: 12 }]], 99, (v) => v.id).map((v) => v.id)).toEqual([1, 2, 12, 3]);
    expect(interleave([], 5, (v: { id: number }) => v.id)).toEqual([]);
    expect(interleave([pb], 0, (v) => v.id)).toEqual([]);
  });

  it('hero menampilkan 7 slot kartu; setiap brand Super-App punya copy panel', () => {
    expect(HERO_MAX_ITEMS).toBe(7);
    for (const id of SUPERAPP_BRANDS) {
      expect(BRAND_PANEL_COPY[id].kind.length).toBeGreaterThan(0);
    }
  });
});
