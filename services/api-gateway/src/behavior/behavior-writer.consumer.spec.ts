import { randomUUID } from 'node:crypto';
import { AmqpService, PermanentMessageError } from '../common/amqp/amqp.service';
import { FakeDb, testConfig } from '../common/testing/fakes';
import { BehaviorWriterConsumer } from './behavior-writer.consumer';

const NOW = Date.UTC(2026, 10, 20, 3, 0, 0);
const SUBJECT = 'd'.repeat(64);

const event = (extra: Record<string, unknown> = {}): Record<string, unknown> => ({
  event_id: randomUUID(),
  type: 'product_view',
  client_ts: new Date(NOW - 1000).toISOString(),
  channel_code: 'SMD',
  company_code: 'SMD',
  outlet_code: 'SMD01',
  product_id: 10,
  duration_s: 5,
  qty: null,
  query: null,
  result_count: null,
  stock_status: 'in_stock',
  price_idr: 12000,
  ...extra,
});
const message = (events: unknown[], extra: Record<string, unknown> = {}): Record<string, unknown> => ({
  schema_version: 1,
  batch_id: randomUUID(),
  received_at: new Date(NOW).toISOString(),
  subject_key: SUBJECT,
  subject_kind: 'device',
  channel: 'app',
  events,
  ...extra,
});

function setup(opts: { claimed?: boolean; freshIds?: (ids: string[]) => string[]; mappedCompany?: string | null } = {}) {
  const db = new FakeDb((t, p) => {
    if (t.includes('INSERT INTO platform.processed_message')) return opts.claimed === false ? { rows: [], rowCount: 0 } : { rows: [{ consumer: 'x' }], rowCount: 1 };
    if (t.includes('FROM platform.online_catalog_sync')) {
      const c = opts.mappedCompany === undefined ? 'SMD' : opts.mappedCompany;
      return c === null ? { rows: [], rowCount: 0 } : { rows: [{ channel_code: 'SMD', code: 'SMD', active: 'true', company_code: c }], rowCount: 1 };
    }
    if (t.includes('INSERT INTO behavior.event_dedup')) {
      const ids = (opts.freshIds ?? ((x: string[]) => x))(p[1] as string[]);
      return { rows: ids.map((event_id) => ({ event_id })), rowCount: ids.length };
    }
    return undefined;
  });
  const consumer = new BehaviorWriterConsumer({} as AmqpService, db, testConfig({ BEHAVIOR_TRACKING_ENABLED: 'true' }));
  consumer.now = () => NOW;
  return { db, consumer };
}

describe('BehaviorWriterConsumer (behavior.event → PG staging)', () => {
  it('menulis batch ke event_raw lewat unnest setelah klaim processed_message + dedup', async () => {
    const ctx = setup();
    const out = await ctx.consumer.handle(message([event(), event()]), { userId: 'efn-bff' });
    expect(out).toEqual({ status: 'written', inserted: 2, deduped: 0 });
    const ins = ctx.db.find('INSERT INTO behavior.event_raw')[0]!;
    expect(ins.params[2]).toBe(SUBJECT);
    expect((ins.params[5] as string[]).length).toBe(2);
    // company_code = kolom ke-18; ingested_at TIDAK dikirim (DEFAULT clock_timestamp(), cursor ETL)
    expect(ins.params[17]).toEqual(['SMD', 'SMD']);
    expect(ins.text).toContain('company_code');
    expect(ins.text).not.toContain('ingested_at');
    expect(ctx.db.find('INSERT INTO platform.processed_message')[0]!.params[0]).toBe('bff.behavior-writer');
  });

  it('PT diturunkan ulang dari pemetaan kanal saat tulis; kanal sementara tak terpetakan → PT tervalidasi dari pesan', async () => {
    const remap = setup({ mappedCompany: 'GL' });
    await remap.consumer.handle(message([event()]), { userId: 'efn-bff' });
    expect(remap.db.find('INSERT INTO behavior.event_raw')[0]!.params[17]).toEqual(['GL']);
    const gone = setup({ mappedCompany: null });
    await gone.consumer.handle(message([event()]), { userId: 'efn-bff' });
    expect(gone.db.find('INSERT INTO behavior.event_raw')[0]!.params[17]).toEqual(['SMD']);
  });

  it('event tanpa company_code / PT di luar COMPANY_CODES → DLQ (tidak pernah disimpan tanpa PT)', async () => {
    const ctx = setup();
    const noCompany = event();
    delete noCompany.company_code;
    await expect(ctx.consumer.handle(message([noCompany]), { userId: 'efn-bff' })).rejects.toBeInstanceOf(PermanentMessageError);
    await expect(ctx.consumer.handle(message([event({ company_code: 'ZZ' })]), { userId: 'efn-bff' })).rejects.toBeInstanceOf(PermanentMessageError);
  });

  it('event yang sudah ada di staging (subject_key, event_id) tidak ditulis ulang', async () => {
    const ctx = setup({ freshIds: (ids) => ids.slice(1) });
    expect(await ctx.consumer.handle(message([event(), event()]), { userId: 'efn-bff' })).toEqual({ status: 'written', inserted: 1, deduped: 1 });
  });

  it('batch yang sama dikirim ulang → duplicate tanpa insert', async () => {
    const ctx = setup({ claimed: false });
    expect(await ctx.consumer.handle(message([event()]), { userId: 'efn-bff' })).toEqual({ status: 'duplicate' });
    expect(ctx.db.find('INSERT INTO behavior.event_raw')).toHaveLength(0);
  });

  it('publisher bukan efn-bff / tanpa user_id → DLQ', async () => {
    const ctx = setup();
    await expect(ctx.consumer.handle(message([event()]), { userId: 'efn-ocr' })).rejects.toBeInstanceOf(PermanentMessageError);
    await expect(ctx.consumer.handle(message([event()]), { userId: undefined })).rejects.toBeInstanceOf(PermanentMessageError);
  });

  it('payload rusak (subject bukan hex, tipe tak dikenal, cart_abandon dari antrean) → DLQ', async () => {
    const ctx = setup();
    for (const bad of [
      message([event()], { subject_key: 'x' }),
      message([event({ type: 'login' })]),
      message([event({ type: 'cart_abandon' })]),
      message([]),
      message([event({ query: 'q'.repeat(65) })]),
      { schema_version: 2 },
    ]) {
      await expect(ctx.consumer.handle(bad, { userId: 'efn-bff' })).rejects.toBeInstanceOf(PermanentMessageError);
    }
  });

  it('batch lebih tua dari retensi staging → diabaikan (ack) tanpa tulis', async () => {
    const ctx = setup();
    const old = message([event()], { received_at: new Date(NOW - 8 * 86_400_000).toISOString() });
    expect(await ctx.consumer.handle(old, { userId: 'efn-bff' })).toEqual({ status: 'stale' });
    expect(ctx.db.queries).toHaveLength(0);
  });
});
