import sharp from 'sharp';
import { MetricsService } from '../common/metrics/metrics.service';
import { FakeDb, FakeKvStore, catchApiError, testConfig } from '../common/testing/fakes';
import { MockOdooOnline } from '../odoo-rpc/odoo-online.mock';
import { OdooRpcError } from '../odoo-rpc/odoo-online.types';
import { OnlineMetrics } from '../odoo-rpc/online-metrics';
import { CatalogImageRejected, CatalogStorage, catalogImageKey, normalizeCatalogImage } from './catalog-storage';
import { CatalogSyncService } from './catalog-sync.service';
import { Logger } from '@nestjs/common';
import { CatalogService, decodeCursor, encodeCursor } from './catalog.service';
import { brandParam } from './catalog.controller';
import { parseCatalogPage } from '../odoo-rpc/odoo-online.parse';

const CHANNEL_DATA = {
  code: 'GL',
  company_code: 'GL',
  name: 'EFN Retail',
  currency: 'IDR',
  active: true,
  fulfilment: { pickup: true, delivery: false },
  outlets: [
    { outlet_code: 'TOKO-01', public_name: 'Toko 01', pickup_enabled: true, prep_minutes: 20, active: true },
    { outlet_code: 'TOKO-09', public_name: 'Tutup', pickup_enabled: true, prep_minutes: 20, active: false },
  ],
};

const SHA = 'a'.repeat(40);

function memStorage() {
  const objects = new Map<string, Buffer>();
  const storage: CatalogStorage = {
    get: async (k) => objects.get(k) ?? null,
    put: async (k, d) => void objects.set(k, d),
    remove: async (k) => void objects.delete(k),
  };
  return { objects, storage };
}

function itemRow(id: number, extra: Record<string, unknown> = {}) {
  return {
    product_id: id,
    data: { name: `P${id}`, description: 'desc', uom: 'Unit', max_qty: 9, prices: [{ outlet_code: null, price_incl: 11_100, price_excl: 10_000, tax_labels: ['PPN 11%'] }] },
    image_sha: SHA,
    image_ready_sha: SHA,
    category_id: 1,
    sequence: id,
    ...extra,
  };
}

function setup(
  handler: (t: string, p: readonly unknown[]) => { rows: Record<string, unknown>[]; rowCount: number } | undefined = () => undefined,
  channelData: unknown = CHANNEL_DATA,
) {
  const config = testConfig({ ONLINE_ENABLED: 'true', ODOO_ONLINE_MOCK: 'true' });
  const db = new FakeDb((t, p) => {
    if (t.includes('FROM platform.online_catalog_sync WHERE channel_code = ANY')) {
      return { rows: [{ channel_code: 'GL', channel_data: channelData, last_ok_at: new Date() }], rowCount: 1 };
    }
    return handler(t, p);
  });
  const kv = new FakeKvStore();
  const odoo = new MockOdooOnline();
  const metrics = new OnlineMetrics(new MetricsService());
  const { objects, storage } = memStorage();
  const svc = new CatalogService(config, db, kv, odoo, storage, metrics);
  return { config, db, kv, odoo, metrics, objects, storage, svc };
}

