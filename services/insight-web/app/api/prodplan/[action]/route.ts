import { errorResponse, failResponse, guardApi, jsonResponse, readJsonBody } from '@/lib/prodplan-guard';
import { getAction, roleAllowed } from '@/lib/prodplan-actions';
import { ppCaps } from '@/lib/prodplan-access';
import { ppRequest, ppSessionView } from '@/lib/prodplan-client';
import { log } from '@/lib/log';

export const dynamic = 'force-dynamic';

/**
 * `POST /api/prodplan/<aksi>` (docs/04 §30.12 + §30.17): mutasi Production Order. Hanya aksi di lib/prodplan-actions.ts (tanpa
 * penerusan path bebas). Urutan: gerbang (flag, cookie `__Host-epp`, same-origin, batas) -> aksi dikenal -> peran dari SESI
 * prodplan sesuai aksi -> badan JSON -> validasi input -> panggil prodplan dengan asersi `ppa2` (sid). Respons: `{ok:true,data}`
 * atau `{error:{code,message,details?}}` dengan pesan Indonesia dari tabel kita sendiri. `session_expired` -> 401 + hapus cookie.
 */
export async function POST(req: Request, ctx: { params: Promise<{ action: string }> }): Promise<Response> {
  const g = await guardApi(req, 'mutation');
  if (!g.ok) return g.response;

  const { action } = await ctx.params;
  const spec = getAction(action);
  if (!spec) return errorResponse(404, 'unknown_action');

  // Peran dari sesi prodplan (tanpa panggilan ESB). prodplan tetap menegakkan peran + cabang di sisinya.
  const sess = await ppSessionView(g.sid);
  if (!sess.ok) return failResponse(sess);
  if (!roleAllowed(spec.role, ppCaps(sess.view.roles))) {
    log('warn', 'prodplan_api', { code: 'forbidden_action', action });
    return errorResponse(403, 'forbidden');
  }

  const input = await readJsonBody(req);
  if (!input) return errorResponse(400, 'bad_input');
  const built = spec.build(input);
  if (!built.ok) return errorResponse(422, 'bad_input', {}, { fields: [built.field] });

  const res = await ppRequest(g.sid, built.method, built.path, built.body);
  log('info', 'prodplan_api', { action, ok: res.ok, status: res.status });
  if (!res.ok) return failResponse(res, '10');
  return jsonResponse(200, { ok: true, status: res.status, data: res.data });
}
