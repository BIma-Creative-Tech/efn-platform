import { describe, expect, it } from 'vitest';
import { BRANDS } from '@/lib/brands';
import { PORTFOLIO, portfolioCounts, safePortfolioLogo, STATUS_LABEL } from '@/lib/portfolio';
import { SUPERAPP_BRANDS } from '@/lib/superapp';

describe('portofolio brand EFN (landing app.)', () => {
  it('memuat 8 brand: 3 online, CHAGEE/Wetzel\'s/Curry Up di gerai, I\'m Donut & Oriental Kopi segera', () => {
    expect(PORTFOLIO.map((b) => b.id)).toEqual(['parisbaguette', 'bacha', 'grandlucky', 'chagee', 'wetzels', 'curryup', 'imdonut', 'orientalkopi']);
    expect(portfolioCounts(PORTFOLIO)).toEqual({ all: 8, online: 3, store: 3, soon: 2 });
    for (const b of PORTFOLIO) {
      if (b.status === 'soon') expect(b.eta).toBeTruthy();
      else expect(b.eta).toBeNull();
      expect(STATUS_LABEL[b.status]).toBeTruthy();
    }
  });

  it('id unik; brand online = tenant Super-App yang ada', () => {
    expect(new Set(PORTFOLIO.map((b) => b.id)).size).toBe(PORTFOLIO.length);
    const online = PORTFOLIO.filter((b) => b.status === 'online');
    expect(online.map((b) => b.tenant).sort()).toEqual([...SUPERAPP_BRANDS].sort());
    for (const b of online) expect(BRANDS[b.tenant!].name).toBe(b.name);
    for (const b of PORTFOLIO.filter((x) => x.status !== 'online')) expect(b.tenant).toBeNull();
  });

  it('logo hanya path same-origin /brands/<id>/*.svg|png', () => {
    expect(safePortfolioLogo({ logo: null })).toBeNull();
    expect(safePortfolioLogo({ logo: '/brands/chagee/logo.svg' })).toBe('/brands/chagee/logo.svg');
    expect(safePortfolioLogo({ logo: '/brands/efn/ebn.png' })).toBe('/brands/efn/ebn.png');
    expect(safePortfolioLogo({ logo: 'https://cdn.example.com/logo.png' })).toBeNull();
    expect(safePortfolioLogo({ logo: '/brands/../x/logo.svg' })).toBeNull();
    expect(safePortfolioLogo({ logo: '/brands/a/logo.svg?x=1' })).toBeNull();
  });

  it('palet EBN (logo PT Era Boga Nusantara) dipakai tema EFN', () => {
    const t = BRANDS.efn.theme;
    expect(t.scheme).toBe('dark');
    expect(t.surfaceAlt).toBe('#062e2a');
    expect(t.accent).toBe('#dfb422');
  });
});
