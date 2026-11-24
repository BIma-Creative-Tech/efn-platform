/**
 * Gerbang route handler `/api/prodplan/*` (di luar matcher proxy.ts; autentikasi sendiri seperti route `/api/*` lain,
 * docs/04 §30.12). Server-only. Urutan: flag modul -> cookie sesi sah -> peran PP (T9 + kode ESB + tanpa cabang) -> [mutasi:
 * same-origin ketat + Content-Type JSON] -> batas per pengguna. Gagal = JSON galat generik; tidak pernah memuat detail internal.
 */
import 'server-only';
import { API_CSP } from './csp';
import { authFromRequest } from './auth';
import { log } from './log';
import { isSameOriginMutation } from './origin-guard';
import { ppAccessDenial, ppCaps, type PpCaps } from './prodplan-access';
import { messageFor } from './prodplan-errors';
import { getConfig, getPpMutationLimiter, getPpReadLimiter } from './runtime';
import type { Session } from './session';

const BASE: Record<string, string> = {
  'Cache-Control': 'no-store',
  'X-Content-Type-Options': 'nosniff',
  'Referrer-Policy': 'no-referrer',
  'Content-Security-Policy': API_CSP,
  'Content-Type': 'application/json; charset=utf-8',
};

export function jsonResponse(status: number, body: unknown, extra: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), { status, headers: { ...BASE, ...extra } });
}

export function errorResponse(status: number, code: string, extra: Record<string, string> = {}, details?: unknown): Response {
  const error: Record<string, unknown> = { code, message: messageFor(code) };
  if (details !== undefined) error.details = details;
  return jsonResponse(status, { error }, extra);
}

export type GuardResult = { ok: true; session: Session; caps: PpCaps } | { ok: false; response: Response };

/** Batas ukuran badan mutasi (500 baris permintaan + margin). */
export const MAX_BODY_BYTES = 256 * 1024;

export async function guardApi(req: Request, kind: 'mutation' | 'read'): Promise<GuardResult> {
  const cfg = getConfig();
  if (!cfg.ppEnabled) return { ok: false, response: errorResponse(503, 'disabled') };
  const auth = authFromRequest(req);
  if (auth.status === 'disabled' || auth.status === 'misconfigured') return { ok: false, response: errorResponse(503, 'disabled') };
  if (auth.status !== 'ok') return { ok: false, response: errorResponse(401, 'unauthorized') };
  const session = auth.session;
  if (ppAccessDenial(session, cfg.ppEnabled) !== null) {
    log('warn', 'prodplan_api', { code: 'forbidden_role', sub: session.sub });
    return { ok: false, response: errorResponse(403, 'forbidden') };
  }
  if (kind === 'mutation') {
    if (!isSameOriginMutation(req.headers.get('sec-fetch-site'), req.headers.get('origin'), req.headers.get('host'))) {
      log('warn', 'prodplan_api', { code: 'forbidden_origin', sub: session.sub });
      return { ok: false, response: errorResponse(403, 'forbidden_origin') };
    }
    const ct = (req.headers.get('content-type') ?? '').toLowerCase();
    if (!ct.startsWith('application/json')) return { ok: false, response: errorResponse(400, 'bad_input') };
  }
  const limiter = kind === 'mutation' ? getPpMutationLimiter() : getPpReadLimiter();
  const rl = limiter.hit(String(session.sub), Date.now());
  if (!rl.allowed) {
    log('warn', 'prodplan_api', { code: 'rate_limited', sub: session.sub, retry_after: rl.retryAfterSec });
    return { ok: false, response: errorResponse(429, 'rate_limited', { 'Retry-After': String(rl.retryAfterSec) }) };
  }
  return { ok: true, session, caps: ppCaps(session.roles) };
}

/** Baca badan JSON objek dengan batas ukuran; null bila tidak sah. */
export async function readJsonBody(req: Request): Promise<Record<string, unknown> | null> {
  const len = Number(req.headers.get('content-length') ?? '0');
  if (Number.isFinite(len) && len > MAX_BODY_BYTES) return null;
  let text: string;
  try {
    text = await req.text();
  } catch {
    return null;
  }
  if (Buffer.byteLength(text, 'utf8') > MAX_BODY_BYTES) return null;
  try {
    const v: unknown = JSON.parse(text);
    return typeof v === 'object' && v !== null && !Array.isArray(v) ? (v as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}
