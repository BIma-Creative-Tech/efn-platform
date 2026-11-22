import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { AISLES, aisleHref, aisleOfCategory, fastestPrep, featuredAisles, PHI, phiScale, pickupStores } from '@/lib/supermarket';
import { orderModes } from '@/lib/order-modes';
import { BRANDS } from '@/lib/brands';
import type { CatalogChannel } from '@/lib/catalog';

const channel = (outlets: CatalogChannel['outlets']): CatalogChannel => ({
  channel_code: 'SMD',
  company_code: 'SMD',
  name: 'Supermarket Demo',
  brand_code: 'SMD',
  payment_modes: null,
  business_type: 'retail',
  loyalty: null,
  fulfilment: { pickup: true, delivery: false },
  outlets,
});

describe('golden ratio', () => {
  it('skala φ^-2..φ^4 sama dengan token CSS landing', () => {
    expect(PHI).toBe(1.618);
    const scale = phiScale();
    expect(scale).toEqual([0.382, 0.618, 1, 1.618, 2.618, 4.236, 6.854]);
    const css = readFileSync(join(__dirname, '../components/supermarket/supermarket.module.css'), 'utf8');
    for (const v of scale) expect(css).toContain(`${v}rem`);
    expect(css).toContain('minmax(0, 1.618fr) minmax(0, 1fr)'); // hero
    expect(css).toContain('aspect-ratio: 1 / 1.618'); // kartu produk
  });
  it('gerak dimatikan untuk prefers-reduced-motion', () => {
    const css = readFileSync(join(__dirname, '../components/supermarket/supermarket.module.css'), 'utf8');
    expect(css).toMatch(/@media \(prefers-reduced-motion: reduce\)[\s\S]*animation: none !important/);
  });
});

describe('lorong', () => {
  it('nama kategori unik; enam lorong unggulan; Alkohol tidak dipromosikan online', () => {
    const names = AISLES.map((a) => a.category);
    expect(new Set(names).size).toBe(names.length);
    expect(featuredAisles()).toHaveLength(6);
    expect(names).not.toContain('Alkohol');
  });
  it('aisleOfCategory tidak peka huruf besar', () => {
    expect(aisleOfCategory('ikan & seafood')?.id).toBe('ikan');
    expect(aisleOfCategory('Tidak Ada')).toBeNull();
    expect(aisleOfCategory(null)).toBeNull();
  });
  it('aisleHref: kategori katalog bila ada, kalau tidak pencarian', () => {
    const cats = [{ category_id: 7, name: 'Buah', parent_id: null, sequence: 1, image_sha: null }];
    const buah = AISLES.find((a) => a.id === 'buah')!;
    const sayur = AISLES.find((a) => a.id === 'sayur')!;
    expect(aisleHref(cats, buah, null)).toBe('/belanja?category=7');
    expect(aisleHref(cats, sayur, null)).toBe('/belanja?q=Sayur');
    expect(aisleHref(cats, sayur, 'SMD')).toBe('/belanja?channel=SMD&q=Sayur');
  });
});

describe('toko pickup', () => {
  it('hanya outlet pickup, tanpa duplikat, urut nama; menit tercepat > 0', () => {
    const c = channel([
      { outlet_code: 'SMD02', public_name: 'Toko B', pickup_enabled: true, prep_minutes: 45, slot_enabled: true },
      { outlet_code: 'SMD01', public_name: 'Toko A', pickup_enabled: true, prep_minutes: 30 },
      { outlet_code: 'SMD03', public_name: 'Toko C', pickup_enabled: false, prep_minutes: 10 },
    ]);
    const stores = pickupStores([c, c]);
    expect(stores.map((s) => s.code)).toEqual(['SMD01', 'SMD02']);
    expect(stores[1]!.preorder).toBe(true);
    expect(fastestPrep(stores)).toBe(30);
    expect(fastestPrep([])).toBeNull();
  });
  it('mode pesan supermarket: pickup + pre-order + pesan-antar (segera), TANPA dine-in/reservasi', () => {
    const c = channel([{ outlet_code: 'SMD01', public_name: 'Toko A', pickup_enabled: true, prep_minutes: 30, slot_enabled: true }]);
    const ids = orderModes({ brand: BRANDS.supermarket, channels: [c] }).map((m) => m.id);
    expect(ids).toEqual(['pickup', 'preorder', 'delivery']);
  });
});
