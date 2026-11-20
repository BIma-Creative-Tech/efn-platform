import { randomUUID } from 'node:crypto';
import { OutboxService } from '../common/outbox/outbox.service';
import { FakeDb, randomSecret, testConfig } from '../common/testing/fakes';
import { BehaviorEventMessage, parseBehaviorMessage } from './behavior-message';
import { PurchaseEventsService, purchaseEvents } from './purchase-events.service';
import { deterministicUuid, memberSubjectKey } from './subject-key';

const PEPPER = randomSecret();
const MEMBER = randomUUID();
const PART = randomUUID();
const NOW = Date.UTC(2026, 10, 20, 5, 0, 0);

const part = (extra: Record<string, unknown> = {}) => ({
  part_id: PART,
  member_id: MEMBER,
  channel_code: 'SMD',
  company_code: 'SMD',
  outlet_code: 'SMD01',
  confirmed_at: new Date(NOW - 600_000),
  lines: [
    { product_id: 10, qty: 2, price_unit_incl: 12000.4 },
    { product_id: 10, qty: 1, price_unit_incl: 12000 },
    { product_id: 11, qty: 1, price_unit_incl: 5000 },
    { product_id: 99, qty: 1, price_unit_incl: -2000, reward: true },
    { product_id: 12, qty: 0, price_unit_incl: 1 },
  ],
  ...extra,
});

function setup(opts: { parts?: Record<string, unknown>[]; claimed?: boolean; lastChannel?: string; lock?: boolean; env?: Record<string, string> } = {}) {
  const enqueued: { queue: string; payload: Record<string, unknown>; id: string }[] = [];
  const outbox = { enqueue: jest.fn(async (_q: unknown, queue: string, payload: Record<string, unknown>, id: string) => void enqueued.push({ queue, payload, id })) } as unknown as OutboxService;
  const db = new FakeDb((t) => {
    if (t.includes('pg_try_advisory_xact_lock')) return { rows: [{ ok: opts.lock !== false }], rowCount: 1 };
    if (t.includes('FROM platform.online_order_part p')) return { rows: opts.parts ?? [part()], rowCount: (opts.parts ?? [part()]).length };
    if (t.includes('INSERT INTO behavior.purchase_emitted')) return opts.claimed === false ? { rows: [], rowCount: 0 } : { rows: [{}], rowCount: 1 };
    if (t.includes('SELECT channel FROM behavior.event_raw')) return opts.lastChannel ? { rows: [{ channel: opts.lastChannel }], rowCount: 1 } : { rows: [], rowCount: 0 };
    return undefined;
  });
  const svc = new PurchaseEventsService(testConfig({ BEHAVIOR_TRACKING_ENABLED: 'true', BEHAVIOR_SUBJECT_PEPPER: PEPPER, BEHAVIOR_DEVICE_PEPPER: randomSecret(), ...opts.env }), db, outbox);
  svc.now = () => NOW;
  return { svc, db, enqueued };
}

describe('PurchaseEventsService (event server purchase, QA G50 #7)', () => {
  it('bagian lunas → outbox behavior.event (bukan tulis staging langsung), subject member, satu event per produk', async () => {
    const ctx = setup({ lastChannel: 'web' });
    expect(await ctx.svc.runOnce()).toBe(1);
    expect(ctx.enqueued).toHaveLength(1);
    const { queue, payload, id } = ctx.enqueued[0]!;
    expect(queue).toBe('behavior.event');
    const msg = payload as unknown as BehaviorEventMessage;
    expect(id).toBe(`behavior:${msg.batch_id}`);
    expect(msg.batch_id).toBe(deterministicUuid(`efn:purchase:batch:${PART}:0`));
    expect(msg).toMatchObject({ subject_kind: 'member', subject_key: memberSubjectKey(PEPPER, MEMBER), channel: 'web' });
    expect(msg.events.map((e) => [e.type, e.product_id, e.qty, e.price_idr, e.company_code])).toEqual([
      ['purchase', 10, 3, 12000, 'SMD'],
      ['purchase', 11, 1, 5000, 'SMD'],
    ]);
    // pesan lolos validasi consumer writer (jalur behavior yang sama)
    expect(() => parseBehaviorMessage(payload)).not.toThrow();
    expect(ctx.db.find('INSERT INTO behavior.event_raw')).toHaveLength(0);
    // gate consent tracking akun ada di SQL kandidat, member terhapus dikecualikan
    const sel = ctx.db.find('FROM platform.online_order_part p')[0]!.text;
    expect(sel).toContain("c.purpose = 'tracking'");
    expect(sel).toContain('m.deleted_at IS NULL');
  });

  it('idempoten: bagian sudah ditandai → tanpa outbox; event_id deterministik per (bagian, produk)', async () => {
    const ctx = setup({ claimed: false });
    expect(await ctx.svc.runOnce()).toBe(0);
    expect(ctx.enqueued).toHaveLength(0);
    const ev = purchaseEvents(part());
    expect(ev[0]!.event_id).toBe(deterministicUuid(`efn:purchase:${PART}:10`));
  });

  it('tanpa jejak channel di staging → app; kanal/PT di luar kontrak → ditandai tanpa event', async () => {
    const ctx = setup();
    await ctx.svc.runOnce();
    expect((ctx.enqueued[0]!.payload as unknown as BehaviorEventMessage).channel).toBe('app');
    const bad = setup({ parts: [part({ channel_code: 'TOO-LONG-CODE' }), part({ part_id: randomUUID(), company_code: 'ZZ' })] });
    expect(await bad.svc.runOnce()).toBe(0);
    expect(bad.enqueued).toHaveLength(0);
    expect(bad.db.find('INSERT INTO behavior.purchase_emitted')).toHaveLength(2);
  });

  it('tracking mati / lock replika lain → tidak memproses; error DB tidak melempar', async () => {
    const off = setup({ env: { BEHAVIOR_TRACKING_ENABLED: 'false' } });
    expect(await off.svc.runOnce()).toBe(0);
    expect(off.db.queries).toHaveLength(0);
    const locked = setup({ lock: false });
    expect(await locked.svc.runOnce()).toBe(0);
    expect(locked.db.find('FROM platform.online_order_part p')).toHaveLength(0);
    const svc = new PurchaseEventsService(
      testConfig({ BEHAVIOR_TRACKING_ENABLED: 'true', BEHAVIOR_SUBJECT_PEPPER: PEPPER }),
      new FakeDb(() => {
        throw new Error('db down');
      }),
      {} as OutboxService,
    );
    await expect(svc.runOnce()).resolves.toBe(0);
  });
});
