/**
 * DEV/STAGING SAJA: POST /api/orders/:id/simulate-payment `{ status: "paid"|"expired"|"failed" }` ->
 * BFF POST /v1/orders/:id/simulate-payment (docs/04 §10.7; BFF sendiri 404 di production atau provider ≠ mock).
 *
 * Lapis kedua di frontend: aktif hanya bila `paymentSimulatorEnabled()` (lib/orders-server.ts); selain itu 404 tanpa
 * memanggil BFF.
 */
import type { NextRequest } from 'next/server';
import { bffFetch } from '@/lib/bff';
import { isUuid } from '@/lib/catalog';
import { mapOnlineFailure, noStoreJson, onlineError, readJsonBody, requireMember } from '@/lib/orders-route';
import { paymentSimulatorEnabled } from '@/lib/orders-server';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

const ALLOWED = new Set(['paid', 'expired', 'failed']);

export async function POST(req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  if (!paymentSimulatorEnabled()) return onlineError(404, 'not_found');
  const { id } = await ctx.params;
  const g = requireMember(req, true);
  if (!g.ok) return g.response;
  if (!isUuid(id)) return onlineError(404, 'not_found');
  const body = await readJsonBody(req, 1024);
  if (!body.ok) return body.response;
  const status = (body.value as { status?: unknown } | null)?.status;
  if (typeof status !== 'string' || !ALLOWED.has(status)) return onlineError(400, 'invalid_request');
  const r = await bffFetch<unknown>(`/v1/orders/${id.toLowerCase()}/simulate-payment`, {
    method: 'POST',
    token: g.token,
    json: { status },
    incoming: req.headers,
  });
  if (!r.ok) return mapOnlineFailure(r);
  return noStoreJson({ ok: true });
}
