import { parseBomQuery } from '@/lib/prodplan-actions';
import { loadBoms } from '@/lib/prodplan-data';
import { publicStatus } from '@/lib/prodplan-errors';
import { errorResponse, guardApi, jsonResponse } from '@/lib/prodplan-guard';

export const dynamic = 'force-dynamic';

const STATUS_BY_CODE: Record<string, number> = {
  bad_input: 422,
  invalid_request: 422,
  forbidden: 403,
  actor_invalid: 401,
  rate_limited: 429,
  disabled: 503,
  not_found: 404,
};

/**
 * `GET /api/prodplan/boms?brand=&q=` (docs/04 §30.8 `GET /v1/boms`): pencarian BOM untuk baris permintaan. Peran PP mana pun;
 * hanya `brand` dan `q` (>= 2 karakter) yang divalidasi dan diteruskan (limit tetap di server, parameter lain diabaikan).
 */
export async function GET(req: Request): Promise<Response> {
  const g = await guardApi(req, 'read');
  if (!g.ok) return g.response;
  const sp = new URL(req.url).searchParams;
  const parsed = parseBomQuery(sp.get('brand'), sp.get('q'));
  if (!parsed) return errorResponse(422, 'bad_input', {}, { fields: ['q'] });
  const res = await loadBoms(g.session, parsed.brand, parsed.q);
  if (!res.ok) return jsonResponse(publicStatus(STATUS_BY_CODE[res.error.code] ?? 502), { error: res.error });
  return jsonResponse(200, { ok: true, data: { items: res.data } });
}
