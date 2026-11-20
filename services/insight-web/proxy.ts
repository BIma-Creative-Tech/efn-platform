/**
 * Proxy (Next.js 16, runtime Node): untuk setiap halaman
 *  1. membuat nonce + CSP ketat (lib/csp.ts) dan meneruskannya ke Server Components lewat header permintaan;
 *  2. gerbang sesi: kill switch / kunci hilang -> /belum-aktif; cookie hilang/invalid/kedaluwarsa -> /sesi-berakhir
 *     (cookie dihapus bila ada). Halaman dan route data tetap memverifikasi ulang sendiri (lapis kedua).
 * Route handler (/api/*, /sso, /logout) dikecualikan: mereka memasang header & autentikasi sendiri.
 * Gerbang memakai rewrite internal (bukan redirect): host di belakang sidecar/Traefik tidak dipercaya untuk URL absolut.
 */
import { NextResponse, type NextRequest } from 'next/server';
import { buildCsp } from './lib/csp';
import { authenticate } from './lib/gate';
import { getConfig, getKey } from './lib/runtime';
import { SESSION_COOKIE, clearCookieHeader } from './lib/session';
import { DENIAL_PATH, smRouteDenial } from './lib/sm-personas';

const PUBLIC_PATHS = new Set(['/belum-aktif', '/sesi-berakhir', '/keluar', '/sm-belum-aktif', '/tidak-berwenang', '/buka-ulang']);

// Rewrite internal ke halaman publik (URL di browser tetap). Redirect dengan Location relatif membuat proxy Next 16
// melempar `TypeError: Invalid URL` (500, ditemukan uji E2E G42); URL absolut dari request.url akan memuat host
// internal di belakang sidecar TLS. Rewrite tidak mengirim Location sama sekali.
function rewriteTo(
  request: NextRequest,
  path: string,
  csp: string,
  nonce: string,
  clearCookie: boolean,
): NextResponse {
  const url = request.nextUrl.clone();
  url.pathname = path;
  url.search = '';
  // Rewrite = proxy internal ke server Next.js sendiri, yang HANYA mendengarkan HTTP di loopback (TLS + mTLS oleh
  // sidecar insight-web-tls). nextUrl mewarisi `https` dari X-Forwarded-Proto Traefik -> tanpa baris ini Next memproxy
  // lewat TLS ke port plaintext (EPROTO "wrong version number", 500 di /belum-aktif & /sesi-berakhir; ditemukan di
  // gladi prod lokal 20 Nov 2026). Bukan downgrade: hop ini tetap di loopback namespace container.
  url.protocol = 'http:';
  const requestHeaders = new Headers(request.headers);
  requestHeaders.set('x-nonce', nonce);
  requestHeaders.set('Content-Security-Policy', csp);
  const res = NextResponse.rewrite(url, { request: { headers: requestHeaders } });
  if (clearCookie) res.headers.append('Set-Cookie', clearCookieHeader());
  res.headers.set('Content-Security-Policy', csp);
  res.headers.set('Cache-Control', 'no-store');
  return res;
}

export function proxy(request: NextRequest): NextResponse {
  const isDev = process.env.NODE_ENV !== 'production';
  const nonce = Buffer.from(crypto.randomUUID()).toString('base64');
  const csp = buildCsp(nonce, isDev);
  const path = request.nextUrl.pathname;

  if (!PUBLIC_PATHS.has(path)) {
    const cfg = getConfig();
    const auth = authenticate(
      request.cookies.get(SESSION_COOKIE)?.value,
      cfg.enabled,
      getKey('session'),
      Math.floor(Date.now() / 1000),
      cfg.acceptEit1,
    );
    if (auth.status === 'disabled' || auth.status === 'misconfigured') {
      return rewriteTo(request, '/belum-aktif', csp, nonce, false);
    }
    if (auth.status === 'unauthenticated') return rewriteTo(request, '/sesi-berakhir', csp, nonce, false);
    if (auth.status === 'invalid') return rewriteTo(request, '/sesi-berakhir', csp, nonce, true);
    // Lapis pertama akses persona (lapis kedua di halaman: lib/sm-page.ts). Rewrite = status 200 + halaman ramah.
    const denial = smRouteDenial(path, auth.session, cfg.smEnabled);
    if (denial) return rewriteTo(request, DENIAL_PATH[denial], csp, nonce, false);
  }

  const requestHeaders = new Headers(request.headers);
  requestHeaders.set('x-nonce', nonce);
  requestHeaders.set('Content-Security-Policy', csp);
  const response = NextResponse.next({ request: { headers: requestHeaders } });
  response.headers.set('Content-Security-Policy', csp);
  return response;
}

export const config = {
  matcher: ['/((?!_next/static|_next/image|favicon.ico|theme-init.js|api/|sso$|logout$).*)'],
};
