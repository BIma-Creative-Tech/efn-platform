import { isDestination, loadConfig } from '@/lib/config';
import { API_CSP } from '@/lib/csp';

export const dynamic = 'force-dynamic';

const BASE_HEADERS = { 'Cache-Control': 'no-store', 'Content-Security-Policy': API_CSP };

/**
 * Redirect 302 ke tujuan dari allowlist env server (ADR-050). Satu-satunya input dari klien adalah segmen path `dest`,
 * dipakai sebagai KUNCI allowlist, bukan sebagai bagian URL. Query/body/header tidak dibaca (tidak ada open redirect).
 */
export async function GET(_request: Request, ctx: { params: Promise<{ dest: string }> }): Promise<Response> {
  const { dest } = await ctx.params;
  if (!isDestination(dest)) return new Response('Tidak ditemukan', { status: 404, headers: BASE_HEADERS });
  const state = loadConfig()[dest];
  if (!state.enabled) return new Response('Tujuan belum tersedia', { status: 503, headers: BASE_HEADERS });
  return new Response(null, { status: 302, headers: { ...BASE_HEADERS, Location: state.url } });
}
