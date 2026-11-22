import { API_CSP } from '@/lib/csp';

export const dynamic = 'force-dynamic';

/** Probe blackbox/healthcheck: tanpa auth, tanpa detail, tanpa versi. */
export function GET(): Response {
  return Response.json({ status: 'ok' }, { headers: { 'Cache-Control': 'no-store', 'Content-Security-Policy': API_CSP } });
}
