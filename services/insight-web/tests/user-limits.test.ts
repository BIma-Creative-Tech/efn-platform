import { describe, expect, it } from 'vitest';
import {
  createExportLimiter,
  createPageQueryLimiter,
  EXPORT_LIMIT,
  EXPORT_WINDOW_MS,
  exportConcurrencyFromEnv,
  PAGE_QUERY_LIMIT,
  PAGE_QUERY_WINDOW_MS,
} from '@/lib/user-limits';
import { concurrencyFromEnv } from '@/lib/semaphore';

describe('batas per pengguna (SEC-189)', () => {
  it('nilai batas sesuai keputusan: halaman 300 / 5 menit, ekspor 5 / 10 menit', () => {
    expect(PAGE_QUERY_LIMIT).toBe(300);
    expect(PAGE_QUERY_WINDOW_MS).toBe(300_000);
    expect(EXPORT_LIMIT).toBe(5);
    expect(EXPORT_WINDOW_MS).toBe(600_000);
  });

  it('query halaman: ke-300 lolos, ke-301 ditolak untuk sub yang sama; sub lain tidak terdampak; reset setelah jendela', () => {
    const l = createPageQueryLimiter();
    for (let i = 0; i < 300; i += 1) expect(l.hit('7', 1_000).allowed).toBe(true);
    const over = l.hit('7', 2_000);
    expect(over.allowed).toBe(false);
    expect(over.retryAfterSec).toBeGreaterThanOrEqual(1);
    expect(over.retryAfterSec).toBeLessThanOrEqual(300);
    expect(l.hit('8', 2_000).allowed).toBe(true);
    expect(l.hit('7', 1_000 + 300_000).allowed).toBe(true);
  });

  it('ekspor: ke-5 lolos, ke-6 ditolak dengan Retry-After <= 600 dtk; jendela 10 menit', () => {
    const l = createExportLimiter();
    for (let i = 0; i < 5; i += 1) expect(l.hit('7', 0).allowed).toBe(true);
    const over = l.hit('7', 1_000);
    expect(over.allowed).toBe(false);
    expect(over.retryAfterSec).toBeGreaterThanOrEqual(1);
    expect(over.retryAfterSec).toBeLessThanOrEqual(600);
    expect(l.hit('9', 1_000).allowed).toBe(true);
    expect(l.hit('7', 600_000).allowed).toBe(true);
  });

  it('semaphore ekspor: default 3, rentang 1..8; semaphore halaman default 12 (batas gabungan <= 24)', () => {
    expect(exportConcurrencyFromEnv(undefined)).toBe(3);
    expect(exportConcurrencyFromEnv('')).toBe(3);
    expect(exportConcurrencyFromEnv('2')).toBe(2);
    expect(exportConcurrencyFromEnv('8')).toBe(8);
    expect(exportConcurrencyFromEnv('9')).toBe(3);
    expect(exportConcurrencyFromEnv('0')).toBe(3);
    expect(exportConcurrencyFromEnv('abc')).toBe(3);
    expect(concurrencyFromEnv(undefined)).toBe(12);
    expect(concurrencyFromEnv(undefined) + exportConcurrencyFromEnv(undefined)).toBeLessThanOrEqual(24);
  });
});
