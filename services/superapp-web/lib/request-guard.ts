/**
 * Pertahanan CSRF tambahan untuk Route Handler yang mengubah state (selain SameSite=Lax):
 * header Origin wajib ada dan host-nya sama dengan Host request.
 * Traefik meneruskan Host asli (passHostHeader default true).
 */
import 'server-only';
import type { NextRequest } from 'next/server';
import { normalizeHost } from './brands';

export function isSameOrigin(req: NextRequest): boolean {
  const origin = req.headers.get('origin');
  if (!origin) return false;
  let originHost: string;
  try {
    originHost = new URL(origin).host;
  } catch {
    return false;
  }
  // Sengaja memakai Host, bukan X-Forwarded-Host (bisa disuntik klien bila proxy salah konfigurasi).
  const host = req.headers.get('host');
  return normalizeHost(originHost) === normalizeHost(host) && normalizeHost(host) !== '';
}

/**
 * Untuk GET yang membaca data milik pengguna/challenge: tolak permintaan lintas situs bila browser
 * mengirim Fetch Metadata. GET same-origin tidak selalu membawa header Origin, jadi isSameOrigin tidak dipakai.
 * Header tidak ada (browser lama / curl) -> diizinkan; cookie SameSite=Lax & CORS tetap berlaku.
 */
export function isNotCrossSite(req: NextRequest): boolean {
  const site = req.headers.get('sec-fetch-site');
  return site === null || site === 'same-origin' || site === 'none';
}

export function jsonError(status: number, code: string, message: string): Response {
  return Response.json({ error: { code, message } }, { status, headers: { 'cache-control': 'no-store' } });
}
