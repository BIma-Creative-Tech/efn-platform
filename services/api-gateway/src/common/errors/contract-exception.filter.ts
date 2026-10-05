import { ArgumentsHost, Catch, ExceptionFilter, HttpException, Logger } from '@nestjs/common';
import { FastifyReply } from 'fastify';
import { ApiError, ErrorBody } from './api-error';

export interface ErrorResponse {
  status: number;
  body: object;
  headers: Record<string, string>;
}

const STATUS_CODES: Record<number, { code: string; message: string }> = {
  400: { code: 'invalid_request', message: 'Permintaan tidak valid.' },
  401: { code: 'unauthorized', message: 'Sesi tidak valid. Silakan masuk kembali.' },
  403: { code: 'forbidden', message: 'Akses ditolak.' },
  404: { code: 'not_found', message: 'Data tidak ditemukan.' },
  405: { code: 'method_not_allowed', message: 'Metode tidak diizinkan.' },
  406: { code: 'not_acceptable', message: 'Permintaan tidak dapat diproses.' },
  408: { code: 'request_timeout', message: 'Waktu permintaan habis. Silakan coba lagi.' },
  409: { code: 'conflict', message: 'Permintaan bentrok dengan data yang ada.' },
  413: { code: 'payload_too_large', message: 'Ukuran berkas melebihi batas.' },
  415: { code: 'unsupported_media_type', message: 'Format tidak didukung.' },
  422: { code: 'invalid_request', message: 'Permintaan tidak valid.' },
  429: { code: 'rate_limited', message: 'Terlalu banyak permintaan. Silakan coba lagi nanti.' },
  503: { code: 'service_unavailable', message: 'Layanan sedang tidak tersedia. Silakan coba lagi.' },
};

const INTERNAL: ErrorBody = { error: { code: 'internal_error', message: 'Terjadi kesalahan. Silakan coba lagi.' } };

function generic(status: number): ErrorBody {
  const known = STATUS_CODES[status];
  if (known) return { error: { ...known } };
  if (status >= 500) return INTERNAL;
  return { error: { code: 'request_error', message: 'Permintaan tidak dapat diproses.' } };
}

/**
 * Filter global: semua error keluar dalam format kontrak. Detail internal (stack, pesan library,
 * query) hanya masuk log server, tidak ke respons.
 */
@Catch()
export class ContractExceptionFilter implements ExceptionFilter {
  private readonly logger = new Logger('HttpError');

  catch(exception: unknown, host: ArgumentsHost): void {
    const reply = host.switchToHttp().getResponse<FastifyReply>();
    const res = this.toResponse(exception);
    for (const [k, v] of Object.entries(res.headers)) reply.header(k, v);
    void reply.status(res.status).send(res.body);
  }

  toResponse(exception: unknown): ErrorResponse {
    if (exception instanceof ApiError) {
      return { status: exception.getStatus(), body: exception.body, headers: { ...exception.headers } };
    }
    if (exception instanceof HttpException) {
      const status = exception.getStatus();
      if (status >= 500) this.logger.error(`HttpException ${status}: ${exception.message}`);
      return { status, body: generic(status), headers: {} };
    }
    // Error Fastify (content-type parser, body limit, multipart) membawa statusCode 4xx
    const statusCode = (exception as { statusCode?: unknown } | null)?.statusCode;
    if (typeof statusCode === 'number' && statusCode >= 400 && statusCode < 500) {
      return { status: statusCode, body: generic(statusCode), headers: {} };
    }
    const err = exception instanceof Error ? exception : new Error('non-error thrown');
    this.logger.error(`Unhandled error: ${err.name}: ${err.message}`, err.stack);
    return { status: 500, body: INTERNAL, headers: {} };
  }
}