describe('CatalogService (§10.3.2)', () => {
  it('L-11: kanal dari blok channel Odoo (outlet aktif saja); blok tidak ada / mata uang ≠ IDR / kode beda → disembunyikan + log', async () => {
    expect(await setup().svc.channels()).toEqual([
      {
        channel_code: 'GL',
        company_code: 'GL',
        brand_code: null, // channel_data pra-G19 tanpa brand_code → null
        name: 'EFN Retail',
        fulfilment: { pickup: true, delivery: false },
        outlets: [{ outlet_code: 'TOKO-01', public_name: 'Toko 01', pickup_enabled: true, prep_minutes: 20 }],
        payment_modes: ['qris', 'counter'], // L-52/L-55: channel_data tanpa payment_modes → ONLINE_PAYMENT_METHODS (default test qris,counter)
        business_type: 'retail', // G22 B-2: salinan lama tanpa field
        loyalty: { earn: false, redeem: false }, // G22: flag kanal absen / kill switch mati
      },
    ]);
    const warn = jest.spyOn(Logger.prototype, 'warn').mockImplementation(() => undefined);
    try {
      for (const data of [null, { ...CHANNEL_DATA, currency: 'USD' }, { ...CHANNEL_DATA, code: 'PB' }, { ...CHANNEL_DATA, company_code: 'XYZ' }]) {
        expect(await setup(undefined, data).svc.channels()).toEqual([]);
      }
      expect(warn.mock.calls.filter((c) => String(c[0]).includes('L-11'))).toHaveLength(4);
    } finally {
      warn.mockRestore();
    }
  });

  it('produk: keyset cursor opak, pencarian di salinan BFF (wildcard di-escape), image_sha hanya bila gambar siap', async () => {
    const ctx = setup((t) => {
      if (t.includes('ORDER BY sequence, product_id LIMIT')) return { rows: [itemRow(1), itemRow(2, { image_ready_sha: null }), itemRow(3)], rowCount: 3 };
      if (t.includes('SELECT last_ok_at FROM platform.online_catalog_sync')) return { rows: [{ last_ok_at: new Date(0) }], rowCount: 1 };
      return undefined;
    });
    const res = await ctx.svc.products('GL', { category: null, q: '50%_off', cursor: null, limit: 2 });
    expect(res.items.map((i) => [i.product_id, i.image_sha, i.price_incl])).toEqual([
      [1, SHA, 11_100],
      [2, null, 11_100],
    ]);
    expect(decodeCursor(res.next_cursor!)).toEqual({ seq: 2, id: 2 });
    const q = ctx.db.find('ORDER BY sequence, product_id LIMIT')[0];
    expect(q.params).toContain('%50\\%\\_off%');
    expect(JSON.stringify(res)).not.toMatch(/odoo|http/i);
    expect(decodeCursor('!!')).toBeNull();
    expect(decodeCursor(encodeCursor(5, 7))).toEqual({ seq: 5, id: 7 });
    expect((await catchApiError(() => ctx.svc.products('PB', { category: null, q: null, cursor: null, limit: 5 }))).getStatus()).toBe(404);
  });

  it('gambar: hanya hash 40/64 hex yang dirujuk item aktif & siap; selain itu 404', async () => {
    const ctx = setup((t, p) => (t.includes('WHERE image_sha = $1 AND image_ready_sha = $1') && p[0] === SHA ? { rows: [{ channel_code: 'GL' }], rowCount: 1 } : undefined));
    ctx.objects.set(catalogImageKey('GL', SHA, 512), Buffer.from('jpeg'));
    expect((await ctx.svc.image(SHA, 512)).toString()).toBe('jpeg');
    expect((await catchApiError(() => ctx.svc.image('b'.repeat(40), 512))).getStatus()).toBe(404);
    expect((await catchApiError(() => ctx.svc.image('a'.repeat(32), 512))).getStatus()).toBe(404);
    expect((await catchApiError(() => ctx.svc.image(SHA, 1024))).getStatus()).toBe(404);
  });

  it('stok: cache Redis ocat:stk 30 s, qty tidak diteruskan; outlet asing → 404; Odoo gagal → 503', async () => {
    const ctx = setup((t, p) =>
      t.includes('product_id = ANY($2::int[])')
        ? { rows: (p[1] as number[]).filter((id) => [11, 12, 13, 15].includes(id)).map((id) => itemRow(id)), rowCount: 1 }
        : undefined,
    );
    const r1 = await ctx.svc.stock('GL', 'TOKO-01', [11, 12]);
    expect(r1.items).toEqual([
      { product_id: 11, status: 'in_stock' },
      { product_id: 12, status: 'low' },
    ]);
    expect(await ctx.kv.get('ocat:stk:GL:TOKO-01:12')).toBe('low');
    expect(await ctx.kv.ttl('ocat:stk:GL:TOKO-01:12')).toBe(30);
    await ctx.svc.stock('GL', 'TOKO-01', [11, 12]);
    expect(ctx.odoo.calls.filter((c) => c.method === 'stock')).toHaveLength(1);
    expect((await catchApiError(() => ctx.svc.stock('GL', 'TOKO-99', [11]))).getStatus()).toBe(404);
    ctx.odoo.failNext.set('stock', new OdooRpcError('transport'));
    expect((await catchApiError(() => ctx.svc.stock('GL', 'TOKO-01', [13]))).body).toMatchObject({ error: { code: 'online_unavailable' } });
  });

  it('SEC-96: id di luar katalog kanal → 404 TANPA memanggil Odoo; status kosong dari Odoo → cache negatif 30 s', async () => {
    const ctx = setup((t, p) =>
      t.includes('product_id = ANY($2::int[])')
        ? { rows: (p[1] as number[]).filter((id) => [11, 15].includes(id)).map((id) => itemRow(id)), rowCount: 1 }
        : undefined,
    );
    expect((await catchApiError(() => ctx.svc.stock('GL', 'TOKO-01', [11, 987654]))).getStatus()).toBe(404);
    expect(ctx.odoo.calls.filter((c) => c.method === 'stock')).toHaveLength(0);
    // 15 ada di salinan katalog BFF tetapi Odoo tidak mengembalikan statusnya → cache negatif
    expect((await ctx.svc.stock('GL', 'TOKO-01', [15])).items).toEqual([]);
    expect(await ctx.kv.get('ocat:stk:GL:TOKO-01:15')).toBe('none');
    for (let i = 0; i < 5; i += 1) await ctx.svc.stock('GL', 'TOKO-01', [15]);
    expect(ctx.odoo.calls.filter((c) => c.method === 'stock')).toHaveLength(1);
  });
});

