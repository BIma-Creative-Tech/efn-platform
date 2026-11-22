'use client';
/**
 * Status "sedang memuat" global untuk LoadingOverlay (klien saja, tanpa dependensi).
 * Dua sumber:
 * - `beginNav()`: navigasi yang selesai saat URL berubah (klik tautan internal, drill-down). Diakhiri oleh overlay saat
 *   pathname/search berubah, atau batas aman SAFETY_MS bila URL ternyata tidak berubah.
 * - `useBusy(pending)`: transisi React (filter, Coba lagi/refresh) yang tahu persis kapan selesai.
 */
import { useEffect, useSyncExternalStore } from 'react';

/** Batas aman: query halaman paling lama ±55 dtk (antrean 20 dtk + timeout 35 dtk, lib/clickhouse.ts). */
export const SAFETY_MS = 90_000;

let navAt: number | null = null;
let pendingCount = 0;
let safetyTimer: ReturnType<typeof setTimeout> | null = null;
const listeners = new Set<() => void>();

function emit(): void {
  for (const l of listeners) l();
}

export function beginNav(): void {
  navAt = Date.now();
  if (safetyTimer) clearTimeout(safetyTimer);
  safetyTimer = setTimeout(endNav, SAFETY_MS);
  emit();
}

export function endNav(): void {
  if (navAt === null) return;
  navAt = null;
  if (safetyTimer) clearTimeout(safetyTimer);
  safetyTimer = null;
  emit();
}

function subscribe(l: () => void): () => void {
  listeners.add(l);
  return () => listeners.delete(l);
}

const isBusy = (): boolean => navAt !== null || pendingCount > 0;
const serverSnapshot = (): boolean => false;

export function useIsBusy(): boolean {
  return useSyncExternalStore(subscribe, isBusy, serverSnapshot);
}

/** Sinkronkan `pending` dari useTransition ke status global. */
export function useBusy(pending: boolean): void {
  useEffect(() => {
    if (!pending) return;
    pendingCount += 1;
    emit();
    return () => {
      pendingCount -= 1;
      emit();
    };
  }, [pending]);
}

/**
 * Apakah klik pada `a` ini memicu navigasi klien Next ke URL lain (bukan tab baru, unduhan, situs lain, atau URL sama).
 * Dipisah agar bisa diuji tanpa DOM.
 */
export function isInternalNavClick(
  a: { href: string; target: string; hasDownload: boolean },
  ev: { button: number; metaKey: boolean; ctrlKey: boolean; shiftKey: boolean; altKey: boolean; defaultPrevented: boolean },
  current: string,
): boolean {
  if (ev.defaultPrevented || ev.button !== 0 || ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey) return false;
  if (a.hasDownload || (a.target && a.target !== '_self')) return false;
  let to: URL;
  let from: URL;
  try {
    from = new URL(current);
    to = new URL(a.href, from);
  } catch {
    return false;
  }
  if (to.origin !== from.origin) return false;
  // Rute non-halaman (ekspor CSV, logout) tidak memakai navigasi klien.
  if (to.pathname.startsWith('/api/') || to.pathname === '/logout') return false;
  return to.pathname !== from.pathname || to.search !== from.search;
}
