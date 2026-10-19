/**
 * Kiriman pesanan dari meja (docs/04 §14.A.3, L-70: satu kiriman = satu order draft di meja; sesi punya banyak kiriman).
 * - GET  /api/fnb/orders -> BFF GET /v1/fnb/orders (semua kiriman sesi; meja berbagi token).
 * - POST /api/fnb/orders `{ lines:[{ product_id, qty, attribute_value_ids, note? }], altcha? }` + header `Idempotency-Key`
 *   (dibuat klien per percobaan, `crypto.randomUUID()`) -> BFF POST /v1/fnb/orders. Badan divalidasi ulang dan di-allowlist
 *   (klien tidak pernah mengirim harga, meja, config, member — API3); catatan dinormalisasi dan TIDAK di-log.
 *   202 `{ submission_id, status, lines, indicative_subtotal_excl }`.
 */
import type { NextRequest } from 'next/server';
import { bffFetch } from '@/lib/bff';
import { FNB_IDEMPOTENCY_KEY_RE, parseFnbOrders, parseSubmitAccepted, sanitizeSubmitBody } from '@/lib/fnb';
import { fnbError, fnbGuard, mapFnbFailure, tableHeaders } from '@/lib/fnb-route';
import { noStoreJson, readJsonBody } from '@/lib/orders-route';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function GET(req: NextRequest) {
  const g = fnbGuard(req, false);
  if (!g.ok) return g.response;
  const r = await bffFetch<unknown>('/v1/fnb/orders', { token: g.bearer ?? undefined, headers: tableHeaders(g.table), incoming: req.headers });
  if (!r.ok) return mapFnbFailure(r, g.bearer !== null);
  const items = parseFnbOrders(r.data);
  if (!items) return fnbError(502, 'upstream_error');
  return noStoreJson({ items });
}

export async function POST(req: NextRequest) {
  const g = fnbGuard(req, true);
  if (!g.ok) return g.response;
  const key = req.headers.get('idempotency-key') ?? '';
  if (!FNB_IDEMPOTENCY_KEY_RE.test(key)) return fnbError(400, 'idempotency_key_required');
  const body = await readJsonBody(req);
  if (!body.ok) return body.response;
  const clean = sanitizeSubmitBody(body.value);
  if (!clean.ok) return fnbError(400, clean.code);

  const r = await bffFetch<unknown>('/v1/fnb/orders', {
    method: 'POST',
    token: g.bearer ?? undefined,
    json: clean.body,
    headers: tableHeaders(g.table, { 'idempotency-key': key }),
    incoming: req.headers,
    timeoutMs: 20_000,
  });
  if (!r.ok) return mapFnbFailure(r, g.bearer !== null);
  const accepted = parseSubmitAccepted(r.data);
  if (!accepted) return fnbError(502, 'upstream_error');
  return noStoreJson(accepted, r.status === 200 ? 200 : 202);
}
