import { describe, expect, it } from 'vitest';
import { BRANDS } from '../lib/brands';
import { categoryHref, chapterStats, findCategory, LANDINGS, landingFor, pickPhotos } from '../lib/landing-config';

/** Rasio kontras WCAG 2.x dari dua warna #rrggbb. */
function contrast(a: string, b: string): number {
  const lum = (hex: string) => {
    const c = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255).map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
    return 0.2126 * c[0]! + 0.7152 * c[1]! + 0.0722 * c[2]!;
  };
  const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x) as [number, number];
  return (hi + 0.05) / (lo + 0.05);
}

describe('landing page brand', () => {
  it('Paris Baguette punya landing; brand lain tetap beranda lama', () => {
    expect(landingFor('parisbaguette')?.chapters.length).toBeGreaterThan(0);
    expect(landingFor('efn')).toBeNull();
    expect(landingFor('grandlucky')).toBeNull();
    expect(landingFor('bacha')).toBeNull();
  });

  it('kata penekanan ada di judul hero brand', () => {
    for (const [id, cfg] of Object.entries(LANDINGS)) {
      if (!cfg?.emphasis) continue;
      const words = BRANDS[id as keyof typeof BRANDS].hero.title.toLowerCase().split(/[^\p{L}\p{N}]+/u);
      expect(words).toContain(cfg.emphasis.toLowerCase());
    }
  });

  it('syarat token landing: aksen & teks di latar/kartu ≥ 4.5:1, teks tombol di aksen ≥ 4.5:1', () => {
    for (const id of Object.keys(LANDINGS)) {
      const t = BRANDS[id as keyof typeof BRANDS].theme;
      for (const bg of [t.surfaceAlt, t.surface]) {
        expect(contrast(t.accent, bg)).toBeGreaterThanOrEqual(4.5);
        expect(contrast(t.text, bg)).toBeGreaterThanOrEqual(4.5);
        expect(contrast(t.textMuted, bg)).toBeGreaterThanOrEqual(4.5);
      }
      expect(contrast(t.chrome, t.accent)).toBeGreaterThanOrEqual(4.5); // teks navy di tombol kuning
      expect(contrast(t.chrome, '#ffffff')).toBeGreaterThanOrEqual(4.5); // keterangan di kartu foto putih
    }
  });

  it('PB memakai warna resmi parisbaguette.co.id (navy #002663, putih, #aebcd3, kuning #fcb134)', () => {
    const t = BRANDS.parisbaguette.theme;
    expect(t.scheme).toBe('dark');
    expect(t.surfaceAlt).toBe('#002663');
    expect(t.chrome).toBe('#002663');
    expect(t.text).toBe('#ffffff');
    expect(t.textMuted).toBe('#aebcd3');
    expect(t.accent).toBe('#fcb134');
  });

  it('categoryHref: cocok nama (tak peka huruf) -> ?category=; tidak ada -> /belanja; channel hanya bila diberikan', () => {
    const cats = [
      { category_id: 18, name: 'Breads' },
      { category_id: 19, name: 'Pastries' },
    ];
    expect(categoryHref(cats, 'pastries', null)).toBe('/belanja?category=19');
    expect(categoryHref(cats, 'Cakes', null)).toBe('/belanja');
    expect(categoryHref(cats, 'Breads', 'PB')).toBe('/belanja?channel=PB&category=18');
    expect(categoryHref([], 'Breads', 'PB')).toBe('/belanja?channel=PB');
  });

  it('setiap bab punya judul, isi, label, kategori (copy DRAF tidak kosong); id unik', () => {
    for (const cfg of Object.values(LANDINGS)) {
      for (const m of cfg!.chapters) {
        expect(m.title.trim().length).toBeGreaterThan(0);
        expect(m.body.trim().length).toBeGreaterThan(0);
        expect(m.label.trim().length).toBeGreaterThan(0);
        expect(m.categoryName.trim().length).toBeGreaterThan(0);
      }
      expect(new Set(cfg!.chapters.map((m) => m.id)).size).toBe(cfg!.chapters.length);
    }
  });

  it('findCategory: tak peka huruf besar & spasi tepi', () => {
    const cats = [{ category_id: 20, name: ' Cakes ' }];
    expect(findCategory(cats, 'cakes')?.category_id).toBe(20);
    expect(findCategory(cats, 'Breads')).toBeNull();
  });

  it('pickPhotos: unggulan dulu (urutan featured), lalu sisa bergambar; tanpa gambar & duplikat dibuang; maks n', () => {
    const items = [
      { product_id: 1, name: 'Twist Donut', image_sha: 'a' },
      { product_id: 2, name: 'Croissant', image_sha: null },
      { product_id: 3, name: 'Kouign Amann', image_sha: 'c' },
      { product_id: 4, name: 'Almond Croissant', image_sha: 'd' },
      { product_id: 5, name: 'Pain Au Chocolat', image_sha: '' },
      { product_id: 4, name: 'Almond Croissant', image_sha: 'd' },
    ];
    expect(pickPhotos(items, ['croissant', 'Almond Croissant', 'Kouign Amann'], 3).map((p) => p.product_id)).toEqual([4, 3, 1]);
    expect(pickPhotos(items, [], 2).map((p) => p.product_id)).toEqual([1, 3]);
    expect(pickPhotos(items, ['Tidak Ada'], 5).map((p) => p.product_id)).toEqual([1, 3, 4]);
    expect(pickPhotos([], ['Croissant'])).toEqual([]);
  });

  it('chapterStats: F&B pakai harga sebelum pajak, ritel termasuk pajak; harga 0/tak valid diabaikan; "more" dari cursor', () => {
    const items = [
      { name: 'A', price_incl: 27500, price_excl: 25000 },
      { name: 'B', price_incl: 24200, price_excl: 22000 },
      { name: 'C', price_incl: 0, price_excl: 0 },
      { name: 'D', price_incl: 49500, price_excl: 45000 },
    ];
    expect(chapterStats(items, null, 'fnb')).toEqual({ count: 4, more: false, minPrice: 22000, names: ['A', 'B', 'C'] });
    expect(chapterStats(items, 'next', 'retail').minPrice).toBe(24200);
    expect(chapterStats(items, 'next', 'retail').more).toBe(true);
    expect(chapterStats([], null, 'fnb')).toEqual({ count: 0, more: false, minPrice: null, names: [] });
  });
});
