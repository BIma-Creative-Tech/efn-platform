/**
 * Proxy (Next.js 16): nonce + CSP ketat per halaman. Tidak ada gerbang sesi (service ini tanpa cookie/sesi).
 * Route handler (/go, /healthz) dikecualikan: mereka memasang CSP sendiri (lib/csp.ts API_CSP).
 */
import { NextResponse, type NextRequest } from 'next/server';
import { buildCsp } from './lib/csp';

export function proxy(request: NextRequest): NextResponse {
  const isDev = process.env.NODE_ENV !== 'production';
  const nonce = Buffer.from(crypto.randomUUID()).toString('base64');
  const csp = buildCsp(nonce, isDev);
  const requestHeaders = new Headers(request.headers);
  requestHeaders.set('x-nonce', nonce);
  requestHeaders.set('Content-Security-Policy', csp);
  const response = NextResponse.next({ request: { headers: requestHeaders } });
  response.headers.set('Content-Security-Policy', csp);
  return response;
}

export const config = {
  matcher: ['/((?!_next/static|_next/image|favicon.ico|go/|healthz$|brand/).*)'],
};
