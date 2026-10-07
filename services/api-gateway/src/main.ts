import { GLOBAL_PREFIX, GLOBAL_PREFIX_EXCLUDE } from './common/http/global-prefix';
import { Logger } from '@nestjs/common';
import { NestFactory } from '@nestjs/core';
import { FastifyAdapter, NestFastifyApplication } from '@nestjs/platform-fastify';
import fastifyHelmet from '@fastify/helmet';
import fastifyMultipart from '@fastify/multipart';
import { AppModule } from './app.module';
import { AppConfig } from './config/app-config';

type FastifyAdapterOptions = ConstructorParameters<typeof FastifyAdapter>[0];

async function bootstrap(): Promise<void> {
  const logger = new Logger('Bootstrap');
  // SEC-29: secret dibaca dari file ke memori config (tidak ke env) lalu gagal cepat bila secret wajib tidak ada (pesan hanya memuat nama secret)
  const config = AppConfig.fromProcessEnv();
  config.assertStartupSecrets();
  // docs/04 §7.1: semua kanal TLS terverifikasi; CA/sertifikat tidak terbaca → gagal start (tanpa fallback plaintext)
  const { plaintextChannels } = config.assertTransportSecurity();
  if (plaintextChannels.length > 0) {
    logger.warn(
      `EFN_ALLOW_PLAINTEXT aktif (non-production): kanal TANPA TLS → ${plaintextChannels.join(', ')}. Jangan dipakai dengan data nyata.`,
    );
  }

  const https = config.httpsServerOptions;
  const adapterOptions = {
    // TLS server BFF (docs/04 §7.1): Traefik, Next.js, Odoo, Prometheus memanggil https://api-gateway:3000
    // dan memverifikasi sertifikat dengan CA internal. TLS ≥ 1.2.
    ...(https ? { https: { key: https.key, cert: https.cert, minVersion: https.minVersion } } : {}),
    // Hanya hop tepercaya (TRUSTED_PROXIES: subnet docker + rentang Cloudflare) yang boleh menyetel
    // X-Forwarded-For, sehingga req.ip = IP pelanggan asli dan XFF palsu dari internet diabaikan (SEC-03).
    trustProxy: config.trustProxy,
    bodyLimit: 1024 * 1024, // JSON; multipart punya batas sendiri di bawah
  };
  const app = await NestFactory.create<NestFastifyApplication>(
    AppModule,
    new FastifyAdapter(adapterOptions as FastifyAdapterOptions),
    // rawBody: req.rawBody (Buffer) tersedia untuk verifikasi HMAC webhook (WebhookHmacGuard)
    { bufferLogs: false, rawBody: true },
  );

  // Header keamanan di lapisan aplikasi juga, bukan hanya Traefik (defense in depth). API JSON → CSP tidak relevan.
  await app.register(fastifyHelmet as never, { contentSecurityPolicy: false });

  // Multipart untuk Price Hunter. Batas ditegakkan per byte yang mengalir (berlaku juga untuk
  // Transfer-Encoding: chunked tanpa Content-Length).
  await app.register(fastifyMultipart as never, {
    limits: {
      fileSize: config.marketScoutMaxBytes,
      files: 1,
      fields: 10,
      fieldSize: 1024,
      parts: 12,
      headerPairs: 50,
    },
  });

  app.enableShutdownHooks();
  // Kontrak §1: prefix `/v1` (bukan `/api/v1`). Health & metrics tanpa prefix.
  // Health, metrics & migrasi internal (K-7) tanpa prefix — lihat common/http/global-prefix.ts.
  app.setGlobalPrefix(GLOBAL_PREFIX, { exclude: GLOBAL_PREFIX_EXCLUDE });

  await app.listen(config.port, '0.0.0.0');
  logger.log(`BFF mendengar ${https ? 'HTTPS' : 'HTTP (plaintext, non-production)'} di :${config.port}`);
}

void bootstrap();
