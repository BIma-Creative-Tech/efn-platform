import { describe, expect, it } from 'vitest';
import { DEFAULT_NEXT_PATH, safeNextPath } from '../lib/safe-next';

describe('safeNextPath (SEC-13 open redirect)', () => {
  it.each([
    ['/akun', '/akun'],
    ['/pantau-harga', '/pantau-harga'],
    ['/pantau-harga/3f2b8c1e-9d4a-4b7e-8f00-1234567890ab', '/pantau-harga/3f2b8c1e-9d4a-4b7e-8f00-1234567890ab'],
    ['/pantau-harga?tab=riwayat#atas', '/pantau-harga?tab=riwayat#atas'],
    ['/a/../akun', '/akun'],
  ])('menerima path internal %j', (input, expected) => {
    expect(safeNextPath(input)).toBe(expected);
  });

  it.each([
    null,
    undefined,
    '',
    123,
    'akun',
    '//evil.example',
    '///evil.example',
    '/\\evil.example',
    '\\/evil.example',
    '/\t/evil.example', // %09 setelah di-decode searchParams (skenario temuan)
    '/\n/evil.example',
    '/\r/evil.example',
    '/\u0000/evil.example',
    '/\u007f/evil.example',
    '/%09/evil.example', // %09 literal (double-encoded di URL asli)
    '/%0a/evil.example',
    '/%0D/evil.example',
    '/%5cevil.example',
    '/%5C%5Cevil.example',
    '/%2F/evil.example',
    '/%2f%2fevil.example',
    '/%', // percent-encoding rusak
    ' /akun',
    '/akun ',
    '/ak un',
    '/　/evil.example', // ideographic space
    '／／evil.example', // solidus full-width -> NFKC "//"
    '/.//evil.example', // parser URL menormalkan jadi "//evil.example"
    'https://evil.example',
    'http:evil.example',
    'javascript:alert(1)',
    'data:text/html,hi',
    `/${'a'.repeat(1100)}`,
  ])('menolak %j', (input) => {
    expect(safeNextPath(input)).toBe(DEFAULT_NEXT_PATH);
  });

  it('memakai fallback kustom', () => {
    expect(safeNextPath('//evil.example', '/')).toBe('/');
  });

  it('hasil selalu same-origin terhadap origin sentinel', () => {
    const out = safeNextPath('/akun?x=1');
    expect(new URL(out, 'https://app.efn.local').origin).toBe('https://app.efn.local');
  });
});
