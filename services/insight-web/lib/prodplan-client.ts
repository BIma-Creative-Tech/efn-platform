/**
 * Satu-satunya pemanggil API prodplan (docs/04 §30.1, §30.12; ADR-051 §2.3; ADR-041 §3.7). Server-only.
 *
 * - Tujuan: sidecar ghostunnel client di loopback (`http://127.0.0.1:7443`, lib/config.ts parsePpUrl). TLS/mTLS ke
 *   `prodplan-tls` dipegang sidecar; kunci TLS tidak ada di proses Node.
 * - Setiap request membawa `X-EFN-Actor: ppa1...` yang dibuat dari SESI TERVERIFIKASI (bukan input browser), terikat
 *   method + path(+query) + hash body, umur 60 detik (lib/prodplan-token.ts).
 * - Timeout 15 detik (§30.12). Galat dipetakan ke pesan Indonesia tanpa detail internal (lib/prodplan-errors.ts).
 * - Tidak mengikuti redirect. Tidak mencatat body, header aktor, atau query ke log.
 */
import 'server-only';
import { log } from './log';
import { getConfig, getKey } from './runtime';
import { mapPpError, type PpErrorBody } from './prodplan-errors';
import { ACTOR_HEADER, ActorError, isValidActorPath, signActor, type ActorSession } from './prodplan-token';

export const PP_TIMEOUT_MS = 15_000;
/** Batas ukuran respons yang dibaca (rencana besar + pratinjau): 8 MB. */
export const PP_MAX_RESPONSE_BYTES = 8 * 1024 * 1024;

export type PpResult<T> = { ok: true; status: number; data: T } | { ok: false; status: number; error: PpErrorBody };

export interface PpDeps {
  /** URL dasar sidecar; null = fitur nonaktif. */
  baseUrl: string | null;
  /** prodplan_assert_key (hex >= 64); null = tidak terbaca. */
  key: string | null;
  fetchFn?: typeof fetch;
  now?: () => number;
  timeoutMs?: number;
}

const localError = (status: number, code: string): PpResult<never> => ({ ok: false, status, error: mapPpError(status, { error: { code } }) });

/** Inti tanpa akses runtime (dapat diuji dengan fetch palsu). */
export async function ppCall(
  session: ActorSession,
  method: 'GET' | 'POST' | 'PUT',
  path: string,
  body: Record<string, unknown> | undefined,
  deps: PpDeps,
): Promise<PpResult<unknown>> {
  if (!deps.baseUrl) return localError(503, 'disabled');
  if (!deps.key) {
    log('error', 'prodplan_client', { code: 'key_missing', sub: session.sub });
    return localError(503, 'disabled');
  }
  if (!isValidActorPath(path)) return localError(400, 'bad_input');

  const bodyStr = body === undefined ? '' : JSON.stringify(body);
  const nowMs = (deps.now ?? Date.now)();
  let actor: string;
  try {
    actor = signActor({ session, method, path, body: bodyStr, key: deps.key, nowSec: Math.floor(nowMs / 1000) });
  } catch (e) {
    // Sesi tanpa peran PP / tanpa kode ESB: tidak pernah boleh sampai sini (lapis sebelumnya menolak); fail closed.
    log('warn', 'prodplan_client', { code: e instanceof ActorError ? e.reason : 'actor_error', sub: session.sub });
    return localError(403, 'forbidden');
  }

  const headers: Record<string, string> = { Accept: 'application/json', [ACTOR_HEADER]: actor };
  if (bodyStr !== '') headers['Content-Type'] = 'application/json';

  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), deps.timeoutMs ?? PP_TIMEOUT_MS);
  const t0 = Date.now();
  try {
    const res = await (deps.fetchFn ?? fetch)(`${deps.baseUrl}${path}`, {
      method,
      headers,
      body: bodyStr === '' ? undefined : bodyStr,
      signal: ctl.signal,
      cache: 'no-store',
      redirect: 'error',
    });
    const text = await readLimited(res);
    let parsed: unknown = null;
    if (text !== null && text !== '') {
      try {
        parsed = JSON.parse(text);
      } catch {
        parsed = undefined;
      }
    }
    const ms = Date.now() - t0;
    if (text === null) {
      log('warn', 'prodplan_client', { code: 'response_too_large', status: res.status, sub: session.sub, ms });
      return localError(502, 'bad_response');
    }
    if (res.ok) {
      if (parsed === undefined) {
        log('warn', 'prodplan_client', { code: 'bad_json', status: res.status, sub: session.sub, ms });
        return localError(502, 'bad_response');
      }
      return { ok: true, status: res.status, data: parsed };
    }
    const error = mapPpError(res.status, parsed);
    log('info', 'prodplan_client', { method, status: res.status, code: error.code, sub: session.sub, ms });
    return { ok: false, status: res.status, error };
  } catch (e) {
    const aborted = e instanceof Error && e.name === 'AbortError';
    log('warn', 'prodplan_client', { method, code: aborted ? 'timeout' : 'unreachable', sub: session.sub, ms: Date.now() - t0 });
    return localError(aborted ? 504 : 502, aborted ? 'timeout' : 'unreachable');
  } finally {
    clearTimeout(timer);
  }
}

/** Baca badan maksimum PP_MAX_RESPONSE_BYTES; null bila melebihi batas. */
async function readLimited(res: Response): Promise<string | null> {
  const len = Number(res.headers.get('content-length') ?? '0');
  if (Number.isFinite(len) && len > PP_MAX_RESPONSE_BYTES) return null;
  const text = await res.text();
  return Buffer.byteLength(text, 'utf8') > PP_MAX_RESPONSE_BYTES ? null : text;
}

/** Pemanggil bertenaga runtime: baca konfigurasi + kunci dari file secret. */
export function ppRequest(
  session: ActorSession,
  method: 'GET' | 'POST' | 'PUT',
  path: string,
  body?: Record<string, unknown>,
): Promise<PpResult<unknown>> {
  const cfg = getConfig();
  return ppCall(session, method, path, body, { baseUrl: cfg.ppEnabled ? cfg.ppUrl : null, key: getKey('ppassert') });
}

/** Pembuat query string aman (nilai kosong dibuang) untuk GET daftar. */
export function ppQuery(params: Record<string, string | number | undefined>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && String(v) !== '') q.set(k, String(v));
  }
  const s = q.toString();
  return s === '' ? '' : `?${s}`;
}
