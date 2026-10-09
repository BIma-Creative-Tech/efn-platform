import { AppConfig } from '../config/app-config';
import { PgDb } from '../common/db/db';
import { createPgPool } from '../common/db/pg.provider';
import { MetricsService } from '../common/metrics/metrics.service';
import { KvStore } from '../common/redis/kv-store';
import { Json2OdooOnlineClient, createJson2Transport } from '../odoo-rpc/json2-client';
import { MockOdooOnline } from '../odoo-rpc/odoo-online.mock';
import { OdooOnlineApi } from '../odoo-rpc/odoo-online.types';
import { OnlineMetrics } from '../odoo-rpc/online-metrics';
import { OnlineAdminService } from '../orders/online-admin.service';
import { PgOnlineRepo } from '../orders/online.repository';
import { OnlineSagaService } from '../orders/online-saga.service';
import { createPaymentGateway } from '../payments/payment-gateway.factory';
import { PaymentsService } from '../payments/payments.service';

/**
 * Konteks bersama CLI CS online (L-3): config & secret persis seperti BFF (`*_FILE`, TLS verify-full PostgreSQL, mTLS
 * ke odoo-rpc-gateway), user DB `efn_bff`. Tidak butuh RabbitMQ/Redis: pesan saga ditulis ke `platform.outbox` dan
 * dikirim relay BFF yang berjalan. Output CLI hanya id/status/nominal (tanpa PII).
 */
export interface OnlineCli {
  admin: OnlineAdminService;
  close(): Promise<void>;
}

const NO_REDIS: KvStore = new Proxy({} as KvStore, {
  get: () => () => Promise.reject(new Error('Redis tidak tersedia di CLI')),
});

export function createOnlineCli(): OnlineCli {
  const config = AppConfig.fromProcessEnv();
  const pool = createPgPool(config);
  const db = new PgDb(pool);
  const metrics = new OnlineMetrics(new MetricsService());
  const odoo: OdooOnlineApi = config.odooOnlineMock ? new MockOdooOnline() : new Json2OdooOnlineClient(config, createJson2Transport(config), metrics);
  let gw: ReturnType<typeof createPaymentGateway> | undefined;
  const payments = new PaymentsService(config, db, NO_REDIS, { get: () => (gw === undefined ? (gw = createPaymentGateway(config)) : gw) });
  const repo = new PgOnlineRepo(db, db);
  const saga = new OnlineSagaService(repo, odoo, payments, metrics);
  return { admin: new OnlineAdminService(repo, odoo, saga, metrics), close: () => pool.end() };
}

/** Parser argumen `--nama=nilai` / `--flag` (tanpa nilai bebas yang tidak dikenal). */
export function parseFlags(argv: readonly string[], allowed: readonly string[]): Map<string, string> {
  const out = new Map<string, string>();
  for (const a of argv) {
    const m = /^--([a-z-]+)(?:=(.*))?$/s.exec(a);
    if (!m || !allowed.includes(m[1])) throw new Error(`argumen tidak dikenal: ${a.split('=')[0]}`);
    out.set(m[1], m[2] ?? 'true');
  }
  return out;
}

export function required(f: Map<string, string>, k: string): string {
  const v = f.get(k);
  if (!v || v === 'true') throw new Error(`--${k} wajib diisi`);
  return v;
}

export async function runCli(name: string, fn: (cli: OnlineCli) => Promise<Record<string, unknown>>): Promise<void> {
  let cli: OnlineCli | null = null;
  try {
    cli = createOnlineCli();
    const out = await fn(cli);
    process.stdout.write(`${name}: ${JSON.stringify(out)}\n`);
    process.exitCode = 0;
  } catch (err) {
    process.stderr.write(`${name} gagal: ${err instanceof Error ? err.message : String(err)}\n`);
    process.exitCode = 1;
  } finally {
    await cli?.close().catch(() => undefined);
  }
}
