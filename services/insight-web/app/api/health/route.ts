export const dynamic = 'force-dynamic';

/** docs/04 §26.6: tanpa auth, tanpa ClickHouse, tanpa versi/detail. */
export function GET(): Response {
  return Response.json({ status: 'ok' }, { headers: { 'Cache-Control': 'no-store' } });
}
