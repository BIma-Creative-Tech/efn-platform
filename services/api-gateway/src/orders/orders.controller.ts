import { Body, Controller, Get, Headers, HttpCode, HttpStatus, Param, Post, Query, RawBodyRequest, Req, Res, UseGuards } from '@nestjs/common';
import { FastifyReply, FastifyRequest } from 'fastify';
import { AppConfig } from '../config/app-config';
import { Errors } from '../common/errors/api-error';
import { RateLimiter } from '../common/rate-limit/rate-limiter';
import { AuthUser } from '../common/security/access-token.service';
import { CurrentUser, JwtAuthGuard } from '../common/security/jwt-auth.guard';
import { ServiceKeyGuard } from '../common/security/service-key.guard';
import { asRecord, isUuid } from '../common/validation';
import { assertBodySize, limitByMember, noStore, requireOnline } from '../catalog/online-http';
import { PaymentStatus } from '../payments/payment-gateway';
import { isPaymentMethod } from './online.model';
import { OnlineEventHmacGuard } from './order-events.guard';
import { EventOutcome, OrderEventsService, parseOdooEvent } from './order-events.service';
import { CheckoutInput, OrderView, OrdersService } from './orders.service';

/** id bukan UUID → 404 (bukan 400), sama dengan order milik member lain (anti-enumeration, §10.3.1). */
function orderIdParam(id: string): string {
  if (!isUuid(id)) throw Errors.notFound();
  return id.toLowerCase();
}

/** G20 §12.2 no. 2: `payment_method` opsional `qris` (default) | `counter`; nilai lain (termasuk null/huruf besar) → 400. */
export function parseCheckout(body: unknown): CheckoutInput {
  const o = asRecord(body);
  if (Object.keys(o).some((k) => k !== 'quote_id' && k !== 'accept_total' && k !== 'payment_method')) throw Errors.invalidRequest();
  if (!isUuid(o.quote_id)) throw Errors.invalidRequest();
  const t = o.accept_total;
  if (typeof t !== 'number' || !Number.isSafeInteger(t) || t <= 0) throw Errors.invalidRequest();
  const pm = o.payment_method === undefined ? 'qris' : o.payment_method;
  if (!isPaymentMethod(pm)) throw Errors.invalidRequest();
  return { quoteId: o.quote_id.toLowerCase(), acceptTotal: t, paymentMethod: pm };
}

/** Order pelanggan (docs/04 §10.3.5–10.3.6, §10.7): Bearer member, `no-store`, rate limit per member, kill switch. */
@Controller('orders')
@UseGuards(JwtAuthGuard)
export class OrdersController {
  constructor(
    private readonly config: AppConfig,
    private readonly orders: OrdersService,
    private readonly limiter: RateLimiter,
  ) {}

  @Post()
  @HttpCode(HttpStatus.CREATED)
  async checkout(
    @Body() body: unknown,
    @Headers('idempotency-key') key: string | undefined,
    @CurrentUser() user: AuthUser,
    @Req() req: RawBodyRequest<FastifyRequest>,
    @Res({ passthrough: true }) reply: FastifyReply,
  ): Promise<OrderView> {
    requireOnline(this.config);
    noStore(reply);
    assertBodySize(req);
    await limitByMember(this.limiter, this.config, 'CHECKOUT', user.memberId);
    return this.orders.checkout(user.memberId, typeof key === 'string' && key.length > 0 ? key : undefined, parseCheckout(body));
  }

  @Get()
  async list(
    @Query('cursor') cursor: unknown,
    @Query('limit') limit: unknown,
    @CurrentUser() user: AuthUser,
    @Res({ passthrough: true }) reply: FastifyReply,
  ): Promise<unknown> {
    requireOnline(this.config);
    noStore(reply);
    await limitByMember(this.limiter, this.config, 'ORDERS', user.memberId);
    if (cursor !== undefined && (typeof cursor !== 'string' || cursor.length > 120)) throw Errors.invalidRequest();
    let n = 20;
    if (limit !== undefined && limit !== '') {
      if (typeof limit !== 'string' || !/^\d{1,2}$/.test(limit) || Number(limit) < 1 || Number(limit) > 20) throw Errors.invalidRequest();
      n = Number(limit);
    }
    return this.orders.list(user.memberId, typeof cursor === 'string' && cursor !== '' ? cursor : null, n);
  }

  @Get(':order_id')
  async get(@Param('order_id') id: string, @CurrentUser() user: AuthUser, @Res({ passthrough: true }) reply: FastifyReply): Promise<OrderView> {
    requireOnline(this.config);
    noStore(reply);
    await limitByMember(this.limiter, this.config, 'ORDERS', user.memberId);
    return this.orders.get(user.memberId, orderIdParam(id));
  }

  @Post(':order_id/cancel')
  @HttpCode(HttpStatus.OK)
  async cancel(@Param('order_id') id: string, @CurrentUser() user: AuthUser, @Res({ passthrough: true }) reply: FastifyReply): Promise<{ status: 'cancelled' }> {
    requireOnline(this.config);
    noStore(reply);
    await limitByMember(this.limiter, this.config, 'CANCEL', user.memberId);
    return this.orders.cancel(user.memberId, orderIdParam(id));
  }

  @Get(':order_id/pickup-code')
  async pickupCode(@Param('order_id') id: string, @CurrentUser() user: AuthUser, @Res({ passthrough: true }) reply: FastifyReply): Promise<unknown> {
    requireOnline(this.config);
    noStore(reply);
    await limitByMember(this.limiter, this.config, 'PICKUP', user.memberId);
    return this.orders.pickupCodes(user.memberId, orderIdParam(id));
  }

  /** DEV/STAGING SAJA (§10.7): 404 di production atau provider ≠ mock. */
  @Post(':order_id/simulate-payment')
  @HttpCode(HttpStatus.OK)
  async simulate(@Param('order_id') id: string, @Body() body: unknown, @CurrentUser() user: AuthUser, @Res({ passthrough: true }) reply: FastifyReply): Promise<OrderView> {
    if (this.config.isProductionDeployment) throw Errors.notFound();
    requireOnline(this.config);
    noStore(reply);
    const s = asRecord(body).status;
    if (s !== 'paid' && s !== 'expired' && s !== 'failed') throw Errors.invalidRequest();
    return this.orders.simulatePayment(user.memberId, orderIdParam(id), s as Exclude<PaymentStatus, 'pending'>);
  }
}

/** Event Odoo → BFF (docs/04 §10.5): service key + HMAC body + replay 300 s + idempotensi + `seq`. */
@Controller('pos/online')
@UseGuards(ServiceKeyGuard, OnlineEventHmacGuard)
export class OrderEventsController {
  constructor(private readonly events: OrderEventsService) {}

  @Post('order-events')
  @HttpCode(HttpStatus.OK)
  handle(@Req() req: RawBodyRequest<FastifyRequest>): Promise<{ ok: true; status: EventOutcome }> {
    return this.events.handle(parseOdooEvent(req.rawBody));
  }
}
