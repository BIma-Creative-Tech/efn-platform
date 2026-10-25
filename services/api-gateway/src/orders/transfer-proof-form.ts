import { ApiError, Errors } from '../common/errors/api-error';
import { detectImageKind } from '../market-scout/image/magic-bytes';

interface PartLike {
  fieldname?: unknown;
  toBuffer?: () => Promise<Buffer>;
  file?: { truncated?: boolean };
}

/**
 * G26 §17.4: form unggah bukti transfer — TEPAT satu berkas `photo`, TANPA field teks (tidak ada catatan bebas → tidak
 * ada PII tambahan). Batas byte ditegakkan busboy per byte mengalir (chunked tanpa Content-Length juga). Magic bytes
 * JPEG/PNG/WebP dicek sebelum decode (`415 transfer_proof_invalid`); decode penuh + re-encode oleh ImageProcessor.
 */
export async function readTransferProofForm(parts: AsyncIterable<unknown>, opts: { maxFileBytes: number }): Promise<Buffer> {
  let photo: Buffer | null = null;
  try {
    for await (const raw of parts) {
      const part = raw as PartLike;
      if (typeof part.toBuffer !== 'function') throw Errors.invalidRequest('Hanya berkas "photo" yang diizinkan.');
      const buf = await part.toBuffer();
      if (part.file?.truncated || buf.length > opts.maxFileBytes) throw Errors.payloadTooLarge();
      if (part.fieldname !== 'photo' || photo !== null) throw Errors.invalidRequest('Hanya satu berkas "photo" yang diizinkan.');
      photo = buf;
    }
  } catch (err) {
    if (err instanceof ApiError) throw err;
    const code = (err as { code?: unknown } | null)?.code;
    if (code === 'FST_REQ_FILE_TOO_LARGE') throw Errors.payloadTooLarge();
    if (code === 'FST_INVALID_MULTIPART_CONTENT_TYPE') throw Errors.unsupportedMediaType();
    if (code === 'FST_FILES_LIMIT' || code === 'FST_PARTS_LIMIT' || code === 'FST_FIELDS_LIMIT') throw Errors.invalidRequest('Terlalu banyak bagian form.');
    throw Errors.invalidRequest('Form tidak dapat dibaca.');
  }
  if (!photo || photo.length === 0) throw Errors.invalidRequest('Berkas "photo" wajib.');
  if (detectImageKind(photo) === null) {
    throw ApiError.of(415, 'transfer_proof_invalid', 'Foto bukti tidak dapat dibaca. Kirim foto JPG, PNG, atau WebP.');
  }
  return photo;
}
