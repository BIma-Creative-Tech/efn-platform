import { MetricsService } from '../common/metrics/metrics.service';
import { FakeDb, FakeKvStore, randomSecret, testConfig } from '../common/testing/fakes';
import { CatalogStorage } from '../catalog/catalog-storage';
import { CatalogService } from '../catalog/catalog.service';
import { ConfigError, onlineApiKeyEnvName, onlineApiKeySecretName } from '../config/app-config';
import { INVOICE_COMPANIES } from '../orders/invoice-request';
import { MockOdooOnline } from './odoo-online.mock';
import { parseCatalogPage } from './odoo-online.parse';
import { OnlineMetrics } from './online-metrics';

/**
 * Kanal belanja online supermarket `SMD` (keputusan pemilik proyek 23 Nov 2026; ADR-045 Proposed, K25-07).
 * SMD menyala HANYA lewat `ONLINE_CHANNELS`; tidak pernah lewat QR meja (`FNB_QR_COMPANIES`) / reservasi
 * (`BOOKING_COMPANIES`) karena supermarket tanpa dine-in.
 */
/** = nama `pos.category` efn_setup_supermarket_online (kontrak situs; test Odoo `test_pos_categories_exact_names`). */
const SMD_CATEGORIES = [
  'Sayur',
  'Buah',
  'Daging',
  'Ikan & Seafood',
  'Bakery',
  'Dairy & Chilled',
  'Frozen',
  'Dry Grocery',
  'Minuman',
  'Snack',
  'Personal Care',
  'Household',
  'Impor Premium',
];

const NOT_FOUND = { name: 'OdooRpcError', kind: 'business', code: 'not_found' };

const noStorage: CatalogStorage = { get: async () => null, put: async () => undefined, remove: async () => undefined };

async function catalogWithMockChannel(over: Record<string, string>) {
  const odoo = new MockOdooOnline();
  const page = parseCatalogPage(JSON.parse(JSON.stringify(await odoo.catalog('SMD', null))));
  const config = testConfig({ ONLINE_ENABLED: 'true', ODOO_ONLINE_MOCK: 'true', ...over });
  const db = new FakeDb((t, p) => {
    if (!t.includes('FROM platform.online_catalog_sync WHERE channel_code = ANY')) return undefined;
    // salinan katalog SMD sudah ada (mis. sinkron lama) — tampil atau tidak ditentukan ONLINE_CHANNELS
    const rows = (p[0] as string[]).includes('SMD') ? [{ channel_code: 'SMD', channel_data: page.channel, last_ok_at: new Date() }] : [];
    return { rows, rowCount: rows.length };
  });
  return new CatalogService(config, db, new FakeKvStore(), odoo, noStorage, new OnlineMetrics(new MetricsService()));
}

