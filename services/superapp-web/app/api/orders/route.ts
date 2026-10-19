/**
 * Pesanan member (docs/04 §10.3.5–§10.3.6).
 * - GET  /api/orders?cursor=&limit=≤20 -> BFF GET /v1/orders.
 * - POST /api/orders `{ quote_id, accept_total, payment_method?: 'qris'|'counter' }` (§12.2) -> BFF POST /v1/orders dengan header `Idempotency-Key` yang DIBUAT DI
 *   SINI (deterministik dari quote_id, lib/orders-route.ts) — browser tidak memilih key. `accept_total` hanya pembanding
 *   (BFF/Odoo menghitung ulang); bukan harga yang dipercaya.
 * Semua respons `no-store`.
 */
import type { NextRequest } from 'next/server';
import { bffFetch } from '@/lib/bff';
import { CURSOR_RE, isUuid, parseLimit } from '@/lib/catalog';
import { parseCheckoutResult, parseOrderList } from '@/lib/orders';
import { isPaymentMethod } from '@/lib/payment';
import {
  checkoutIdempotencyKey,
  mapOnlineFailure,
  noStoreJson,
  onlineError,
  readJsonBody,
  requireMember,
} from '@/lib/orders-route';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function GET(req: NextRequest) {
  const g = requireMember(req, false);
  if (!g.ok) return g.response;
  const sp = req.nextUrl.searchParams;
  const q = new URLSearchParams({ limit: String(parseLimit(sp.get('limit'), 20, 20)) });
  const cursor = sp.get('cursor');
  if (cursor) {
    if (!CURSOR_RE.test(cursor)) return onlineError(400, 'invalid_request');
    q.set('cursor', cursor);
  }
  const r = await bffFetch<unknown>(`/v1/orders?${q}`, { token: g.token, incoming: req.headers });
  if (!r.ok) return mapOnlineFailure(r);
  const list = parseOrderList(r.data);
  if (!list) return onlineError(502, 'upstream_error');
  return noStoreJson(list);
}

export async function POST(req: NextRequest) {
  const g = requireMember(req, true);
  if (!g.ok) return g.response;
  const body = await readJsonBody(req);
  if (!body.ok) return body.response;
  const v = (body.value ?? {}) as { quote_id?: unknown; accept_total?: unknown; payment_method?: unknown };
  const quoteId = v.quote_id;
  const acceptTotal = v.accept_total;
  if (!isUuid(quoteId) || typeof acceptTotal !== 'number' || !Number.isSafeInteger(acceptTotal) || acceptTotal < 0) {
    return onlineError(400, 'invalid_request');
  }
  // §12.2: `payment_method` opsional (default BFF = qris); nilai selain qris|counter ditolak sebelum ke BFF.
  if (v.payment_method !== undefined && !isPaymentMethod(v.payment_method)) return onlineError(400, 'invalid_request');
  const r = await bffFetch<unknown>('/v1/orders', {
    method: 'POST',
    token: g.token,
    json: {
      quote_id: quoteId.toLowerCase(),
      accept_total: acceptTotal,
      ...(v.payment_method !== undefined ? { payment_method: v.payment_method } : {}),
    },
    headers: { 'idempotency-key': checkoutIdempotencyKey(quoteId) },
    incoming: req.headers,
    timeoutMs: 30_000,
  });
  if (!r.ok) return mapOnlineFailure(r);
  const created = parseCheckoutResult(r.data);
  if (!created) return onlineError(502, 'upstream_error');
  // Hanya id + status ke browser; detail (QR, kode bayar di kasir, hitung mundur) diambil halaman pesanan lewat GET.
  // `payment.counter_code` dari respons checkout SENGAJA dibuang di sini (tidak ke log/analytics/respons ini).
  return noStoreJson(created, r.status === 201 ? 201 : 200);
}
