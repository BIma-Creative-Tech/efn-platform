/**
 * Helper BROWSER pesan dari meja: panggilan ke Route Handler origin sendiri (`/api/fnb/*`) dengan header
 * `X-Table-Session` dari MEMORI (lib/table-token.ts). Token tidak ditulis ke storage/log/URL.
 *
 * - `sessionStorage` hanya dipakai untuk `Idempotency-Key` percobaan kirim terakhir + sidik isi pesanan (BUKAN token, BUKAN
 *   isi catatan). Tujuan: retry setelah jaringan putus tidak menghasilkan pesanan ganda (docs/04 §14.A.3).
 * - Bearer (cookie HttpOnly) ikut otomatis. Bila server menjawab `unauthenticated` (cookie sesi kedaluwarsa dan sudah dihapus
 *   oleh server), permintaan diulang SEKALI sebagai tamu: pesan dari meja tidak mewajibkan login.
 */
import { apiFetch, type ApiResult } from './cart-client';
import { fnbErrorMessage, parseStoredAttempt, TABLE_SESSION_HEADER, type Attempt } from './fnb';
import { getTableToken } from './table-token';

const ATTEMPT_KEY = 'efn_fnb_attempt_v1';

export async function fnbApi<T>(path: string, init: RequestInit = {}): Promise<ApiResult<T>> {
  const token = getTableToken();
  if (!token) return { ok: false, status: 401, code: 'table_session_invalid', message: fnbErrorMessage('table_session_invalid') };
  const run = () =>
    apiFetch<T>(path, { ...init, headers: { ...((init.headers as Record<string, string> | undefined) ?? {}), [TABLE_SESSION_HEADER]: token } });
  const first = await run();
  if (!first.ok && first.code === 'unauthenticated' && !path.startsWith('/api/fnb/points-claim')) return run();
  return first;
}

export function readAttempt(): Attempt | null {
  try {
    return parseStoredAttempt(window.sessionStorage.getItem(ATTEMPT_KEY));
  } catch {
    return null;
  }
}

export function writeAttempt(a: Attempt): void {
  try {
    window.sessionStorage.setItem(ATTEMPT_KEY, JSON.stringify(a));
  } catch {
    // sessionStorage tidak tersedia: retry tetap bekerja selama halaman terbuka, hanya tanpa pemakaian ulang key
  }
}

export function clearAttempt(): void {
  try {
    window.sessionStorage.removeItem(ATTEMPT_KEY);
  } catch {
    // diabaikan
  }
}
