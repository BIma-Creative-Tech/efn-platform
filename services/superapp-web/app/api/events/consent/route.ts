/**
 * POST /api/events/consent { device_id, granted, version } -> BFF POST /v1/events/consent (docs/04 §28.6).
 * Consent PERANGKAT (append-only di BFF). Publik, tanpa Bearer: tidak mengubah consent akun.
 * Pemberian wajib atas versi teks terbaru (`409 stale_text`); penarikan selalu diterima.
 * 404/501 dari BFF = fitur belum aktif -> `{ disabled: true }` (banner tidak lagi ditampilkan klien).
 */
import type { NextRequest } from 'next/server';
import { bffFetch } from '@/lib/bff';
import { isSameOrigin } from '@/lib/request-guard';
import { isUuidV4, TRACKING_TEXT } from '@/lib/tracking';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

const NO_STORE = { 'cache-control': 'no-store' } as const;

function err(status: number, code: string, message: string, headers: Record<string, string> = {}): Response {
  return Response.json({ error: { code, message } }, { status, headers: { ...NO_STORE, ...headers } });
}

export async function POST(req: NextRequest) {
  if (!isSameOrigin(req)) return err(403, 'forbidden', 'Permintaan ditolak.');
  let body: unknown;
  try {
    const text = await req.text();
    if (text.length > 1024) return err(413, 'payload_too_large', 'Data terlalu besar.');
    body = JSON.parse(text);
  } catch {
    return err(400, 'invalid_request', 'Permintaan tidak valid.');
  }
  const b = body && typeof body === 'object' ? (body as Record<string, unknown>) : null;
  if (!b || !isUuidV4(b.device_id) || typeof b.granted !== 'boolean' || typeof b.version !== 'string') {
    return err(400, 'invalid_request', 'Permintaan tidak valid.');
  }
  if (b.granted && b.version !== TRACKING_TEXT.version) {
    return err(409, 'stale_text', 'Teks persetujuan telah diperbarui. Muat ulang halaman lalu tinjau kembali.');
  }
  const version = b.granted ? b.version : TRACKING_TEXT.version; // penarikan dicatat dengan versi terbaru

  const r = await bffFetch<unknown>('/v1/events/consent', {
    method: 'POST',
    json: { device_id: b.device_id, granted: b.granted, version },
    incoming: req.headers,
  });
  if (r.ok) {
    const disabled = Boolean(r.data && typeof r.data === 'object' && (r.data as { disabled?: unknown }).disabled === true);
    return Response.json({ ok: !disabled, disabled }, { status: 200, headers: NO_STORE });
  }
  if (r.status === 404 || r.status === 501) return Response.json({ ok: false, disabled: true }, { status: 200, headers: NO_STORE });
  if (r.status === 429) {
    return err(429, 'rate_limited', 'Terlalu banyak permintaan. Coba lagi beberapa saat lagi.', r.retryAfterS ? { 'retry-after': String(r.retryAfterS) } : {});
  }
  if (r.status === 400 || r.status === 409 || r.status === 422) return err(400, 'invalid_request', 'Pilihan belum tersimpan. Silakan coba lagi.');
  return err(r.status === 503 ? 503 : 502, 'upstream_error', 'Pilihan belum tersimpan. Silakan coba lagi.');
}
