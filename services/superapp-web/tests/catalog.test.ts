import { describe, expect, it } from 'vitest';
import {
  buildProductQuery,
  channelsForBrand,
  companyDisplayName,
  imageUrl,
  isChannelCode,
  isImageSha,
  isOutletCode,
  parseCategories,
  parseChannels,
  parseIdList,
  parseImageSize,
  parseLimit,
  parsePositiveInt,
  parseProductDetail,
  parseProductPage,
  parseStock,
  sanitizeQuery,
} from '@/lib/catalog';

const SHA256 = 'a'.repeat(64);
const SHA1 = 'b'.repeat(40);

describe('validator kode §10.2', () => {
  it('channel_code', () => {
    for (const c of ['GL', 'PB', 'BCR', 'BCF']) expect(isChannelCode(c)).toBe(true);
    expect(isChannelCode('gl')).toBe(false);
    expect(isChannelCode('R')).toBe(false);
    expect(isChannelCode('GL&x=1')).toBe(false);
    expect(isChannelCode('A'.repeat(17))).toBe(false);
  });
  it('outlet_code', () => {
    expect(isOutletCode('TOKO-01')).toBe(true);
    expect(isOutletCode('-TOKO')).toBe(false);
    expect(isOutletCode('toko-01')).toBe(false);
  });
  it('image_sha: hex 40/64 huruf kecil saja', () => {
    expect(isImageSha(SHA256)).toBe(true);
    expect(isImageSha(SHA1)).toBe(true);
    expect(isImageSha('A'.repeat(64))).toBe(false);
    expect(isImageSha('a'.repeat(50))).toBe(false);
    expect(isImageSha('../etc/passwd')).toBe(false);
  });
  it('parsePositiveInt menolak nol, negatif, desimal, notasi aneh', () => {
    expect(parsePositiveInt('11')).toBe(11);
    expect(parsePositiveInt(5)).toBe(5);
    for (const v of ['0', '-1', '1.5', '1e3', '01', ' 1', '', null, 1.5, 99999999999999999]) {
      expect(parsePositiveInt(v)).toBeNull();
    }
  });
});

describe('query katalog', () => {
  it('sanitizeQuery: trim, buang kontrol, maks 64 karakter', () => {
    expect(sanitizeQuery('  susu\u0000  segar ')).toBe('susu segar');
    expect(sanitizeQuery('   ')).toBeNull();
    expect(sanitizeQuery('x'.repeat(100))).toHaveLength(64);
  });
  it('limit dibatasi 50, ukuran gambar 512|1024', () => {
    expect(parseLimit('500')).toBe(50);
    expect(parseLimit('abc')).toBe(24);
    expect(parseImageSize('1024')).toBe(1024);
    expect(parseImageSize('9999')).toBe(512);
  });
  it('parseIdList: unik, ≤ 50, satu id rusak -> null', () => {
    expect(parseIdList('1,2,2,3')).toEqual([1, 2, 3]);
    expect(parseIdList('1,x')).toBeNull();
    expect(parseIdList(Array.from({ length: 51 }, (_, i) => i + 1).join(','))).toBeNull();
    expect(parseIdList('')).toBeNull();
  });
  it('buildProductQuery membangun ulang parameter tervalidasi saja', () => {
    const qs = buildProductQuery({ channel: 'GL', category: '7', q: ' kopi ', cursor: 'abc_123', limit: '100' });
    expect(new URLSearchParams(qs!).toString()).toBe('channel=GL&category=7&q=kopi&cursor=abc_123&limit=50');
    const bad = buildProductQuery({ channel: 'GL', category: '-1', cursor: 'a&b=c', q: '' });
    expect(bad).toBe('channel=GL&limit=24');
    expect(buildProductQuery({ channel: 'gl' })).toBeNull();
  });
  it('imageUrl hanya origin sendiri', () => {
    expect(imageUrl(SHA256)).toBe(`/api/catalog/images/${SHA256}?size=512`);
    expect(imageUrl('https://odoo/x')).toBeNull();
    expect(imageUrl(null)).toBeNull();
  });
});

describe('normalisasi respons', () => {
  const raw = {
    product_id: 11,
    name: 'Susu UHT 1L',
    uom: 'pcs',
    category_id: 3,
    image_sha: SHA256,
    price_incl: 19900,
    price_excl: 17927.93,
    tax_labels: ['PPN 11%'],
    max_qty: 20,
    odoo_url: 'https://odoo.internal/web/image/1',
    description: '<b>teks</b>',
  };
  it('produk: allowlist field, deskripsi teks polos', () => {
    const d = parseProductDetail(raw)!;
    expect(d).not.toHaveProperty('odoo_url');
    expect(d.description).toBe('<b>teks</b>'); // dirender sebagai teks oleh React
    expect(d.tax_labels).toEqual(['PPN 11%']);
    expect(parseProductDetail({ ...raw, price_incl: -1 })).toBeNull();
    expect(parseProductDetail({ ...raw, description: 'x'.repeat(5000) })!.description).toHaveLength(2000);
  });
  it('weight_kg (§12.3): nullable, hanya angka tak negatif', () => {
    expect(parseProductDetail(raw)!.weight_kg).toBeNull();
    expect(parseProductDetail({ ...raw, weight_kg: 0.5 })!.weight_kg).toBe(0.5);
    expect(parseProductDetail({ ...raw, weight_kg: -1 })!.weight_kg).toBeNull();
    expect(parseProductDetail({ ...raw, weight_kg: '2' })!.weight_kg).toBeNull();
  });
  it('halaman produk membuang item rusak dan cursor tidak aman', () => {
    const p = parseProductPage({ items: [raw, { product_id: 'x' }], next_cursor: 'a b', catalog_synced_at: '2026-10-09T00:00:00Z' })!;
    expect(p.items).toHaveLength(1);
    expect(p.next_cursor).toBeNull();
    expect(parseProductPage({})).toBeNull();
  });
  it('kanal, kategori, stok', () => {
    const ch = parseChannels({
      items: [
        {
          channel_code: 'GL',
          company_code: 'GL',
          name: 'Supermarket',
          payment_modes: ['counter', 'bogus'],
          fulfilment: { pickup: true, delivery: false },
          outlets: [{ outlet_code: 'TOKO-01', public_name: 'Toko Pusat', pickup_enabled: true, prep_minutes: 30 }, { outlet_code: 'bad code' }],
        },
        { channel_code: 'bad' },
      ],
    })!;
    expect(ch).toHaveLength(1);
    expect(ch[0]!.outlets).toHaveLength(1);
    expect(ch[0]!.payment_modes).toEqual(['counter']);
    expect(channelsForBrand(ch, ['PB'])).toHaveLength(0);
    expect(channelsForBrand(ch, ['GL', 'PB'])).toHaveLength(1);

    const cats = parseCategories({ items: [{ category_id: 2, name: 'B', sequence: 2 }, { category_id: 1, name: 'A', sequence: 1 }, { name: 'x' }] })!;
    expect(cats.map((c) => c.category_id)).toEqual([1, 2]);

    expect(parseStock({ items: [{ product_id: 1, status: 'low', qty: 3 }, { product_id: 2, status: 'banyak' }] })).toEqual([
      { product_id: 1, status: 'low' },
    ]);
  });
  it('nama PT', () => {
    expect(companyDisplayName('GL')).toBe('PT Grand Lucky');
    expect(companyDisplayName('BCR')).toBe('Bacha Coffee Retail');
    expect(companyDisplayName('RTL')).toBe('RTL'); // kode lama sudah dihapus
    expect(companyDisplayName('XYZ')).toBe('XYZ');
  });
});
