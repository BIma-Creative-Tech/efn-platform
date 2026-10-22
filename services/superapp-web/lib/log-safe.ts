/**
 * Nilai yang berasal dari request (x-request-id, path berisi id) aman ditulis ke log: tanpa CR/LF/karakter kontrol
 * (log injection - CodeQL js/log-injection), panjang dibatasi.
 */

const REQUEST_ID_RE = /^[A-Za-z0-9._:-]{1,128}$/;

/** Hapus CR/LF dan karakter kontrol lain; potong ke `max` karakter. */
export function logSafe(value: unknown, max = 256): string {
  const s = String(value)
    .replace(/\r|\n/g, ' ')
    .replace(/[\u0000-\u001f\u007f]/g, '?');
  return s.length > max ? `${s.slice(0, max)}…` : s;
}

/** Request id dari header klien hanya dipakai bila bentuknya wajar; selain itu dibuat baru. */
export function safeRequestId(incoming: string | null | undefined): string {
  return incoming && REQUEST_ID_RE.test(incoming) ? incoming : crypto.randomUUID();
}
