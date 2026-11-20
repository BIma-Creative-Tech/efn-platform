import { randomUUID } from 'node:crypto';
import { CatalogService } from '../catalog/catalog.service';
import { AmqpService, PermanentMessageError } from '../common/amqp/amqp.service';
import { FakeDb, testCipher, testConfig } from '../common/testing/fakes';
import { PII_AAD } from '../common/pii/crypto';
import { ConsolePushSender } from '../promo/push-sender';
import { EmailNotifierPort, WaUnofficialNotifierPort } from '../campaign/channel-ports';
import { AlertDispatcherConsumer } from './alert-dispatcher.consumer';

const M1 = randomUUID();
const M2 = randomUUID();

function setup(opts: { claimed?: boolean; alerts?: { id: string; member_id: string; channels: string[] }[]; env?: Record<string, string> } = {}) {
  const config = testConfig({ RESTOCK_ALERT_ENABLED: 'true', PRICE_ALERT_ENABLED: 'true', ...opts.env });
  const cipher = testCipher(config);
  const token = 'ExponentPushToken[abcdefghijklmnop]';
  const db = new FakeDb((t) => {
    if (t.includes('INSERT INTO platform.processed_message')) return opts.claimed === false ? { rows: [], rowCount: 0 } : { rows: [{}], rowCount: 1 };
    if (t.includes('UPDATE platform.restock_alert') || t.includes('UPDATE platform.price_alert')) {
      const rows = opts.alerts ?? [{ id: randomUUID(), member_id: M1, channels: ['push', 'email'] }, { id: randomUUID(), member_id: M2, channels: ['wa_unofficial'] }];
      return { rows, rowCount: rows.length };
    }
    if (t.includes('FROM platform.push_token')) return { rows: [{ token_enc: cipher.encrypt(token, PII_AAD.PUSH_TOKEN) }], rowCount: 1 };
    return undefined;
  });
  const catalog = {
    productsByIds: jest.fn(async () => new Map([[10, { product_id: 10, name: 'Susu UHT', price_incl: 9000 }]])),
    channel: jest.fn(async () => ({ outlets: [{ outlet_code: 'O1', public_name: 'SMD Kemang' }] })),
  } as unknown as CatalogService;
  const email = { enqueue: jest.fn(async () => 'queued' as const) } as unknown as EmailNotifierPort & { enqueue: jest.Mock };
  const wa = { enqueue: jest.fn(async () => 'skipped_no_consent' as const) } as unknown as WaUnofficialNotifierPort & { enqueue: jest.Mock };
  const push = new ConsolePushSender();
  const c = new AlertDispatcherConsumer({} as AmqpService, db, config, catalog, cipher, email, wa, push);
  return { c, db, email, wa, push, token };
}

const restock = { schema_version: 1, idempotency_key: 'restock:SMD:O1:10:1', channel_code: 'SMD', outlet_code: 'O1', product_id: 10, detected_at: '2026-11-20T03:00:00.000Z' };
const price = { schema_version: 1, idempotency_key: 'price:SMD:10:2', channel_code: 'SMD', product_id: 10, new_price_idr: 9000, detected_at: '2026-11-20T03:00:00.000Z' };

describe('AlertDispatcherConsumer', () => {
  it('restock: alert ditandai sent dalam transaksi klaim, lalu push/email/WA sesuai kanal pilihan', async () => {
    const ctx = setup();
    const out = await ctx.c.handleRestock(restock, { userId: 'efn-bff' });
    expect(out).toEqual({ status: 'dispatched', alerts: 2, push: 1, email: 1, wa: 0 });
    expect(ctx.push.sent[0]).toMatchObject({ to: ctx.token, title: 'Stok tersedia lagi' });
    expect(ctx.email.enqueue).toHaveBeenCalledWith(
      expect.objectContaining({ memberId: M1, source: 'restock', template: 'restock_available', params: { product_name: 'Susu UHT', outlet_name: 'SMD Kemang' } }),
    );
    expect(ctx.wa.enqueue).toHaveBeenCalledWith(expect.objectContaining({ memberId: M2, category: 'notify_restock', source: 'restock', template: 'restock_available' }));
    const waArg = ctx.wa.enqueue.mock.calls[0]![0] as { expiresAt: Date };
    expect(waArg.expiresAt.getTime() - Date.now()).toBeLessThanOrEqual(24 * 3600 * 1000);
    expect(ctx.db.find('UPDATE platform.restock_alert a SET sent_at = now()')).toHaveLength(1);
  });

  it('pesan yang sama lagi → duplicate tanpa pengiriman', async () => {
    const ctx = setup({ claimed: false });
    expect(await ctx.c.handleRestock(restock, { userId: 'efn-bff' })).toEqual({ status: 'duplicate' });
    expect(ctx.email.enqueue).not.toHaveBeenCalled();
    expect(ctx.push.sent).toHaveLength(0);
  });

  it('price alert: hanya alert dengan target ≥ harga baru (filter SQL), kategori WA price_alert', async () => {
    const ctx = setup({ alerts: [{ id: randomUUID(), member_id: M1, channels: ['wa_unofficial'] }] });
    await ctx.c.handlePrice(price, { userId: 'efn-bff' });
    expect(ctx.db.find('r.target_price_idr >= $3')[0]!.params).toEqual(['SMD', 10, 9000, 1000]);
    expect(ctx.wa.enqueue).toHaveBeenCalledWith(
      expect.objectContaining({ category: 'price_alert', template: 'price_drop', params: { product_name: 'Susu UHT', new_price_idr: '9.000' } }),
    );
  });

  it('notifier melempar → kanal lain tetap jalan, consumer tidak gagal (alert sudah ditandai: at-most-once)', async () => {
    const ctx = setup();
    ctx.email.enqueue.mockRejectedValueOnce(new Error('smtp'));
    await expect(ctx.c.handleRestock(restock, { userId: 'efn-bff' })).resolves.toMatchObject({ status: 'dispatched', email: 0 });
    expect(ctx.wa.enqueue).toHaveBeenCalled();
  });

  it('publisher asing / payload rusak → DLQ; flag mati → disabled (ack)', async () => {
    const ctx = setup();
    await expect(ctx.c.handleRestock(restock, { userId: 'efn-ocr' })).rejects.toBeInstanceOf(PermanentMessageError);
    await expect(ctx.c.handleRestock({ ...restock, outlet_code: '../x' }, { userId: 'efn-bff' })).rejects.toBeInstanceOf(PermanentMessageError);
    await expect(ctx.c.handlePrice({ ...price, new_price_idr: -1 }, { userId: 'efn-bff' })).rejects.toBeInstanceOf(PermanentMessageError);
    const off = setup({ env: { RESTOCK_ALERT_ENABLED: 'false' } });
    expect(await off.c.handleRestock(restock, { userId: 'efn-bff' })).toEqual({ status: 'disabled' });
    expect(off.db.queries).toHaveLength(0);
  });
});
