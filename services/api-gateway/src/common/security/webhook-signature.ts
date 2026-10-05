import { createHmac, timingSafeEqual } from 'node:crypto';

/**
 * Verifikasi webhook payment/3PL (kontrak §2, R-S09):
 *   header `X-EFN-Signature: t=<unix>,v1=<hex>`; HMAC-SHA256 dengan secret `webhook_payment_secret`;
 *   replay window 300 s.
 *
 * Interpretasi string yang ditandatangani: `"<t>." + rawBody` (gaya Stripe). Timestamp WAJIB ikut
 * ditandatangani — bila hanya raw body yang di-HMAC, penyerang bisa mengganti `t` dan replay window
 * tidak berarti. (Perlu dikonfirmasi di kontrak; lihat laporan agent.)
 * Beberapa `v1=` diperbolehkan (rotasi secret di sisi pengirim).
 * Idempotency key per event tetap wajib di endpoint (signature valid ≠ event belum pernah diproses).
 */
export const WEBHOOK_SIGNATURE_HEADER = 'x-efn-signature';
export const WEBHOOK_TOLERANCE_SECONDS = 300;

export type WebhookVerifyFailure =
  | 'missing_signature'
  | 'missing_body'
  | 'malformed_signature'
  | 'timestamp_out_of_window'
  | 'signature_mismatch';

export type WebhookVerifyResult = { ok: true; timestamp: number } | { ok: false; reason: WebhookVerifyFailure };

export interface ParsedSignature {
  timestamp: number;
  signatures: string[];
}

export function parseSignatureHeader(header: string): ParsedSignature | null {
  if (header.length > 1024) return null;
  let timestamp: number | null = null;
  const signatures: string[] = [];
  for (const part of header.split(',')) {
    const idx = part.indexOf('=');
    if (idx <= 0) return null;
    const key = part.slice(0, idx).trim();
    const value = part.slice(idx + 1).trim();
    if (key === 't') {
      if (!/^\d{1,12}$/.test(value) || timestamp !== null) return null;
      timestamp = Number(value);
    } else if (key === 'v1') {
      if (!/^[0-9a-fA-F]{64}$/.test(value)) return null;
      signatures.push(value.toLowerCase());
    }
    // skema lain (v0, dst.) diabaikan
  }
  if (timestamp === null || signatures.length === 0) return null;
  return { timestamp, signatures };
}

export function computeWebhookSignature(secret: string, timestamp: number, rawBody: Buffer): string {
  return createHmac('sha256', secret).update(`${timestamp}.`, 'utf8').update(rawBody).digest('hex');
}

export interface VerifyWebhookParams {
  header: string | string[] | undefined;
  rawBody: Buffer | undefined;
  secret: string;
  nowSeconds: number;
  toleranceSeconds?: number;
}

export function verifyWebhookSignature(p: VerifyWebhookParams): WebhookVerifyResult {
  if (typeof p.header !== 'string' || p.header.length === 0) return { ok: false, reason: 'missing_signature' };
  if (!p.rawBody) return { ok: false, reason: 'missing_body' };
  const parsed = parseSignatureHeader(p.header);
  if (!parsed) return { ok: false, reason: 'malformed_signature' };
  const tolerance = p.toleranceSeconds ?? WEBHOOK_TOLERANCE_SECONDS;
  if (Math.abs(p.nowSeconds - parsed.timestamp) > tolerance) return { ok: false, reason: 'timestamp_out_of_window' };

  const expected = Buffer.from(computeWebhookSignature(p.secret, parsed.timestamp, p.rawBody), 'hex');
  let match = false;
  for (const sig of parsed.signatures) {
    // Bandingkan semua kandidat (tanpa short-circuit) agar waktu tidak bergantung posisi
    if (timingSafeEqual(expected, Buffer.from(sig, 'hex'))) match = true;
  }
  return match ? { ok: true, timestamp: parsed.timestamp } : { ok: false, reason: 'signature_mismatch' };
}
