/**
 * Header keamanan login-gateway (ADR-050). Pola sama dengan insight-web.
 * - CSP halaman dibuat per request (nonce) di proxy.ts -> lib/csp.ts. Route handler (/go, /healthz) memasang CSP sendiri.
 * - Tidak ada cookie, tidak ada sesi; semua respons no-store kecuali aset statis /_next/static.
 */
const isDev = process.env.NODE_ENV !== 'production';

export const securityHeaders = [
  { key: 'X-Content-Type-Options', value: 'nosniff' },
  { key: 'Referrer-Policy', value: 'no-referrer' },
  { key: 'X-Frame-Options', value: 'DENY' },
  { key: 'Cross-Origin-Opener-Policy', value: 'same-origin' },
  { key: 'Cross-Origin-Resource-Policy', value: 'same-origin' },
  { key: 'X-Robots-Tag', value: 'noindex, nofollow' },
  { key: 'Permissions-Policy', value: 'camera=(), microphone=(), geolocation=(), payment=(), usb=()' },
  ...(isDev ? [] : [{ key: 'Strict-Transport-Security', value: 'max-age=31536000; includeSubDomains' }]),
];

/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'standalone',
  poweredByHeader: false,
  reactStrictMode: true,
  // next/image optimizer dimatikan (permukaan lebih kecil); logo statis dari /public.
  images: { unoptimized: true },
  async headers() {
    return [
      { source: '/:path*', headers: securityHeaders },
      { source: '/', headers: [{ key: 'Cache-Control', value: 'no-store' }] },
      { source: '/:path((?!_next/).+)', headers: [{ key: 'Cache-Control', value: 'no-store' }] },
    ];
  },
};

export default nextConfig;
