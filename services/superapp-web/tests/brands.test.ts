import { describe, expect, it } from 'vitest';
import { getBrandById, isBrandId, normalizeHost, resolveBrandIdFromHost } from '../lib/brands';

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
    ['mart.efn.local', 'efn-mart'],
    ['Mart.Efn.Local:443', 'efn-mart'],
    ['kitchen.efn.local', 'efn-kitchen'],
    ['mart.contoh-produksi.co.id', 'efn-mart'], // prefix subdomain di domain apa pun
    ['kitchen.contoh-produksi.co.id', 'efn-kitchen'],
    ['app.contoh-produksi.co.id', 'efn'],
    ['mart', 'efn'], // tanpa titik: bukan subdomain
    ['evil.mart.contoh.co.id', 'efn'], // hanya label pertama yang dicocokkan
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
    expect(isBrandId('efn-mart')).toBe(true);
    expect(getBrandById('efn-kitchen').companies).toEqual(['FNB']);
  });
});
