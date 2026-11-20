/**
 * Pengingat harga (docs/04 §28.8). Bearer member. Kontrak hanya menulis "GET/POST/DELETE /v1/me/price-alerts
 * { channel_code, product_id, target_price_idr }" -> ASUMSI: DELETE memakai body `{ channel_code, product_id }` pada koleksi.
 *   GET    -> { items: [{ channel_code, product_id, target_price_idr, created_at?, expires_at? }] }
 *   POST   { channel_code, product_id, target_price_idr } (bilangan bulat > 0, < harga saat ini; <= 50 aktif)
 *   DELETE { channel_code, product_id } -> 204
 * Perbandingan "< harga saat ini" dilakukan BFF (sumber harga); klien hanya validasi UX.
 */
import type { NextRequest } from 'next/server';
import { bffFetch } from '@/lib/bff';
import { parsePriceAlerts, validatePriceAlertCreate, validateWishlistRef } from '@/lib/engage';
import { engageError, engageFailure, noContent } from '@/lib/engage-route';
import { noStoreJson, readJsonBody, requireMember } from '@/lib/orders-route';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function GET(req: NextRequest) {
  const g = requireMember(req, false);
  if (!g.ok) return g.response;
  const r = await bffFetch<unknown>('/v1/me/price-alerts', { token: g.token, incoming: req.headers });
  if (!r.ok) return engageFailure(r);
  return noStoreJson({ items: parsePriceAlerts(r.data) });
}

export async function POST(req: NextRequest) {
  const g = requireMember(req, true);
  if (!g.ok) return g.response;
  const body = await readJsonBody(req, 2048);
  if (!body.ok) return body.response;
  const v = validatePriceAlertCreate(body.value);
  if (!v.ok) return engageError(400, v.code);
  const r = await bffFetch<unknown>('/v1/me/price-alerts', { method: 'POST', token: g.token, json: v.value, incoming: req.headers });
  if (!r.ok) return engageFailure(r);
  return noStoreJson({ ok: true }, 201);
}

export async function DELETE(req: NextRequest) {
  const g = requireMember(req, true);
  if (!g.ok) return g.response;
  const body = await readJsonBody(req, 1024);
  if (!body.ok) return body.response;
  const v = validateWishlistRef(body.value);
  if (!v.ok) return engageError(400, v.code);
  const r = await bffFetch<unknown>('/v1/me/price-alerts', { method: 'DELETE', token: g.token, json: v.value, incoming: req.headers });
  if (!r.ok && r.status !== 404) return engageFailure(r);
  return noContent();
}
