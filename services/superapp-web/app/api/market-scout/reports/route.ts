/**
 * POST /api/market-scout/reports — proxy streaming multipart ke BFF
 * POST /v1/market-scout/reports (docs/04 §3.3), meneruskan Bearer dari cookie efn_session.
 *
 * - Body TIDAK di-buffer/di-parse di Next.js: stream diteruskan apa adanya (duplex: 'half'),
 *   dengan batas ukuran (MAX_BODY_BYTES) yang dipaksa saat streaming.
 * - Validasi isi (magic bytes, re-encode, strip EXIF) tetap di BFF.
 */
import { NextResponse, type NextRequest } from 'next/server';
import { bffFetch, type ReportAccepted } from '@/lib/bff';
import { clearSessionCookie, getSessionTokenFromRequest } from '@/lib/session';
import { isSameOrigin, jsonError } from '@/lib/request-guard';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

/** Foto maks 8 MB (kontrak) + ruang untuk field teks & boundary multipart. */
const MAX_BODY_BYTES = 8 * 1024 * 1024 + 256 * 1024;

function limitStream(max: number, onExceeded: () => void): TransformStream<Uint8Array, Uint8Array> {
  let seen = 0;
  return new TransformStream<Uint8Array, Uint8Array>({
    transform(chunk, controller) {
      seen += chunk.byteLength;
      if (seen > max) {
        onExceeded();
        controller.error(new Error('payload_too_large'));
        return;
      }
      controller.enqueue(chunk);
    },
  });
}

export async function POST(req: NextRequest) {
  if (!isSameOrigin(req)) return jsonError(403, 'forbidden', 'Permintaan ditolak.');

  const token = getSessionTokenFromRequest(req);
  if (!token) return jsonError(401, 'unauthenticated', 'Silakan masuk terlebih dahulu.');

  const contentType = req.headers.get('content-type') ?? '';
  if (!contentType.toLowerCase().startsWith('multipart/form-data')) {
    return jsonError(415, 'unsupported_media_type', 'Format kiriman tidak didukung.');
  }

  const declaredLength = Number(req.headers.get('content-length') ?? 'NaN');
  if (Number.isFinite(declaredLength) && declaredLength > MAX_BODY_BYTES) {
    return jsonError(413, 'payload_too_large', 'Ukuran foto terlalu besar (maks 8 MB).');
  }
  if (!req.body) return jsonError(400, 'invalid_request', 'Permintaan tidak valid.');

  // content-length sengaja tidak diteruskan: body stream dikirim chunked oleh undici.
  const headers: Record<string, string> = { 'content-type': contentType };

  let tooLarge = false;
  const result = await bffFetch<ReportAccepted>('/v1/market-scout/reports', {
    method: 'POST',
    token,
    rawBody: req.body.pipeThrough(
      limitStream(MAX_BODY_BYTES, () => {
        tooLarge = true;
      }),
    ),
    headers,
    incoming: req.headers,
    timeoutMs: 30_000,
  });

  if (result.ok) {
    return Response.json(
      { report_id: result.data.report_id, status: result.data.status ?? 'queued' },
      { status: 202, headers: { 'cache-control': 'no-store' } },
    );
  }

  if (tooLarge) return jsonError(413, 'payload_too_large', 'Ukuran foto terlalu besar (maks 8 MB).');

  switch (result.status) {
    case 401: {
      const res = NextResponse.json(
        { error: { code: 'unauthenticated', message: 'Sesi berakhir. Silakan masuk kembali.' } },
        { status: 401 },
      );
      clearSessionCookie(res);
      return res;
    }
    case 400:
    case 413:
    case 415:
    case 422:
    case 429:
      // Pesan dari BFF sudah generik & berbahasa Indonesia (kontrak §3).
      return jsonError(result.status, result.code, result.message);
    default:
      return jsonError(502, 'upload_failed', 'Laporan belum dapat dikirim. Silakan coba lagi.');
  }
}
