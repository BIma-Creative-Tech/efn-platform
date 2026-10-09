/**
 * Sesi pelanggan: access token BFF (JWT, exp 1 jam) di cookie `efn_session`
 * HttpOnly; Secure; SameSite=Lax. Token TIDAK pernah dikirim ke JavaScript browser.
 *
 * Cookie host-only (per domain brand). SSO lintas subdomain belum dibahas (lihat laporan).
 * Dev tanpa HTTPS: set env `COOKIE_INSECURE=true` (jangan pernah di staging/prod).
 */
import 'server-only';
import { cookies } from 'next/headers';
import type { NextRequest, NextResponse } from 'next/server';
import { DSAR_RECEIPT_COOKIE, DSAR_RECEIPT_PATH, isPlausibleReceipt, receiptMaxAge } from './dsar-receipt';

export const SESSION_COOKIE = 'efn_session';

function secureFlag(): boolean {
  // Cookie sesi selalu Secure di production; COOKIE_INSECURE hanya dihormati di dev.
  if (process.env.NODE_ENV === 'production') return true;
  return process.env.COOKIE_INSECURE !== 'true';
}

export function setSessionCookie(res: NextResponse, token: string, expiresInSeconds: number): void {
  const maxAge = Number.isFinite(expiresInSeconds) && expiresInSeconds > 0 ? Math.floor(expiresInSeconds) : 3600;
  res.cookies.set(SESSION_COOKIE, token, {
    httpOnly: true,
    secure: secureFlag(),
    sameSite: 'lax',
    path: '/',
    maxAge,
  });
}

export function clearSessionCookie(res: NextResponse): void {
  res.cookies.set(SESSION_COOKIE, '', {
    httpOnly: true,
    secure: secureFlag(),
    sameSite: 'lax',
    path: '/',
    maxAge: 0,
  });
}

/**
 * Token step-up DSAR (`purpose: dsar`, maks 10 menit — docs/04 §8.4). Cookie terpisah, HttpOnly,
 * SameSite=Strict, Path=/api/me (tidak dikirim ke halaman/endpoint lain), tidak pernah ke JavaScript.
 */
export const STEP_UP_COOKIE = 'efn_stepup';
const STEP_UP_PATH = '/api/me';
const STEP_UP_MAX_S = 600;

export function setStepUpCookie(res: NextResponse, token: string, expiresInSeconds: number): void {
  const maxAge =
    Number.isFinite(expiresInSeconds) && expiresInSeconds > 0 ? Math.min(Math.floor(expiresInSeconds), STEP_UP_MAX_S) : STEP_UP_MAX_S;
  res.cookies.set(STEP_UP_COOKIE, token, {
    httpOnly: true,
    secure: secureFlag(),
    sameSite: 'strict',
    path: STEP_UP_PATH,
    maxAge,
  });
}

export function clearStepUpCookie(res: NextResponse): void {
  res.cookies.set(STEP_UP_COOKIE, '', {
    httpOnly: true,
    secure: secureFlag(),
    sameSite: 'strict',
    path: STEP_UP_PATH,
    maxAge: 0,
  });
}

export function getStepUpTokenFromRequest(req: NextRequest): string | null {
  return req.cookies.get(STEP_UP_COOKIE)?.value || null;
}

/**
 * R6 (docs/04 §8.13): `receipt_token` hapus akun di cookie HttpOnly `efn_dsar_receipt`, SameSite=Strict,
 * Path=/api/dsar (hanya dikirim ke Route Handler status), Max-Age <= 30 hari. Tidak pernah ke JavaScript/log.
 * Token yang sudah kedaluwarsa/bentuknya tidak masuk akal tidak disimpan.
 */
export function setDsarReceiptCookie(res: NextResponse, token: string, nowMs: number = Date.now()): boolean {
  if (!isPlausibleReceipt(token)) return false;
  const maxAge = receiptMaxAge(token, nowMs);
  if (maxAge === null) return false;
  res.cookies.set(DSAR_RECEIPT_COOKIE, token, {
    httpOnly: true,
    secure: secureFlag(),
    sameSite: 'strict',
    path: DSAR_RECEIPT_PATH,
    maxAge,
  });
  return true;
}

export function clearDsarReceiptCookie(res: NextResponse): void {
  res.cookies.set(DSAR_RECEIPT_COOKIE, '', {
    httpOnly: true,
    secure: secureFlag(),
    sameSite: 'strict',
    path: DSAR_RECEIPT_PATH,
    maxAge: 0,
  });
}

export function getDsarReceiptFromRequest(req: NextRequest): string | null {
  const v = req.cookies.get(DSAR_RECEIPT_COOKIE)?.value;
  return isPlausibleReceipt(v) ? v : null;
}

/** Untuk Server Components (Next 15+: cookies() async). */
export async function getSessionToken(): Promise<string | null> {
  const store = await cookies();
  return store.get(SESSION_COOKIE)?.value || null;
}

/** Untuk Route Handlers. */
export function getSessionTokenFromRequest(req: NextRequest): string | null {
  return req.cookies.get(SESSION_COOKIE)?.value || null;
}

/** Validator `?next=` (SEC-13) dipindah ke modul murni agar bisa dipakai client & diuji. */
export { safeNextPath } from './safe-next';
