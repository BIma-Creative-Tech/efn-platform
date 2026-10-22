/**
 * Header keamanan Super-App.
 * - CSP tanpa nonce: Next.js App Router menyisipkan inline script untuk hydration, sehingga
 *   `script-src 'unsafe-inline'` masih diperlukan. Peningkatan berikutnya: CSP berbasis nonce
 *   via proxy.ts (membuat semua halaman dinamis — sudah dinamis karena multi-tenant).
 * - Browser hanya bicara ke origin sendiri (Route Handler -> BFF), jadi connect-src 'self'.
 * - Nilai dihitung saat `next build` (NODE_ENV=production) / `next dev` (development).
 * Traefik juga memasang middleware `secure-headers@file`; header duplikat dengan nilai sama aman.
 */
const isDev = process.env.NODE_ENV !== 'production';

const csp = [
  "default-src 'self'",
  `script-src 'self' 'unsafe-inline'${isDev ? " 'unsafe-eval'" : ''}`,
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob:",
  "font-src 'self'",
  `connect-src 'self'${isDev ? ' ws: wss:' : ''}`,
  "media-src 'self' blob:",
  "worker-src 'self' blob:",
  "manifest-src 'self'",
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
  ...(isDev ? [] : ['upgrade-insecure-requests']),
].join('; ');

const securityHeaders = [
  { key: 'Content-Security-Policy', value: csp },
  { key: 'X-Content-Type-Options', value: 'nosniff' },
  { key: 'Referrer-Policy', value: 'strict-origin-when-cross-origin' },
  { key: 'X-Frame-Options', value: 'DENY' },
  { key: 'Cross-Origin-Opener-Policy', value: 'same-origin' },
  {
    key: 'Permissions-Policy',
    value: 'geolocation=(self), camera=(self), microphone=(), payment=(), usb=()',
  },
  ...(isDev ? [] : [{ key: 'Strict-Transport-Security', value: 'max-age=31536000; includeSubDomains' }]),
];

/** @type {import('next').NextConfig} */
const nextConfig = {
  // Dockerfile menyalin .next/standalone -> wajib.
  output: 'standalone',
  poweredByHeader: false,
  reactStrictMode: true,
  // Dev saja (Next 16 memblokir /_next/hmr lintas origin): tanpa ini klien dev di https://<brand>.efn.local tidak
  // pernah terhidrasi (tombol/komponen klien mati). Tidak berpengaruh pada `next build`/produksi.
  ...(isDev ? { allowedDevOrigins: ['efn.local', '*.efn.local'] } : {}),
  async headers() {
    return [
      { source: '/:path*', headers: securityHeaders },
      // G17: header next.config MENIMPA Cache-Control yang di-set Route Handler (diverifikasi di runtime standalone).
      // Jadi `no-store` default berlaku untuk semua `/api/*` KECUALI katalog publik (`/api/catalog/**`), yang men-set
      // cache sendiri: daftar/detail `public, max-age=60`, gambar `immutable`, error `no-store`.
      // Stok live tetap dipaksa `no-store` di sini (lapis kedua selain handler). Data member (cart/orders/pickup-code) tetap no-store.
      { source: '/api/:path((?!catalog(?:/|$)).*)', headers: [{ key: 'Cache-Control', value: 'no-store' }] },
      { source: '/api/catalog/stock', headers: [{ key: 'Cache-Control', value: 'no-store' }] },
      // G22: halaman sesi meja (token di fragment) — jangan di-cache atau diindeks. Header berbeda dari securityHeaders (tanpa duplikasi kunci).
      {
        source: '/meja',
        headers: [
          { key: 'Cache-Control', value: 'no-store' },
          { key: 'X-Robots-Tag', value: 'noindex, nofollow' },
        ],
      },
    ];
  },
};

export default nextConfig;
