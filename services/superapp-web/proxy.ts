/**
 * Proxy multi-tenant (Next.js 16: `middleware.ts` -> `proxy.ts`, fungsi `middleware` -> `proxy`): host -> brand, diteruskan ke Server Components lewat
 * request header `x-efn-brand`. Header dari klien selalu ditimpa (tidak bisa dipalsukan).
 *
 * Dev saja (NODE_ENV !== 'production'): `?brand=grandlucky` (atau `parisbaguette`, `bacha`, `wetzels`, `efn`) menyimpan
 * cookie `efn_dev_brand` agar bisa mencoba brand lain di http://localhost:3001 tanpa mengatur /etc/hosts. Cookie hanya
 * berlaku di localhost/IP (`isDevOverrideHost`); di domain bernama (`app.efn.local`, ...) `?brand=` hanya untuk satu
 * permintaan dan cookie lama dihapus, jadi domain selalu menampilkan brand-nya sendiri.
 *
 * ADR terkait: multi-tenant by domain (Proposed). Next 16: proxy berjalan di runtime Node.js
 * (bukan Edge lagi); `lib/brands.ts` tetap murni sehingga aman di kedua runtime.
 */
import { NextResponse, type NextRequest } from 'next/server';
import { isBrandId, isDevOverrideHost, resolveBrandIdFromHost } from './lib/brands';

const BRAND_HEADER = 'x-efn-brand';
const DEV_BRAND_COOKIE = 'efn_dev_brand';

export function proxy(request: NextRequest) {
  const isDev = process.env.NODE_ENV !== 'production';
  const host = request.headers.get('host');
  let brandId = resolveBrandIdFromHost(host);
  let devOverride: string | null = null;
  let clearStale = false;

  if (isDev) {
    const sticky = isDevOverrideHost(host);
    const fromQuery = request.nextUrl.searchParams.get('brand');
    const fromCookie = request.cookies.get(DEV_BRAND_COOKIE)?.value;
    if (isBrandId(fromQuery)) {
      brandId = fromQuery;
      if (sticky) devOverride = fromQuery;
    } else if (sticky && isBrandId(fromCookie)) {
      brandId = fromCookie;
    }
    clearStale = !sticky && fromCookie !== undefined;
  } else {
    clearStale = request.cookies.has(DEV_BRAND_COOKIE);
  }

  const requestHeaders = new Headers(request.headers);
  requestHeaders.set(BRAND_HEADER, brandId);

  const response = NextResponse.next({ request: { headers: requestHeaders } });
  if (devOverride) {
    response.cookies.set(DEV_BRAND_COOKIE, devOverride, { path: '/', sameSite: 'lax', httpOnly: true });
  } else if (clearStale) {
    response.cookies.delete(DEV_BRAND_COOKIE);
  }
  return response;
}

export const config = {
  matcher: ['/((?!_next/static|_next/image|favicon.ico|robots.txt).*)'],
};
