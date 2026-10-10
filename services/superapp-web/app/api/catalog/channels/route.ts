/** GET /api/catalog/channels -> BFF GET /v1/catalog/channels (docs/04 §10.3.2). Publik. */
import type { NextRequest } from 'next/server';
import { parseChannels } from '@/lib/catalog';
import { proxyCatalog } from '@/lib/catalog-route';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function GET(req: NextRequest) {
  return proxyCatalog('/v1/catalog/channels', req.headers, (v) => {
    const items = parseChannels(v);
    return items === null ? null : { items };
  });
}
