/**
 * proxy.ts: gerbang sesi memakai REWRITE internal (bukan redirect; docs/04 §26.5, laporan G42): tidak boleh ada
 * header `Location`, URL browser tetap, cookie hapus bila tidak valid. Menguji fungsi `proxy()` langsung dengan
 * NextRequest; state runtime (config + kunci) disetel ulang tiap test.
 */
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { NextRequest } from 'next/server';
import { afterAll, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import { proxy } from '@/proxy';
import { SESSION_COOKIE, createSessionValue } from '@/lib/session';
import type { SsoPayload } from '@/lib/token';
import { BASE_PAYLOAD } from './helpers';

const SESSION_KEY = 'cd'.repeat(32);
let dir = '';
const saved: Record<string, string | undefined> = {};
const ENV_KEYS = ['INSIGHT_ENABLED', 'INSIGHT_SESSION_KEY_FILE', 'APP_ENV', 'EFN_INSIGHT_ODOO_URL'];

function resetRuntime(): void {
  delete (globalThis as unknown as { __insightRuntime?: unknown }).__insightRuntime;
}

beforeAll(() => {
  dir = mkdtempSync(join(tmpdir(), 'insight-proxy-'));
  writeFileSync(join(dir, 'session_key'), `${SESSION_KEY}\n`, 'ascii');
  for (const k of ENV_KEYS) saved[k] = process.env[k];
});

afterAll(() => {
  rmSync(dir, { recursive: true, force: true });
  for (const k of ENV_KEYS) {
    if (saved[k] === undefined) delete process.env[k];
    else process.env[k] = saved[k];
  }
  resetRuntime();
});

beforeEach(() => {
  process.env.INSIGHT_ENABLED = 'true';
  process.env.INSIGHT_SESSION_KEY_FILE = join(dir, 'session_key');
  process.env.APP_ENV = 'development';
  process.env.EFN_INSIGHT_ODOO_URL = 'https://erp.test:9443';
  resetRuntime();
});

function request(path: string, cookie?: string): NextRequest {
  return new NextRequest(`https://insight.test${path}`, cookie ? { headers: { cookie: `${SESSION_COOKIE}=${cookie}` } } : {});
}

function validCookie(): string {
  return createSessionValue(BASE_PAYLOAD as unknown as SsoPayload, SESSION_KEY, Math.floor(Date.now() / 1000));
}

const rewriteOf = (res: Response): string | null => res.headers.get('x-middleware-rewrite');

describe('proxy: gerbang sesi', () => {
  it('cookie sah -> lanjut (tanpa rewrite) dengan CSP bernonce', () => {
    const res = proxy(request('/sales?from=2026-11-01', validCookie()));
    expect(rewriteOf(res)).toBeNull();
    expect(res.headers.get('x-middleware-next')).toBe('1');
    expect(res.headers.get('content-security-policy')).toContain("frame-ancestors 'none'");
    expect(res.headers.get('location')).toBeNull();
  });

  it('tanpa cookie -> rewrite ke /sesi-berakhir, tanpa Location, tanpa Set-Cookie', () => {
    const res = proxy(request('/sales?pt=PB'));
    expect(rewriteOf(res)).toContain('/sesi-berakhir');
    expect(rewriteOf(res)).not.toContain('pt=PB'); // query dibuang pada halaman publik
    expect(res.headers.get('location')).toBeNull();
    expect(res.headers.get('set-cookie')).toBeNull();
    expect(res.headers.get('cache-control')).toBe('no-store');
    expect(res.headers.get('content-security-policy')).toContain("default-src 'self'");
  });

  it('cookie palsu/kedaluwarsa -> rewrite ke /sesi-berakhir + cookie dihapus (Max-Age=0)', () => {
    for (const bad of ['eis1.aaaaaaaaaaaaaaaa.' + 'a'.repeat(43), 'sampah', validCookie().replace(/.$/, (c) => (c === 'A' ? 'B' : 'A'))]) {
      const res = proxy(request('/', bad));
      expect(rewriteOf(res), bad).toContain('/sesi-berakhir');
      expect(res.headers.get('set-cookie')).toContain(`${SESSION_COOKIE}=; Max-Age=0`);
      expect(res.headers.get('location')).toBeNull();
    }
  });

  it('kill switch mati -> /belum-aktif walau cookie masih sah', () => {
    process.env.INSIGHT_ENABLED = 'false';
    resetRuntime();
    const res = proxy(request('/', validCookie()));
    expect(rewriteOf(res)).toContain('/belum-aktif');
    expect(res.headers.get('location')).toBeNull();
  });

  it('INSIGHT_ENABLED selain "true" persis (mis. "1", "True") tetap mati', () => {
    for (const v of ['1', 'True', 'yes', '']) {
      process.env.INSIGHT_ENABLED = v;
      resetRuntime();
      expect(rewriteOf(proxy(request('/', validCookie()))), v).toContain('/belum-aktif');
    }
  });

  it('kunci sesi tidak terbaca -> /belum-aktif (fail closed), bukan lolos', () => {
    process.env.INSIGHT_SESSION_KEY_FILE = join(dir, 'tidak-ada');
    resetRuntime();
    expect(rewriteOf(proxy(request('/', validCookie())))).toContain('/belum-aktif');
  });

  it('halaman publik (/belum-aktif, /sesi-berakhir, /keluar) tidak butuh sesi', () => {
    for (const p of ['/belum-aktif', '/sesi-berakhir', '/keluar']) {
      const res = proxy(request(p));
      expect(rewriteOf(res), p).toBeNull();
      expect(res.headers.get('x-middleware-next'), p).toBe('1');
    }
  });

  it('nama cookie lama "eis" (tanpa __Host-) tidak diterima sebagai sesi (C2)', () => {
    const req = new NextRequest('https://insight.test/', { headers: { cookie: `eis=${validCookie()}` } });
    expect(rewriteOf(proxy(req))).toContain('/sesi-berakhir');
  });
});
