import { randomBytes } from 'node:crypto';
import { toHash } from './contract';
import type { Limits } from './config';
import { Metrics } from './metrics';
import { Processor } from './processor';
import { MinuteWindow, PersistentState } from './state';
import { isStopText } from './templates';
import { FakeClient, FakeSink, MemoryBackend, captureLog } from './testing/fakes';

const T0 = Date.UTC(2026, 10, 20, 3, 0, 0);
const TO = '6281234567890';
const LIMITS: Limits = { ratePerMin: 10, maxPerDay: 300, maxPerMemberPerDay: 1, jitterMinMs: 2000, jitterMaxMs: 8000 };

async function setup(limits: Partial<Limits> = {}) {
  const hashKey = Buffer.from(randomBytes(32).toString('hex'), 'utf8');
  let now = T0;
  const sleeps: number[] = [];
  const backend = new MemoryBackend();
  const state = await PersistentState.load(backend, now);
  const client = new FakeClient();
  const sink = new FakeSink();
  const metrics = new Metrics();
  const { log, lines } = captureLog();
  const lim = { ...LIMITS, ...limits };
  const p = new Processor({
    client,
    state,
    minute: new MinuteWindow(lim.ratePerMin),
    limits: lim,
    hashKey,
    sink,
    log,
    metrics,
    senderLabel: 'Promo SMD',
    now: () => now,
    sleep: async (ms) => {
      sleeps.push(ms);
      now += ms;
    },
    random: () => 0.5,
    connectWaitMs: 5000,
  });
  const msg = (over: Record<string, unknown> = {}, to = TO, src = 'r1'): Record<string, unknown> => {
    const h = toHash(hashKey, to);
    return {
      schema_version: 1,
      idempotency_key: `wa:restock:${src}:${h}`,
      to_e164: to,
      to_hash: h,
      category: 'notify_restock',
      template: 'restock_available',
      params: { product_name: 'Telur 1kg', outlet_name: 'SMD Outlet 1' },
      expires_at: new Date(T0 + 3600_000).toISOString(),
      ...over,
    };
  };
  return { p, client, sink, state, backend, metrics, lines, sleeps, msg, hashKey, advance: (ms: number) => (now += ms) };
}

const BFF = 'efn-bff';

