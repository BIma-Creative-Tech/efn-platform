import { describe, expect, it } from 'vitest';
import { BRANDS, COMPANIES, getBrandById, isBrandId, isSafeThemeValue, normalizeHost, resolveBrandIdFromHost, safeLogoSrc, themeToCssVars } from '../lib/brands';

describe('normalizeHost', () => {
  it.each([
    ['MART.EFN.LOCAL:3001', 'mart.efn.local'],
    ['mart.efn.local.', 'mart.efn.local'],
    ['mart.efn.local, kitchen.efn.local', 'mart.efn.local'],
    ['[::1]:3001', '[::1]'],
    ['', ''],
  ])('%j -> %j', (input, expected) => {
    expect(normalizeHost(input)).toBe(expected);
  });

  it('null/undefined -> string kosong', () => {
    expect(normalizeHost(null)).toBe('');
    expect(normalizeHost(undefined)).toBe('');
  });
});

describe('resolveBrandIdFromHost', () => {
  it.each([
    ['app.efn.local', 'efn'],
    ['efn.local', 'efn'],
    ['localhost:3001', 'efn'],
    ['grandlucky.efn.local', 'grandlucky'],
    ['GrandLucky.Efn.Local:443', 'grandlucky'],
    ['parisbaguette.efn.local', 'parisbaguette'],
    ['bacha.efn.local', 'bacha'],
    ['wetzels.efn.local', 'wetzels'],
    ['wetzels.contoh-produksi.co.id', 'wetzels'],
    ['grandlucky.contoh-produksi.co.id', 'grandlucky'], // prefix subdomain di domain apa pun
    ['parisbaguette.contoh-produksi.co.id', 'parisbaguette'],
    ['bacha.contoh-produksi.co.id', 'bacha'],
    ['mart.efn.local', 'grandlucky'], // alias subdomain lama
    ['kitchen.contoh-produksi.co.id', 'parisbaguette'], // alias subdomain lama
    ['app.contoh-produksi.co.id', 'efn'],
    ['mart', 'efn'], // tanpa titik: bukan subdomain
    ['bacha', 'efn'],
    ['evil.mart.contoh.co.id', 'efn'], // hanya label pertama yang dicocokkan
    ['evil.bacha.contoh.co.id', 'efn'],
    ['efn-mart.efn.local', 'efn'], // id tenant lama bukan subdomain
    ['tidak-dikenal.example.com', 'efn'],
    ['[::1]:3001', 'efn'],
    ['', 'efn'],
  ])('%j -> %j', (host, expected) => {
    expect(resolveBrandIdFromHost(host)).toBe(expected);
  });

  it('brand tidak dikenal -> default, bukan error', () => {
    expect(resolveBrandIdFromHost(null)).toBe('efn');
    expect(resolveBrandIdFromHost(undefined)).toBe('efn');
  });
});

