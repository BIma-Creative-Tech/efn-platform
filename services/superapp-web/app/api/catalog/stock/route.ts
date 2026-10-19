/**
 * GET /api/catalog/stock?channel=GL&outlet=TOKO-01&ids=1,2,3 -> BFF GET /v1/catalog/stock (docs/04 §10.3.2). Publik.
 * Status live (`in_stock|low|out`, tanpa qty) -> `no-store`.
 */
import type { NextRequest } from 'next/server';
import { isChannelCode, isOutletCode, parseIdList, parseStock } from '@/lib/catalog';
import { proxyCatalog } from '@/lib/catalog-route';
import { onlineError } from '@/lib/orders-route';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function GET(req: NextRequest) {
  const sp = req.nextUrl.searchParams;
  const channel = sp.get('channel');
  const outlet = sp.get('outlet');
  const ids = parseIdList(sp.get('ids'));
  if (!isChannelCode(channel) || !isOutletCode(outlet) || !ids) return onlineError(400, 'invalid_request');
  const q = new URLSearchParams({ channel, outlet, ids: ids.join(',') });
  return proxyCatalog(
    `/v1/catalog/stock?${q}`,
    req.headers,
    (v) => {
      const items = parseStock(v);
      return items === null ? null : { items };
    },
    'no-store',
  );
}
