import { CanActivate, ExecutionContext, Injectable, Logger, RawBodyRequest } from '@nestjs/common';
import { FastifyRequest } from 'fastify';
import { AppConfig } from '../../config/app-config';
import { Errors } from '../errors/api-error';
import { WEBHOOK_SIGNATURE_HEADER, verifyWebhookSignature } from './webhook-signature';

/**
 * Guard untuk endpoint webhook payment/3PL (belum ada endpoint-nya di fase ini).
 * Membutuhkan `rawBody: true` di NestFactory.create (sudah di main.ts) agar `req.rawBody` tersedia.
 */
@Injectable()
export class WebhookHmacGuard implements CanActivate {
  private readonly logger = new Logger(WebhookHmacGuard.name);
  now: () => number = () => Date.now();

  constructor(private readonly config: AppConfig) {}

  canActivate(ctx: ExecutionContext): boolean {
    const req = ctx.switchToHttp().getRequest<RawBodyRequest<FastifyRequest>>();
    const result = verifyWebhookSignature({
      header: req.headers[WEBHOOK_SIGNATURE_HEADER],
      rawBody: req.rawBody,
      secret: this.config.webhookPaymentSecret,
      nowSeconds: Math.floor(this.now() / 1000),
    });
    if (!result.ok) {
      this.logger.warn(`Webhook ditolak: ${result.reason}`);
      throw Errors.invalidSignature();
    }
    return true;
  }
}