describe('isBrandId / getBrandById', () => {
  it('tidak tertipu properti prototype', () => {
    expect(isBrandId('__proto__')).toBe(false);
    expect(isBrandId('toString')).toBe(false);
    expect(getBrandById('constructor').id).toBe('efn');
  });
  it('brandCode non-rahasia & valid (G19 §11.3); default = semua kanal', async () => {
    const { BRANDS } = await import('../lib/brands');
    const { isBrandCode, channelsPath } = await import('../lib/catalog');
    expect(BRANDS.efn.brandCode).toBeNull();
    for (const b of Object.values(BRANDS)) expect(b.brandCode === null || isBrandCode(b.brandCode)).toBe(true);
    expect(channelsPath(null)).toBe('/v1/catalog/channels');
    expect(channelsPath('GL')).toBe('/v1/catalog/channels?brand=GL');
    expect(channelsPath('gl')).toBe('/v1/catalog/channels');
  });
  it('id valid', () => {
    expect(isBrandId('grandlucky')).toBe(true);
    expect(isBrandId('efn-mart')).toBe(false); // id lama dihapus
    expect(getBrandById('efn-kitchen').id).toBe('efn'); // id lama -> default, bukan error
    expect(getBrandById('parisbaguette').companies).toEqual(['PB']);
    expect(getBrandById('grandlucky').companies).toEqual(['GL']);
    expect(getBrandById('bacha').companies).toEqual(['BCR', 'BCF']);
    expect(getBrandById('wetzels').companies).toEqual(['WTZ']);
    expect(getBrandById('efn').companies).toEqual(['GL', 'PB', 'BCR', 'BCF', 'WTZ']);
  });
  it('kode RTL/FNB sudah dihapus dari peta PT; tujuh entitas (§12.1 + G24 §16 WTZ)', async () => {
    const { COMPANIES, BRANDS } = await import('../lib/brands');
    expect(Object.keys(COMPANIES).sort()).toEqual(['BCF', 'BCR', 'EFN', 'FARM', 'GL', 'PB', 'WTZ']);
    expect(COMPANIES.WTZ.businessType).toBe('fnb');
    // Layanan meja (QR meja + reservasi) hanya PB/BCF; WTZ kios takeaway.
    expect(Object.values(COMPANIES).filter((c) => c.tableService).map((c) => c.code).sort()).toEqual(['BCF', 'PB']);
    for (const b of Object.values(BRANDS)) for (const c of b.companies) expect(COMPANIES).toHaveProperty(c);
  });
  it('peta brand Bacha = dua kanal BCR + BCF; kode tak dikenal -> kosong', async () => {
    const { channelsOfBrandCode } = await import('../lib/brands');
    expect(channelsOfBrandCode('BACHA')).toEqual(['BCR', 'BCF']);
    expect(channelsOfBrandCode('GL')).toEqual(['GL']);
    expect(channelsOfBrandCode('PB')).toEqual(['PB']);
    expect(channelsOfBrandCode('WTZ')).toEqual(['WTZ']);
    expect(channelsOfBrandCode('RTL')).toEqual([]);
    expect(channelsOfBrandCode('__proto__')).toEqual([]);
    expect(channelsOfBrandCode(null)).toEqual([]);
  });
  it('L-33 (diperbarui 20 Okt 2026): tenant brand memakai brandCode-nya, holding tetap null; kanal brand = PT tenant', async () => {
    const { channelsOfBrandCode } = await import('../lib/brands');
    expect(BRANDS.efn.brandCode).toBeNull();
    expect(BRANDS.grandlucky.brandCode).toBe('GL');
    expect(BRANDS.parisbaguette.brandCode).toBe('PB');
    expect(BRANDS.bacha.brandCode).toBe('BACHA');
    expect(BRANDS.wetzels.brandCode).toBe('WTZ');
    for (const b of Object.values(BRANDS)) {
      if (b.brandCode !== null) expect([...channelsOfBrandCode(b.brandCode)]).toEqual(b.companies);
    }
  });
});

describe('tema per brand (R-T14, token aman untuk atribut style)', () => {
  it('setiap brand menghasilkan semua CSS var (tidak ada token yang terbuang validasi)', () => {
    for (const b of Object.values(BRANDS)) {
      const vars = themeToCssVars(b.theme);
      // G24: + font-display/label/nav, heading-transform, nav-transform, ui-weight, logo-weight (20 -> 27).
      expect(Object.keys(vars)).toHaveLength(27);
      expect(vars['--font-body']).toBe(b.theme.fontBody);
      expect(vars['--font-display']).toBe(b.theme.fontDisplay);
      expect(vars['--radius']).toBe(b.theme.radius);
    }
  });
  it('nilai berbahaya dibuang, bukan diteruskan ke style', () => {
    const evil = {
      ...BRANDS.efn.theme,
      primary: 'red;background:url(https://x)',
      fontBody: 'x; } body { display:none',
      fontHeading: 'Arial", evil',
      radius: 'calc(1px)',
      cardShadow: 'url(https://x)',
    };
    const vars = themeToCssVars(evil);
    expect(vars['--color-primary']).toBeUndefined();
    expect(vars['--font-body']).toBeUndefined();
    expect(vars['--font-heading']).toBeUndefined();
    expect(vars['--radius']).toBeUndefined();
    expect(vars['--card-shadow']).toBeUndefined();
    expect(vars['--color-text']).toBe(BRANDS.efn.theme.text);
    expect(isSafeThemeValue('color', '#fff')).toBe(false); // hanya #rrggbb
    expect(isSafeThemeValue('font', "Georgia, 'Times New Roman', serif")).toBe(true);
  });

  /** Rasio kontras WCAG 2.x. */
  function contrast(a: string, b: string): number {
    const lum = (hex: string) => {
      const [r, g, bl] = [1, 3, 5].map((i) => {
        const c = parseInt(hex.slice(i, i + 2), 16) / 255;
        return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
      });
      return 0.2126 * r! + 0.7152 * g! + 0.0722 * bl!;
    };
    const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x);
    return (hi! + 0.05) / (lo! + 0.05);
  }
  it.each(Object.values(BRANDS).map((b) => [b.id, b.theme] as const))('%s: kontras teks ≥ 4.5:1, fokus ≥ 3:1 (WCAG 2.2 AA)', (_id, t) => {
    for (const bg of [t.surface, t.surfaceAlt, t.accentSoft]) {
      expect(contrast(t.text, bg)).toBeGreaterThanOrEqual(4.5);
      expect(contrast(t.textMuted, bg)).toBeGreaterThanOrEqual(4.5);
      expect(contrast(t.primary, bg)).toBeGreaterThanOrEqual(4.5); // tautan & judul hero
      expect(contrast(t.focus, bg)).toBeGreaterThanOrEqual(3);
    }
    expect(contrast(t.primaryContrast, t.primary)).toBeGreaterThanOrEqual(4.5); // tombol
    expect(contrast(t.chromeContrast, t.chrome)).toBeGreaterThanOrEqual(4.5); // header & footer
    expect(contrast(t.accent, t.chrome)).toBeGreaterThanOrEqual(3); // garis aksen header (non-teks)
  });
  it('logo hanya path svg/png same-origin di /brands/<id>/', () => {
    expect(safeLogoSrc(BRANDS.bacha)).toBeNull();
    expect(safeLogoSrc(BRANDS.parisbaguette)).toBe('/brands/parisbaguette/logo.png');
    expect(safeLogoSrc(BRANDS.wetzels)).toBe('/brands/wetzels/logo.svg');
    expect(safeLogoSrc({ ...BRANDS.bacha, logoSrc: '/brands/bacha/logo.svg' })).toBe('/brands/bacha/logo.svg');
    expect(safeLogoSrc({ ...BRANDS.bacha, logoSrc: '/brands/bacha/logo.gif' })).toBeNull();
    expect(safeLogoSrc({ ...BRANDS.bacha, logoSrc: 'https://cdn.example.com/logo.svg' })).toBeNull();
    expect(safeLogoSrc({ ...BRANDS.bacha, logoSrc: '/brands/../secrets/logo.svg' })).toBeNull();
    expect(safeLogoSrc({ ...BRANDS.bacha, logoSrc: 'javascript:alert(1)' })).toBeNull();
  });
});

