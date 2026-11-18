/**
 * Klien ClickHouse HTTPS (server-only). Satu-satunya jalur data: katalog query bernama (lib/queries.ts) dengan
 * parameter bertipe + setting cakupan SQL_efn_* yang dihitung dari SESI terverifikasi (docs/04 §26.7).
 * - User `efn_insight` (read-only, profil insight_readonly), DB `efn_insight`. Kredensial dari file secret.
 * - Koneksi TLS divalidasi CA internal lewat NODE_EXTRA_CA_CERTS (diperiksa saat boot). Tanpa fallback plaintext.
 * - Galat dikembalikan sebagai ChError generik + referensi korelasi; SQL/host/baris galat ClickHouse tidak bocor ke UI.
 */
import 'server-only';
import { buildChSearchParams, ParamError, type ParamBag } from './ch-params';
import { log, newRef } from './log';
import type { QueryName } from './queries';
import { QUERIES } from './queries';
import { getChPassword, getConfig, getKey, getPageQueryLimiter } from './runtime';
import { scopeSettings } from './scope';
import { Semaphore, concurrencyFromEnv } from './semaphore';
import type { Session } from './session';
import { exportConcurrencyFromEnv } from './user-limits';

export class ChError extends Error {
  constructor(
    public readonly ref: string,
    public readonly kind: 'config' | 'param' | 'http' | 'timeout' | 'network' | 'parse' | 'rate_limited',
  ) {
    super(`ClickHouse query gagal (${kind})`);
    this.name = 'ChError';
  }
}

const TIMEOUT_MS = 35_000;
/** Lama maksimum menunggu slot antrean sebelum query dianggap timeout. */
const QUEUE_WAIT_MS = 20_000;
// Dua semaphore per proses (replika = 1, L-124) membatasi query bersamaan ke user ClickHouse bersama `efn_insight`.
// SEC-189: ekspor CSV punya semaphore TERPISAH (3 slot, antrean 10) agar tidak memakan slot halaman (12).
const pools = {
  page: new Semaphore(concurrencyFromEnv(process.env.INSIGHT_CH_MAX_CONCURRENCY)),
  export: new Semaphore(exportConcurrencyFromEnv(process.env.INSIGHT_CH_EXPORT_CONCURRENCY), 10),
};

export type ChPool = keyof typeof pools;

/**
 * `pool: 'page'` (default): dihitung terhadap batas per pengguna (300 query / 5 menit per `sub`) dan memakai semaphore halaman.
 * `pool: 'export'`: semaphore ekspor; batas per pengguna untuk ekspor diterapkan di route handler (5 / 10 menit).
 */
export async function chQuery<T>(
  name: QueryName,
  session: Pick<Session, 'cids' | 'codes' | 'sub'>,
  bag: ParamBag,
  pool: ChPool = 'page',
): Promise<T[]> {
  const ref = newRef();
  const limiter = pools[pool];
  const cfg = getConfig();
  const scopeKey = getKey('scope');
  const password = getChPassword();
  if (!cfg.enabled || !cfg.chUrl || cfg.problems.length > 0 || !scopeKey || !password) {
    log('error', 'ch_query', { q: name, ref, kind: 'config' });
    throw new ChError(ref, 'config');
  }

  if (pool === 'page') {
    const rl = getPageQueryLimiter().hit(String(session.sub), Date.now());
    if (!rl.allowed) {
      log('warn', 'ch_query', { q: name, ref, kind: 'rate_limited', sub: session.sub, retry_after: rl.retryAfterSec });
      throw new ChError(ref, 'rate_limited');
    }
  }

  let url: string;
  try {
    const sp = buildChSearchParams(name, scopeSettings(session.cids, session.codes, scopeKey), bag);
    url = `${cfg.chUrl}/?${sp.toString()}`;
  } catch (err) {
    log('error', 'ch_query', { q: name, ref, kind: 'param', msg: err instanceof ParamError ? err.message : 'scope' });
    throw new ChError(ref, 'param');
  }

  let release: () => void;
  try {
    release = await limiter.acquire(QUEUE_WAIT_MS);
  } catch {
    log('error', 'ch_query', { q: name, ref, kind: 'timeout', queued: limiter.waiting });
    throw new ChError(ref, 'timeout');
  }

  const started = Date.now();
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), TIMEOUT_MS);
  try {
    const res = await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'text/plain; charset=utf-8',
        'X-ClickHouse-User': cfg.chUser,
        'X-ClickHouse-Key': password,
      },
      body: QUERIES[name].sql,
      signal: ctrl.signal,
      cache: 'no-store',
    });
    const text = await res.text();
    if (!res.ok) {
      const code = /Code:\s*(\d+)/.exec(text)?.[1] ?? '';
      log('error', 'ch_query', { q: name, ref, kind: 'http', status: res.status, ch_code: code, ms: Date.now() - started });
      throw new ChError(ref, 'http');
    }
    let parsed: unknown;
    try {
      parsed = JSON.parse(text);
    } catch {
      log('error', 'ch_query', { q: name, ref, kind: 'parse' });
      throw new ChError(ref, 'parse');
    }
    const data = (parsed as { data?: unknown }).data;
    if (!Array.isArray(data)) {
      log('error', 'ch_query', { q: name, ref, kind: 'parse' });
      throw new ChError(ref, 'parse');
    }
    log('info', 'ch_query', { q: name, rows: data.length, ms: Date.now() - started });
    return data as T[];
  } catch (err) {
    if (err instanceof ChError) throw err;
    const aborted = err instanceof Error && err.name === 'AbortError';
    log('error', 'ch_query', { q: name, ref, kind: aborted ? 'timeout' : 'network' });
    throw new ChError(ref, aborted ? 'timeout' : 'network');
  } finally {
    clearTimeout(timer);
    release();
  }
}
