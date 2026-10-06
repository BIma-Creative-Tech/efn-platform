/**
 * GET /api/dsar/requests/:id -> BFF GET /v1/dsar/requests/:id (docs/04 §8.13, R6) dengan header `X-DSAR-Receipt`
 * dari cookie HttpOnly `efn_dsar_receipt`. TANPA sesi: dipakai setelah akun keluar/terhapus.
 *
 * - id bukan UUID / cookie tidak ada / BFF 404 -> 404 generik (tidak membocorkan keberadaan permintaan).
 * - Respons hanya field allowlist `{ id, status, requested_at, scheduled_at, completed_at }`; token tidak dikembalikan.
 * - Permintaan lintas situs ditolak lewat Fetch Metadata; cookie juga SameSite=Strict. Rate limit di BFF (per IP).
 */
import type { NextRequest } from 'next/server';
import { bffFetch } from '@/lib/bff';
import { isDsarRequestId, parseDeletionStatus } from '@/lib/dsar-receipt';
import { isNotCrossSite, jsonError } from '@/lib/request-guard';
import { getDsarReceiptFromRequest } from '@/lib/session';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

const NOT_FOUND_MESSAGE =
  'Status permintaan tidak ditemukan. Bukti status hanya tersimpan di peramban yang dipakai saat mengajukan penghapusan dan berlaku 30 hari.';

function notFound(): Response {
  return jsonError(404, 'not_found', NOT_FOUND_MESSAGE);
}

export async function GET(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  if (!isNotCrossSite(req)) return jsonError(403, 'forbidden', 'Permintaan ditolak.');
  const { id } = await params;
  if (!isDsarRequestId(id)) return notFound();
  const receipt = getDsarReceiptFromRequest(req);
  if (!receipt) return notFound();

  const r = await bffFetch<unknown>(`/v1/dsar/requests/${encodeURIComponent(id.toLowerCase())}`, {
    method: 'GET',
    headers: { 'x-dsar-receipt': receipt },
    incoming: req.headers,
  });

  if (r.ok) {
    const view = parseDeletionStatus(r.data, id);
    if (!view) return jsonError(502, 'upstream_error', 'Status belum dapat dimuat. Silakan coba lagi.');
    return Response.json(view, { status: 200, headers: { 'cache-control': 'no-store' } });
  }
  if (r.status === 404 || r.status === 400) return notFound();
  if (r.status === 429) return jsonError(429, 'rate_limited', 'Terlalu sering memeriksa. Coba lagi beberapa menit lagi.');
  return jsonError(502, 'upstream_error', 'Status belum dapat dimuat. Silakan coba lagi.');
}
