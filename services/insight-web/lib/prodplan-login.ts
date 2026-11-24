/**
 * Handler `POST /api/prodplan/login` dan `POST /api/prodplan/logout` (docs/04 §30.17.2) sebagai fungsi atas Request/Response
 * agar bisa diuji. Server-only.
 *
 * Login, berurutan: flag -> same-origin ketat -> Content-Type form -> badan <= 4096 byte -> batas 10 / 15 menit per IP ->
 * validasi bentuk (gagal = `e=invalid`, prodplan TIDAK dipanggil) -> `POST /v1/session` {username,password,n} dengan `ppa2`
 * (sid="") -> 303 /produksi + `Set-Cookie __Host-epp`. Gagal -> 303 /produksi/masuk?e=<kode>. Selalu `Cache-Control: no-store`.
 *
 * Password: hanya ada di variabel lokal fungsi ini selama satu request. TIDAK pernah di-log, disimpan, dikembalikan ke
 * klien, dimasukkan ke URL/cookie/pesan galat. `n` = 16 byte acak per percobaan agar hash body (`bh` di asersi) bukan
 * hash password yang bisa ditebak offline.
 */
import { log } from './log';
import { isSameOriginMutation } from './origin-guard';
import { hmacSha256B64Url } from './hmac';
import { ppCall, type PpDeps } from './prodplan-client';
import { loginErrorFor, normalizeUsername, parseLoginResult, ppClearCookie, ppSetCookie, ppSidFromHeader, validPassword, PP_COOKIE_FALLBACK_SEC } from './prodplan-session';
import { newJti } from './prodplan-token';
import { clientIp, type RateLimiter } from './rate-limit';
import { readBodyLimited } from './sso-handler';

export const LOGIN_MAX_BODY = 4096;
/** SEC-236: >= waktu terburuk prodplan (antrean login + ESB); hanya untuk `POST /v1/session`. */
export const LOGIN_TIMEOUT_MS = 65_000;

/** SEC-234: 22 karakter pertama base64url(HMAC-SHA256(kunci asersi sbg byte ASCII hex, "ppc1|" + ip)). */
export function clientBinding(key: string, ip: string): string {
  return hmacSha256B64Url(key, `ppc1|${ip}`).slice(0, 22);
}

export interface LoginDeps {
  enabled: boolean;
  /** Dasar prodplan (sidecar) + kunci asersi; null = fitur nonaktif. */
  pp: Pick<PpDeps, 'baseUrl' | 'key' | 'fetchFn' | 'timeoutMs'>;
  limiter: RateLimiter;
  nowMs: () => number;
  /** Hanya untuk test; produksi memakai newJti() (16 byte acak base64url). */
  nonce?: () => string;
}

const HEADERS: Record<string, string> = {
  'Cache-Control': 'no-store',
  'X-Content-Type-Options': 'nosniff',
  'Referrer-Policy': 'no-referrer',
};

function see(location: string, extra: Record<string, string> = {}): Response {
  return new Response(null, { status: 303, headers: { ...HEADERS, Location: location, ...extra } });
}

const toLogin = (e: string): Response => see(`/produksi/masuk?e=${e}`);

function text(status: number, body: string): Response {
  return new Response(body, {
    status,
    headers: { ...HEADERS, 'Content-Type': 'text/plain; charset=utf-8', 'Content-Security-Policy': "default-src 'none'; frame-ancestors 'none'" },
  });
}

const originOk = (req: Request): boolean => isSameOriginMutation(req.headers.get('sec-fetch-site'), req.headers.get('origin'), req.headers.get('host'));

export async function handleLogin(req: Request, d: LoginDeps): Promise<Response> {
  const started = d.nowMs();
  const ms = (): number => d.nowMs() - started;

  if (!d.enabled) return see('/pp-belum-aktif');
  if (!originOk(req)) {
    log('warn', 'pp_login', { code: 'forbidden_origin', ms: ms() });
    return text(403, 'Permintaan ditolak');
  }
  const ctype = (req.headers.get('content-type') ?? '').toLowerCase();
  if (!ctype.startsWith('application/x-www-form-urlencoded')) return text(400, 'Permintaan tidak valid');
  const raw = await readBodyLimited(req, LOGIN_MAX_BODY);
  if (raw === null) return text(400, 'Permintaan tidak valid');

  const ip = clientIp(req.headers.get('x-forwarded-for'));
  const rl = d.limiter.hit(ip, d.nowMs());
  if (!rl.allowed) {
    log('warn', 'pp_login', { code: 'rate_limited', ms: ms() });
    return toLogin('rate');
  }

  const form = new URLSearchParams(raw);
  const username = normalizeUsername(form.get('username'));
  const password = form.get('password');
  if (username === null || !validPassword(password)) return toLogin('invalid');

  const nowMs = d.nowMs();
  // SEC-234: `c` mengikat percobaan ke IP klien (helper IP yang sama dengan batas laju); tidak di-log, tidak ke browser.
  const c = clientBinding(d.pp.key ?? '', ip);
  const res = await ppCall(
    { sid: '' },
    'POST',
    '/v1/session',
    { username, password, n: (d.nonce ?? newJti)(), c },
    { ...d.pp, now: () => nowMs, timeoutMs: d.pp.timeoutMs ?? LOGIN_TIMEOUT_MS },
  );
  if (!res.ok) {
    const e = loginErrorFor(res.error.code, res.status);
    log('info', 'pp_login', { code: e, status: res.status, ms: ms() });
    return toLogin(e);
  }
  const out = parseLoginResult(res.data);
  if (!out) {
    log('warn', 'pp_login', { code: 'bad_response', ms: ms() });
    return toLogin('unavailable');
  }
  const maxAge = out.expiresAtSec === null ? PP_COOKIE_FALLBACK_SEC : out.expiresAtSec - Math.floor(d.nowMs() / 1000);
  if (maxAge < 1) return toLogin('unavailable');
  log('info', 'pp_login', { code: 'ok', ms: ms() });
  return see('/produksi', { 'Set-Cookie': ppSetCookie(out.sid, maxAge) });
}

/**
 * SEC-236: tujuan redirect halaman server saat sesi dianggap habis. Bila cookie berbentuk sah, `DELETE /v1/session`
 * best-effort (galat diabaikan, timeout 3 dtk) agar token ESB tidak tertinggal di prodplan; lalu hapus cookie.
 */
export async function handleExpired(req: Request, d: Pick<LoginDeps, 'enabled' | 'pp'>): Promise<Response> {
  const sid = ppSidFromHeader(req.headers.get('cookie'));
  if (d.enabled && sid) {
    try {
      await ppCall({ sid }, 'DELETE', '/v1/session', undefined, { ...d.pp, timeoutMs: d.pp.timeoutMs ?? 3000 });
    } catch {
      /* abaikan */
    }
  }
  return see('/produksi/masuk?e=expired', { 'Set-Cookie': ppClearCookie() });
}

/** Keluar: `DELETE /v1/session` (galat diabaikan) lalu hapus cookie dan kembali ke halaman masuk. */
export async function handleLogout(req: Request, d: Pick<LoginDeps, 'enabled' | 'pp'>): Promise<Response> {
  if (!originOk(req)) return text(403, 'Permintaan ditolak');
  const sid = ppSidFromHeader(req.headers.get('cookie'));
  if (d.enabled && sid) {
    try {
      await ppCall({ sid }, 'DELETE', '/v1/session', undefined, d.pp);
    } catch {
      /* abaikan: cookie tetap dihapus */
    }
  }
  return see('/produksi/masuk', { 'Set-Cookie': ppClearCookie() });
}
