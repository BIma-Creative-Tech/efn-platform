import { errorResponse, guardApi, jsonResponse, readJsonBody } from '@/lib/prodplan-guard';
import { getAction, roleAllowed } from '@/lib/prodplan-actions';
import { publicStatus } from '@/lib/prodplan-errors';
import { ppRequest } from '@/lib/prodplan-client';
import { log } from '@/lib/log';

export const dynamic = 'force-dynamic';

/**
 * `POST /api/prodplan/<aksi>` (docs/04 §30.12): mutasi Production Order. Hanya aksi di lib/prodplan-actions.ts (tanpa penerusan
 * path bebas). Urutan: gerbang (flag, sesi, peran PP, same-origin, batas) -> aksi dikenal -> peran sesuai aksi -> badan JSON ->
 * validasi input -> panggil prodplan dengan asersi aktor dari SESI (bukan dari input). Respons: `{ok:true,data}` atau
 * `{error:{code,message,details?}}` dengan pesan Indonesia dari tabel kita sendiri.
 */
export async function POST(req: Request, ctx: { params: Promise<{ action: string }> }): Promise<Response> {
  const g = await guardApi(req, 'mutation');
  if (!g.ok) return g.response;

  const { action } = await ctx.params;
  const spec = getAction(action);
  if (!spec) return errorResponse(404, 'unknown_action');
  if (!roleAllowed(spec.role, g.caps)) {
    log('warn', 'prodplan_api', { code: 'forbidden_action', action, sub: g.session.sub });
    return errorResponse(403, 'forbidden');
  }

  const input = await readJsonBody(req);
  if (!input) return errorResponse(400, 'bad_input');
  const built = spec.build(input);
  if (!built.ok) return errorResponse(422, 'bad_input', {}, { fields: [built.field] });

  const res = await ppRequest(g.session, built.method, built.path, built.body);
  log('info', 'prodplan_api', { action, sub: g.session.sub, ok: res.ok, status: res.status });
  if (!res.ok) {
    const retry: Record<string, string> = res.status === 429 ? { 'Retry-After': '10' } : {};
    return jsonResponse(publicStatus(res.status), { error: res.error }, retry);
  }
  return jsonResponse(200, { ok: true, status: res.status, data: res.data });
}
