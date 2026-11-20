import { randomBytes } from 'node:crypto';
import { AddressInfo } from 'node:net';
import { createServer, Server } from 'node:http';
import { createAdminHandler } from './admin-server';
import { tokenDigest } from './config';
import { Metrics } from './metrics';
import { FakeClient, captureLog } from './testing/fakes';

async function start(mode: 'idle' | 'run', withMetrics = false) {
  const token = randomBytes(32).toString('hex');
  const metricsToken = randomBytes(32).toString('hex');
  const client = new FakeClient();
  client.current = 'logged_out';
  const { log, lines } = captureLog();
  const server: Server = createServer(
    createAdminHandler({
      mode,
      client: mode === 'run' ? client : null,
      adminTokenDigest: mode === 'run' ? tokenDigest(token) : null,
      metricsTokenDigest: withMetrics ? tokenDigest(metricsToken) : null,
      metrics: new Metrics(),
      log,
      sentToday: () => 3,
      maxPerDay: 300,
    }),
  );
  await new Promise<void>((r) => server.listen(0, '127.0.0.1', () => r()));
  const base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  const call = (path: string, init: { method?: string; token?: string; body?: unknown } = {}) =>
    fetch(`${base}${path}`, {
      method: init.method ?? 'GET',
      headers: { ...(init.token ? { Authorization: `Bearer ${init.token}` } : {}), ...(init.body ? { 'Content-Type': 'application/json' } : {}) },
      body: init.body ? JSON.stringify(init.body) : undefined,
    });
  return { server, call, token, metricsToken, client, lines };
}

describe('server admin wa-unofficial (§28.9)', () => {
  let ctx: Awaited<ReturnType<typeof start>>;
  afterEach(async () => {
    await new Promise<void>((r) => ctx.server.close(() => r()));
  });

  it('/health tanpa auth: idle vs ok', async () => {
    ctx = await start('idle');
    expect(await (await ctx.call('/health')).json()).toEqual({ status: 'idle' });
    expect((await ctx.call('/v1/admin/session')).status).toBe(401);
  });

  it('endpoint admin wajib token; token salah → 401; status sesi tanpa nomor utuh; no-store', async () => {
    ctx = await start('run');
    expect((await ctx.call('/v1/admin/session')).status).toBe(401);
    expect((await ctx.call('/v1/admin/session', { token: 'salah'.repeat(10) })).status).toBe(401);
    const res = await ctx.call('/v1/admin/session', { token: ctx.token });
    expect(res.status).toBe(200);
    expect(res.headers.get('cache-control')).toBe('no-store');
    expect(await res.json()).toEqual({ status: 'disconnected', detail: 'logged_out', self: null, enabled: true, sent_today: 3, max_per_day: 300 });
  });

  it('pair QR / kode; nomor tidak valid → 400; sudah terhubung → 409; QR tidak masuk log', async () => {
    ctx = await start('run');
    const qr = await ctx.call('/v1/admin/session/pair', { method: 'POST', token: ctx.token, body: { method: 'qr' } });
    expect(await qr.json()).toEqual({ qr: 'QR-PAYLOAD' });
    const code = await ctx.call('/v1/admin/session/pair', { method: 'POST', token: ctx.token, body: { method: 'code', phone: '6281100000000' } });
    expect(await code.json()).toEqual({ code: 'ABCD-EFGH' });
    expect((await ctx.call('/v1/admin/session/pair', { method: 'POST', token: ctx.token, body: { method: 'code', phone: '+1 415' } })).status).toBe(400);
    expect((await ctx.call('/v1/admin/session/pair', { method: 'POST', token: ctx.token, body: { method: 'sms' } })).status).toBe(400);
    ctx.client.current = 'connected';
    expect((await ctx.call('/v1/admin/session/pair', { method: 'POST', token: ctx.token, body: { method: 'qr' } })).status).toBe(409);
    expect(ctx.lines.join('\n')).not.toContain('QR-PAYLOAD');
    expect(ctx.lines.join('\n')).not.toContain('ABCD-EFGH');
  });

  it('logout → status disconnected', async () => {
    ctx = await start('run');
    ctx.client.current = 'connected';
    const r = await ctx.call('/v1/admin/session/logout', { method: 'POST', token: ctx.token });
    expect(await r.json()).toEqual({ status: 'disconnected' });
  });

  it('gagal autentikasi berulang → 429 (bahkan token benar) dalam menit yang sama', async () => {
    ctx = await start('run');
    for (let i = 0; i < 10; i += 1) await ctx.call('/v1/admin/session', { token: `x${i}`.repeat(20) });
    expect((await ctx.call('/v1/admin/session', { token: ctx.token })).status).toBe(429);
  });

  it('/metrics: 404 bila token metrik tidak ada; token admin tidak berlaku untuk metrik', async () => {
    ctx = await start('run');
    expect((await ctx.call('/metrics', { token: ctx.token })).status).toBe(404);
    await new Promise<void>((r) => ctx.server.close(() => r()));
    ctx = await start('run', true);
    expect((await ctx.call('/metrics', { token: ctx.token })).status).toBe(401);
    const m = await ctx.call('/metrics', { token: ctx.metricsToken });
    expect(m.status).toBe(200);
    expect(await m.text()).toContain('efn_wa_unofficial_session_up 0');
  });
});
