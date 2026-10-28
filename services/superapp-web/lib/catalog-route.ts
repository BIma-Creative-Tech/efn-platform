/**
 * Helper Route Handler katalog publik (`/api/catalog/**` -> BFF `/v1/catalog/**`, docs/04 §10.3.2). SERVER-SIDE SAJA.
 *
 * - Tanpa sesi: katalog, gambar, dan stok publik. Bearer TIDAK diteruskan (cookie sesi tidak dipakai di sini).
 * - Input divalidasi & dibangun ulang (tidak ada query mentah yang diteruskan ke BFF).
 * - Respons di-allowlist (lib/catalog.ts). Cache: katalog `public, max-age=60`; stok `no-store` (live);
 *   gambar `public, max-age=31536000, immutable` (alamat berbasis hash). next.config.mjs MENIMPA Cache-Control handler,
 *   sehingga `/api/catalog/**` sengaja dikecualikan dari aturan `no-store` default di sana (diverifikasi G17).
 * - Gambar di-stream dari BFF (origin sendiri -> CSP `img-src 'self'` cukup). Hanya `image/jpeg`, maks. 5 MB (juga tanpa `content-length`).
 */
import 'server-only';
import { bffBaseUrl, bffFetch } from './bff';
import { logSafe, safeRequestId } from './log-safe';
import { mapOnlineFailure, onlineError } from './orders-route';

export const CATALOG_CACHE = 'public, max-age=60';
export const IMAGE_CACHE = 'public, max-age=31536000, immutable';
/**
 * Batas ukuran gambar yang diteruskan (gambar katalog 1024 px JPEG jauh di bawah ini). Ditegakkan dua kali:
 * `content-length` di atas batas -> 502 sebelum stream; tanpa `content-length` (chunked) -> stream dihitung dan
 * diputus (error stream + upstream dibatalkan) begitu melewati batas.
 */
export const MAX_IMAGE_BYTES = 5 * 1024 * 1024;

/**
 * Bungkus stream dengan batas byte. Lewat batas -> upstream dibatalkan (`reader.cancel`, menutup koneksi ke BFF) dan
 * stream keluar di-error sehingga respons ke browser terputus (status 200 sudah terkirim; gambar dianggap rusak).
 */
export function limitStream(body: ReadableStream<Uint8Array>, maxBytes: number, onExceed?: () => void): ReadableStream<Uint8Array> {
  const reader = body.getReader();
  let total = 0;
  return new ReadableStream<Uint8Array>({
    async pull(controller) {
      const { done, value } = await reader.read();
      if (done) {
        controller.close();
        return;
      }
      total += value.byteLength;
      if (total > maxBytes) {
        await reader.cancel('size_limit').catch(() => undefined);
        onExceed?.();
        controller.error(new Error('image_too_large'));
        return;
      }
      controller.enqueue(value);
    },
    cancel(reason) {
      return reader.cancel(reason);
    },
  });
}

/** GET publik ke BFF + allowlist `parse`. `parse` -> null berarti respons BFF rusak -> 502. */
export async function proxyCatalog<T>(
  path: string,
  incoming: Headers,
  parse: (v: unknown) => T | null,
  cacheControl: string = CATALOG_CACHE,
): Promise<Response> {
  const r = await bffFetch<unknown>(path, { incoming });
  if (!r.ok) return mapOnlineFailure(r, { publicRoute: true });
  const data = parse(r.data);
  if (data === null) return onlineError(502, 'upstream_error', 'Katalog belum dapat dimuat. Silakan coba lagi.');
  return Response.json(data, { headers: { 'cache-control': cacheControl } });
}

/**
 * Stream gambar katalog dari BFF. `bffFetch` membaca JSON, jadi gambar memakai fetch langsung dengan aturan transport
 * yang sama (base URL tervalidasi https + CA internal dari lib/bff.ts, timeout, tanpa redirect).
 */
export function proxyCatalogImage(sha: string, size: number, incoming: Headers): Promise<Response> {
  return proxyBffImage(`/v1/catalog/images/${sha}?size=${size}`, incoming);
}

/** Stream gambar JPEG BFF (katalog / promo G27) dengan aturan yang sama. `path` = konstanta tervalidasi pemanggil. */
export async function proxyBffImage(path: string, incoming: Headers): Promise<Response> {
  let base: string;
  try {
    base = bffBaseUrl();
  } catch {
    return new Response(null, { status: 503, headers: { 'cache-control': 'no-store' } });
  }
  const headers: Record<string, string> = {
    accept: 'image/jpeg',
    'x-request-id': safeRequestId(incoming.get('x-request-id')),
  };
  const xff = incoming.get('x-forwarded-for');
  if (xff) headers['x-forwarded-for'] = xff;

  let res: Response;
  try {
    res = await fetch(`${base}${path}`, {
      headers,
      cache: 'no-store',
      redirect: 'manual',
      signal: AbortSignal.timeout(10_000),
    });
  } catch {
    return new Response(null, { status: 503, headers: { 'cache-control': 'no-store' } });
  }
  if (res.status === 404) return new Response(null, { status: 404, headers: { 'cache-control': 'public, max-age=60' } });
  const type = (res.headers.get('content-type') ?? '').split(';')[0]!.trim().toLowerCase();
  const length = Number(res.headers.get('content-length') ?? '0');
  if (!res.ok || type !== 'image/jpeg' || !res.body || (Number.isFinite(length) && length > MAX_IMAGE_BYTES)) {
    await res.body?.cancel().catch(() => undefined);
    return new Response(null, { status: res.status === 429 ? 429 : 502, headers: { 'cache-control': 'no-store' } });
  }
  const out: Record<string, string> = {
    'content-type': 'image/jpeg',
    'cache-control': IMAGE_CACHE,
    'x-content-type-options': 'nosniff',
    'content-security-policy': "default-src 'none'; sandbox",
  };
  if (length > 0) out['content-length'] = String(length);
  const limited = limitStream(res.body, MAX_IMAGE_BYTES, () =>
    console.warn(`[catalog] gambar melewati batas ${MAX_IMAGE_BYTES} byte, stream diputus req=${logSafe(headers['x-request-id'])}`),
  );
  return new Response(limited, { status: 200, headers: out });
}
