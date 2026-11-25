/**
 * Lapis kedua gerbang `/produksi*` untuk halaman (server-only), berdasarkan SESI ESB (docs/04 §30.17.2), BUKAN grup Odoo:
 * flag aktif -> cookie `__Host-epp` sah -> `GET /v1/session` (tanpa panggilan ESB). Tidak ada cookie/kedaluwarsa -> halaman
 * masuk. Lapis pertama = proxy.ts; route handler API memakai lib/prodplan-guard.ts. Cookie sesi Odoo tidak dibaca di sini.
 */
import 'server-only';
import { cookies } from 'next/headers';
import { redirect } from 'next/navigation';
import { ppCaps, type PpCaps } from './prodplan-access';
import { ppSessionView } from './prodplan-client';
import { PP_COOKIE, isPpSid, type SessionView } from './prodplan-session';
import { getConfig } from './runtime';

/** Route handler yang menghapus cookie lalu mengalihkan ke `/produksi/masuk?e=expired` (render tidak bisa menulis cookie). */
export const PP_EXPIRED_PATH = '/api/prodplan/expired';

export async function requirePpSession(): Promise<{ sid: string; view: SessionView; caps: PpCaps }> {
  if (!getConfig().ppEnabled) redirect('/pp-belum-aktif');
  const raw = (await cookies()).get(PP_COOKIE)?.value;
  if (!isPpSid(raw)) redirect('/produksi/masuk');
  const res = await ppSessionView(raw);
  if (!res.ok) {
    if (res.error.code === 'session_expired') redirect(PP_EXPIRED_PATH);
    redirect('/produksi/masuk?e=unavailable');
  }
  return { sid: raw, view: res.view, caps: ppCaps(res.view.roles) };
}

/** Pemuat data yang dibalas `session_expired` di tengah render (sesi habis antara layout dan halaman) -> masuk ulang. */
export function redirectIfExpired(load: { ok: boolean; error?: { code: string } }): void {
  if (!load.ok && load.error?.code === 'session_expired') redirect(PP_EXPIRED_PATH);
}

export type SearchParams = Promise<Record<string, string | string[] | undefined>>;

/** Nilai pertama parameter query sebagai string (atau undefined). */
export function firstParam(raw: Record<string, string | string[] | undefined>, key: string): string | undefined {
  const v = raw[key];
  const s = Array.isArray(v) ? v[0] : v;
  return typeof s === 'string' ? s : undefined;
}
