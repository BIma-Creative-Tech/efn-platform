/**
 * Pengingat harga (docs/04 §28.8). Bearer member. Kontrak hanya menulis "GET/POST/DELETE /v1/me/price-alerts
 * { channel_code, product_id, target_price_idr }" -> ASUMSI: DELETE memakai body `{ channel_code, product_id }` pada koleksi.
 *   GET    -> { items: [{ channel_code, product_id, target_price_idr, created_at?, expires_at? }] }
 *   POST   { channel_code, product_id, target_price_idr } (bilangan bulat > 0, < harga saat ini; <= 50 aktif)
 *   DELETE { channel_code, product_id } -> 204 (route ini mencari id lewat GET lalu BFF DELETE /v1/me/price-alerts/:id)
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
  // QA G50: BFF hanya punya `DELETE /v1/me/price-alerts/:id` (id UUID dari GET). Klien mengirim (channel_code, product_id);
  // cari id alert aktif yang cocok lewat GET lalu hapus per id. Tidak ada alert cocok -> 204 (idempoten).
  const list = await bffFetch<unknown>('/v1/me/price-alerts', { token: g.token, incoming: req.headers });
  if (!list.ok) return list.status === 404 ? noContent() : engageFailure(list);
  const items = list.data && typeof list.data === 'object' ? (list.data as { items?: unknown }).items : null;
  const hit = (Array.isArray(items) ? items : []).find((it): it is { id: string } => {
    if (!it || typeof it !== 'object') return false;
    const r = it as Record<string, unknown>;
    return r.channel_code === v.value.channel_code && r.product_id === v.value.product_id && typeof r.id === 'string';
  });
  if (!hit || !/^[0-9a-fA-F-]{36}$/.test(hit.id)) return noContent();
  const r = await bffFetch<unknown>(`/v1/me/price-alerts/${encodeURIComponent(hit.id)}`, {
    method: 'DELETE',
    token: g.token,
    incoming: req.headers,
  });
  if (!r.ok && r.status !== 404) return engageFailure(r);
  return noContent();
}
