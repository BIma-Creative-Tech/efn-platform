/**
 * Gerbang route handler `/api/prodplan/*` (di luar matcher proxy.ts; autentikasi sendiri seperti route `/api/*` lain,
 * docs/04 §30.17.2). Server-only. Urutan: flag modul -> cookie `__Host-epp` berbentuk sah -> [mutasi: same-origin ketat +
 * Content-Type JSON] -> batas per `sha256(sid)`. Peran/cabang TIDAK diperiksa di sini: berasal dari sesi prodplan (otoritas)
 * dan ditegakkan di sana; route yang butuh peran memanggil `ppSessionView`. Cookie sesi Odoo (`__Host-eis`) tidak pernah dibaca.
 * Gagal = JSON galat generik; tidak pernah memuat detail internal.
 */
import 'server-only';
import { createHash } from 'node:crypto';
import { API_CSP } from './csp';
import { log } from './log';
import { isSameOriginMutation } from './origin-guard';
import type { PpResult } from './prodplan-client';
import { messageFor, publicStatus, type PpErrorBody } from './prodplan-errors';
import { ppClearCookie, ppSidFromHeader } from './prodplan-session';
import { getConfig, getPpMutationLimiter, getPpReadLimiter } from './runtime';

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

/** 401 `session_expired` + hapus cookie `__Host-epp` (UI mengalihkan/meminta masuk ulang). */
export function sessionExpiredResponse(): Response {
  return errorResponse(401, 'session_expired', { 'Set-Cookie': ppClearCookie() });
}

/** Galat hasil panggilan prodplan -> respons; `session_expired` selalu menghapus cookie. */
export function failResponse(res: Extract<PpResult<unknown>, { ok: false }> | { status: number; error: PpErrorBody }, retryAfter?: string): Response {
  if (res.error.code === 'session_expired') return sessionExpiredResponse();
  const extra: Record<string, string> = res.status === 429 && retryAfter ? { 'Retry-After': retryAfter } : {};
  return jsonResponse(publicStatus(res.status), { error: res.error }, extra);
}

export type GuardResult = { ok: true; sid: string } | { ok: false; response: Response };

/** Batas ukuran badan mutasi (500 baris permintaan + margin). */
export const MAX_BODY_BYTES = 256 * 1024;

/** Kunci batas laju: hash sid (sid mentah tidak disimpan di tabel limiter). */
export const sidKey = (sid: string): string => createHash('sha256').update(sid, 'utf8').digest('hex');

export async function guardApi(req: Request, kind: 'mutation' | 'read'): Promise<GuardResult> {
  const cfg = getConfig();
  if (!cfg.ppEnabled) return { ok: false, response: errorResponse(503, 'disabled') };
  const sid = ppSidFromHeader(req.headers.get('cookie'));
  if (!sid) return { ok: false, response: sessionExpiredResponse() };
  if (kind === 'mutation') {
    if (!isSameOriginMutation(req.headers.get('sec-fetch-site'), req.headers.get('origin'), req.headers.get('host'))) {
      log('warn', 'prodplan_api', { code: 'forbidden_origin' });
      return { ok: false, response: errorResponse(403, 'forbidden_origin') };
    }
    const ct = (req.headers.get('content-type') ?? '').toLowerCase();
    if (!ct.startsWith('application/json')) return { ok: false, response: errorResponse(400, 'bad_input') };
  }
  const limiter = kind === 'mutation' ? getPpMutationLimiter() : getPpReadLimiter();
  const rl = limiter.hit(sidKey(sid), Date.now());
  if (!rl.allowed) {
    log('warn', 'prodplan_api', { code: 'rate_limited', retry_after: rl.retryAfterSec });
    return { ok: false, response: errorResponse(429, 'rate_limited', { 'Retry-After': String(rl.retryAfterSec) }) };
  }
  return { ok: true, sid };
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
