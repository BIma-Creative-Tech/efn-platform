import { CatalogService } from '../catalog/catalog.service';
import { OutboxService } from '../common/outbox/outbox.service';
import { FakeDb, FakeKvStore, testConfig } from '../common/testing/fakes';
import { AlertWatcherService } from './alert-watcher.service';

interface State {
  stock?: { status: string; restock_seq: number } | null;
  price?: { price_idr: number; price_version: number } | null;
}

function setup(opts: { state?: State; liveStock?: string; livePrice?: number; stockThrows?: boolean; env?: Record<string, string> } = {}) {
  const enqueued: { queue: string; payload: Record<string, unknown>; id: string }[] = [];
  const outbox = { enqueue: jest.fn(async (_q: unknown, queue: string, payload: Record<string, unknown>, id: string) => void enqueued.push({ queue, payload, id })) } as unknown as OutboxService;
  const catalog = {
    stock: jest.fn(async (_ch: string, _o: string, ids: number[]) => {
      if (opts.stockThrows) throw new Error('odoo down');
      return { items: ids.map((id) => ({ product_id: id, status: opts.liveStock ?? 'in_stock' })), checked_at: '' };
    }),
    productsByIds: jest.fn(async (_ch: string, ids: number[]) => new Map(ids.map((id) => [id, { product_id: id, name: 'X', price_incl: opts.livePrice ?? 9000 }]))),
  } as unknown as CatalogService;
  const db = new FakeDb((t) => {
    if (t.includes('FROM platform.restock_alert')) return { rows: [{ channel_code: 'SMD', outlet_code: 'O1', product_id: 10 }], rowCount: 1 };
    if (t.includes('FROM platform.price_alert')) return { rows: [{ channel_code: 'SMD', product_id: 10 }], rowCount: 1 };
    if (t.includes('FROM platform.alert_stock_state')) return opts.state?.stock ? { rows: [opts.state.stock], rowCount: 1 } : { rows: [], rowCount: 0 };
    if (t.includes('FROM platform.alert_price_state')) return opts.state?.price ? { rows: [opts.state.price], rowCount: 1 } : { rows: [], rowCount: 0 };
    return undefined;
  });
  const kv = new FakeKvStore();
  const svc = new AlertWatcherService(testConfig({ RESTOCK_ALERT_ENABLED: 'true', PRICE_ALERT_ENABLED: 'true', ...opts.env }), db, catalog, outbox, kv);
  return { svc, db, enqueued, kv };
}

describe('AlertWatcherService (publisher notify.restock / notify.price_alert lewat outbox)', () => {
  it('out → in_stock: restock_seq naik, outbox notify.restock dengan idempotency key §28.7', async () => {
    const ctx = setup({ state: { stock: { status: 'out', restock_seq: 2 } } });
    const r = await ctx.svc.runOnce();
    expect(r.restockPublished).toBe(1);
    const msg = ctx.enqueued.find((e) => e.queue === 'notify.restock')!;
    expect(msg.id).toBe('restock:SMD:O1:10:3');
    expect(msg.payload).toMatchObject({ schema_version: 1, channel_code: 'SMD', outlet_code: 'O1', product_id: 10 });
    expect(ctx.db.find('INSERT INTO platform.alert_stock_state')[0]!.params).toEqual(['SMD', 'O1', 10, 'in_stock', 3]);
  });

  it('masih out / sudah in_stock sebelumnya → tidak menerbitkan', async () => {
    expect((await setup({ state: { stock: { status: 'out', restock_seq: 0 } }, liveStock: 'out' }).svc.runOnce()).restockPublished).toBe(0);
    expect((await setup({ state: { stock: { status: 'low', restock_seq: 1 } }, liveStock: 'in_stock' }).svc.runOnce()).restockPublished).toBe(0);
  });

  it('harga turun di bawah harga teramati → price_version naik + notify.price_alert; pengamatan pertama tidak menerbitkan', async () => {
    const ctx = setup({ state: { price: { price_idr: 10000, price_version: 4 } }, livePrice: 9000, env: { RESTOCK_ALERT_ENABLED: 'false' } });
    expect((await ctx.svc.runOnce()).pricePublished).toBe(1);
    expect(ctx.enqueued[0]).toMatchObject({ queue: 'notify.price_alert', id: 'price:SMD:10:5', payload: { new_price_idr: 9000 } });
    const first = setup({ state: { price: null }, env: { RESTOCK_ALERT_ENABLED: 'false' } });
    expect((await first.svc.runOnce()).pricePublished).toBe(0);
    expect(first.db.find('INSERT INTO platform.alert_price_state')).toHaveLength(1);
  });

  it('Odoo/stok gagal → tidak melempar, tanpa publish; lock Redis dipegang replika lain → dilewati', async () => {
    const ctx = setup({ stockThrows: true, env: { PRICE_ALERT_ENABLED: 'false' } });
    await expect(ctx.svc.runOnce()).resolves.toMatchObject({ restockPublished: 0 });
    const locked = setup();
    await locked.kv.setEx('ocat:alertwatch:lock', '1', 60);
    expect(await locked.svc.runOnce()).toEqual({ restockChecked: 0, restockPublished: 0, priceChecked: 0, pricePublished: 0 });
  });
});
