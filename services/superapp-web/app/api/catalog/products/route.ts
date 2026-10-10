/**
 * GET /api/catalog/products?channel=&category=&q=&cursor=&limit= -> BFF GET /v1/catalog/products (docs/04 §10.3.2).
 * Publik. Query dibangun ulang dari input tervalidasi (`q` ≤ 64 karakter, `limit` ≤ 50).
 */
import type { NextRequest } from 'next/server';
import { buildProductQuery, parseProductPage } from '@/lib/catalog';
import { proxyCatalog } from '@/lib/catalog-route';
import { onlineError } from '@/lib/orders-route';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function GET(req: NextRequest) {
  const sp = req.nextUrl.searchParams;
  const query = buildProductQuery({
    channel: sp.get('channel') ?? '',
    category: sp.get('category'),
    q: sp.get('q'),
    cursor: sp.get('cursor'),
    limit: sp.get('limit'),
  });
  if (!query) return onlineError(400, 'invalid_request');
  return proxyCatalog(`/v1/catalog/products?${query}`, req.headers, parseProductPage);
}
