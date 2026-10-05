import { CanActivate, ExecutionContext, Injectable } from '@nestjs/common';
import { FastifyRequest } from 'fastify';
import { AppConfig } from '../../config/app-config';
import { Errors } from '../errors/api-error';
import { safeEqualString } from './safe-equal';

export const SERVICE_KEY_HEADER = 'x-efn-service-key';

/**
 * Autentikasi Odoo → BFF (kontrak §2): header `X-EFN-Service-Key` dibandingkan dengan secret
 * `odoo_bff_api_key` secara waktu-konstan. Dipakai untuk semua endpoint `/v1/pos/*`.
 */
@Injectable()
export class ServiceKeyGuard implements CanActivate {
  constructor(private readonly config: AppConfig) {}

  canActivate(ctx: ExecutionContext): boolean {
    const req = ctx.switchToHttp().getRequest<FastifyRequest>();
    const provided = req.headers[SERVICE_KEY_HEADER];
    if (typeof provided !== 'string' || provided.length === 0 || provided.length > 512) {
      throw Errors.unauthorized();
    }
    if (!safeEqualString(provided, this.config.odooBffApiKey)) throw Errors.unauthorized();
    return true;
  }
}
