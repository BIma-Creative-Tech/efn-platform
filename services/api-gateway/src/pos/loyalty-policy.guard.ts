import { CanActivate, ExecutionContext, Injectable, Logger, RawBodyRequest } from '@nestjs/common';
import { FastifyRequest } from 'fastify';
import { AppConfig } from '../config/app-config';
import { Errors } from '../common/errors/api-error';
import { WEBHOOK_SIGNATURE_HEADER, verifyWebhookSignature } from '../common/security/webhook-signature';

/** Replay window publikasi kebijakan (docs/04 §13.1 no. 3). */
export const LOYALTY_POLICY_TOLERANCE_SECONDS = 300;

/**
 * G21 §13.1 no. 3 (L-61): `PUT /v1/pos/loyalty/policies/*` wajib `X-EFN-Signature: t=<unix>,v1=<hex HMAC-SHA256(
 * loyalty_policy_signing_key, "<t>." + rawBody)>`, |now − t| ≤ 300 s. Dipasang SETELAH `ServiceKeyGuard` (service key
 * POS + tanda tangan: bocornya service key saja tidak cukup untuk mengubah kebijakan). Kunci belum dikonfigurasi →
 * 503 (tanpa fallback tanpa tanda tangan). Alasan penolakan hanya di log server; respons 401 `invalid_signature`.
 */
@Injectable()
export class LoyaltyPolicySignatureGuard implements CanActivate {
  private readonly logger = new Logger(LoyaltyPolicySignatureGuard.name);
  now: () => number = () => Date.now();

  constructor(private readonly config: AppConfig) {}

  canActivate(ctx: ExecutionContext): boolean {
    const req = ctx.switchToHttp().getRequest<RawBodyRequest<FastifyRequest>>();
    let secret: string;
    try {
      secret = this.config.loyaltyPolicySigningKey;
    } catch {
      this.logger.error('Secret loyalty_policy_signing_key belum dikonfigurasi — publikasi kebijakan poin ditolak');
      throw Errors.serviceUnavailable();
    }
    const res = verifyWebhookSignature({
      header: req.headers[WEBHOOK_SIGNATURE_HEADER],
      rawBody: req.rawBody,
      secret,
      nowSeconds: Math.floor(this.now() / 1000),
      toleranceSeconds: LOYALTY_POLICY_TOLERANCE_SECONDS,
    });
    if (!res.ok) {
      this.logger.warn(`Publikasi kebijakan poin ditolak: ${res.reason}`);
      throw Errors.invalidSignature();
    }
    return true;
  }
}
