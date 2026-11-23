/**
 * POST /api/events[?link=1] -> BFF POST /v1/events (docs/04 §28.6, ADR-047).
 *
 * - Origin wajib sama (CSRF). Body <= 32 KB; event divalidasi ulang dengan allowlist kontrak (lib/tracking.ts).
 * - Bearer member HANYA diteruskan bila klien menandai `?link=1` (klien tahu consent akun `tracking` aktif). Tanpa itu,
 *   event dikirim sebagai perangkat saja: consent perangkat TIDAK otomatis menjadi consent akun (ADR-047 §3.1).
 * - Pemetaan: 202 diteruskan (`disabled: true` = klien berhenti); 404/501 = fitur belum aktif -> `disabled: true`;
 *   403 -> `tracking_consent_required`; 429 -> 429 + `Retry-After`; 400/413/422 -> 400 (batch dibuang klien).
 * - V-01: event anonim (tanpa consent, `features.visit_log`) memakai jalur yang sama; Bearer tidak diteruskan tanpa `?link=1`
 *   (klien hanya menandainya bila consent aktif) dan BFF mengabaikan Bearer untuk identitas anonim. Route ini tidak
 *   meneruskan IP/UA di luar yang sudah dilakukan `bffFetch` (rate limit). Field `page` divalidasi enum oleh `sanitizeEvents`.
 * - Tidak me-log body/ID perangkat.
 */
import type { NextRequest } from 'next/server';
import { bffFetch } from '@/lib/bff';
import { isSameOrigin } from '@/lib/request-guard';
import { getSessionTokenFromRequest } from '@/lib/session';
import { buildBatchBody, isUuidV4, MAX_BODY_BYTES, parseBatchResponse, sanitizeEvents, SCHEMA_VERSION } from '@/lib/tracking';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

const NO_STORE = { 'cache-control': 'no-store' } as const;

function err(status: number, code: string, headers: Record<string, string> = {}): Response {
  return Response.json({ error: { code } }, { status, headers: { ...NO_STORE, ...headers } });
}

export async function POST(req: NextRequest) {
  if (!isSameOrigin(req)) return err(403, 'forbidden');
  const declared = Number(req.headers.get('content-length') ?? '0');
  if (Number.isFinite(declared) && declared > MAX_BODY_BYTES) return err(413, 'payload_too_large');
  let text: string;
  try {
    text = await req.text();
  } catch {
    return err(400, 'invalid_request');
  }
  if (new TextEncoder().encode(text).byteLength > MAX_BODY_BYTES) return err(413, 'payload_too_large');

  let body: unknown;
  try {
    body = JSON.parse(text);
  } catch {
    return err(400, 'invalid_request');
  }
  const b = body && typeof body === 'object' ? (body as Record<string, unknown>) : null;
  if (!b || b.schema_version !== SCHEMA_VERSION || !isUuidV4(b.device_id) || (b.channel !== undefined && b.channel !== 'web')) {
    return err(400, 'invalid_request');
  }
  const events = sanitizeEvents(b.events);
  if (!events) return err(400, 'invalid_request');

  const link = req.nextUrl.searchParams.get('link') === '1';
  const token = link ? getSessionTokenFromRequest(req) : null;

  const r = await bffFetch<unknown>('/v1/events', {
    method: 'POST',
    json: JSON.parse(buildBatchBody(b.device_id, events, 'web')) as unknown,
    ...(token ? { token } : {}),
    incoming: req.headers,
  });
  if (r.ok) {
    const out = parseBatchResponse(r.data);
    return Response.json({ accepted: out.accepted, disabled: out.disabled }, { status: 202, headers: NO_STORE });
  }
  if (r.status === 404 || r.status === 501) return Response.json({ accepted: 0, disabled: true }, { status: 202, headers: NO_STORE });
  if (r.status === 401 && token) return err(403, 'tracking_consent_required'); // token kedaluwarsa: berhenti, tanpa menghapus sesi di sini
  if (r.status === 403) return err(403, r.code === 'tracking_consent_required' ? 'tracking_consent_required' : 'forbidden');
  if (r.status === 429) return err(429, 'rate_limited', r.retryAfterS ? { 'retry-after': String(r.retryAfterS) } : {});
  if (r.status === 400 || r.status === 413 || r.status === 422) return err(400, 'invalid_request');
  return err(r.status === 503 ? 503 : 502, 'upstream_error');
}
