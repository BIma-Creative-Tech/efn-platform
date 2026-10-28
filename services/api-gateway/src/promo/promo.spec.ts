import { createHash } from 'node:crypto';
import sharp from 'sharp';
import { FakeDb, testCipher, testConfig } from '../common/testing/fakes';
import { MockOdooPromo, OdooPromoError, parsePromoFeed, parsePromoImage, parsePromoLink, parsePromoPush } from './odoo-promo';
import { PromoService, isExpoPushToken, normalizePromoImage, promoImageKey } from './promo.service';
import { ConsolePushSender } from './push-sender';
import { CatalogStorage } from '../catalog/catalog-storage';

const ISO = (d: number) => new Date(Date.UTC(2026, 9, 27) + d * 86_400_000).toISOString().replace(/\.\d{3}Z$/, 'Z');
const item = (id: number, extra: Record<string, unknown> = {}) => ({
  id,
  title: `Promo ${id}`,
  subtitle: 'Teks',
  cta_label: 'Lihat',
  link: { type: 'brand', brand: 'PB' },
  web: true,
  app: true,
  date_start: ISO(-1),
  date_end: ISO(3),
  sequence: 10,
  image_sha: 'a'.repeat(64),
  ...extra,
});

class MemStorage implements CatalogStorage {
  readonly objects = new Map<string, Buffer>();
  async get(key: string): Promise<Buffer | null> {
    return this.objects.get(key) ?? null;
  }
  async put(key: string, data: Buffer): Promise<void> {
    this.objects.set(key, data);
  }
  async remove(key: string): Promise<void> {
    this.objects.delete(key);
  }
}

describe('promo: parser respons Odoo (allowlist, docs/04 §18.1)', () => {
  it('feed: field di luar whitelist dibuang, item rusak dilewati, frequency popup default once', () => {
    const f = parsePromoFeed({
      banners: [item(1, { secret: 'x' }), item(2, { date_end: ISO(-2) }), item(3, { title: '' }), { id: 'x' }],
      popups: [item(9, { frequency: 'daily' }), item(10, { frequency: 'bogus' })],
    });
    expect(f.banners.map((b) => b.id)).toEqual([1]);
    expect(f.banners[0]).not.toHaveProperty('secret');
    expect(f.popups.map((p) => p.frequency)).toEqual(['daily', 'once']);
    expect(() => parsePromoFeed({ banners: [] })).toThrow(OdooPromoError);
  });
  it('tautan: hanya brand/product/url https yang valid', () => {
    expect(parsePromoLink({ type: 'brand', brand: 'PB' })).toEqual({ type: 'brand', brand: 'PB' });
    expect(parsePromoLink({ type: 'product', channel: 'GL', product_id: 5 })).toEqual({ type: 'product', channel: 'GL', product_id: 5 });
    expect(parsePromoLink({ type: 'url', url: 'https://parisbaguette.co.id/promo' })).toEqual({ type: 'url', url: 'https://parisbaguette.co.id/promo' });
    for (const bad of [{ type: 'url', url: 'http://x.test' }, { type: 'url', url: 'javascript:alert(1)' }, { type: 'brand', brand: 'pb' }, { type: 'product', channel: 'GL', product_id: -1 }, null]) {
      expect(parsePromoLink(bad)).toBeNull();
    }
  });
  it('gambar: sha klaim harus cocok dengan byte', () => {
    const data = Buffer.from('img');
    const sha = createHash('sha256').update(data).digest('hex');
    expect(parsePromoImage({ sha, data_b64: data.toString('base64') }).data.equals(data)).toBe(true);
    expect(() => parsePromoImage({ sha: 'b'.repeat(64), data_b64: data.toString('base64') })).toThrow(OdooPromoError);
  });
  it('push: judul/isi wajib dan dipotong', () => {
    expect(parsePromoPush({ items: [{ id: 1, push_title: 'P', push_body: 'B', link: null }, { id: 2, push_title: '', push_body: 'B' }] })).toEqual([{ id: 1, title: 'P', body: 'B', link: null }]);
  });
});

