import { odooHomeUrl } from '@/lib/config';
import { isSameOriginRequest } from '@/lib/origin-guard';
import { getConfig } from '@/lib/runtime';
import { clearCookieHeader } from '@/lib/session';

export const dynamic = 'force-dynamic';

/**
 * POST /logout (docs/04 §26.5): hapus cookie -> 303 ke beranda Odoo (`{EFN_INSIGHT_ODOO_URL}/odoo`). Target berasal
 * dari env yang sudah divalidasi (bukan input request). Fallback ke halaman "Anda sudah keluar" bila belum dikonfigurasi.
 */
export async function POST(req: Request): Promise<Response> {
  if (!isSameOriginRequest(req.headers.get('sec-fetch-site'))) {
    return new Response(null, { status: 403, headers: { 'Cache-Control': 'no-store' } });
  }
  return new Response(null, {
    status: 303,
    headers: { Location: odooHomeUrl(getConfig()) ?? '/keluar', 'Set-Cookie': clearCookieHeader(), 'Cache-Control': 'no-store' },
  });
}
