import { UUID_RE } from '@/lib/prodplan-actions';
import { publicStatus } from '@/lib/prodplan-errors';
import { ppRequest } from '@/lib/prodplan-client';
import { errorResponse, guardApi, jsonResponse } from '@/lib/prodplan-guard';
import { parseJob, parseJobInfo } from '@/lib/prodplan-types';

export const dynamic = 'force-dynamic';

/**
 * `GET /api/prodplan/jobs/{id}` (docs/04 §30.12): polling status job impor PR / hitung (klien: tiap 2 dtk, maks 5 menit).
 * Peran apa pun dari PP; id divalidasi UUID sebelum diteruskan. Hanya `status`/`kind`/`error_code` yang dikembalikan
 * (hasil rinci dibaca ulang lewat halaman rencana).
 */
export async function GET(req: Request, ctx: { params: Promise<{ id: string }> }): Promise<Response> {
  const g = await guardApi(req, 'read');
  if (!g.ok) return g.response;
  const { id } = await ctx.params;
  if (!UUID_RE.test(id)) return errorResponse(404, 'not_found');
  const res = await ppRequest(g.session, 'GET', `/v1/jobs/${id}`);
  if (!res.ok) return jsonResponse(publicStatus(res.status), { error: res.error });
  const job = parseJob(res.data);
  if (!job) return errorResponse(502, 'bad_response');
  // Hanya ringkasan aman dari job.result: plan_status, changed, path siklus BOM (disaring); sisanya tidak diteruskan.
  const info = parseJobInfo(job.result);
  return jsonResponse(200, {
    ok: true,
    data: { status: job.status, kind: job.kind ?? null, error_code: job.error_code ?? null, plan_status: info.plan_status, changed: info.changed, path: info.path },
  });
}
