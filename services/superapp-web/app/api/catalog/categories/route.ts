/** GET /api/catalog/categories?channel=RTL -> BFF GET /v1/catalog/categories (docs/04 §10.3.2). Publik. */
import type { NextRequest } from 'next/server';
import { isChannelCode, parseCategories } from '@/lib/catalog';
import { proxyCatalog } from '@/lib/catalog-route';
import { onlineError } from '@/lib/orders-route';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function GET(req: NextRequest) {
  const channel = req.nextUrl.searchParams.get('channel');
  if (!isChannelCode(channel)) return onlineError(400, 'invalid_request');
  return proxyCatalog(`/v1/catalog/categories?${new URLSearchParams({ channel })}`, req.headers, (v) => {
    const items = parseCategories(v);
    return items === null ? null : { items };
  });
}
