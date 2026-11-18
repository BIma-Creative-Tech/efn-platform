import { isSameOriginRequest } from '@/lib/origin-guard';
import { clearCookieHeader } from '@/lib/session';

export const dynamic = 'force-dynamic';

/** POST /logout (docs/04 §26.5): hapus cookie -> 303 ke halaman "Anda sudah keluar". */
export async function POST(req: Request): Promise<Response> {
  if (!isSameOriginRequest(req.headers.get('sec-fetch-site'))) {
    return new Response(null, { status: 403, headers: { 'Cache-Control': 'no-store' } });
  }
  return new Response(null, {
    status: 303,
    headers: { Location: '/keluar', 'Set-Cookie': clearCookieHeader(), 'Cache-Control': 'no-store' },
  });
}
