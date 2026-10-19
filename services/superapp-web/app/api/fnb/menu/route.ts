/**
 * GET /api/fnb/menu -> BFF GET /v1/fnb/menu (docs/04 §14.A.3). Menu per meja (overlay per config): kategori, produk,
 * harga SEBELUM pajak & layanan, atribut (modifier). Gambar lewat `/api/catalog/images/:sha` yang sudah ada.
 * Respons di-allowlist. `Cache-Control: no-store` (header next.config menimpa; menu diambil sekali per kunjungan).
 */
import type { NextRequest } from 'next/server';
import { bffFetch } from '@/lib/bff';
import { parseFnbMenu } from '@/lib/fnb';
import { fnbError, fnbGuard, mapFnbFailure, tableHeaders } from '@/lib/fnb-route';
import { noStoreJson } from '@/lib/orders-route';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function GET(req: NextRequest) {
  const g = fnbGuard(req, false);
  if (!g.ok) return g.response;
  const r = await bffFetch<unknown>('/v1/fnb/menu', { token: g.bearer ?? undefined, headers: tableHeaders(g.table), incoming: req.headers });
  if (!r.ok) return mapFnbFailure(r, g.bearer !== null);
  const menu = parseFnbMenu(r.data);
  if (!menu) return fnbError(502, 'upstream_error');
  return noStoreJson(menu);
}
