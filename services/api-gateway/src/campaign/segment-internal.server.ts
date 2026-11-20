import { Injectable, Logger, OnApplicationBootstrap, OnApplicationShutdown } from '@nestjs/common';
import { IncomingMessage, ServerResponse } from 'node:http';
import { Server as HttpsServer, createServer as createHttpsServer } from 'node:https';
import { TLSSocket } from 'node:tls';
import { AppConfig, TLS_MIN_VERSION } from '../config/app-config';
import { clientNameAllowed } from '../common/metrics/metrics-server';
import { SegmentIngestService } from './segment-ingest.service';

export const SEGMENT_PATH_RE = /^\/internal\/v1\/segments\/([0-9a-fA-F-]{36})\/members$/;
export const SEGMENT_MAX_BODY_BYTES = 512 * 1024;

/**
 * Listener HTTPS mTLS untuk `POST /internal/v1/segments/{run_id}/members` (docs/04 §28.7). Fastify tidak bisa meminta
 * sertifikat klien per path, jadi endpoint ini dilayani listener TERPISAH (pola `/metrics`, §8.5) di
 * `SEGMENT_INTERNAL_PORT` (default 9466) dengan `requestCert + rejectUnauthorized` terhadap CA internal
 * (`SEGMENT_CLIENT_CA_FILE`) dan sertifikat server BFF. Hanya CN/SAN di `SEGMENT_TLS_CLIENT_ALLOWED_CN` (default
 * `segment-worker`). TANPA TLS listener tidak dibuka (tidak ada mode plaintext, berbeda dengan metrics) — endpoint
 * menulis data. Hanya dibuka bila `PROMO_CAMPAIGN_ENABLED=true`. Body ≤ 512 KB, JSON. Error generik tanpa detail.
 */
@Injectable()
export class SegmentInternalServer implements OnApplicationBootstrap, OnApplicationShutdown {
  private readonly logger = new Logger(SegmentInternalServer.name);
  private server: HttpsServer | null = null;

  constructor(
    private readonly config: AppConfig,
    private readonly ingest: SegmentIngestService,
  ) {}

  async onApplicationBootstrap(): Promise<void> {
    const port = this.config.segmentInternalPort;
    if (!this.config.promoCampaignEnabled || port === 0) return;
    const https = this.config.httpsServerOptions;
    if (!https) {
      this.logger.warn('Endpoint segmen TIDAK dibuka: butuh TLS_CERT_FILE/TLS_KEY_FILE (mTLS wajib, tanpa mode plaintext)');
      return;
    }
    const allowed = this.config.segmentAllowedClientNames;
    this.server = createHttpsServer(
      { cert: https.cert, key: https.key, ca: this.config.segmentClientCa, requestCert: true, rejectUnauthorized: true, minVersion: TLS_MIN_VERSION },
      (req, res) => void this.handle(req, res, allowed),
    );
    await new Promise<void>((resolve, reject) => {
      this.server!.once('error', reject);
      this.server!.listen(port, '0.0.0.0', () => resolve());
    });
    this.logger.log(`Endpoint segmen HTTPS mTLS di :${port} (klien: ${allowed.join(', ')})`);
  }

  async onApplicationShutdown(): Promise<void> {
    const s = this.server;
    this.server = null;
    if (s) await new Promise<void>((resolve) => s.close(() => resolve()));
  }

  async handle(req: IncomingMessage, res: ServerResponse, allowed: readonly string[]): Promise<void> {
    const reply = (status: number, body: unknown): void => {
      res.writeHead(status, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' }).end(JSON.stringify(body));
    };
    const cert = (req.socket as TLSSocket).getPeerCertificate?.();
    if (!clientNameAllowed(cert, allowed)) return reply(403, { error: { code: 'forbidden', message: 'Akses ditolak.' } });
    const m = SEGMENT_PATH_RE.exec((req.url ?? '').split('?')[0] ?? '');
    if (!m) return reply(404, { error: { code: 'not_found', message: 'Data tidak ditemukan.' } });
    if (req.method !== 'POST') return reply(405, { error: { code: 'method_not_allowed', message: 'Metode tidak diizinkan.' } });
    if (!String(req.headers['content-type'] ?? '').toLowerCase().startsWith('application/json')) {
      return reply(415, { error: { code: 'unsupported_media_type', message: 'Gunakan application/json.' } });
    }
    let raw: Buffer;
    try {
      raw = await readBody(req, SEGMENT_MAX_BODY_BYTES);
    } catch {
      return reply(413, { error: { code: 'payload_too_large', message: 'Ukuran permintaan melebihi batas.' } });
    }
    let body: unknown;
    try {
      body = JSON.parse(raw.toString('utf8'));
    } catch {
      return reply(400, { error: { code: 'invalid_request', message: 'Permintaan tidak valid.' } });
    }
    try {
      const out = await this.ingest.ingest(m[1]!, body);
      return reply(out.status, out.body);
    } catch (err) {
      this.logger.warn(`Ingest segmen gagal: ${err instanceof Error ? err.message : 'db'}`);
      return reply(503, { error: { code: 'service_unavailable', message: 'Layanan sedang tidak tersedia. Silakan coba lagi.' } });
    }
  }
}

/** Baca body dengan batas byte yang mengalir (berlaku juga untuk chunked tanpa Content-Length). */
export function readBody(req: IncomingMessage, max: number): Promise<Buffer> {
  return new Promise((resolve, reject) => {
    const declared = Number(req.headers['content-length'] ?? '0');
    if (Number.isFinite(declared) && declared > max) {
      reject(new Error('too large'));
      req.resume();
      return;
    }
    const chunks: Buffer[] = [];
    let size = 0;
    let done = false;
    req.on('data', (c: Buffer) => {
      if (done) return;
      size += c.length;
      if (size > max) {
        done = true;
        reject(new Error('too large'));
        req.resume();
        return;
      }
      chunks.push(c);
    });
    req.on('end', () => {
      if (!done) {
        done = true;
        resolve(Buffer.concat(chunks));
      }
    });
    req.on('error', (e) => {
      if (!done) {
        done = true;
        reject(e);
      }
    });
  });
}
