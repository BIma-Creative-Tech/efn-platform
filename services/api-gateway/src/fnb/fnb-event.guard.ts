import { CanActivate, ExecutionContext, Injectable, Logger, RawBodyRequest } from '@nestjs/common';
import { FastifyRequest } from 'fastify';
import { AppConfig } from '../config/app-config';
import { Errors } from '../common/errors/api-error';
import { WEBHOOK_SIGNATURE_HEADER, verifyWebhookSignature } from '../common/security/webhook-signature';
import { FnbErrors } from './fnb.model';

/**
 * docs/04 §14.A.4: event QR Odoo → BFF wajib `X-EFN-Signature: t=<unix>,v1=<hex HMAC-SHA256(odoo_fnb_event_hmac,
 * "<t>." + rawBody)>`, replay window 300 s. Dipasang SETELAH `ServiceKeyGuard`. Secret belum ada / QR mati → 503
 * (tanpa fallback tanpa tanda tangan). Alasan penolakan hanya di log server.
 */
@Injectable()
export class FnbEventHmacGuard implements CanActivate {
  private readonly logger = new Logger(FnbEventHmacGuard.name);
  now: () => number = () => Date.now();

  constructor(private readonly config: AppConfig) {}

  canActivate(ctx: ExecutionContext): boolean {
    const req = ctx.switchToHttp().getRequest<RawBodyRequest<FastifyRequest>>();
    let secret: string;
    try {
      secret = this.config.odooFnbEventHmac;
    } catch {
      this.logger.error('Secret odoo_fnb_event_hmac belum dikonfigurasi — event QR ditolak');
      throw FnbErrors.orderingUnavailable();
    }
    const res = verifyWebhookSignature({
      header: req.headers[WEBHOOK_SIGNATURE_HEADER],
      rawBody: req.rawBody,
      secret,
      nowSeconds: Math.floor(this.now() / 1000),
    });
    if (!res.ok) {
      this.logger.warn(`Event QR ditolak: ${res.reason}`);
      throw Errors.invalidSignature();
    }
    return true;
  }
}
