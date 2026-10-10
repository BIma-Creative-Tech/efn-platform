import { CanActivate, ExecutionContext, Injectable, Logger, RawBodyRequest } from '@nestjs/common';
import { FastifyRequest } from 'fastify';
import { AppConfig } from '../config/app-config';
import { Errors } from '../common/errors/api-error';
import { WEBHOOK_SIGNATURE_HEADER, verifyWebhookSignature } from '../common/security/webhook-signature';
import { OnlineErrors } from '../odoo-rpc/online-errors';

/**
 * L-2 (docs/04 §10.5): event Odoo → BFF wajib `X-EFN-Signature: t=<unix>,v1=<hex HMAC-SHA256(odoo_online_event_hmac,
 * "<t>." + rawBody)>`, replay window 300 s terhadap `t`. Dipasang SETELAH `ServiceKeyGuard`. Secret belum ada → 503
 * (tanpa fallback tanpa tanda tangan). Alasan penolakan hanya di log server.
 */
@Injectable()
export class OnlineEventHmacGuard implements CanActivate {
  private readonly logger = new Logger(OnlineEventHmacGuard.name);
  now: () => number = () => Date.now();

  constructor(private readonly config: AppConfig) {}

  canActivate(ctx: ExecutionContext): boolean {
    const req = ctx.switchToHttp().getRequest<RawBodyRequest<FastifyRequest>>();
    let secret: string;
    try {
      secret = this.config.odooOnlineEventHmac;
    } catch {
      this.logger.error('Secret odoo_online_event_hmac belum dikonfigurasi — event online ditolak');
      throw OnlineErrors.unavailable();
    }
    const res = verifyWebhookSignature({
      header: req.headers[WEBHOOK_SIGNATURE_HEADER],
      rawBody: req.rawBody,
      secret,
      nowSeconds: Math.floor(this.now() / 1000),
    });
    if (!res.ok) {
      this.logger.warn(`Event online ditolak: ${res.reason}`);
      throw Errors.invalidSignature();
    }
    return true;
  }
}
