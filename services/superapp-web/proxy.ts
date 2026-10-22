/**
 * Proxy multi-tenant (Next.js 16: `middleware.ts` -> `proxy.ts`, fungsi `middleware` -> `proxy`): host -> brand, diteruskan ke Server Components lewat
 * request header `x-efn-brand`. Header dari klien selalu ditimpa (tidak bisa dipalsukan).
 *
 * Dev saja (NODE_ENV !== 'production'): `?brand=grandlucky` (atau `parisbaguette`, `bacha`, `efn`) menyimpan cookie `efn_dev_brand`
 * agar bisa mencoba brand lain di http://localhost:3001 tanpa mengatur /etc/hosts.
 *
 * ADR terkait: multi-tenant by domain (Proposed). Next 16: proxy berjalan di runtime Node.js
 * (bukan Edge lagi); `lib/brands.ts` tetap murni sehingga aman di kedua runtime.
 */
import { NextResponse, type NextRequest } from 'next/server';
import { isBrandId, resolveBrandIdFromHost } from './lib/brands';

const BRAND_HEADER = 'x-efn-brand';
const DEV_BRAND_COOKIE = 'efn_dev_brand';

export function proxy(request: NextRequest) {
  const isDev = process.env.NODE_ENV !== 'production';
  let brandId = resolveBrandIdFromHost(request.headers.get('host'));
  let devOverride: string | null = null;

  if (isDev) {
    const fromQuery = request.nextUrl.searchParams.get('brand');
    const fromCookie = request.cookies.get(DEV_BRAND_COOKIE)?.value;
    if (isBrandId(fromQuery)) {
      brandId = fromQuery;
      devOverride = fromQuery;
    } else if (isBrandId(fromCookie)) {
      brandId = fromCookie;
    }
  }

  const requestHeaders = new Headers(request.headers);
  requestHeaders.set(BRAND_HEADER, brandId);

  const response = NextResponse.next({ request: { headers: requestHeaders } });
  if (devOverride) {
    response.cookies.set(DEV_BRAND_COOKIE, devOverride, { path: '/', sameSite: 'lax', httpOnly: true });
  }
  return response;
}

export const config = {
  matcher: ['/((?!_next/static|_next/image|favicon.ico|robots.txt).*)'],
};
