/**
 * Lapis kedua gerbang `/produksi*` untuk halaman (server-only): sesi sah + flag + peran PP; selain itu dialihkan ke halaman
 * informasi publik yang sama dengan rewrite proxy.ts. Lapis pertama = proxy.ts; route handler API memakai lib/prodplan-guard.ts.
 */
import 'server-only';
import { redirect } from 'next/navigation';
import { requireSession } from './auth';
import { PP_DENIAL_PATH, ppAccessDenial, ppCaps, type PpCaps } from './prodplan-access';
import { getConfig } from './runtime';
import type { Session } from './session';

export async function requirePpSession(): Promise<{ session: Session; caps: PpCaps }> {
  const session = await requireSession();
  const denial = ppAccessDenial(session, getConfig().ppEnabled);
  if (denial) redirect(PP_DENIAL_PATH[denial]);
  return { session, caps: ppCaps(session.roles) };
}

export type SearchParams = Promise<Record<string, string | string[] | undefined>>;

/** Nilai pertama parameter query sebagai string (atau undefined). */
export function firstParam(raw: Record<string, string | string[] | undefined>, key: string): string | undefined {
  const v = raw[key];
  const s = Array.isArray(v) ? v[0] : v;
  return typeof s === 'string' ? s : undefined;
}
