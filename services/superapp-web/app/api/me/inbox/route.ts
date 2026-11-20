/**
 * GET /api/me/inbox -> BFF GET /v1/me/inbox (Bearer dari cookie HttpOnly; no-store). Respons di-allowlist (lib/inbox.ts).
 * 404 dari BFF (PROMO_CAMPAIGN_ENABLED mati) -> 404 `feature_unavailable` (klien menyembunyikan fitur).
 * Kode voucher tidak pernah di-log.
 */
import type { NextRequest } from 'next/server';
import { bffFetch } from '@/lib/bff';
import { engageFailure } from '@/lib/engage-route';
import { parseInbox } from '@/lib/inbox';
import { noStoreJson, requireMember } from '@/lib/orders-route';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function GET(req: NextRequest) {
  const g = requireMember(req, false);
  if (!g.ok) return g.response;
  const r = await bffFetch<unknown>('/v1/me/inbox', { token: g.token, incoming: req.headers });
  if (!r.ok) return engageFailure(r);
  return noStoreJson({ items: parseInbox(r.data) });
}
