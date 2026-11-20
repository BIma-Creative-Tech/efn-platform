import { randomUUID } from 'node:crypto';
import { CatalogService } from '../catalog/catalog.service';
import { RateLimiter } from '../common/rate-limit/rate-limiter';
import { FakeDb, FakeKvStore, catchApiError, testConfig } from '../common/testing/fakes';
import { parsePriceAlert, parseRestockAlert, parseWishlistItem } from './dto/wishlist.dto';
import { WishlistService } from './wishlist.service';
import { eraseWishlistMember, exportWishlistMember } from './wishlist-privacy';

const user = { memberId: randomUUID(), memberHash: 'a'.repeat(64) };
const ALL_ON = { WISHLIST_ENABLED: 'true', RESTOCK_ALERT_ENABLED: 'true', PRICE_ALERT_ENABLED: 'true' };

function setup(opts: { env?: Record<string, string>; count?: number; exists?: boolean; stock?: string | null; existingAlert?: boolean; deleted?: number } = {}) {
  const catalog = {
    productsByIds: jest.fn(async (_ch: string, ids: number[]) => new Map(ids.filter((id) => id === 10).map((id) => [id, { product_id: id, name: 'Susu', price_incl: 20000 }]))),
    stock: jest.fn(async () => ({ items: opts.stock === null ? [] : [{ product_id: 10, status: opts.stock ?? 'out' }], checked_at: '' })),
    product: jest.fn(async () => ({ product_id: 10, name: 'Susu', price_incl: 20000 })),
  } as unknown as CatalogService;
  const db = new FakeDb((t) => {
    if (t.includes('SELECT 1 FROM platform.member_wishlist')) return opts.exists ? { rows: [{}], rowCount: 1 } : { rows: [], rowCount: 0 };
    if (t.includes('count(*)')) return { rows: [{ n: opts.count ?? 0 }], rowCount: 1 };
    if (t.includes('SELECT id::text AS id FROM platform.restock_alert') || t.includes('SELECT id::text AS id FROM platform.price_alert')) {
      return opts.existingAlert ? { rows: [{ id: 'existing-id' }], rowCount: 1 } : { rows: [], rowCount: 0 };
    }
    if (t.startsWith('DELETE FROM platform.restock_alert WHERE id') || t.startsWith('DELETE FROM platform.price_alert WHERE id')) {
      return { rows: [], rowCount: opts.deleted ?? 1 };
    }
    return undefined;
  });
  const kv = new FakeKvStore();
  const svc = new WishlistService(testConfig({ ...ALL_ON, ...opts.env }), db, catalog, new RateLimiter(kv));
  return { svc, db, catalog };
}

describe('wishlist DTO (§28.8)', () => {
  it('allowlist ketat', async () => {
    expect(parseWishlistItem({ channel_code: 'SMD', product_id: 10 })).toEqual({ channel_code: 'SMD', product_id: 10 });
    for (const bad of [{ channel_code: 'smd', product_id: 10 }, { channel_code: 'SMD', product_id: 0 }, { channel_code: 'SMD', product_id: 10, x: 1 }]) {
      expect((await catchApiError(() => parseWishlistItem(bad))).getStatus()).toBe(400);
    }
    expect((await catchApiError(() => parseRestockAlert({ channel_code: 'SMD', outlet_code: 'O1', product_id: 1, channels: ['sms'] }))).getStatus()).toBe(400);
    expect((await catchApiError(() => parseRestockAlert({ channel_code: 'SMD', outlet_code: 'O1', product_id: 1, channels: ['push', 'push'] }))).getStatus()).toBe(400);
    expect(parsePriceAlert({ channel_code: 'SMD', product_id: 1, target_price_idr: 900 }).channels).toEqual(['push']);
    expect((await catchApiError(() => parsePriceAlert({ channel_code: 'SMD', product_id: 1, target_price_idr: 9.5 }))).getStatus()).toBe(400);
  });
});