describe('Kanal online SMD (23 Nov 2026)', () => {
  it('default ONLINE_CHANNELS tetap GL: SMD tidak aktif dan tidak disinkron; dikenali bila dinyalakan eksplisit', () => {
    expect(testConfig().onlineChannels).toEqual(['GL']);
    expect(testConfig().catalogSyncChannels).not.toContain('SMD');
    expect(testConfig({ ONLINE_CHANNELS: 'GL,SMD' }).onlineChannels).toEqual(['GL', 'SMD']);
    expect(testConfig({ ONLINE_CHANNELS: 'SMD' }).catalogSyncChannels).toEqual(['SMD']);
    // QR meja / reservasi yang menyala TIDAK menyeret SMD ke salinan katalog
    expect(testConfig({ ONLINE_CHANNELS: 'GL', FNB_QR_ENABLED: 'true', BOOKING_ENABLED: 'true' }).catalogSyncChannels).not.toContain('SMD');
  });

  it('SMD ditolak di FNB_QR_COMPANIES dan BOOKING_COMPANIES (retail, tanpa dine-in)', () => {
    for (const v of ['SMD', 'PB,SMD', 'BCF SMD']) {
      expect(() => testConfig({ FNB_QR_COMPANIES: v }).fnbQrCompanies).toThrow(ConfigError);
      expect(() => testConfig({ BOOKING_COMPANIES: v }).bookingCompanies).toThrow(ConfigError);
    }
    // validasi start ikut gagal walau fitur mati (tidak diam-diam diabaikan)
    expect(() => testConfig({ FNB_QR_COMPANIES: 'SMD' }).assertStartupSecrets()).toThrow(/FNB_QR_COMPANIES/);
    expect(() => testConfig({ BOOKING_COMPANIES: 'SMD' }).assertStartupSecrets()).toThrow(/BOOKING_COMPANIES/);
  });

  it('API key kanal SMD: ODOO_API_KEY_ONLINE_SMD_FILE, fallback ${SECRETS_DIR}/odoo_api_key_online_smd; placeholder & literal ditolak', () => {
    expect(onlineApiKeyEnvName('SMD')).toBe('ODOO_API_KEY_ONLINE_SMD');
    expect(onlineApiKeySecretName('SMD')).toBe('odoo_api_key_online_smd');
    const key = randomSecret();
    const reader = (expected: string) => (p: string): Buffer => {
      if (p === expected) return Buffer.from(`${key}\n`);
      throw new Error('ENOENT');
    };
    expect(testConfig({ ODOO_API_KEY_ONLINE_SMD_FILE: '/s/smd' }, reader('/s/smd')).odooApiKeyOnline('SMD')).toBe(key);
    expect(testConfig({ SECRETS_DIR: '/run/secrets' }, reader('/run/secrets/odoo_api_key_online_smd')).odooApiKeyOnline('SMD')).toBe(key);
    expect(() => testConfig({ ODOO_API_KEY_ONLINE_SMD_FILE: '/s/smd' }, () => Buffer.from('disabled')).odooApiKeyOnline('SMD')).toThrow(/placeholder 'disabled'/);
    // kanal aktif tanpa secret → gagal start menyebut NAMA env saja
    const missing = testConfig({ ONLINE_ENABLED: 'true', ONLINE_CHANNELS: 'GL,SMD', ODOO_ONLINE_EVENT_HMAC_FILE: '/s/hmac', ODOO_API_KEY_ONLINE_GL_FILE: '/s/gl' }, (p) => {
      if (p === '/s/hmac' || p === '/s/gl') return Buffer.from(randomSecret());
      throw new Error('ENOENT');
    });
    expect(() => missing.assertStartupSecrets()).toThrow(/ODOO_API_KEY_ONLINE_SMD/);
    const lit = testConfig({ ONLINE_ENABLED: 'true', ONLINE_CHANNELS: 'SMD', ODOO_API_KEY_ONLINE_SMD: randomSecret() });
    expect(() => lit.assertStartupSecrets()).toThrow(/ODOO_API_KEY_ONLINE_SMD sebagai env literal ditolak/);
  });

  it('CatalogService: SMD tampil hanya bila di ONLINE_CHANNELS; retail, brand SMD, outlet ber-slot, tanpa counter', async () => {
    expect(await (await catalogWithMockChannel({})).channels()).toEqual([]);
    expect(await (await catalogWithMockChannel({ ONLINE_CHANNELS: 'GL' })).channels('SMD')).toEqual([]);
    const svc = await catalogWithMockChannel({ ONLINE_CHANNELS: 'GL,SMD', ONLINE_SLOTS_ENABLED: 'true' });
    const [smd] = await svc.channels('SMD');
    expect(smd).toMatchObject({ channel_code: 'SMD', company_code: 'SMD', brand_code: 'SMD', business_type: 'retail' });
    // Odoo mengirim qris,va,ewallet (K25-07, hanya gateway); BFF belum mendukung va/ewallet → hanya qris ditawarkan
    expect(smd.payment_modes).toEqual(['qris']);
    expect(smd.outlets.map((o) => [o.outlet_code, o.prep_minutes, o.slot_enabled, o.slot_minutes])).toEqual(
      ['SMD01', 'SMD02', 'SMD03', 'SMD04', 'SMD05'].map((c) => [c, 30, true, 60]),
    );
  });

  it('mock Odoo SMD: payment_modes mentah = data Odoo (qris,va,ewallet); hasil filter parser = qris', async () => {
    const odoo = new MockOdooOnline();
    expect(odoo.paymentModes.get('SMD')).toEqual(['qris', 'va', 'ewallet']);
    expect((await odoo.catalog('SMD', null)).channel?.payment_modes).toEqual(['qris']);
  });

  it('mock Odoo SMD: katalog sendiri dengan 13 kategori supermarket (nama persis Odoo), produk kanal lain tidak tercampur', async () => {
    const odoo = new MockOdooOnline();
    const page = parseCatalogPage(JSON.parse(JSON.stringify(await odoo.catalog('SMD', null))));
    expect(page.channel).toMatchObject({ code: 'SMD', company_code: 'SMD', brand_code: 'SMD', business_type: 'retail' });
    const cats = new Map<number, { name: string; sequence: number }>();
    for (const i of page.items) if (i.categ) cats.set(i.categ.id, { name: i.categ.name, sequence: i.categ.sequence });
    expect([...cats.values()].sort((a, b) => a.sequence - b.sequence).map((c) => c.name)).toEqual(SMD_CATEGORIES);
    expect(page.items.every((i) => i.product_id >= 201 && i.product_id <= 226)).toBe(true);
    // kanal lama tetap katalog lama (tanpa produk SMD)
    const gl = await odoo.catalog('GL', null);
    expect(gl.items.map((i) => i.product_id)).toEqual([11, 12, 13, 14]);
  });

  it('mock Odoo SMD: stok/quote per outlet SMD; outlet/produk kanal lain → not_found; produk habis → out_of_stock', async () => {
    const odoo = new MockOdooOnline();
    expect(await odoo.stock('SMD', 'SMD02', [203, 208, 11])).toEqual([
      { product_id: 203, status: 'low' },
      { product_id: 208, status: 'out' },
    ]);
    const q = await odoo.quote('SMD', 'SMD01', 'pickup', [{ product_id: 201, qty: 2 }]);
    expect(q).toMatchObject({ company_code: 'SMD', amount_payable: 13_000, issues: [] });
    expect((await odoo.quote('SMD', 'SMD01', 'pickup', [{ product_id: 208, qty: 1 }])).issues).toEqual([{ product_id: 208, code: 'out_of_stock' }]);
    await expect(odoo.quote('SMD', 'TOKO-01', 'pickup', [{ product_id: 201, qty: 1 }])).rejects.toMatchObject(NOT_FOUND);
    await expect(odoo.quote('SMD', 'SMD01', 'pickup', [{ product_id: 11, qty: 1 }])).rejects.toMatchObject(NOT_FOUND);
    await expect(odoo.quote('GL', 'SMD01', 'pickup', [{ product_id: 11, qty: 1 }])).rejects.toMatchObject(NOT_FOUND);
    await expect(odoo.stock('GL', 'TOKO-01', [201])).resolves.toEqual([]);
  });

  it('mock Odoo SMD: slot pre-order outlet SMD (09:00–17:00 WIB)', async () => {
    const odoo = new MockOdooOnline();
    odoo.now = () => Date.parse('2026-11-23T00:00:00Z'); // 07:00 WIB
    const s = await odoo.slots('SMD', 'SMD03', '2026-11-24');
    expect(s.slots).toHaveLength(8);
    expect(s.slots[0]).toMatchObject({ start_at: '2026-11-24T02:00:00Z', remaining: 3 });
    await expect(odoo.slots('SMD', 'TOKO-01', '2026-11-24')).rejects.toMatchObject(NOT_FOUND);
  });

  it('faktur atas nama (§13.5): SMD = PT PPN → diizinkan BFF; PT F&B tetap tidak', () => {
    expect(INVOICE_COMPANIES).toContain('SMD');
    for (const c of ['PB', 'BCF', 'WTZ', 'IMD', 'CUP']) expect(INVOICE_COMPANIES).not.toContain(c);
  });
});
