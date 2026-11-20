/**
 * Klien ClickHouse HTTPS (server-only). Satu-satunya jalur data: katalog query bernama (lib/queries.ts untuk ESB,
 * lib/queries-sm.ts untuk supermarket) dengan parameter bertipe + setting cakupan SQL_efn_* yang dihitung dari SESI
 * terverifikasi (docs/04 §26.7, §28.13.6).
 * - User `efn_insight` (read-only, profil insight_readonly), DB `efn_insight`. Kredensial dari file secret.
 * - Koneksi TLS divalidasi CA internal lewat NODE_EXTRA_CA_CERTS (diperiksa saat boot). Tanpa fallback plaintext.
 * - Galat dikembalikan sebagai ChError generik + referensi korelasi; SQL/host/baris galat ClickHouse tidak bocor ke UI.
 * - Query ESB hanya dikirim bila sesi punya kode ESB (codes ∩ ESB_CODES); query SM hanya bila INSIGHT_SM_ENABLED=true dan
 *   sesi eis2 membawa persona + wids (G47).
 */
import 'server-only';
import { buildChSearchParams, ParamError, type ParamBag } from './ch-params';
import { buildSmSearchParams, type SmParamBag } from './ch-params-sm';
import { esbCodesOf } from './claims';
import { log, newRef } from './log';
import type { QueryName } from './queries';
import { QUERIES } from './queries';
import { SM_QUERIES, type SmQueryName } from './queries-sm';
import { getChPassword, getConfig, getKey, getPageQueryLimiter } from './runtime';
import { scopeSettings, scopeSettingsSm } from './scope';
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
 * Inti eksekusi bersama (ESB dan SM): konfigurasi -> batas per pengguna -> URL bercakupan -> semaphore -> POST.
 * `label` hanya nama katalog (untuk log); `sql` konstanta katalog; `buildQuery` melempar ParamError bila parameter/cakupan tak sah.
 */
async function execute<T>(
  label: string,
  sql: string,
  buildQuery: (scopeKey: string) => URLSearchParams,
  session: Pick<Session, 'sub'>,
  pool: ChPool,
  extraGate: () => boolean,
): Promise<T[]> {
  const ref = newRef();
  const limiter = pools[pool];
  const cfg = getConfig();
  const scopeKey = getKey('scope');
  const password = getChPassword();
  if (!cfg.enabled || !cfg.chUrl || cfg.problems.length > 0 || !scopeKey || !password || !extraGate()) {
    log('error', 'ch_query', { q: label, ref, kind: 'config' });
    throw new ChError(ref, 'config');
  }

  if (pool === 'page') {
    const rl = getPageQueryLimiter().hit(String(session.sub), Date.now());
    if (!rl.allowed) {
      log('warn', 'ch_query', { q: label, ref, kind: 'rate_limited', sub: session.sub, retry_after: rl.retryAfterSec });
      throw new ChError(ref, 'rate_limited');
    }
  }

  let url: string;
  try {
    url = `${cfg.chUrl}/?${buildQuery(scopeKey).toString()}`;
  } catch (err) {
    log('error', 'ch_query', { q: label, ref, kind: 'param', msg: err instanceof ParamError ? err.message : 'scope' });
    throw new ChError(ref, 'param');
  }

  let release: () => void;
  try {
    release = await limiter.acquire(QUEUE_WAIT_MS);
  } catch {
    log('error', 'ch_query', { q: label, ref, kind: 'timeout', queued: limiter.waiting });
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
      body: sql,
      signal: ctrl.signal,
      cache: 'no-store',
    });
    const text = await res.text();
    if (!res.ok) {
      const code = /Code:\s*(\d+)/.exec(text)?.[1] ?? '';
      log('error', 'ch_query', { q: label, ref, kind: 'http', status: res.status, ch_code: code, ms: Date.now() - started });
      throw new ChError(ref, 'http');
    }
    let parsed: unknown;
    try {
      parsed = JSON.parse(text);
    } catch {
      log('error', 'ch_query', { q: label, ref, kind: 'parse' });
      throw new ChError(ref, 'parse');
    }
    const data = (parsed as { data?: unknown }).data;
    if (!Array.isArray(data)) {
      log('error', 'ch_query', { q: label, ref, kind: 'parse' });
      throw new ChError(ref, 'parse');
    }
    log('info', 'ch_query', { q: label, rows: data.length, ms: Date.now() - started });
    return data as T[];
  } catch (err) {
    if (err instanceof ChError) throw err;
    const aborted = err instanceof Error && err.name === 'AbortError';
    log('error', 'ch_query', { q: label, ref, kind: aborted ? 'timeout' : 'network' });
    throw new ChError(ref, aborted ? 'timeout' : 'network');
  } finally {
    clearTimeout(timer);
    release();
  }
}

/**
 * Query ESB. `pool: 'page'` (default): dihitung terhadap batas per pengguna (300 query / 5 menit per `sub`) dan memakai
 * semaphore halaman. `pool: 'export'`: semaphore ekspor; batas per pengguna untuk ekspor diterapkan di route handler (5 / 10 menit).
 * Sesi tanpa kode ESB (mis. kepala toko SMD) tidak pernah mengirim query ESB (kind 'param').
 */
export async function chQuery<T>(
  name: QueryName,
  session: Pick<Session, 'cids' | 'codes' | 'sub'>,
  bag: ParamBag,
  pool: ChPool = 'page',
): Promise<T[]> {
  return execute<T>(
    name,
    QUERIES[name].sql,
    (scopeKey) => {
      const esb = esbCodesOf(session.codes);
      if (esb.length === 0) throw new ParamError('sesi tanpa kode ESB');
      return buildChSearchParams(name, scopeSettings(session.cids, esb, scopeKey), bag);
    },
    session,
    pool,
    () => true,
  );
}

/** Query supermarket (docs/04 §28.13.6): setting SQL_efn_wids/personas/cats/sig2 dari sesi eis2; kill switch INSIGHT_SM_ENABLED. */
export async function chQuerySm<T>(
  name: SmQueryName,
  session: Pick<Session, 'cids' | 'codes' | 'roles' | 'wids' | 'cats' | 'sub'>,
  bag: SmParamBag,
  pool: ChPool = 'page',
): Promise<T[]> {
  return execute<T>(
    name,
    SM_QUERIES[name].sql,
    (scopeKey) => buildSmSearchParams(name, scopeSettingsSm(session, scopeKey), bag),
    session,
    pool,
    () => getConfig().smEnabled === true,
  );
}
