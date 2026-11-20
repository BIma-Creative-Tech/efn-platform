/**
 * Akses sesi di Server Components / route handler (server-only). Setiap halaman dan route data memverifikasi
 * cookie sendiri (lapis kedua setelah proxy.ts): INSIGHT_ENABLED=false menolak SEMUA, termasuk cookie yang masih berlaku.
 */
import 'server-only';
import { cookies } from 'next/headers';
import { redirect } from 'next/navigation';
import { authenticate, type AuthState } from './gate';
import { getConfig, getKey } from './runtime';
import { readCookie, SESSION_COOKIE, type Session } from './session';

function stateFor(cookieValue: string | undefined): AuthState {
  const cfg = getConfig();
  return authenticate(cookieValue, cfg.enabled, getKey('session'), Math.floor(Date.now() / 1000), cfg.acceptEit1);
}

/** Untuk halaman: redirect ke halaman informasi bila tidak sah. */
export async function requireSession(): Promise<Session> {
  const jar = await cookies();
  const st = stateFor(jar.get(SESSION_COOKIE)?.value);
  if (st.status === 'ok') return st.session;
  if (st.status === 'disabled' || st.status === 'misconfigured') redirect('/belum-aktif');
  redirect('/sesi-berakhir');
}

/** Untuk route data (JSON/CSV): status saja; pemanggil membalas 401/503 tanpa badan rinci. */
export function authFromRequest(req: Request): AuthState {
  return stateFor(readCookie(req.headers.get('cookie'), SESSION_COOKIE));
}
