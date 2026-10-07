import { CanActivate, ExecutionContext, Injectable } from '@nestjs/common';
import { FastifyRequest } from 'fastify';
import { AppConfig } from '../config/app-config';
import { Errors } from '../common/errors/api-error';
import { safeEqualString } from '../common/security/safe-equal';

export const MIGRATION_KEY_HEADER = 'x-efn-migration-key';

/**
 * K-7 (docs/04 §9.6): endpoint migrasi hanya aktif bila `MIGRATION_ENABLED=true` (selain itu 404 — tidak membocorkan
 * keberadaan route) dan hanya dengan kunci `migration_api_key` yang TERPISAH dari `odoo_bff_api_key`
 * (header `X-EFN-Migration-Key`, perbandingan waktu-konstan). Route `/internal/*` tidak dirutekan Traefik publik.
 */
@Injectable()
export class MigrationKeyGuard implements CanActivate {
  constructor(private readonly config: AppConfig) {}

  canActivate(ctx: ExecutionContext): boolean {
    if (!this.config.migrationEnabled) throw Errors.notFound();
    let expected: string;
    try {
      expected = this.config.migrationApiKey;
    } catch {
      throw Errors.notFound(); // kunci belum dikonfigurasi = fitur mati
    }
    const req = ctx.switchToHttp().getRequest<FastifyRequest>();
    const provided = req.headers[MIGRATION_KEY_HEADER];
    if (typeof provided !== 'string' || provided.length === 0 || provided.length > 512) throw Errors.unauthorized();
    if (!safeEqualString(provided, expected)) throw Errors.unauthorized();
    return true;
  }
}
