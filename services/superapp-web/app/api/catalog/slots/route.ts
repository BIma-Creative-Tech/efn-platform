/**
 * GET /api/catalog/slots?channel=PB&outlet=TOKO-01&date=2026-10-25 -> BFF GET /v1/catalog/slots (docs/04 §15.4, G23).
 * Publik (tanpa sesi). Sisa kapasitas berubah cepat -> cache publik singkat 30 dtk (sama dengan BFF).
 */
import type { NextRequest } from 'next/server';
import { proxyCatalog } from '@/lib/catalog-route';
import { onlineError } from '@/lib/orders-route';
import { parseSlotDay, slotsQuery } from '@/lib/slots';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function GET(req: NextRequest) {
  const sp = req.nextUrl.searchParams;
  const q = slotsQuery(sp.get('channel'), sp.get('outlet'), sp.get('date'));
  if (!q) return onlineError(400, 'invalid_request');
  return proxyCatalog(`/v1/catalog/slots?${q}`, req.headers, parseSlotDay, 'public, max-age=30');
}
