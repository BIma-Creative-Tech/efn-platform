import { hostname, userInfo } from 'node:os';
import { AppConfig } from '../config/app-config';
import { PgDb } from '../common/db/db';
import { createPgPool } from '../common/db/pg.provider';
import { MetricsService } from '../common/metrics/metrics.service';
import { KvStore } from '../common/redis/kv-store';
import { Json2OdooOnlineClient, createJson2Transport } from '../odoo-rpc/json2-client';
import { MockOdooOnline } from '../odoo-rpc/odoo-online.mock';
import { OdooOnlineApi } from '../odoo-rpc/odoo-online.types';
import { OnlineMetrics } from '../odoo-rpc/online-metrics';
import { CsRole, OnlineAdminService, Operator } from '../orders/online-admin.service';
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
  /** W-18: query read-only (daftar kerja erasure CS). */
  db: PgDb;
  config: AppConfig;
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
  return { admin: new OnlineAdminService(repo, odoo, saga, metrics, config), db, config, close: () => pool.end() };
}

/**
 * SEC-98/SEC-107 (L-16, L-19c): identitas & PERAN operator dari wrapper host `efn-online-cs` (root, sudo): `EFN_CS_OPERATOR`
 * (= SUDO_USER), `EFN_CS_HOST`, `EFN_CS_ROLE` (`cs` | `approver`, dari grup efn-cs / efn-cs-approver). Tanpa operator +
 * peran → CLI MENOLAK jalan (tidak ada cadangan `node/<container>`), kecuali `APP_ENV=dev|test` (cadangan user OS,
 * peran `cs` kecuali `EFN_CS_ROLE` diisi). Format aktor `<user>/<host>`, karakter [A-Za-z0-9._-] (tanpa email).
 */
export function operatorIdentity(
  env: NodeJS.ProcessEnv = process.env,
  osUser: () => string = () => userInfo().username,
  host: () => string = hostname,
): Operator {
  const clean = (v: string): string => v.replace(/[^A-Za-z0-9._-]/g, '_').slice(0, 64);
  const devLike = env.APP_ENV === 'dev' || env.APP_ENV === 'test';
  const roleRaw = env.EFN_CS_ROLE;
  const role: CsRole | null = roleRaw === 'cs' || roleRaw === 'approver' ? roleRaw : null;
  if (!devLike && (!env.EFN_CS_OPERATOR || !role)) {
    throw new Error('identitas/peran operator tidak tersedia: jalankan lewat wrapper host efn-online-cs (EFN_CS_OPERATOR, EFN_CS_ROLE)');
  }
  const user = env.EFN_CS_OPERATOR || (devLike ? env.SUDO_USER || osUser() : '');
  const h = env.EFN_CS_HOST || host();
  if (!user || clean(user).length < 2) throw new Error('identitas operator tidak tersedia (jalankan lewat wrapper efn-online-cs)');
  return { actor: `${clean(user)}/${clean(h) || 'unknown'}`, role: role ?? 'cs' };
}

/**
 * SEC-108 (L-19e): alasan dibaca dari stdin (wrapper mengirimnya lewat `docker exec -i`, tidak di argv/log sudo).
 * Dibaca bila `--reason-stdin` diberikan, atau bila `--reason=` tidak ada dan stdin bukan terminal. Satu baris, ≤ 2 KB.
 */
export async function reasonFromStdin(
  argv: readonly string[],
  stdin: NodeJS.ReadableStream & { isTTY?: boolean } = process.stdin,
): Promise<string | undefined> {
  if (argv.some((a) => a.startsWith('--reason='))) return undefined;
  if (!argv.includes('--reason-stdin') && stdin.isTTY) return undefined;
  const chunks: Buffer[] = [];
  let size = 0;
  for await (const c of stdin) {
    const b = Buffer.isBuffer(c) ? c : Buffer.from(String(c));
    size += b.length;
    if (size > 2048) throw new Error('alasan dari stdin terlalu panjang');
    chunks.push(b);
  }
  const line = Buffer.concat(chunks).toString('utf8').split(/\r?\n/)[0]?.trim() ?? '';
  return line.length > 0 ? line : undefined;
}

/** Alasan: `--reason=` atau dari stdin (`reason-stdin` hanya penanda). */
export function reasonOf(f: Map<string, string>, stdinReason: string | undefined): string {
  const v = f.get('reason');
  if (v && v !== 'true') return v;
  if (stdinReason) return stdinReason;
  throw new Error('alasan wajib (--reason=… atau lewat stdin)');
}

/** Jalankan CLI: baca alasan stdin + identitas wrapper, parse (gagal → exit 2), lalu eksekusi. */
export async function cliMain<T>(
  name: string,
  argv: readonly string[],
  parse: (argv: readonly string[], op: Operator, stdinReason: string | undefined) => T,
  run: (cli: OnlineCli, args: T) => Promise<Record<string, unknown>>,
): Promise<void> {
  let args: T;
  try {
    const reason = await reasonFromStdin(argv);
    args = parse(argv, operatorIdentity(), reason);
  } catch (err) {
    process.stderr.write(`${err instanceof Error ? err.message : String(err)}\n`);
    process.exitCode = 2;
    return;
  }
  await runCli(name, (cli) => run(cli, args));
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
