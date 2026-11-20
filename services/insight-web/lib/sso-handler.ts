/**
 * Handler `POST /sso` (docs/04 §26.4, ADR-041) sebagai fungsi murni atas Request/Response agar bisa diuji.
 * Urutan: kill switch (404) -> konfigurasi (503) -> Origin C1 (401) -> rate limit (429) -> content type/ukuran (400)
 * -> field `t` -> verifikasi token (401 generik) -> simpan jti -> 303 + Set-Cookie `__Host-eis`.
 * Log hanya kode hasil, sub, jti (setelah tanda tangan sah), durasi. Tidak pernah token/cookie.
 */
import { API_CSP, STATIC_PAGE_CSP } from './csp';
import { isOriginAllowed } from './origin-guard';
import type { JtiCache } from './jti-cache';
import { log } from './log';
import { clientIp, type RateLimiter } from './rate-limit';
import { createSessionValue, setCookieHeader } from './session';
import { verifyAnySsoToken } from './token';

export const SSO_MAX_BODY = 4096;

export interface SsoDeps {
  enabled: boolean;
  odooOrigin: string | null;
  odooOpenUrl: string | null;
  ssoKey: string | null;
  sessionKey: string | null;
  /** INSIGHT_ACCEPT_EIT1; tidak diisi = true. */
  acceptEit1?: boolean;
  nowMs: () => number;
  jti: JtiCache;
  limiter: RateLimiter;
}

const BASE_HEADERS: Record<string, string> = {
  'Cache-Control': 'no-store',
  'X-Content-Type-Options': 'nosniff',
  'Referrer-Policy': 'no-referrer',
};

function esc(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function plain(status: number, text: string, extra: Record<string, string> = {}): Response {
  return new Response(text, {
    status,
    headers: { ...BASE_HEADERS, 'Content-Type': 'text/plain; charset=utf-8', 'Content-Security-Policy': API_CSP, ...extra },
  });
}

function errorPage(status: number, title: string, message: string, openUrl: string | null): Response {
  const link = openUrl ? `<p><a href="${esc(openUrl)}">Buka lagi dari Odoo</a></p>` : '';
  const html =
    `<!doctype html><html lang="id"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">` +
    `<meta name="robots" content="noindex"><title>${esc(title)} - EFN Insight</title>` +
    `<style>body{font:16px/1.5 system-ui,sans-serif;max-width:32rem;margin:15vh auto;padding:0 1rem;color:#10201e}` +
    `a{color:#0a5c54}</style></head><body><h1>${esc(title)}</h1><p>${esc(message)}</p>${link}</body></html>`;
  return new Response(html, {
    status,
    headers: { ...BASE_HEADERS, 'Content-Type': 'text/html; charset=utf-8', 'Content-Security-Policy': STATIC_PAGE_CSP },
  });
}

async function readBodyLimited(req: Request, max: number): Promise<string | null> {
  const len = req.headers.get('content-length');
  if (len !== null && Number(len) > max) return null;
  if (!req.body) return '';
  const reader = req.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    total += value.byteLength;
    if (total > max) {
      await reader.cancel();
      return null;
    }
    chunks.push(value);
  }
  return Buffer.concat(chunks).toString('utf8');
}

export async function handleSso(req: Request, d: SsoDeps): Promise<Response> {
  const started = d.nowMs();
  const ms = () => d.nowMs() - started;

  if (!d.enabled) return plain(404, 'Not found');
  if (!d.ssoKey || !d.sessionKey || !d.odooOrigin) {
    log('error', 'sso', { code: 'misconfigured', ms: ms() });
    return errorPage(503, 'Insight belum dikonfigurasi', 'Hubungi administrator.', d.odooOpenUrl);
  }

  const generic = () =>
    errorPage(401, 'Tautan masuk tidak valid atau sudah dipakai', 'Silakan buka kembali dari Odoo.', d.odooOpenUrl);

  // C1: login CSRF / tukar sesi. Dicek SEBELUM rate limit: permintaan lintas situs/tanpa Origin sah tidak boleh
  // menghabiskan jatah login milik IP yang sama (QA risiko 1).
  if (!isOriginAllowed(req.headers.get('origin'), d.odooOrigin)) {
    log('warn', 'sso', { code: 'bad_origin', ms: ms() });
    return generic();
  }

  const rl = d.limiter.hit(clientIp(req.headers.get('x-forwarded-for')), d.nowMs());
  if (!rl.allowed) {
    log('warn', 'sso', { code: 'rate_limited', ms: ms() });
    return plain(429, 'Terlalu banyak permintaan. Coba lagi sebentar.', { 'Retry-After': String(rl.retryAfterSec) });
  }

  const ctype = (req.headers.get('content-type') ?? '').toLowerCase();
  if (!ctype.startsWith('application/x-www-form-urlencoded')) {
    log('warn', 'sso', { code: 'bad_request', ms: ms() });
    return plain(400, 'Permintaan tidak valid');
  }
  const body = await readBodyLimited(req, SSO_MAX_BODY);
  if (body === null) {
    log('warn', 'sso', { code: 'bad_request', ms: ms() });
    return plain(400, 'Permintaan tidak valid');
  }
  const form = new URLSearchParams(body);
  const keys = Array.from(form.keys());
  if (keys.length !== 1 || keys[0] !== 't') {
    log('warn', 'sso', { code: 'bad_request', ms: ms() });
    return plain(400, 'Permintaan tidak valid');
  }

  const nowSec = Math.floor(d.nowMs() / 1000);
  // eit2 selalu; eit1 hanya bila INSIGHT_ACCEPT_EIT1 (default true agar urutan deploy aman, §28.13.5).
  const res = verifyAnySsoToken(form.get('t'), d.ssoKey, nowSec, (jti) => d.jti.has(jti, d.nowMs()), d.acceptEit1 ?? true);
  if (!res.ok) {
    log('warn', 'sso', { code: res.code, ms: ms() });
    return generic();
  }

  d.jti.add(res.payload.jti, d.nowMs());
  const cookie = setCookieHeader(createSessionValue(res.payload, d.sessionKey, nowSec));
  // Log: versi + peran (tanpa wids/cats, tanpa token/nama; §28.13.5). Peran bukan data pribadi.
  log('info', 'sso', { code: 'ok', sub: res.payload.sub, jti: res.payload.jti, v: res.payload.v, roles: res.payload.roles.join(','), ms: ms() });
  return new Response(null, {
    status: 303,
    headers: { ...BASE_HEADERS, Location: '/', 'Set-Cookie': cookie },
  });
}