describe('Processor notify.wa_unofficial', () => {
  it('kirim: teks memuat STOP, jitter 2–8 dtk, hasil sent; log tanpa nomor utuh & tanpa isi', async () => {
    const c = await setup();
    expect(await c.p.handle({ payload: c.msg(), userId: BFF })).toBe('ack');
    expect(c.client.sent).toHaveLength(1);
    expect(c.client.sent[0].to).toBe(TO);
    expect(c.client.sent[0].text).toContain('Balas STOP');
    expect(c.client.sent[0].text).toContain('*Promo SMD*');
    expect(c.sleeps).toContain(5000); // 2000 + floor(0.5 * 6001)
    expect(c.sink.results[0].result).toMatchObject({ kind: 'result', status: 'sent', ref_idempotency_key: c.msg().idempotency_key });
    expect(c.sink.results[0].id).toBe(`warslt:${String(c.msg().idempotency_key)}:sent`);
    expect(JSON.stringify(c.sink.results)).not.toContain(TO);
    const logs = c.lines.join('\n');
    expect(logs).not.toContain(TO);
    expect(logs).toContain('62812****890');
    expect(logs).not.toContain('Telur');
  });

  it('idempoten: pesan sama dua kali → satu kiriman', async () => {
    const c = await setup();
    await c.p.handle({ payload: c.msg(), userId: BFF });
    expect(await c.p.handle({ payload: c.msg(), userId: BFF })).toBe('ack');
    expect(c.client.sent).toHaveLength(1);
    // state dedup bertahan setelah "restart" (dimuat ulang dari backend)
    const reloaded = await PersistentState.load(c.backend, T0 + 1000);
    expect(reloaded.isProcessed(String(c.msg().idempotency_key))).toBe(true);
  });

  it('publisher bukan efn-bff → DLQ tanpa kirim', async () => {
    const c = await setup();
    expect(await c.p.handle({ payload: c.msg(), userId: 'efn-ocr' })).toBe('dlq');
    expect(await c.p.handle({ payload: c.msg(), userId: undefined })).toBe('dlq');
    expect(c.client.sent).toHaveLength(0);
  });

  it.each([['otp'], ['order_status'], ['payment'], ['loyalty']])('category %s di luar allowlist → ack + failed/invalid_category', async (cat) => {
    const c = await setup();
    expect(await c.p.handle({ payload: c.msg({ category: cat }), userId: BFF })).toBe('ack');
    expect(c.client.sent).toHaveLength(0);
    expect(c.sink.results[0].result).toMatchObject({ status: 'failed', reason: 'invalid_category' });
  });

  it('to_hash tidak cocok dengan to_e164 / template beda kategori / nomor non-Indonesia → failed, tidak dikirim', async () => {
    const c = await setup();
    const other = toHash(c.hashKey, '6289999999999');
    await c.p.handle({ payload: c.msg({ to_hash: other, idempotency_key: `wa:restock:x:${other}` }), userId: BFF });
    await c.p.handle({ payload: c.msg({ template: 'promo_generic' }, TO, 'r2'), userId: BFF });
    const intl = '14155550100';
    await c.p.handle({ payload: { ...c.msg({}, TO, 'r3'), to_e164: intl }, userId: BFF });
    expect(c.client.sent).toHaveLength(0);
    expect(c.sink.results.map((r) => r.result.reason)).toEqual(['hash_mismatch', 'invalid_template', 'invalid_recipient']);
  });

  it('payload tanpa idempotency_key/to_hash yang valid → DLQ', async () => {
    const c = await setup();
    expect(await c.p.handle({ payload: { schema_version: 1 }, userId: BFF })).toBe('dlq');
    expect(await c.p.handle({ payload: null, userId: BFF })).toBe('dlq');
    expect(c.sink.results).toHaveLength(0);
  });

  it('kedaluwarsa → failed/expired', async () => {
    const c = await setup();
    await c.p.handle({ payload: c.msg({ expires_at: new Date(T0 - 1).toISOString() }), userId: BFF });
    expect(c.client.sent).toHaveLength(0);
    expect(c.sink.results[0].result).toMatchObject({ status: 'failed', reason: 'expired' });
  });

  it('batas per member per hari (1) → rate_limited; hari WIB berikutnya boleh lagi', async () => {
    const c = await setup();
    await c.p.handle({ payload: c.msg({}, TO, 'a'), userId: BFF });
    await c.p.handle({ payload: c.msg({}, TO, 'b'), userId: BFF });
    expect(c.client.sent).toHaveLength(1);
    expect(c.sink.results[1].result).toMatchObject({ status: 'rate_limited', reason: 'max_per_member_per_day' });
    c.advance(24 * 3600_000);
    await c.p.handle({ payload: c.msg({ expires_at: new Date(T0 + 30 * 3600_000).toISOString() }, TO, 'c'), userId: BFF });
    expect(c.client.sent).toHaveLength(2);
  });

  it('batas harian global → rate_limited', async () => {
    const c = await setup({ maxPerDay: 2 });
    for (const [i, n] of ['6281200000001', '6281200000002', '6281200000003'].entries()) {
      await c.p.handle({ payload: c.msg({}, n, `d${i}`), userId: BFF });
    }
    expect(c.client.sent).toHaveLength(2);
    expect(c.sink.results[2].result).toMatchObject({ status: 'rate_limited', reason: 'max_per_day' });
  });

  it('batas per menit → menunggu slot (bukan menolak)', async () => {
    const c = await setup({ ratePerMin: 1, jitterMinMs: 2000, jitterMaxMs: 2000 });
    await c.p.handle({ payload: c.msg({}, '6281200000001', 'm1'), userId: BFF });
    await c.p.handle({ payload: c.msg({}, '6281200000002', 'm2'), userId: BFF });
    expect(c.client.sent).toHaveLength(2);
    // tunggu sisa jendela 60 dtk (dikurangi jitter yang sudah berlalu)
    expect(c.sleeps.some((s) => s > 50_000 && s <= 60_000)).toBe(true);
  });

  it('sesi banned → result banned + DLQ; terputus lama → disconnected + DLQ; pulih sebentar → kirim', async () => {
    const c = await setup();
    c.client.current = 'banned';
    expect(await c.p.handle({ payload: c.msg({}, TO, 'x1'), userId: BFF })).toBe('dlq');
    expect(c.sink.results[0].result.status).toBe('banned');
    c.client.current = 'logged_out';
    expect(await c.p.handle({ payload: c.msg({}, TO, 'x2'), userId: BFF })).toBe('dlq');
    expect(c.sink.results[1].result.status).toBe('disconnected');
    c.client.current = 'connecting';
    expect(await c.p.handle({ payload: c.msg({}, TO, 'x3'), userId: BFF })).toBe('dlq'); // > connectWaitMs
    expect(c.client.sent).toHaveLength(0);
    // pesan yang di-DLQ TIDAK ditandai diproses → bisa diputar ulang
    expect(c.state.isProcessed(String(c.msg({}, TO, 'x1').idempotency_key))).toBe(false);
  });

  it('nomor tidak terdaftar WhatsApp → failed/not_on_whatsapp; kirim gagal → failed/send_error (tidak diulang)', async () => {
    const c = await setup();
    c.client.notOnWa.add(TO);
    await c.p.handle({ payload: c.msg({}, TO, 'n1'), userId: BFF });
    expect(c.sink.results[0].result).toMatchObject({ status: 'failed', reason: 'not_on_whatsapp' });
    const c2 = await setup();
    c2.client.failSend = true;
    expect(await c2.p.handle({ payload: c2.msg(), userId: BFF })).toBe('ack');
    expect(c2.sink.results[0].result).toMatchObject({ status: 'failed', reason: 'send_error' });
    expect(c2.state.isProcessed(String(c2.msg().idempotency_key))).toBe(true);
  });

  it('STOP masuk → opt_out dipublikasikan + penekan lokal; pesan berikutnya ke nomor itu → failed/opted_out', async () => {
    const c = await setup();
    await c.p.handleInbound(TO, ' stop! ', isStopText);
    expect(c.sink.results[0].result).toMatchObject({ kind: 'opt_out', ref_idempotency_key: null, to_hash: toHash(c.hashKey, TO) });
    expect(c.sink.results[0].id).toMatch(/^waoptout:[0-9a-f]{64}:\d+$/);
    await c.p.handle({ payload: c.msg(), userId: BFF });
    expect(c.client.sent).toHaveLength(0);
    expect(c.sink.results[1].result).toMatchObject({ status: 'failed', reason: 'opted_out' });
  });

  it('STOP dari pengirim tak terpetakan (LID) → hanya metrik, tanpa publish; teks biasa diabaikan', async () => {
    const c = await setup();
    await c.p.handleInbound(null, 'STOP', isStopText);
    await c.p.handleInbound(TO, 'halo, stok ada?', isStopText);
    expect(c.sink.results).toHaveLength(0);
    expect(c.metrics.get('stop_unmapped')).toBe(1);
  });
});
