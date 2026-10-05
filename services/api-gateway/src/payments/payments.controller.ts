import { Body, Controller, Get, HttpCode, HttpStatus, Param, Post, RawBodyRequest, Req, UseGuards } from '@nestjs/common';
import { FastifyRequest } from 'fastify';
import { Errors } from '../common/errors/api-error';
import { ServiceKeyGuard } from '../common/security/service-key.guard';
import { isUuid } from '../common/validation';
import { parseCreatePayment, parseSimulate } from './dto/payment.dto';
import { CreatePaymentResponse, PaymentStatusResponse, PaymentsService } from './payments.service';

function requirePaymentId(id: string): string {
  if (!isUuid(id)) throw Errors.notFound();
  return id.toLowerCase();
}

/** `/v1/pos/payments*` (docs/04 §8.3) — server Odoo, service key, in-cluster saja. */
@Controller('pos/payments')
@UseGuards(ServiceKeyGuard)
export class PosPaymentsController {
  constructor(private readonly payments: PaymentsService) {}

  @Post()
  @HttpCode(HttpStatus.CREATED)
  create(@Body() body: unknown): Promise<CreatePaymentResponse> {
    return this.payments.create(parseCreatePayment(body));
  }

  @Get(':payment_id')
  get(@Param('payment_id') id: string): Promise<PaymentStatusResponse> {
    return this.payments.get(requirePaymentId(id));
  }

  @Post(':payment_id/cancel')
  @HttpCode(HttpStatus.OK)
  cancel(@Param('payment_id') id: string): Promise<{ status: string }> {
    return this.payments.cancel(requirePaymentId(id));
  }

  /** DEV/STAGING SAJA (provider mock, bukan production → selain itu 404). */
  @Post(':payment_id/simulate')
  @HttpCode(HttpStatus.OK)
  simulate(@Param('payment_id') id: string, @Body() body: unknown): Promise<PaymentStatusResponse> {
    return this.payments.simulate(requirePaymentId(id), parseSimulate(body).status);
  }
}

/**
 * `POST /v1/webhooks/payments/:provider` — publik via Traefik. Verifikasi memakai RAW body (`rawBody: true` di
 * main.ts) agar signature dihitung atas byte persis yang dikirim provider.
 */
@Controller('webhooks/payments')
export class PaymentWebhookController {
  constructor(private readonly payments: PaymentsService) {}

  @Post(':provider')
  @HttpCode(HttpStatus.OK)
  webhook(@Param('provider') provider: string, @Req() req: RawBodyRequest<FastifyRequest>): Promise<{ ok: true }> {
    return this.payments.handleWebhook(provider, { headers: req.headers, rawBody: req.rawBody });
  }
}
