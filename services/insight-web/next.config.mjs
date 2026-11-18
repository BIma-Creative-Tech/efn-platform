/**
 * Header keamanan insight-web (docs/04 §26.6).
 * - CSP halaman dibuat per request (nonce) di proxy.ts -> lib/csp.ts. Di sini hanya CSP untuk /api/* (tanpa muatan).
 * - Semua respons ber-sesi: Cache-Control no-store (kecuali aset statis /_next/static|image yang di-cache Next).
 * - Referrer-Policy no-referrer, nosniff, frame-ancestors 'none' (+ X-Frame-Options), COOP/CORP same-origin.
 * Traefik memasang secure-headers-insight; header duplikat bernilai sama aman.
 */
const isDev = process.env.NODE_ENV !== 'production';

const common = [
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
  // SEC-196: next/image tidak dipakai; matikan optimizer (/_next/image, di luar gerbang sesi) untuk mengecilkan permukaan.
  images: { unoptimized: true },
  async headers() {
    return [
      { source: '/:path*', headers: common },
      { source: '/', headers: [{ key: 'Cache-Control', value: 'no-store' }] },
      { source: '/:path((?!_next/).+)', headers: [{ key: 'Cache-Control', value: 'no-store' }] },
      {
        source: '/api/:path*',
        headers: [{ key: 'Content-Security-Policy', value: "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'" }],
      },
    ];
  },
};

export default nextConfig;
