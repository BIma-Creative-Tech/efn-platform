/**
 * Penyimpanan token sesi meja di MEMORI modul (docs/04 §14.A.2; keputusan lead G22).
 *
 * - Token dibaca dari fragment `#t=` SEKALI, disimpan di variabel modul, lalu fragment dihapus dari address bar
 *   (`history.replaceState`). Tidak ada `localStorage`/`sessionStorage`, cookie, query string, log, atau analytics.
 * - Variabel modul bertahan selama dokumen yang sama hidup (navigasi klien, StrictMode remount, HMR) tetapi hilang saat
 *   halaman dimuat ulang -> pelanggan memindai ulang QR. Itu disengaja.
 * - `Location`/`History` diterima sebagai parameter supaya fungsi ini bisa diuji tanpa browser.
 */
import { isTableToken, tableTokenFromHash } from './fnb';

let held: string | null = null;

export interface LocationLike {
  hash: string;
  pathname: string;
}

export interface HistoryLike {
  state: unknown;
  replaceState(data: unknown, unused: string, url?: string | URL | null): void;
}

/**
 * Pindahkan token dari fragment ke memori dan bersihkan URL. Dipanggil sekali saat halaman `/meja` dimuat; panggilan
 * berikutnya (mis. StrictMode) mengembalikan token yang sudah ditahan. Fragment/query APA PUN dibersihkan: URL akhir
 * hanya `pathname` (query `?ts=` tidak diterima kontrak dan tidak pernah dibaca).
 */
export function captureTableToken(loc: LocationLike, hist: HistoryLike, search = ''): string | null {
  const fromHash = tableTokenFromHash(loc.hash);
  if (fromHash) held = fromHash;
  if (loc.hash !== '' || search !== '') {
    try {
      hist.replaceState(hist.state, '', loc.pathname);
    } catch {
      // history tidak tersedia: token tetap di memori
    }
  }
  return held;
}

export function getTableToken(): string | null {
  return held && isTableToken(held) ? held : null;
}

/** Buang token (sesi tidak valid / sudah ditutup). */
export function clearTableToken(): void {
  held = null;
}
