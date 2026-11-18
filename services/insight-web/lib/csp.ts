/**
 * CSP halaman (dibuat per request di proxy.ts, berbasis nonce; Next.js memasang nonce ke script-nya sendiri
 * dengan membaca header CSP permintaan).
 *
 * Keputusan (G42, perlu diverifikasi di browser oleh lead):
 * - script-src: 'self' + nonce + 'strict-dynamic'. TANPA 'unsafe-inline' dan TANPA 'unsafe-eval' di production
 *   (ECharts 5 modular + renderer SVG tidak memakai eval/new Function). 'unsafe-eval' hanya di dev (React dev).
 * - style-src (elemen <style>/<link>): 'self' + nonce. TANPA 'unsafe-inline' di production.
 * - style-src-attr 'unsafe-inline': atribut style="" (tooltip ECharts memasang gaya lewat atribut; bar proporsi
 *   server-rendered). Atribut style tidak bisa mengeksekusi skrip; dicatat sebagai pelonggaran terbatas.
 * - Dev: Turbopack menyisipkan <style> tanpa nonce -> style-src 'unsafe-inline' (nonce dilepas karena nonce
 *   membuat 'unsafe-inline' diabaikan).
 */
export function buildCsp(nonce: string, isDev: boolean): string {
  const scriptSrc = `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'${isDev ? " 'unsafe-eval'" : ''}`;
  const styleSrc = isDev ? "style-src 'self' 'unsafe-inline'" : `style-src 'self' 'nonce-${nonce}'`;
  return [
    "default-src 'self'",
    scriptSrc,
    styleSrc,
    "style-src-attr 'unsafe-inline'",
    "img-src 'self' data:",
    "font-src 'self'",
    `connect-src 'self'${isDev ? ' ws: wss:' : ''}`,
    "object-src 'none'",
    "base-uri 'none'",
    "form-action 'self'",
    "frame-ancestors 'none'",
    ...(isDev ? [] : ['upgrade-insecure-requests']),
  ].join('; ');
}

/** CSP respons non-halaman (route handler JSON/CSV): tidak memuat apa pun. */
export const API_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'";

/** CSP halaman galat statis dari /sso (gaya inline satu blok; tanpa skrip, tanpa form). */
export const STATIC_PAGE_CSP =
  "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'";
