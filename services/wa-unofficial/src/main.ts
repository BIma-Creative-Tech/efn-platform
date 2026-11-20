import { createServer as createHttpServer, RequestListener, Server } from 'node:http';
import { createServer as createHttpsServer } from 'node:https';
import { createAdminHandler } from './admin-server';
import { AmqpLink } from './amqp';
import { BaileysWaClient } from './baileys/baileys-client';
import { ConfigError, decideStartup } from './config';
import type { ServerTls } from './config';
import { EncryptedDir } from './encrypted-files';
import { createLog, errMsg } from './log';
import { Metrics } from './metrics';
import { Processor } from './processor';
import { MinuteWindow, PersistentState } from './state';
import { isStopText } from './templates';

/**
 * Entrypoint wa-unofficial (ADR-044 Proposed, K18-01). Lihat README untuk gerbang start, env, dan secret.
 * Tidak ada akses PostgreSQL/Redis/Odoo/ClickHouse; satu-satunya I/O: RabbitMQ (2 queue), WhatsApp (lewat proxy
 * egress), volume sesi terenkripsi, dan server admin HTTPS.
 */
function listen(handler: RequestListener, tls: ServerTls | null, port: number): Server {
  const server = tls ? createHttpsServer({ cert: tls.cert, key: tls.key, minVersion: tls.minVersion }, handler) : createHttpServer(handler);
  server.headersTimeout = 10_000;
  server.requestTimeout = 30_000;
  server.listen(port);
  return server;
}

async function main(): Promise<void> {
  const boot = createLog('info');
  let decision: ReturnType<typeof decideStartup>;
  try {
    decision = decideStartup({ ...process.env });
  } catch (err) {
    boot.error('konfigurasi ditolak — service tidak dijalankan', { err: err instanceof ConfigError ? err.message : 'konfigurasi tidak valid' });
    process.exit(78); // EX_CONFIG
    return;
  }
  const metrics = new Metrics();

  if (decision.mode === 'idle') {
    const c = decision.config;
    boot.warn('mode IDLE: WhatsApp tidak resmi nonaktif (tidak konek RabbitMQ/WhatsApp)', { reason: c.reason });
    if (!c.serverTls) boot.warn('server admin berjalan TANPA TLS (EFN_ALLOW_PLAINTEXT, dev/test saja)');
    const server = listen(createAdminHandler({ mode: 'idle', client: null, adminTokenDigest: null, metricsTokenDigest: null, metrics, log: boot }), c.serverTls, c.port);
    const shutdown = (): void => {
      server.close(() => process.exit(0));
    };
    process.on('SIGTERM', shutdown);
    process.on('SIGINT', shutdown);
    return;
  }

  const cfg = decision.config;
  const log = createLog(cfg.logLevel);
  metrics.enabled = true;
  log.warn('WhatsApp tidak resmi AKTIF (ADR-044: hanya promo & notify-me; nomor terpisah)', {
    app_env: cfg.appEnv,
    risk_acceptance_ref: cfg.riskRef,
  });
  if (!cfg.serverTls) log.warn('server admin berjalan TANPA TLS (EFN_ALLOW_PLAINTEXT, dev/test saja)');

  const stateDir = new EncryptedDir(`${cfg.stateDir}/state`, cfg.sessionKey, 'state');
  await stateDir.init();
  const state = await PersistentState.load(stateDir, Date.now());
  metrics.dayCount = state.dayCount(Date.now());

  const client = new BaileysWaClient({ stateDir: cfg.stateDir, sessionKey: cfg.sessionKey, log, proxyUrl: cfg.proxyUrl });
  client.onStateChange((s) => {
    metrics.session = s;
  });

  let link: AmqpLink | null = null;
  const processor = new Processor({
    client,
    state,
    minute: new MinuteWindow(cfg.limits.ratePerMin),
    limits: cfg.limits,
    hashKey: cfg.hashKey,
    sink: {
      publish: (r, id) => {
        if (!link) return Promise.reject(new Error('RabbitMQ belum siap'));
        return link.publish(r, id);
      },
    },
    log,
    metrics,
    senderLabel: cfg.senderLabel,
  });
  client.onInboundText((from, text) => {
    processor.handleInbound(from, text, isStopText).catch((err: unknown) => log.error('pemrosesan STOP gagal', { err: errMsg(err) }));
  });

  const server = listen(
    createAdminHandler({
      mode: 'run',
      client,
      adminTokenDigest: cfg.adminTokenDigest,
      metricsTokenDigest: cfg.metricsTokenDigest,
      metrics,
      log,
      sentToday: () => state.dayCount(Date.now()),
      maxPerDay: cfg.limits.maxPerDay,
    }),
    cfg.serverTls,
    cfg.port,
  );

  try {
    await client.start();
  } catch (err) {
    // Library gagal dimuat/sesi rusak: service tetap hidup agar admin bisa logout/pairing ulang; pesan → DLQ (disconnected)
    log.error('klien WhatsApp gagal dimulai', { err: errMsg(err) });
  }
  link = new AmqpLink(cfg.amqp, log, (d) => processor.handle(d));
  await link.start();

  const shutdown = (): void => {
    log.info('berhenti (SIGTERM)');
    void (async () => {
      await link?.stop();
      await client.stop();
      await state.flush().catch(() => undefined);
      server.close(() => process.exit(0));
      setTimeout(() => process.exit(0), 5000).unref();
    })();
  };
  process.on('SIGTERM', shutdown);
  process.on('SIGINT', shutdown);
}

void main().catch((err: unknown) => {
  createLog('info').error('fatal', { err: errMsg(err) });
  process.exit(1);
});
