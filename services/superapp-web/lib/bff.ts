/**
 * Klien BFF (NestJS api-gateway) — SERVER-SIDE SAJA.
 *
 * - Base URL: env `BFF_INTERNAL_URL` (fallback `API_INTERNAL_URL`, lalu `https://api-gateway:3000`).
 *   Bukan NEXT_PUBLIC_*, tidak pernah ke browser.
 * - Transport (docs/04 §7.1): WAJIB TLS terverifikasi. `http://` ditolak kecuali `EFN_ALLOW_PLAINTEXT=true`
 *   di non-production. CA internal dipercaya lewat `NODE_EXTRA_CA_CERTS=/run/secrets/efn_ca_crt`
 *   (dibaca Node saat start; alasan pemilihan & pemeriksaan file CA: lihat `lib/bff-config.ts`).
 *   Konfigurasi tidak aman -> proses gagal start (`instrumentation.ts`) dan setiap request -> 503.
 * - Kontrak: docs/04_SERVICE_CONTRACTS.md §3. Error BFF: `{ error: { code, message } }`.
 * - Tidak pernah melempar ke pemanggil untuk error jaringan/HTTP: hasil berupa union `BffResult`
 *   supaya Route Handler bisa memetakan status secara eksplisit.
 * - Tidak me-log body/nomor HP/token (UU PDP).
 */
import 'server-only';
import { readFileSync } from 'node:fs';
import { assertBffTransport, type BffTransport } from './bff-config';

const DEFAULT_TIMEOUT_MS = 10_000;

let cachedTransport: BffTransport | null = null;

function readFileOrNull(path: string): string | null {
  try {
    return readFileSync(path, 'utf8');
  } catch {
    return null;
  }
}

/** Validasi + cache transport. Melempar `BffConfigError` bila konfigurasi tidak aman. */
export function bffTransport(): BffTransport {
  if (!cachedTransport) {
    cachedTransport = assertBffTransport(process.env, readFileOrNull);
    for (const w of cachedTransport.warnings) console.warn(`[bff] ${w}`);
  }
  return cachedTransport;
}

export function bffBaseUrl(): string {
  return bffTransport().baseUrl;
}

export interface BffErrorBody {
  error?: { code?: string; message?: string };
  [key: string]: unknown;
}

export type BffResult<T> =
  | { ok: true; status: number; data: T }
  | { ok: false; status: number; code: string; message: string; body: BffErrorBody | null };

export interface BffRequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
  /** Bearer access token (dari cookie efn_session). */
  token?: string;
  /** Body JSON. Untuk body stream/multipart gunakan `rawBody`. */
  json?: unknown;
  rawBody?: BodyInit | ReadableStream<Uint8Array>;
  headers?: Record<string, string>;
  timeoutMs?: number;
  /** Header permintaan asli, untuk meneruskan X-Forwarded-For & request id. */
  incoming?: Headers;
}

export const GENERIC_ERROR_MESSAGE = 'Terjadi kendala. Silakan coba lagi beberapa saat lagi.';

