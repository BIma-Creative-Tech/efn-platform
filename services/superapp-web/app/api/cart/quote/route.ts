/**
 * POST /api/cart/quote -> BFF POST /v1/cart/quote (docs/04 §10.3.4, §11.2). Quote dihitung Odoo dari cart tersimpan;
 * satu-satunya input klien yang diteruskan adalah `promo_codes` (maks 3, dinormalisasi huruf besar). Harga/total dari
 * klien diabaikan. Respons di-allowlist (`quote_hash` tidak pernah ke browser).
 *
 * Kode promo: tidak di-log, tidak masuk URL/query, tidak masuk analytics. Format salah -> 400 dengan pesan generik yang sama
 * dengan kode tidak berlaku (anti-enumerasi, §11.2 no. 4).
 */
import type { NextRequest } from 'next/server';
import { bffFetch } from '@/lib/bff';
import { PROMO_INVALID_MESSAGE, parseQuote, sanitizePromoCodes } from '@/lib/cart';
import { mapOnlineFailure, noStoreJson, onlineError, readJsonBody, requireMember } from '@/lib/orders-route';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function POST(req: NextRequest) {
  const g = requireMember(req, true);
  if (!g.ok) return g.response;

  const body = await readJsonBody(req, 2 * 1024);
  if (!body.ok) return body.response;
  const raw = typeof body.value === 'object' && body.value !== null && !Array.isArray(body.value) ? (body.value as Record<string, unknown>).promo_codes : undefined;
  const codes = sanitizePromoCodes(raw);
  if (codes === null) return onlineError(400, 'promo_invalid', PROMO_INVALID_MESSAGE);

  const r = await bffFetch<unknown>('/v1/cart/quote', {
    method: 'POST',
    token: g.token,
    json: codes.length > 0 ? { promo_codes: codes } : {},
    incoming: req.headers,
    timeoutMs: 20_000,
  });
  if (!r.ok) return mapOnlineFailure(r);
  const quote = parseQuote(r.data);
  if (!quote) return onlineError(502, 'upstream_error');
  return noStoreJson(quote);
}
