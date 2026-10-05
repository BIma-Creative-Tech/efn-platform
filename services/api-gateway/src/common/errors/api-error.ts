import { HttpException } from '@nestjs/common';

/**
 * Error HTTP dengan body yang sudah berbentuk kontrak (docs/04 §3):
 * - umum: `{ "error": { "code": "<snake_case>", "message": "<pesan generik Bahasa Indonesia>" } }`
 * - khusus OTP verify / consume token: `{ ok: false, reason, ... }`
 *
 * Pesan selalu generik (ditampilkan apa adanya oleh frontend) dan tidak membocorkan detail internal
 * atau apakah nomor terdaftar.
 */
export interface ErrorBody {
  error: { code: string; message: string };
}

export class ApiError extends HttpException {
  constructor(
    status: number,
    public readonly body: object,
    public readonly headers: Readonly<Record<string, string>> = {},
  ) {
    super(body as Record<string, unknown>, status);
  }

  static of(status: number, code: string, message: string, headers?: Record<string, string>): ApiError {
    const body: ErrorBody = { error: { code, message } };
    return new ApiError(status, body, headers);
  }
}

export const Errors = {
  invalidRequest: (message = 'Permintaan tidak valid.'): ApiError => ApiError.of(400, 'invalid_request', message),
  /** Kontrak §3.1 v2 / SEC-06: hanya nomor Indonesia `^62[0-9]{8,13}$`. */
  invalidPhone: (): ApiError =>
    ApiError.of(400, 'invalid_phone', 'Nomor HP tidak valid. Gunakan nomor Indonesia (diawali 08 atau 62).'),
  unauthorized: (): ApiError => ApiError.of(401, 'unauthorized', 'Sesi tidak valid. Silakan masuk kembali.'),
  forbidden: (): ApiError => ApiError.of(403, 'forbidden', 'Akses ditolak.'),
  notFound: (): ApiError => ApiError.of(404, 'not_found', 'Data tidak ditemukan.'),
  payloadTooLarge: (): ApiError => ApiError.of(413, 'payload_too_large', 'Ukuran berkas melebihi batas.'),
  unsupportedMediaType: (): ApiError =>
    ApiError.of(415, 'unsupported_media_type', 'Format berkas tidak didukung. Gunakan foto JPEG, PNG, atau WebP.'),
  rateLimited: (retryAfterSeconds: number): ApiError =>
    ApiError.of(429, 'rate_limited', 'Terlalu banyak permintaan. Silakan coba lagi nanti.', {
      'Retry-After': String(Math.max(1, Math.ceil(retryAfterSeconds))),
    }),
  serviceUnavailable: (code = 'service_unavailable'): ApiError =>
    ApiError.of(503, code, 'Layanan sedang tidak tersedia. Silakan coba lagi.'),
  invalidSignature: (): ApiError => ApiError.of(401, 'invalid_signature', 'Tanda tangan tidak valid.'),
  /** docs/04 §8.1: payload ALTCHA tidak ada / tidak valid / kedaluwarsa / sudah dipakai (tidak dibedakan). */
  captchaRequired: (): ApiError =>
    ApiError.of(400, 'captcha_required', 'Verifikasi keamanan diperlukan. Muat ulang halaman lalu coba lagi.'),
  /** docs/04 §8.4: aksi DSAR sensitif butuh token step-up (re-OTP) yang valid. */
  stepUpRequired: (): ApiError =>
    ApiError.of(403, 'step_up_required', 'Konfirmasi ulang dengan kode OTP diperlukan untuk tindakan ini.'),
  stepUpInvalid: (): ApiError =>
    ApiError.of(403, 'step_up_invalid', 'Konfirmasi OTP sudah kedaluwarsa atau tidak valid. Ulangi konfirmasi.'),
  conflict: (code: string, message = 'Permintaan bentrok dengan data yang ada.'): ApiError => ApiError.of(409, code, message),
  paymentUnavailable: (): ApiError =>
    ApiError.of(503, 'payment_provider_unavailable', 'Pembayaran QRIS sedang tidak tersedia. Gunakan metode lain.'),
  paymentProviderError: (): ApiError =>
    ApiError.of(502, 'payment_provider_error', 'Penyedia pembayaran tidak merespons. Silakan coba lagi.'),
};
