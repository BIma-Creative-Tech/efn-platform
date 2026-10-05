import { Controller, Get } from '@nestjs/common';
import { HealthCheck, HealthCheckService, HealthIndicatorResult } from '@nestjs/terminus';

@Controller()
export class HealthController {
  constructor(private readonly health: HealthCheckService) {}

  /** Liveness: proses hidup. Dipakai healthcheck Docker & Uptime Kuma. */
  @Get('health')
  @HealthCheck()
  liveness() {
    return this.health.check([
      async (): Promise<HealthIndicatorResult> => ({ app: { status: 'up', version: process.env.npm_package_version ?? 'dev' } }),
    ]);
  }

  /** Readiness: tambahkan indikator Redis, PostgreSQL, RabbitMQ saat modul masing-masing ada. */
  @Get('health/ready')
  @HealthCheck()
  readiness() {
    return this.health.check([]);
  }
}