describe('G19 §11.3: kanal per brand', () => {
  function brandSetup(rows: { code: string; brand: unknown; company?: string }[], over: Record<string, string> = {}) {
    const config = testConfig({ ONLINE_ENABLED: 'true', ODOO_ONLINE_MOCK: 'true', ONLINE_CHANNELS: rows.map((r) => r.code).join(','), ...over });
    const db = new FakeDb((t, p) => {
      if (!t.includes('FROM platform.online_catalog_sync WHERE channel_code = ANY')) return undefined;
      const allowed = p[0] as string[];
      const out = rows
        .filter((r) => allowed.includes(r.code))
        .map((r) => ({
          channel_code: r.code,
          channel_data: { ...CHANNEL_DATA, code: r.code, company_code: r.company ?? 'GL', ...(r.brand === undefined ? {} : { brand_code: r.brand }) },
          last_ok_at: new Date(),
        }));
      return { rows: out, rowCount: out.length };
    });
    const svc = new CatalogService(config, db, new FakeKvStore(), new MockOdooOnline(), memStorage().storage, new OnlineMetrics(new MetricsService()));
    return { config, db, svc };
  }
  // G20 §12.1 (L-48): kode kanal = kode PT; dua kanal Bacha (BCR retail, BCF F&B) berbagi brand BACHA
  const ROWS = [
    { code: 'GL', brand: 'GL', company: 'GL' },
    { code: 'PB', brand: 'PB', company: 'PB' },
    { code: 'BCR', brand: 'BACHA', company: 'BCR' },
    { code: 'BCF', brand: 'BACHA', company: 'BCF' },
  ];

  it('G20: kanal GL/PB/BCR/BCF; blok channel membawa brand_code; ?brand= menyaring, tanpa brand = semua', async () => {
    const { config, svc } = brandSetup(ROWS);
    expect(config.onlineChannels).toEqual(['GL', 'PB', 'BCR', 'BCF']);
    const all = await svc.channels();
    expect(all.map((c) => [c.channel_code, c.company_code, c.brand_code])).toEqual([
      ['GL', 'GL', 'GL'],
      ['PB', 'PB', 'PB'],
      ['BCR', 'BCR', 'BACHA'],
      ['BCF', 'BCF', 'BACHA'],
    ]);
    expect((await svc.channels(null)).length).toBe(4);
    // brand_code tidak unik: BACHA = butik (Bacha Coffee Retail) + kafe (Bacha Coffee F&B)
    expect((await svc.channels('BACHA')).map((c) => [c.channel_code, c.company_code])).toEqual([
      ['BCR', 'BCR'],
      ['BCF', 'BCF'],
    ]);
    expect((await svc.channels('GL')).map((c) => c.channel_code)).toEqual(['GL']);
    expect(await svc.channels('ZZ')).toEqual([]);
    // channel(code) untuk cart/quote TIDAK bergantung pada filter brand
    expect((await svc.channel('PB'))?.brand_code).toBe('PB');
  });

  it('G20 §12.1: kode PT lama RTL/FNB (salinan channel_data pra-G20) → kanal disembunyikan (fail-closed)', async () => {
    const warn = jest.spyOn(Logger.prototype, 'warn').mockImplementation(() => undefined);
    try {
      const { svc } = brandSetup([
        { code: 'GL', brand: 'GL', company: 'RTL' },
        { code: 'PB', brand: 'PB', company: 'FNB' },
        { code: 'BCR', brand: 'BACHA', company: 'BCR' },
      ]);
      expect((await svc.channels()).map((c) => c.channel_code)).toEqual(['BCR']);
    } finally {
      warn.mockRestore();
    }
  });

  it('brand_code tak valid / tipe salah di salinan channel_data → null (kanal tetap tampil); kanal di luar ONLINE_CHANNELS tidak tampil', async () => {
    const { svc } = brandSetup([
      { code: 'GL', brand: 'gl', company: 'GL' },
      { code: 'BCR', brand: 5, company: 'BCR' },
      { code: 'PB', brand: false, company: 'PB' },
    ]);
    expect((await svc.channels()).map((c) => c.brand_code)).toEqual([null, null, null]);
    const only = brandSetup(ROWS, { ONLINE_CHANNELS: 'GL' });
    expect((await only.svc.channels()).map((c) => c.channel_code)).toEqual(['GL']);
    expect(await only.svc.channels('BACHA')).toEqual([]);
  });

  it('L-52/L-55: payment_modes = (blok channel Odoo ?? ONLINE_PAYMENT_METHODS) ∩ ONLINE_PAYMENT_METHODS; mode tak dikenal dibuang', async () => {
    const config = testConfig({ ONLINE_ENABLED: 'true', ODOO_ONLINE_MOCK: 'true', ONLINE_CHANNELS: 'GL,PB,BCR,BCF' });
    const data: Record<string, unknown> = {
      GL: { ...CHANNEL_DATA, code: 'GL', company_code: 'GL', payment_modes: ['qris', 'counter'] },
      PB: { ...CHANNEL_DATA, code: 'PB', company_code: 'PB' }, // pra-G20: tanpa payment_modes
      BCR: { ...CHANNEL_DATA, code: 'BCR', company_code: 'BCR', payment_modes: ['counter', 'va'] }, // `va` belum didukung → dibuang
      BCF: { ...CHANNEL_DATA, code: 'BCF', company_code: 'BCF', payment_modes: ['counter'] },
    };
    const db = new FakeDb((t) =>
      t.includes('FROM platform.online_catalog_sync WHERE channel_code = ANY')
        ? { rows: Object.entries(data).map(([k, v]) => ({ channel_code: k, channel_data: v, last_ok_at: new Date() })), rowCount: 4 }
        : undefined,
    );
    const mk = (c = config) => new CatalogService(c, db, new FakeKvStore(), new MockOdooOnline(), memStorage().storage, new OnlineMetrics(new MetricsService()));
    expect((await mk().channels()).map((c) => [c.channel_code, c.payment_modes])).toEqual([
      ['GL', ['qris', 'counter']],
      ['PB', ['qris', 'counter']], // pra-G20 → daftar BFF (default test qris,counter)
      ['BCR', ['counter']],
      ['BCF', ['counter']],
    ]);
    // pilot tanpa vendor: hanya counter → qris Odoo tidak ditawarkan ke klien
    const pilot = testConfig({ ONLINE_ENABLED: 'true', ODOO_ONLINE_MOCK: 'true', ONLINE_CHANNELS: 'GL,PB,BCR,BCF', ONLINE_PAYMENT_METHODS: 'counter' });
    expect((await mk(pilot).channels()).map((c) => [c.channel_code, c.payment_modes])).toEqual([
      ['GL', ['counter']],
      ['PB', ['counter']],
      ['BCR', ['counter']],
      ['BCF', ['counter']],
    ]);
    const qrisOnly = testConfig({ ONLINE_ENABLED: 'true', ODOO_ONLINE_MOCK: 'true', ONLINE_CHANNELS: 'GL,PB,BCR,BCF', ONLINE_PAYMENT_METHODS: 'qris' });
    expect((await mk(qrisOnly).channel('BCF'))?.payment_modes).toEqual([]); // kanal counter-saja tidak bisa dipesan
  });

  it('G20 §12.3 / L-52: produk membawa weight_kg (nullable; salinan pra-G20 tanpa field → null)', async () => {
    const ctx = setup((t) => {
      if (t.includes('ORDER BY sequence, product_id LIMIT')) {
        return { rows: [itemRow(1, { data: { ...itemRow(1).data, weight_kg: 5 } }), itemRow(2), itemRow(3, { data: { ...itemRow(3).data, weight_kg: null } })], rowCount: 3 };
      }
      if (t.includes('SELECT last_ok_at FROM platform.online_catalog_sync')) return { rows: [{ last_ok_at: new Date(0) }], rowCount: 1 };
      return undefined;
    });
    const res = await ctx.svc.products('GL', { category: null, q: null, cursor: null, limit: 10 });
    expect(res.items.map((i) => [i.product_id, i.weight_kg])).toEqual([
      [1, 5],
      [2, null],
      [3, null],
    ]);
  });

  it('?brand= divalidasi: tidak ada → null; bukan ^[A-Z][A-Z0-9]{1,9}$ → 400 invalid_request', async () => {
    expect(brandParam(undefined)).toBeNull();
    expect(brandParam('GL')).toBe('GL');
    expect(brandParam('BACHA')).toBe('BACHA');
    expect(brandParam('A123456789')).toBe('A123456789');
    for (const bad of ['', 'gl', 'G', 'GL-1', 'GL_1', '1GL', 'A1234567890', '../GL', 'GL%00', ' GL', ['GL'], 5, null]) {
      const err = await catchApiError(() => brandParam(bad));
      expect(err.getStatus()).toBe(400);
      expect(err.body).toMatchObject({ error: { code: 'invalid_request' } });
    }
  });

  it('parse blok channel E-37a: brand_code string/false/absen; bentuk salah → invalid_response', () => {
    const ch = { code: 'GL', name: 'GL', company_code: 'GL', currency: 'IDR', outlets: [] };
    expect(parseCatalogPage({ items: [], server_time: 'now', channel: { ...ch, brand_code: 'GL' } }).channel?.brand_code).toBe('GL');
    expect(parseCatalogPage({ items: [], server_time: 'now', channel: { ...ch, brand_code: false } }).channel?.brand_code).toBeNull();
    expect(parseCatalogPage({ items: [], server_time: 'now', channel: ch }).channel?.brand_code).toBeNull();
    for (const b of ['gl', 'G', 'GL-1', 'ABCDEFGHIJK', 7]) {
      expect(() => parseCatalogPage({ items: [], server_time: 'now', channel: { ...ch, brand_code: b } })).toThrow(OdooRpcError);
    }
  });

  it('stub mock G20: kanal = kode PT, brand, payment_modes, weight_kg; kode lama RTL/FNB tidak dikenal', async () => {
    const odoo = new MockOdooOnline();
    expect((await odoo.catalog('GL', null)).channel).toMatchObject({ code: 'GL', company_code: 'GL', brand_code: 'GL', payment_modes: ['qris', 'counter'] });
    expect((await odoo.catalog('PB', null)).channel).toMatchObject({ code: 'PB', company_code: 'PB', brand_code: 'PB', payment_modes: ['qris'] });
    expect((await odoo.catalog('BCR', null)).channel).toMatchObject({ code: 'BCR', company_code: 'BCR', brand_code: 'BACHA' });
    expect((await odoo.catalog('BCF', null)).channel).toMatchObject({ code: 'BCF', company_code: 'BCF', brand_code: 'BACHA', payment_modes: ['counter'] });
    expect((await odoo.catalog('GL', null)).items.map((i) => [i.product_id, i.weight_kg])).toEqual([
      [11, 5],
      [12, 1.9],
      [13, 0.5],
      [14, null],
    ]);
    for (const old of ['RTL', 'FNB', 'RTL-GL', 'FNB-PB']) {
      await expect(odoo.catalog(old, null)).rejects.toMatchObject({ kind: 'business', code: 'channel_inactive' });
    }
  });
});

