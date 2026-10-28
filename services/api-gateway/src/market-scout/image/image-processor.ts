import { Injectable } from '@nestjs/common';
import sharp from 'sharp';
import { AppConfig } from '../../config/app-config';
import { Semaphore, SemaphoreFullError, SemaphoreTimeoutError } from '../../common/concurrency/semaphore';
import { Errors } from '../../common/errors/api-error';
import { dHashFromGrey9x8 } from './dhash';

export interface NormalizedImage {
  jpeg: Buffer;
  width: number;
  height: number;
  /** dHash 64-bit (16 hex) — tidak lagi ditulis BFF (pemilik `image_phash` = ai-ocr-worker). */
  phash: string;
}

export const MAX_DIMENSION = 2048;
export const JPEG_QUALITY = 85;
/** Batas piksel input (anti decompression bomb), ~50 MP. */
export const MAX_INPUT_PIXELS = 50_000_000;
/** Retry-After saat antrean decode penuh. */
export const IMAGE_BUSY_RETRY_SECONDS = 5;

let sharpConfigured = false;

/**
 * Re-encode foto Price Hunter (kontrak §3.3, R-S06):
 * decode penuh (failOn 'error') → rotasi sesuai EXIF → resize maks 2048 px (tanpa upscale) →
 * JPEG q85. Metadata (EXIF/GPS/ICC) tidak disalin karena `withMetadata()` tidak dipanggil,
 * dan byte asli (termasuk payload polyglot) tidak pernah disimpan.
 *
 * SEC-19 (batas memori/CPU decode): `sharp.concurrency(SHARP_CONCURRENCY)` (thread libvips per operasi, default 1),
 * cache libvips dimatikan, dan semaphore per proses `IMAGE_MAX_CONCURRENCY` (default 2) dengan antrean
 * `IMAGE_QUEUE_MAX` (default 8) & tunggu maks `IMAGE_QUEUE_TIMEOUT_MS`. Antrean penuh/timeout → `429 rate_limited`
 * (Retry-After 5 s). Puncak memori ≈ IMAGE_MAX_CONCURRENCY × (50 MP × 4 byte) ≈ 400 MB per replika pada default.
 */
@Injectable()
export class ImageProcessor {
  private readonly gate: Semaphore;

  constructor(config: AppConfig) {
    if (!sharpConfigured) {
      sharp.concurrency(config.sharpThreads);
      sharp.cache(false);
      sharpConfigured = true;
    }
    this.gate = new Semaphore(config.imageMaxConcurrency, config.imageQueueMax, config.imageQueueTimeoutMs);
  }

  /**
   * `opts` (G26 §17.4): bukti transfer memakai sisi maks lebih kecil agar JPEG ≤ 1,5 MiB (batas Odoo); default = Price
   * Hunter (2048 px, q85). Semaphore/antrean decode sama untuk semua pemakai.
   */
  async normalize(input: Buffer, opts: { maxDimension?: number; quality?: number } = {}): Promise<NormalizedImage> {
    const maxDim = opts.maxDimension ?? MAX_DIMENSION;
    const quality = opts.quality ?? JPEG_QUALITY;
    let release: () => void;
    try {
      release = await this.gate.acquire();
    } catch (err) {
      if (err instanceof SemaphoreFullError || err instanceof SemaphoreTimeoutError) {
        throw Errors.rateLimited(IMAGE_BUSY_RETRY_SECONDS);
      }
      throw err;
    }
    try {
      const { data, info } = await sharp(input, { failOn: 'error', limitInputPixels: MAX_INPUT_PIXELS })
        .rotate()
        .resize({ width: maxDim, height: maxDim, fit: 'inside', withoutEnlargement: true })
        .jpeg({ quality })
        .toBuffer({ resolveWithObject: true });
      const phash = await differenceHash(data);
      return { jpeg: data, width: info.width, height: info.height, phash };
    } finally {
      release();
    }
  }
}

/** dHash: grayscale 9x8, bandingkan piksel bertetangga horizontal → 64 bit → 16 hex. */
export async function differenceHash(image: Buffer): Promise<string> {
  const raw = await sharp(image).greyscale().resize(9, 8, { fit: 'fill' }).raw().toBuffer();
  return dHashFromGrey9x8(raw);
}
