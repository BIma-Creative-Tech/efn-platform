import { Provider } from '@nestjs/common';
import { Agent } from 'node:https';
import { Client } from 'minio';
import { AppConfig, MinioConnectionConfig } from '../../config/app-config';

export const OBJECT_STORAGE = Symbol('OBJECT_STORAGE');

/**
 * Provider lazy: kredensial MinIO yang belum dikonfigurasi tidak boleh menggagalkan start BFF (OTP/POS tetap jalan);
 * kegagalan muncul saat operasi pertama. Dipakai MarketScoutModule & PrivacyModule (hapus foto saat erasure).
 */
export const objectStorageProvider: Provider = {
  provide: OBJECT_STORAGE,
  inject: [AppConfig],
  useFactory: (config: AppConfig): ObjectStorage => {
    let impl: MinioObjectStorage | null = null;
    const get = (): MinioObjectStorage => (impl ??= new MinioObjectStorage(config.minioConnection));
    return {
      put: async (bucket, key, data, contentType) => get().put(bucket, key, data, contentType),
      remove: async (bucket, key) => get().remove(bucket, key),
    };
  },
};

export interface ObjectStorage {
  put(bucket: string, key: string, data: Buffer, contentType: string): Promise<void>;
  remove(bucket: string, key: string): Promise<void>;
}

/**
 * MinIO (S3-compatible) via SDK resmi `minio`.
 *
 * - TLS (docs/04 §7.1): `useSSL` + `transportAgent` https.Agent dengan CA internal, `rejectUnauthorized`, TLS ≥ 1.2.
 * - Kredensial user khusus `efn-bff` (docs/04 §7.3) yang hanya berhak `PutObject`/`DeleteObject` pada
 *   `market-scout/reports/*`. Karena itu BFF TIDAK lagi memanggil `bucketExists`/`makeBucket`
 *   (bucket + SSE-S3 + lifecycle dibuat `minio-init` oleh DevSecOps).
 */
export class MinioObjectStorage implements ObjectStorage {
  private readonly client: Client;

  constructor(conn: MinioConnectionConfig) {
    const transportAgent = conn.tls
      ? new Agent({
          keepAlive: true,
          ca: conn.tls.ca,
          rejectUnauthorized: true,
          minVersion: conn.tls.minVersion,
          ...(conn.tls.servername ? { servername: conn.tls.servername } : {}),
        })
      : undefined;
    this.client = new Client({
      endPoint: conn.endPoint,
      port: conn.port,
      useSSL: conn.useSSL,
      accessKey: conn.accessKey,
      secretKey: conn.secretKey,
      ...(transportAgent ? { transportAgent } : {}),
    });
  }

  async put(bucket: string, key: string, data: Buffer, contentType: string): Promise<void> {
    await this.client.putObject(bucket, key, data, data.length, { 'Content-Type': contentType });
  }

  async remove(bucket: string, key: string): Promise<void> {
    await this.client.removeObject(bucket, key);
  }
}
