import { Provider } from '@nestjs/common';
import { Agent } from 'node:https';
import { Readable } from 'node:stream';
import { Client } from 'minio';
import sharp from 'sharp';
import { AppConfig } from '../config/app-config';
import { Semaphore } from '../common/concurrency/semaphore';
import { detectImageKind } from '../market-scout/image/magic-bytes';

export const CATALOG_STORAGE = Symbol('CATALOG_STORAGE');

/** Penyimpanan gambar katalog (bucket `catalog`, key `<channel>/<image_sha>/<size>.jpg`, §10.8.2). */
export interface CatalogStorage {
  get(key: string): Promise<Buffer | null>;
  put(key: string, data: Buffer): Promise<void>;
  remove(key: string): Promise<void>;
}

const MAX_IMAGE_BYTES = 4 * 1024 * 1024;

export function catalogImageKey(channel: string, sha: string, size: 512 | 1024): string {
  return `${channel}/${sha}/${size}.jpg`;
}

/**
 * MinIO lewat `s3-gateway` (TLS verify-full CA internal, §7.1/§8.14), kredensial `efn-bff` (Get/Put/Delete objek di
 * `catalog`, §10.10). Bucket + SSE dibuat devsecops (`minio-init`); BFF tidak membuat bucket. Lazy: kredensial belum
 * ada tidak menggagalkan start.
 */
export const catalogStorageProvider: Provider = {
  provide: CATALOG_STORAGE,
  inject: [AppConfig],
  useFactory: (config: AppConfig): CatalogStorage => {
    let client: Client | null = null;
    const get = (): Client => {
      if (client) return client;
      const conn = config.minioConnection;
      const transportAgent = conn.tls
        ? new Agent({
            keepAlive: true,
            ca: conn.tls.ca,
            rejectUnauthorized: true,
            minVersion: conn.tls.minVersion,
            ...(conn.tls.servername ? { servername: conn.tls.servername } : {}),
          })
        : undefined;
      client = new Client({
        endPoint: conn.endPoint,
        port: conn.port,
        useSSL: conn.useSSL,
        accessKey: conn.accessKey,
        secretKey: conn.secretKey,
        ...(transportAgent ? { transportAgent } : {}),
      });
      return client;
    };
    const bucket = (): string => config.minioCatalogBucket;
    return {
      get: async (key) => {
        let stream: Readable;
        try {
          stream = (await get().getObject(bucket(), key)) as Readable;
        } catch (err) {
          const code = (err as { code?: unknown } | null)?.code;
          if (code === 'NoSuchKey' || code === 'NotFound') return null;
          throw err;
        }
        const chunks: Buffer[] = [];
        let size = 0;
        for await (const c of stream) {
          const b = Buffer.isBuffer(c) ? c : Buffer.from(c as Uint8Array);
          size += b.length;
          if (size > MAX_IMAGE_BYTES) {
            stream.destroy();
            throw new Error('objek katalog terlalu besar');
          }
          chunks.push(b);
        }
        return Buffer.concat(chunks);
      },
      put: async (key, data) => {
        await get().putObject(bucket(), key, data, data.length, { 'Content-Type': 'image/jpeg' });
      },
      remove: async (key) => {
        await get().removeObject(bucket(), key);
      },
    };
  },
};

export class CatalogImageRejected extends Error {}

const gate = new Semaphore(2, 16, 30_000);

/**
 * Re-encode gambar produk dari Odoo (§10.8.2): magic bytes JPEG/PNG/WebP saja, decode penuh (`failOn: 'error'`, batas
 * piksel), rotasi EXIF, ≤ `size` px tanpa upscale, JPEG q85, metadata tidak disalin. Byte asli tidak pernah disimpan.
 */
export async function normalizeCatalogImage(input: Buffer, size: 512 | 1024): Promise<Buffer> {
  if (input.length === 0 || input.length > 10 * 1024 * 1024) throw new CatalogImageRejected('ukuran gambar tidak valid');
  if (!detectImageKind(input)) throw new CatalogImageRejected('format gambar tidak didukung');
  const release = await gate.acquire();
  try {
    return await sharp(input, { failOn: 'error', limitInputPixels: 50_000_000 })
      .rotate()
      .resize({ width: size, height: size, fit: 'inside', withoutEnlargement: true })
      .jpeg({ quality: 85 })
      .toBuffer();
  } catch (err) {
    if (err instanceof CatalogImageRejected) throw err;
    throw new CatalogImageRejected('gambar tidak dapat di-decode');
  } finally {
    release();
  }
}
