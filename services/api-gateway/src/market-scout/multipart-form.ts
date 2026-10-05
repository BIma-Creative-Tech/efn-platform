import { ApiError, Errors } from '../common/errors/api-error';
import { RawReportForm } from './dto/report.dto';

/** Subset request yang ditambahkan @fastify/multipart (dideklarasikan lokal agar tidak bergantung augmentasi tipe). */
export interface MultipartRequest {
  isMultipart(): boolean;
  parts(): AsyncIterable<unknown>;
}

interface PartLike {
  fieldname?: unknown;
  value?: unknown;
  toBuffer?: () => Promise<Buffer>;
  file?: { truncated?: boolean };
}

export const ALLOWED_TEXT_FIELDS: ReadonlySet<string> = new Set([
  'product_name',
  'price',
  'competitor_name',
  'lat',
  'lng',
]);
const PHOTO_FIELD = 'photo';

export interface ReadFormOptions {
  maxFileBytes: number;
  maxFieldLength?: number;
}

/**
 * Membaca multipart secara streaming. Upload chunked tanpa Content-Length (dari Next.js) didukung karena
 * batas ditegakkan oleh busboy (`limits.fileSize`) per byte yang mengalir, bukan oleh header.
 * Error plugin dipetakan ke kode kontrak: 413 (terlalu besar), 415 (bukan multipart), 400 (lainnya).
 */
export async function readReportForm(parts: AsyncIterable<unknown>, opts: ReadFormOptions): Promise<RawReportForm> {
  const maxFieldLength = opts.maxFieldLength ?? 1000;
  const fields: Record<string, string> = {};
  let photo: Buffer | null = null;
  try {
    for await (const raw of parts) {
      const part = raw as PartLike;
      const fieldname = typeof part.fieldname === 'string' ? part.fieldname : '';
      if (typeof part.toBuffer === 'function') {
        // Stream file harus dikonsumsi agar parser bisa lanjut ke bagian berikutnya
        const buf = await part.toBuffer();
        if (part.file?.truncated || buf.length > opts.maxFileBytes) throw Errors.payloadTooLarge();
        if (fieldname !== PHOTO_FIELD || photo !== null) {
          throw Errors.invalidRequest('Hanya satu berkas "photo" yang diizinkan.');
        }
        photo = buf;
        continue;
      }
      if (!ALLOWED_TEXT_FIELDS.has(fieldname)) continue;
      if (typeof part.value !== 'string') continue;
      if (part.value.length > maxFieldLength) throw Errors.invalidRequest();
      fields[fieldname] = part.value;
    }
  } catch (err) {
    if (err instanceof ApiError) throw err;
    const code = (err as { code?: unknown } | null)?.code;
    if (code === 'FST_REQ_FILE_TOO_LARGE') throw Errors.payloadTooLarge();
    if (code === 'FST_INVALID_MULTIPART_CONTENT_TYPE') throw Errors.unsupportedMediaType();
    if (code === 'FST_FILES_LIMIT' || code === 'FST_PARTS_LIMIT' || code === 'FST_FIELDS_LIMIT') {
      throw Errors.invalidRequest('Terlalu banyak bagian form.');
    }
    throw Errors.invalidRequest('Form tidak dapat dibaca.');
  }
  return { fields, photo };
}
