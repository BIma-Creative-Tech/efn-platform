import { Global, Module } from '@nestjs/common';
import { AppConfig } from '../config/app-config';
import { AmqpService } from './amqp/amqp.service';
import { pgProviders } from './db/pg.provider';
import { DB, PG_POOL } from './db/db';
import { InfraLifecycle } from './infra.lifecycle';
import { MetricsServer } from './metrics/metrics-server';
import { MetricsService } from './metrics/metrics.service';
import { OutboxService } from './outbox/outbox.service';
import { MemberService } from './member/member.service';
import { PiiCipher } from './pii/crypto';
import { PiiKeyRingCheck } from './pii/pii-key-check';
import { RateLimiter } from './rate-limit/rate-limiter';
import { KV_STORE, REDIS_CLIENT } from './redis/kv-store';
import { redisProviders } from './redis/redis.provider';
import { RetentionService } from './retention/retention.service';
import { AccessTokenService } from './security/access-token.service';
import { JwtAuthGuard } from './security/jwt-auth.guard';
import { ServiceKeyGuard } from './security/service-key.guard';
import { WebhookHmacGuard } from './security/webhook-hmac.guard';

/** Infrastruktur bersama (config, Redis, PostgreSQL, RabbitMQ, outbox, enkripsi PII, retensi, rate limit, guard). */
@Global()
@Module({
  providers: [
    MetricsService,
    MetricsServer,
    { provide: AppConfig, useFactory: (): AppConfig => AppConfig.fromProcessEnv() },
    { provide: PiiCipher, inject: [AppConfig], useFactory: (config: AppConfig): PiiCipher => PiiCipher.fromConfig(config) },
    ...redisProviders,
    ...pgProviders,
    InfraLifecycle,
    MemberService,
    PiiKeyRingCheck,
    AmqpService,
    OutboxService,
    RetentionService,
    RateLimiter,
    AccessTokenService,
    ServiceKeyGuard,
    JwtAuthGuard,
    WebhookHmacGuard,
  ],
  exports: [
    MetricsService,
    AppConfig,
    PiiCipher,
    MemberService,
    REDIS_CLIENT,
    KV_STORE,
    PG_POOL,
    DB,
    AmqpService,
    OutboxService,
    RateLimiter,
    AccessTokenService,
    ServiceKeyGuard,
    JwtAuthGuard,
    WebhookHmacGuard,
  ],
})
export class CommonModule {}
