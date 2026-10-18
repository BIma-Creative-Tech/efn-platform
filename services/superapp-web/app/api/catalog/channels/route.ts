/**
 * GET /api/catalog/channels[?brand=GL] -> BFF GET /v1/catalog/channels[?brand=] (docs/04 §10.3.2, §11.3). Publik.
 * `brand` divalidasi `^[A-Z][A-Z0-9]{1,9}$` dan query dibangun ulang; format salah -> 400. Pembatasan brand = UI saja.
 */
import type { NextRequest } from 'next/server';
import { channelsPath, isBrandCode, parseChannels } from '@/lib/catalog';
import { proxyCatalog } from '@/lib/catalog-route';
import { onlineError } from '@/lib/orders-route';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function GET(req: NextRequest) {
  const brand = req.nextUrl.searchParams.get('brand');
  if (brand !== null && !isBrandCode(brand)) return onlineError(400, 'invalid_request');
  return proxyCatalog(channelsPath(brand), req.headers, (v) => {
    const items = parseChannels(v);
    return items === null ? null : { items };
  });
}
