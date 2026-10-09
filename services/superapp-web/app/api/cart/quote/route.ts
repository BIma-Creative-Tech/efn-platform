/**
 * POST /api/cart/quote -> BFF POST /v1/cart/quote `{}` (docs/04 §10.3.4). Quote dihitung Odoo dari cart tersimpan;
 * body klien diabaikan (tidak ada harga/total dari klien). Respons di-allowlist (`quote_hash` tidak pernah ke browser).
 */
import type { NextRequest } from 'next/server';
import { bffFetch } from '@/lib/bff';
import { parseQuote } from '@/lib/cart';
import { mapOnlineFailure, noStoreJson, onlineError, requireMember } from '@/lib/orders-route';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function POST(req: NextRequest) {
  const g = requireMember(req, true);
  if (!g.ok) return g.response;
  const r = await bffFetch<unknown>('/v1/cart/quote', {
    method: 'POST',
    token: g.token,
    json: {},
    incoming: req.headers,
    timeoutMs: 20_000,
  });
  if (!r.ok) return mapOnlineFailure(r);
  const quote = parseQuote(r.data);
  if (!quote) return onlineError(502, 'upstream_error');
  return noStoreJson(quote);
}