describe('font per brand = font situs resmi (G24, diukur 24 Okt 2026)', () => {
  const first = (stack: string) => stack.split(',')[0]!.trim().replace(/^'|'$/g, '');
  it.each([
    ['parisbaguette', 'PB Signature Display', 'PB Special Elite', 'Roboto'],
    ['bacha', 'ITC Quorum', 'ITC Quorum', 'Bacha Lora'],
    ['wetzels', 'Londrina Solid', 'Londrina Solid', 'Roboto'],
    ['grandlucky', 'Lato', 'Lato', 'Lato'],
    ['efn', 'Fraunces Variable', 'Fraunces Variable', 'Geist Variable'],
  ] as const)('%s: display %s, heading %s, body %s', (id, display, heading, body) => {
    const t = BRANDS[id].theme;
    expect(first(t.fontDisplay)).toBe(display);
    expect(first(t.fontHeading)).toBe(heading);
    expect(first(t.fontBody)).toBe(body);
  });
  it('setiap family brand punya @font-face self-host di app/fonts.css dan file-nya ada di public/fonts', async () => {
    const { readFileSync, existsSync } = await import('node:fs');
    const { join } = await import('node:path');
    const css = readFileSync(join(__dirname, '../app/fonts.css'), 'utf8');
    const declared = new Set([...css.matchAll(/font-family: '([^']+)'/g)].map((m) => m[1]));
    for (const b of Object.values(BRANDS)) {
      for (const stack of [b.theme.fontDisplay, b.theme.fontHeading, b.theme.fontBody, b.theme.fontLabel, b.theme.fontNav]) {
        const fam = first(stack);
        if (fam.endsWith(' Variable')) continue; // @fontsource-variable (EFN)
        expect(declared.has(fam), fam).toBe(true);
      }
    }
    for (const m of css.matchAll(/url\('\/fonts\/([^']+)'\)/g)) expect(existsSync(join(__dirname, '../public/fonts', m[1]!)), m[1]).toBe(true);
    expect(css).not.toMatch(/url\(['"]?https?:/); // CSP font-src 'self'
  });
  it('wetzels: warna resmi wetzels.com (biru #004089, kuning #fed900, biru muda #b7e5f0), skema gelap', () => {
    const t = BRANDS.wetzels.theme;
    expect(t.scheme).toBe('dark');
    expect([t.chrome, t.surfaceAlt, t.primaryContrast]).toEqual(['#004089', '#004089', '#004089']);
    expect([t.primary, t.accent]).toEqual(['#fed900', '#fed900']);
    expect(t.textMuted).toBe('#b7e5f0');
    expect(COMPANIES[BRANDS.wetzels.companies[0]!].tableService).toBe(false);
  });
});
