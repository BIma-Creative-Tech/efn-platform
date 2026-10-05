import { Injectable, Logger, OnApplicationBootstrap, OnApplicationShutdown } from '@nestjs/common';
import { IncomingMessage, Server as HttpServer, ServerResponse, createServer as createHttpServer } from 'node:http';
import { Server as HttpsServer, createServer as createHttpsServer } from 'node:https';
import { PeerCertificate, TLSSocket } from 'node:tls';
import { AppConfig, TLS_MIN_VERSION } from '../../config/app-config';
import { MetricsService } from './metrics.service';

/**
 * `/metrics` BFF dengan mTLS (docs/04 §8.5, §8.10; menutup sisi BFF dari pengecualian E12 / SEC-20, SEC-30).
 *
 * Fastify/Nest tidak bisa meminta sertifikat klien hanya untuk satu path, jadi metrics dilayani listener HTTPS
 * TERPISAH di `METRICS_PORT` (default **9464**) dengan `requestCert: true, rejectUnauthorized: true` terhadap CA internal
 * (`METRICS_CLIENT_CA_FILE`, default `/run/secrets/efn_ca_crt`) dan sertifikat server yang sama dengan API
 * (`TLS_CERT_FILE`/`TLS_KEY_FILE`, SAN `api-gateway`). Hanya klien dengan CN atau SAN DNS di
 * `METRICS_TLS_CLIENT_ALLOWED_CN` (default `prometheus`) yang dilayani; lainnya 403. Hanya `GET /metrics`.
 * Port API utama (3000) TIDAK lagi melayani `/metrics`.
 * Plaintext (tanpa TLS) hanya bila `EFN_ALLOW_PLAINTEXT` (non-production), sama seperti server utama.
 */
export function clientNameAllowed(cert: Pick<PeerCertificate, 'subject' | 'subjectaltname'> | null | undefined, allowed: readonly string[]): boolean {
  if (!cert || !cert.subject) return false;
  const names = new Set<string>();
  const cn = (cert.subject as { CN?: string | string[] }).CN;
  for (const v of Array.isArray(cn) ? cn : cn ? [cn] : []) names.add(v.toLowerCase());
  for (const part of (cert.subjectaltname ?? '').split(',')) {
    const m = /^\s*DNS:(.+)$/.exec(part);
    if (m) names.add(m[1].trim().toLowerCase());
  }
  return allowed.some((a) => names.has(a.toLowerCase()));
}

@Injectable()
export class MetricsServer implements OnApplicationBootstrap, OnApplicationShutdown {
  private readonly logger = new Logger(MetricsServer.name);
  private server: HttpsServer | HttpServer | null = null;

  constructor(
    private readonly config: AppConfig,
    private readonly metrics: MetricsService,
  ) {}

  async onApplicationBootstrap(): Promise<void> {
    const port = this.config.metricsPort;
    if (port === 0) return;
    const https = this.config.httpsServerOptions;
    const allowed = this.config.metricsAllowedClientNames;
    const handler = (req: IncomingMessage, res: ServerResponse): void => void this.handle(req, res, https !== null, allowed);
    if (https) {
      this.server = createHttpsServer(
        {
          cert: https.cert,
          key: https.key,
          ca: this.config.metricsClientCa,
          requestCert: true,
          rejectUnauthorized: true,
          minVersion: TLS_MIN_VERSION,
        },
        handler,
      );
    } else {
      this.server = createHttpServer(handler); // hanya non-production + EFN_ALLOW_PLAINTEXT (divalidasi AppConfig)
    }
    await new Promise<void>((resolve, reject) => {
      this.server!.once('error', reject);
      this.server!.listen(port, '0.0.0.0', () => resolve());
    });
    this.logger.log(`Metrics ${https ? 'HTTPS mTLS' : 'HTTP (plaintext, non-production)'} di :${port} (klien: ${allowed.join(', ')})`);
  }

  async onApplicationShutdown(): Promise<void> {
    const s = this.server;
    this.server = null;
    if (s) await new Promise<void>((resolve) => s.close(() => resolve()));
  }

  private async handle(req: IncomingMessage, res: ServerResponse, tls: boolean, allowed: readonly string[]): Promise<void> {
    if (tls) {
      const cert = (req.socket as TLSSocket).getPeerCertificate?.();
      if (!clientNameAllowed(cert, allowed)) {
        res.writeHead(403).end();
        return;
      }
    }
    if (req.method !== 'GET' || (req.url ?? '').split('?')[0] !== '/metrics') {
      res.writeHead(404).end();
      return;
    }
    try {
      const body = await this.metrics.render();
      res.writeHead(200, { 'Content-Type': this.metrics.contentType, 'Cache-Control': 'no-store' }).end(body);
    } catch {
      res.writeHead(500).end();
    }
  }
}