describe('PromoService', () => {
  function setup(env: Record<string, string> = {}, handler?: (t: string, p: readonly unknown[]) => unknown) {
    const config = testConfig({ PROMO_ENABLED: 'true', PROMO_COMPANIES: 'PB', ...env });
    const db = new FakeDb(handler as never);
    const odoo = new MockOdooPromo();
    const storage = new MemStorage();
    const push = new ConsolePushSender();
    const svc = new PromoService(config, db, odoo, storage, testCipher(config), push);
    return { config, db, odoo, storage, push, svc };
  }

  it('PROMO_ENABLED=false → feed kosong tanpa query DB (klien = hero lama)', async () => {
    const { svc, db } = setup({ PROMO_ENABLED: 'false' });
    const r = await svc.list('app');
    expect(r.banners).toEqual([]);
    expect(r.popups).toEqual([]);
    expect(db.queries).toHaveLength(0);
  });

  it('sinkron: upsert item feed, hapus yang hilang dari feed, gambar di-re-encode ke promo/<sha>/<w>.jpg', async () => {
    const png = await sharp({ create: { width: 2400, height: 1350, channels: 3, background: '#cc3344' } }).png().toBuffer();
    const sha = createHash('sha256').update(png).digest('hex');
    const pending = [{ kind: 'banner', odoo_id: 1, image_sha: sha }];
    const { svc, db, odoo, storage } = setup({}, (t) => (t.includes('image_ready = false') ? { rows: pending, rowCount: 1 } : undefined));
    odoo.feeds.set('PB', parsePromoFeed({ banners: [item(1, { image_sha: sha })], popups: [] }));
    odoo.images.set('banner:1', png);
    const res = await svc.syncCompany('PB');
    expect(res).toEqual({ items: 1, images: 1 });
    expect(db.find('DELETE FROM platform.promo_item')[0]!.params).toEqual(['PB', ['banner:1']]);
    const jpeg = storage.objects.get(promoImageKey(sha, 1600))!;
    const meta = await sharp(jpeg).metadata();
    expect(meta.format).toBe('jpeg');
    expect(meta.width).toBe(1600);
    expect(meta.exif).toBeUndefined();
    expect(db.find('SET image_ready = true')).toHaveLength(1);
  });

  it('sinkron gagal → catat galat, tidak melempar', async () => {
    const { svc, db, odoo } = setup();
    odoo.feed = async () => {
      throw new OdooPromoError('transport');
    };
    expect(await svc.syncCompany('PB')).toBeNull();
    expect(db.find('last_error_code = $2')[0]!.params).toEqual(['PB', 'odoo_transport']);
  });

  it('list: id publik `<PT>-<id>`, permukaan dipilih SQL, popup membawa frequency', async () => {
    const { svc, db } = setup({}, (t) =>
      t.includes('FROM platform.promo_item') && t.includes('ORDER BY sequence')
        ? {
            rows: [
              { kind: 'banner', company_code: 'PB', odoo_id: 1, payload: { title: 'A', subtitle: '', cta_label: '', link: null }, image_sha: null },
              { kind: 'popup', company_code: 'PB', odoo_id: 2, payload: { title: 'B', subtitle: '', cta_label: '', link: null, frequency: 'daily' }, image_sha: null },
            ],
            rowCount: 2,
          }
        : undefined,
    );
    const r = await svc.list('web');
    expect(r.banners.map((b) => b.id)).toEqual(['PB-1']);
    expect(r.popups[0]).toMatchObject({ id: 'PB-2', frequency: 'daily' });
    expect(db.find('ORDER BY sequence')[0]!.params).toEqual(['web']);
  });

  it('gambar hanya untuk sha item tayang', async () => {
    const { svc } = setup();
    await expect(svc.image('c'.repeat(64), 1600)).rejects.toMatchObject({ status: 404 });
    await expect(svc.image('../x', 1600)).rejects.toMatchObject({ status: 404 });
  });

  it('token push: format Expo saja, tersimpan terenkripsi (bukan plaintext) + hash', async () => {
    const { svc, db } = setup();
    const token = 'ExponentPushToken[abcdefghijklmnop]';
    expect(isExpoPushToken(token)).toBe(true);
    expect(isExpoPushToken('fcm:xyz')).toBe(false);
    await svc.registerToken('11111111-1111-4111-8111-111111111111', token, 'android');
    const q = db.find('INSERT INTO platform.push_token')[0]!;
    expect(JSON.stringify(q.params)).not.toContain('abcdefghijklmnop');
    expect(q.params[4]).toMatch(/^[0-9a-f]{64}$/);
    await expect(svc.registerToken('11111111-1111-4111-8111-111111111111', 'x', 'android')).rejects.toMatchObject({ status: 400 });
    await expect(svc.registerToken('11111111-1111-4111-8111-111111111111', token, 'windows')).rejects.toMatchObject({ status: 400 });
  });

  it('push hanya ke member ber-consent marketing (JOIN consent terbaru granted) dan hanya bila PROMO_PUSH_ENABLED', async () => {
    const off = setup();
    off.odoo.pushes.set('PB', [{ id: 7, title: 'Promo', body: 'Isi', link: null }]);
    expect(await off.svc.dispatchPush('PB')).toEqual({ claimed: 0, sent: 0 });

    const config = testConfig({ PROMO_ENABLED: 'true', PROMO_PUSH_ENABLED: 'true', PROMO_COMPANIES: 'PB' });
    const cipher = testCipher(config);
    const enc = cipher.encrypt('ExponentPushToken[abcdefghijklmnop]', 'efn:push_token.token_enc:v1');
    const db = new FakeDb((t) => (t.includes('FROM platform.push_token t') ? { rows: [{ token_enc: enc }], rowCount: 1 } : undefined));
    const odoo = new MockOdooPromo();
    const push = new ConsolePushSender();
    const svc = new PromoService(config, db, odoo, new MemStorage(), cipher, push);
    odoo.pushes.set('PB', [{ id: 7, title: 'Promo', body: 'Isi', link: { type: 'brand', brand: 'PB' } }]);
    expect(await svc.dispatchPush('PB')).toEqual({ claimed: 1, sent: 1 });
    const sql = db.find('FROM platform.push_token t')[0]!.text;
    expect(sql).toContain("c.purpose = 'marketing'");
    expect(sql).toContain('ON c.granted');
    expect(push.sent[0]).toMatchObject({ to: 'ExponentPushToken[abcdefghijklmnop]', title: 'Promo', data: { promo_id: 7 } });
  });

  it('normalizePromoImage menolak non-gambar', async () => {
    await expect(normalizePromoImage(Buffer.from('bukan gambar'), 1600)).rejects.toThrow();
  });
});
