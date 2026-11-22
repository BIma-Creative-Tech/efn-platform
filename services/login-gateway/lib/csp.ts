/**
 * CSP halaman (per request di proxy.ts; Next.js memasang nonce ke script/style miliknya dengan membaca header CSP
 * permintaan). Halaman berfungsi tanpa JS; skrip hanya milik runtime Next. Tanpa 'unsafe-inline'/'unsafe-eval' di
 * production. Pola sama dengan insight-web/lib/csp.ts (tanpa style-src-attr: tidak ada atribut style).
 */
export function buildCsp(nonce: string, isDev: boolean): string {
  const scriptSrc = `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'${isDev ? " 'unsafe-eval'" : ''}`;
  const styleSrc = isDev ? "style-src 'self' 'unsafe-inline'" : `style-src 'self' 'nonce-${nonce}'`;
  return [
    "default-src 'self'",
    scriptSrc,
    styleSrc,
    "img-src 'self' data:",
    "font-src 'self'",
    `connect-src 'self'${isDev ? ' ws: wss:' : ''}`,
    "object-src 'none'",
    "base-uri 'none'",
    "form-action 'none'",
    "frame-ancestors 'none'",
    ...(isDev ? [] : ['upgrade-insecure-requests']),
  ].join('; ');
}

/** CSP respons non-halaman (/go, /healthz): tidak memuat apa pun. */
export const API_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'";
