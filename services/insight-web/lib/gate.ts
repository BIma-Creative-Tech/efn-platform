/**
 * Gerbang akses murni: dipakai proxy.ts, halaman (requireSession), dan route data (CSV).
 * Urutan: kill switch -> kunci sesi tersedia -> cookie ada -> cookie valid.
 */
import { verifySessionValue, type Session } from './session';

export type AuthState =
  | { status: 'ok'; session: Session }
  | { status: 'disabled' }
  | { status: 'misconfigured' }
  | { status: 'unauthenticated' }
  | { status: 'invalid' };

export function authenticate(
  cookieValue: string | undefined,
  enabled: boolean,
  sessionKey: string | null,
  nowSec: number,
  acceptV1 = true,
): AuthState {
  if (!enabled) return { status: 'disabled' };
  if (!sessionKey) return { status: 'misconfigured' };
  if (cookieValue === undefined || cookieValue === '') return { status: 'unauthenticated' };
  const session = verifySessionValue(cookieValue, sessionKey, nowSec, acceptV1);
  return session ? { status: 'ok', session } : { status: 'invalid' };
}
