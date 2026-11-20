/**
 * Pengingat stok ("Kabari saya bila tersedia"; docs/04 §28.8). Bearer member.
 *   GET  /api/me/restock-alerts -> BFF GET /v1/me/restock-alerts -> { items }
 *   POST /api/me/restock-alerts { channel_code, outlet_code, product_id, channels[] } -> BFF POST -> 201 { id }
 *        409 already_in_stock bila produk tidak habis di outlet itu; batas 50 aktif.
 */
import type { NextRequest } from 'next/server';
import { bffFetch } from '@/lib/bff';
import { parseRestockAlerts, validateRestockCreate } from '@/lib/engage';
import { engageError, engageFailure } from '@/lib/engage-route';
import { noStoreJson, readJsonBody, requireMember } from '@/lib/orders-route';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function GET(req: NextRequest) {
  const g = requireMember(req, false);
  if (!g.ok) return g.response;
  const r = await bffFetch<unknown>('/v1/me/restock-alerts', { token: g.token, incoming: req.headers });
  if (!r.ok) return engageFailure(r);
  return noStoreJson({ items: parseRestockAlerts(r.data) });
}

export async function POST(req: NextRequest) {
  const g = requireMember(req, true);
  if (!g.ok) return g.response;
  const body = await readJsonBody(req, 2048);
  if (!body.ok) return body.response;
  const v = validateRestockCreate(body.value);
  if (!v.ok) return engageError(400, v.code);
  const r = await bffFetch<unknown>('/v1/me/restock-alerts', { method: 'POST', token: g.token, json: v.value, incoming: req.headers });
  if (!r.ok) return engageFailure(r);
  const id = r.data && typeof r.data === 'object' ? (r.data as { id?: unknown }).id : null;
  return noStoreJson({ id: typeof id === 'string' || typeof id === 'number' ? String(id) : null }, 201);
}
