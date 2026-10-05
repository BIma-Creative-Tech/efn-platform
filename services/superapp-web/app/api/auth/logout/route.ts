/**
 * POST /api/auth/logout — hapus cookie sesi lalu redirect 303 ke beranda.
 * Dipanggil dari form di /keluar. Belum ada endpoint revoke token di BFF (docs/04), jadi
 * JWT tetap valid sampai exp (maks 1 jam) — dicatat di laporan.
 */
import { NextResponse, type NextRequest } from 'next/server';
import { clearSessionCookie } from '@/lib/session';
import { isSameOrigin } from '@/lib/request-guard';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function POST(req: NextRequest) {
  if (!isSameOrigin(req)) {
    return new NextResponse('Permintaan ditolak.', { status: 403 });
  }
  // Location relatif: aman di belakang Traefik (req.url bisa berisi host internal 0.0.0.0).
  const res = new NextResponse(null, { status: 303, headers: { location: '/' } });
  clearSessionCookie(res);
  return res;
}
