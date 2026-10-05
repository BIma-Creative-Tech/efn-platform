/**
 * Validator `?next=` (tujuan redirect setelah login) — SEC-13 / ASVS V5.1.5.
 *
 * Modul murni (tanpa `server-only`, tanpa API Node) agar bisa dipakai di Server Component,
 * Client Component (lapis kedua sebelum `router.replace`), dan unit test.
 *
 * Aturan (semua harus lolos, selain itu -> fallback):
 * 1. string non-kosong, panjang <= 1024;
 * 2. dinormalisasi NFKC (mis. solidus full-width dipetakan ke bentuk kanonik sebelum dicek);
 * 3. diawali satu `/` dan BUKAN `//`;
 * 4. tanpa backslash, karakter kontrol (U+0000–U+001F, U+007F–U+009F) atau whitespace apa pun —
 *    dicek pada nilai mentah DAN nilai setelah satu kali percent-decode (menangkap `%09`, `%0a`, `%5c`);
 *    nilai ter-decode juga tidak boleh diawali `//`;
 * 5. `new URL(next, 'https://x.invalid').origin === 'https://x.invalid'`;
 * 6. hasil kanonik (`pathname + search + hash`) dicek ulang tidak diawali `//`
 *    (mis. `/.//evil.example` dinormalisasi parser menjadi `//evil.example`).
 */

const SENTINEL_ORIGIN = 'https://x.invalid';
const MAX_LENGTH = 1024;
/** Kontrol C0/C1, DEL, backslash, dan seluruh whitespace Unicode. */
// eslint-disable-next-line no-control-regex -- sengaja mencocokkan karakter kontrol
const FORBIDDEN = /[\u0000-\u001f\u007f-\u009f\\\s]/u;

export const DEFAULT_NEXT_PATH = '/akun';

function hasForbidden(value: string): boolean {
  return FORBIDDEN.test(value) || !value.startsWith('/') || value.startsWith('//');
}

export function safeNextPath(next: unknown, fallback: string = DEFAULT_NEXT_PATH): string {
  if (typeof next !== 'string' || next.length === 0 || next.length > MAX_LENGTH) return fallback;

  const normalized = next.normalize('NFKC');
  if (hasForbidden(normalized)) return fallback;

  let decoded: string;
  try {
    decoded = decodeURIComponent(normalized);
  } catch {
    return fallback; // percent-encoding rusak
  }
  if (hasForbidden(decoded.normalize('NFKC'))) return fallback;

  let url: URL;
  try {
    url = new URL(normalized, SENTINEL_ORIGIN);
  } catch {
    return fallback;
  }
  if (url.origin !== SENTINEL_ORIGIN) return fallback;

  const canonical = `${url.pathname}${url.search}${url.hash}`;
  if (!canonical.startsWith('/') || canonical.startsWith('//')) return fallback;
  return canonical;
}