describe('WishlistService', () => {
  it('flag mati → 404 di semua endpoint', async () => {
    const ctx = setup({ env: { WISHLIST_ENABLED: 'false', RESTOCK_ALERT_ENABLED: 'false', PRICE_ALERT_ENABLED: 'false' } });
    expect((await catchApiError(() => ctx.svc.list(user, null))).getStatus()).toBe(404);
    expect((await catchApiError(() => ctx.svc.listRestock(user))).getStatus()).toBe(404);
    expect((await catchApiError(() => ctx.svc.createPrice(user, { channel_code: 'SMD', product_id: 10, target_price_idr: 1, channels: ['push'] }))).getStatus()).toBe(404);
    expect(ctx.db.queries).toHaveLength(0);
  });

  it('PUT: produk di luar katalog kanal → 404; sudah ada → idempoten tanpa insert; penuh (200) → 409', async () => {
    const ctx = setup();
    expect((await catchApiError(() => ctx.svc.put(user, { channel_code: 'SMD', product_id: 99 }))).getStatus()).toBe(404);
    const exists = setup({ exists: true });
    await exists.svc.put(user, { channel_code: 'SMD', product_id: 10 });
    expect(exists.db.find('INSERT INTO platform.member_wishlist')).toHaveLength(0);
    const full = setup({ count: 200 });
    const err = await catchApiError(() => full.svc.put(user, { channel_code: 'SMD', product_id: 10 }));
    expect(err.getStatus()).toBe(409);
    expect((err.body as { error: { code: string } }).error.code).toBe('wishlist_full');
    const ok = setup({ count: 199 });
    await ok.svc.put(user, { channel_code: 'SMD', product_id: 10 });
    expect(ok.db.find('INSERT INTO platform.member_wishlist')[0]!.params).toEqual([user.memberId, 'SMD', 10]);
  });

  it('restock: produk tersedia → 409 already_in_stock; status tak diketahui → 404', async () => {
    expect((await catchApiError(() => setup({ stock: 'low' }).svc.createRestock(user, { channel_code: 'SMD', outlet_code: 'O1', product_id: 10, channels: ['push'] }))).getStatus()).toBe(409);
    expect((await catchApiError(() => setup({ stock: null }).svc.createRestock(user, { channel_code: 'SMD', outlet_code: 'O1', product_id: 10, channels: ['push'] }))).getStatus()).toBe(404);
  });

  it('restock: kanal email/WA saat flag kanal mati → 400 channel_unavailable', async () => {
    const ctx = setup();
    const err = await catchApiError(() => ctx.svc.createRestock(user, { channel_code: 'SMD', outlet_code: 'O1', product_id: 10, channels: ['push', 'wa_unofficial'] }));
    expect((err.body as { error: { code: string } }).error.code).toBe('channel_unavailable');
    const on = setup({ env: { EMAIL_ENABLED: 'true' } });
    await expect(on.svc.createRestock(user, { channel_code: 'SMD', outlet_code: 'O1', product_id: 10, channels: ['email'] })).resolves.toEqual({ id: expect.any(String) });
  });

  it('restock: out → alert 30 hari + state `out` untuk deteksi transisi; batas 50 aktif → 409; duplikat → id yang sama', async () => {
    const ctx = setup();
    const { id } = await ctx.svc.createRestock(user, { channel_code: 'SMD', outlet_code: 'O1', product_id: 10, channels: ['push'] });
    expect(ctx.db.find('INSERT INTO platform.restock_alert')[0]!.params).toEqual([id, user.memberId, 'SMD', 'O1', 10, ['push'], 30]);
    expect(ctx.db.find('INSERT INTO platform.alert_stock_state')).toHaveLength(1);
    expect((await catchApiError(() => setup({ count: 50 }).svc.createRestock(user, { channel_code: 'SMD', outlet_code: 'O1', product_id: 10, channels: ['push'] }))).getStatus()).toBe(409);
    expect(await setup({ existingAlert: true }).svc.createRestock(user, { channel_code: 'SMD', outlet_code: 'O1', product_id: 10, channels: ['push'] })).toEqual({ id: 'existing-id' });
  });

  it('price alert: target ≥ harga saat ini → 400; valid → insert + state harga awal', async () => {
    const ctx = setup();
    const err = await catchApiError(() => ctx.svc.createPrice(user, { channel_code: 'SMD', product_id: 10, target_price_idr: 20000, channels: ['push'] }));
    expect((err.body as { error: { code: string } }).error.code).toBe('invalid_target_price');
    await ctx.svc.createPrice(user, { channel_code: 'SMD', product_id: 10, target_price_idr: 18000, channels: ['push'] });
    expect(ctx.db.find('INSERT INTO platform.price_alert')[0]!.params.slice(1)).toEqual([user.memberId, 'SMD', 10, 18000, 90, ['push']]);
    expect(ctx.db.find('INSERT INTO platform.alert_price_state')[0]!.params).toEqual(['SMD', 10, 20000]);
  });

  it('hapus alert milik member lain → 404', async () => {
    const ctx = setup({ deleted: 0 });
    expect((await catchApiError(() => ctx.svc.deleteRestock(user, randomUUID()))).getStatus()).toBe(404);
    expect((await catchApiError(() => ctx.svc.deletePrice(user, randomUUID()))).getStatus()).toBe(404);
  });
});

describe('wishlist-privacy', () => {
  it('skema belum ada → no-op; ada → hapus 3 tabel + export', async () => {
    const off = new FakeDb((t) => (t.includes('to_regclass') ? { rows: [{ ok: false }], rowCount: 1 } : undefined));
    await eraseWishlistMember(off, user.memberId);
    expect(off.queries.filter((q) => q.text.includes('DELETE'))).toHaveLength(0);
    const on = new FakeDb((t) => (t.includes('to_regclass') ? { rows: [{ ok: true }], rowCount: 1 } : undefined));
    await eraseWishlistMember(on, user.memberId);
    expect(on.queries.filter((q) => q.text.startsWith('DELETE')).map((q) => q.params)).toEqual([[user.memberId], [user.memberId], [user.memberId]]);
    expect(await exportWishlistMember(on, user.memberId)).toEqual({ wishlist: [], restock_alerts: [], price_alerts: [] });
  });
});