describe('CatalogSyncService (§10.8.2)', () => {
  it('pull delta: upsert item & kategori, simpan since=server_time + blok channel, gambar di-re-encode ke bucket catalog', async () => {
    const pending: Record<string, unknown>[] = [];
    const ctx = setup((t) => {
      if (t.includes('pg_try_advisory_xact_lock')) return { rows: [{ ok: true }], rowCount: 1 };
      if (t.includes('SELECT since, cursor FROM platform.online_catalog_sync')) return { rows: [{ since: null, cursor: null }], rowCount: 1 };
      if (t.includes('image_ready_sha IS DISTINCT FROM image_sha')) return { rows: pending.splice(0), rowCount: 1 };
      return undefined;
    });
    const sync = new CatalogSyncService(ctx.config, ctx.db, ctx.odoo, ctx.storage, ctx.svc, ctx.metrics);
    pending.push({ product_id: 11, tmpl_id: 101, image_sha: SHA, image_ready_sha: null });
    const [res] = await sync.runOnce();
    expect(res).toMatchObject({ channel: 'GL', ok: true, upserted: 4, images: 1 });
    expect(ctx.db.find('INSERT INTO platform.online_catalog_item')).toHaveLength(4);
    expect(ctx.db.find('SET channel_data = $2::jsonb')[0].params[1]).toContain('"company_code":"GL"');
    const done = ctx.db.find('SET since = $2')[0];
    expect(done.params[1]).toMatch(/^\d{4}-/);
    const jpeg = ctx.objects.get(catalogImageKey('GL', SHA, 1024))!;
    expect(jpeg.subarray(0, 3)).toEqual(Buffer.from([0xff, 0xd8, 0xff]));
    expect(ctx.objects.has(catalogImageKey('GL', SHA, 512))).toBe(true);
  });

  it('Odoo gagal → last_error_code, tanpa melempar; katalog lama tetap', async () => {
    const ctx = setup((t) => (t.includes('pg_try_advisory_xact_lock') ? { rows: [{ ok: true }], rowCount: 1 } : undefined));
    ctx.odoo.failNext.set('catalog', new OdooRpcError('auth'));
    const sync = new CatalogSyncService(ctx.config, ctx.db, ctx.odoo, ctx.storage, ctx.svc, ctx.metrics);
    const [res] = await sync.runOnce();
    expect(res.ok).toBe(false);
    expect(ctx.db.find('last_error_code')[0].params).toEqual(['GL', 'odoo_auth']);
  });

  it('re-encode: magic bytes selain JPEG/PNG/WebP ditolak; hasil JPEG ≤ ukuran tanpa metadata', async () => {
    await expect(normalizeCatalogImage(Buffer.from('<svg/>'), 512)).rejects.toBeInstanceOf(CatalogImageRejected);
    const png = await sharp({ create: { width: 2000, height: 1000, channels: 3, background: { r: 1, g: 2, b: 3 } } }).png().toBuffer();
    const out = await normalizeCatalogImage(png, 512);
    const meta = await sharp(out).metadata();
    expect(meta).toMatchObject({ format: 'jpeg', width: 512, height: 256 });
    expect(meta.exif).toBeUndefined();
  });
});
