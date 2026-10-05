export const dynamic = 'force-dynamic';

export function GET() {
  return Response.json({ status: 'ok', service: 'superapp-web', time: new Date().toISOString() });
}