export async function bffFetch<T>(path: string, opts: BffRequestOptions = {}): Promise<BffResult<T>> {
  const headers: Record<string, string> = { accept: 'application/json', ...(opts.headers ?? {}) };
  if (opts.token) headers.authorization = `Bearer ${opts.token}`;

  const requestId = opts.incoming?.get('x-request-id') || crypto.randomUUID();
  headers['x-request-id'] = requestId;
  // Asumsi: BFF hanya mempercayai X-Forwarded-For dari jaringan internal (untuk rate limit per IP).
  const xff = opts.incoming?.get('x-forwarded-for');
  if (xff) headers['x-forwarded-for'] = xff;

  let body: BodyInit | ReadableStream<Uint8Array> | undefined;
  if (opts.json !== undefined) {
    headers['content-type'] = 'application/json';
    body = JSON.stringify(opts.json);
  } else if (opts.rawBody !== undefined) {
    body = opts.rawBody;
  }

  const init: RequestInit & { duplex?: 'half' } = {
    method: opts.method ?? (body === undefined ? 'GET' : 'POST'),
    headers,
    body: body as BodyInit | undefined,
    cache: 'no-store',
    redirect: 'manual',
    signal: AbortSignal.timeout(opts.timeoutMs ?? DEFAULT_TIMEOUT_MS),
  };
  // Wajib di Node (undici) saat body berupa stream.
  if (body instanceof ReadableStream) init.duplex = 'half';

  let baseUrl: string;
  try {
    baseUrl = bffBaseUrl();
  } catch (err) {
    // Fail-closed: tidak pernah jatuh ke plaintext. Pesan konfigurasi tidak memuat secret.
    console.error(`[bff] konfigurasi transport ditolak: ${err instanceof Error ? err.message : 'unknown'} req=${requestId}`);
    return {
      ok: false,
      status: 503,
      code: 'bff_misconfigured',
      message: 'Layanan sedang tidak tersedia. Silakan coba lagi.',
      body: null,
    };
  }

  let res: Response;
  try {
    res = await fetch(`${baseUrl}${path}`, init);
  } catch (err) {
    const timedOut = err instanceof Error && (err.name === 'TimeoutError' || err.name === 'AbortError');
    // Kode error (mis. UNABLE_TO_VERIFY_LEAF_SIGNATURE, ERR_TLS_CERT_ALTNAME_INVALID) membantu diagnosa TLS; tanpa PII.
    const cause = err instanceof Error ? (err.cause as { code?: unknown } | undefined) : undefined;
    const causeCode = typeof cause?.code === 'string' ? ` cause=${cause.code}` : '';
    console.error(`[bff] ${init.method} ${path} gagal: ${timedOut ? 'timeout' : 'network'}${causeCode} req=${requestId}`);
    return {
      ok: false,
      status: timedOut ? 504 : 503,
      code: timedOut ? 'bff_timeout' : 'bff_unavailable',
      message: 'Layanan sedang tidak tersedia. Silakan coba lagi.',
      body: null,
    };
  }

  const parsed = await readJson(res);
  if (res.ok) {
    return { ok: true, status: res.status, data: parsed as T };
  }

  const errBody = (parsed && typeof parsed === 'object' ? parsed : null) as BffErrorBody | null;
  if (res.status >= 500) {
    console.error(`[bff] ${init.method} ${path} -> ${res.status} req=${requestId}`);
  }
  return {
    ok: false,
    status: res.status,
    code: errBody?.error?.code ?? `http_${res.status}`,
    message: errBody?.error?.message ?? GENERIC_ERROR_MESSAGE,
    body: errBody,
  };
}

async function readJson(res: Response): Promise<unknown> {
  if (res.status === 204) return null;
  const text = await res.text().catch(() => '');
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

// ---------- Tipe kontrak (docs/04_SERVICE_CONTRACTS.md) ----------

/** §3.2 POST /v1/auth/otp/request -> 202 */
export interface OtpChallenge {
  challenge_id: string;
  expires_in: number;
}

/** §3.2 POST /v1/auth/otp/verify -> 200 */
export interface OtpTokenResponse {
  access_token: string;
  token_type: 'Bearer';
  expires_in: number;
}

/** §3.2/§3.1 POST /v1/auth/otp/verify -> 400 */
export interface OtpVerifyFailure {
  ok: false;
  reason: 'invalid' | 'expired' | 'locked';
  attempts_left: number;
}

/** §3.2 GET /v1/me */
export interface MeResponse {
  member_ref_masked: string;
  points_balance: number;
  /** G22 (§14.C.2): poin yang sedang ditahan untuk pesanan online yang belum dibayar. Opsional. */
  points_reserved?: number;
  /** G22 (§14.D.6): info poin hangus; `expires_on` hanya terisi bila ≤ 60 hari lagi. Opsional; diparse defensif (lib/points.ts). */
  points_expiry?: unknown;
}

export type ReportStatus = 'queued' | 'processing' | 'valid' | 'rejected' | 'flagged';

/** §3.3 POST /v1/market-scout/reports -> 202 */
export interface ReportAccepted {
  report_id: string;
  status: 'queued';
}

/** §3.3 GET /v1/market-scout/reports/:id */
export interface ReportStatusResponse {
  report_id: string;
  status: ReportStatus;
  reason: string | null;
  points_awarded: number;
}

// ---------- §8.4 Privasi (v3). Bentuk yang TIDAK dirinci kontrak = asumsi (lihat laporan Gelombang 5). ----------

/** §8.4 GET /v1/me/consents -> item */
export interface ConsentItem {
  purpose: 'location' | 'marketing';
  granted: boolean;
  version: string;
  updated_at: string;
}

/** ASUMSI: POST /v1/me/reauth `{}` -> 202 (OTP dikirim ke nomor akun sendiri). */
export interface ReauthChallenge {
  challenge_id: string;
  expires_in: number;
}

/** ASUMSI: POST /v1/me/reauth `{ challenge_id, code }` -> 200. Kontrak: token step-up `purpose: dsar`, 10 menit. */
export interface ReauthToken {
  step_up_token: string;
  expires_in: number;
}

/** PDP_DSAR_DESIGN §3: DELETE /v1/me -> 202 */
export interface DeleteAccepted {
  request_id: string;
  scheduled_at: string;
  points_forfeited: number;
}
