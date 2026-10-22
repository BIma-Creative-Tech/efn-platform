import {
  Body,
  CanActivate,
  Controller,
  ExecutionContext,
  Get,
  Headers,
  HttpCode,
  HttpStatus,
  Injectable,
  Logger,
  Param,
  Post,
  Query,
  RawBodyRequest,
  Req,
  Res,
  UseGuards,
} from '@nestjs/common';
import { FastifyReply, FastifyRequest } from 'fastify';
import { AppConfig } from '../config/app-config';
import { Errors } from '../common/errors/api-error';
import { clientIp } from '../common/net/client-ip';
import { redisKeyHash } from '../common/pii/pii';
import { RateLimiter } from '../common/rate-limit/rate-limiter';
import { AuthUser } from '../common/security/access-token.service';
import { CurrentUser, JwtAuthGuard } from '../common/security/jwt-auth.guard';
import { ServiceKeyGuard } from '../common/security/service-key.guard';
import { WEBHOOK_SIGNATURE_HEADER, verifyWebhookSignature } from '../common/security/webhook-signature';
import { BRAND_CODE_RE } from '../odoo-rpc/odoo-online.parse';
import { noStore } from '../catalog/online-http';
import { BOOKING_BODY_LIMIT, BookingErrors, BookingView, bookingIdParam, parseAvailabilityQuery, parseBookingEvent, parseCreateBooking } from './booking.model';
import { BookingEventOutcome, BookingOutlet, BookingService } from './booking.service';

/** Batas laju §15.5 (tetap; tidak dikonfigurasi lewat env agar tidak dilonggarkan tanpa review). */
const RL = {
  availabilityIp: { limit: 30, window: 60 },
  createMember: { limit: 10, window: 3600 },
  readMember: { limit: 60, window: 60 },
  cancelMember: { limit: 10, window: 3600 },
  outletsIp: { limit: 60, window: 60 },
};

function assertSmallBody(req: RawBodyRequest<FastifyRequest>): void {
  const raw = req.rawBody;
  const len = raw ? raw.length : Number(req.headers['content-length'] ?? 0);
  if (len > BOOKING_BODY_LIMIT) throw Errors.payloadTooLarge();
}

/** §15.5: event reservasi Odoo → BFF wajib `X-EFN-Signature` HMAC `odoo_booking_event_hmac`, replay 300 s. */
@Injectable()
export class BookingEventHmacGuard implements CanActivate {
  private readonly logger = new Logger(BookingEventHmacGuard.name);
  now: () => number = () => Date.now();

  constructor(private readonly config: AppConfig) {}

  canActivate(ctx: ExecutionContext): boolean {
    const req = ctx.switchToHttp().getRequest<RawBodyRequest<FastifyRequest>>();
    if (!this.config.bookingEnabled) throw BookingErrors.unavailable();
    let secret: string;
    try {
      secret = this.config.odooBookingEventHmac;
    } catch {
      this.logger.error('Secret odoo_booking_event_hmac belum dikonfigurasi — event reservasi ditolak');
      throw BookingErrors.unavailable();
    }
    const res = verifyWebhookSignature({
      header: req.headers[WEBHOOK_SIGNATURE_HEADER],
      rawBody: req.rawBody,
      secret,
      nowSeconds: Math.floor(this.now() / 1000),
    });
    if (!res.ok) {
      this.logger.warn(`Event reservasi ditolak: ${res.reason}`);
      throw Errors.invalidSignature();
    }
    return true;
  }
}

/** Publik + pelanggan (docs/04 §15.5). Kill switch `BOOKING_ENABLED` → 503 `booking_unavailable`. */
@Controller('booking')
export class BookingController {
  constructor(
    private readonly config: AppConfig,
    private readonly booking: BookingService,
    private readonly limiter: RateLimiter,
  ) {}

  private async byIp(name: string, req: FastifyRequest, rl: { limit: number; window: number }): Promise<void> {
    const ip = redisKeyHash(this.config.redisKeyPepper, clientIp(req) ?? 'unknown');
    await this.limiter.enforce(`booking:${name}:ip:${ip}`, rl.limit, rl.window);
  }

  private async byMember(name: string, memberId: string, rl: { limit: number; window: number }): Promise<void> {
    await this.limiter.enforce(`booking:${name}:m:${memberId}`, rl.limit, rl.window);
  }

  @Get('outlets')
  async outlets(@Query('brand') brand: unknown, @Req() req: FastifyRequest, @Res({ passthrough: true }) reply: FastifyReply): Promise<{ items: BookingOutlet[] }> {
    this.booking.requireEnabled();
    await this.byIp('outlets', req, RL.outletsIp);
    if (brand !== undefined && (typeof brand !== 'string' || !BRAND_CODE_RE.test(brand))) throw Errors.invalidRequest();
    void reply.header('Cache-Control', 'public, max-age=60');
    return { items: await this.booking.outlets(typeof brand === 'string' ? brand : null) };
  }

  @Get('availability')
  async availability(@Query() q: Record<string, unknown>, @Req() req: FastifyRequest, @Res({ passthrough: true }) reply: FastifyReply): Promise<unknown> {
    this.booking.requireEnabled();
    await this.byIp('availability', req, RL.availabilityIp);
    const p = parseAvailabilityQuery(q);
    void reply.header('Cache-Control', 'public, max-age=30');
    return this.booking.availability(p.outlet_code, p.date, p.party);
  }

  @Post('reservations')
  @HttpCode(HttpStatus.CREATED)
  @UseGuards(JwtAuthGuard)
  async create(
    @Body() body: unknown,
    @Headers('idempotency-key') key: string | undefined,
    @CurrentUser() user: AuthUser,
    @Req() req: RawBodyRequest<FastifyRequest>,
    @Res({ passthrough: true }) reply: FastifyReply,
  ): Promise<BookingView> {
    noStore(reply);
    this.booking.requireEnabled();
    assertSmallBody(req);
    await this.byMember('create', user.memberId, RL.createMember);
    return this.booking.create(user.memberId, typeof key === 'string' && key.length > 0 ? key : undefined, parseCreateBooking(body));
  }

  @Get('reservations')
  @UseGuards(JwtAuthGuard)
  async list(@CurrentUser() user: AuthUser, @Res({ passthrough: true }) reply: FastifyReply): Promise<{ items: BookingView[] }> {
    noStore(reply);
    this.booking.requireEnabled();
    await this.byMember('read', user.memberId, RL.readMember);
    return this.booking.list(user.memberId);
  }

  @Get('reservations/:id')
  @UseGuards(JwtAuthGuard)
  async get(@Param('id') id: string, @CurrentUser() user: AuthUser, @Res({ passthrough: true }) reply: FastifyReply): Promise<BookingView> {
    noStore(reply);
    this.booking.requireEnabled();
    await this.byMember('read', user.memberId, RL.readMember);
    return this.booking.get(user.memberId, bookingIdParam(id));
  }

  @Post('reservations/:id/cancel')
  @HttpCode(HttpStatus.OK)
  @UseGuards(JwtAuthGuard)
  async cancel(@Param('id') id: string, @CurrentUser() user: AuthUser, @Res({ passthrough: true }) reply: FastifyReply): Promise<{ status: 'cancelled' }> {
    noStore(reply);
    this.booking.requireEnabled();
    await this.byMember('cancel', user.memberId, RL.cancelMember);
    return this.booking.cancel(user.memberId, bookingIdParam(id));
  }
}

/** Event Odoo → BFF (service key + HMAC). */
@Controller('pos/booking')
@UseGuards(ServiceKeyGuard)
export class BookingPosController {
  constructor(private readonly booking: BookingService) {}

  @Post('events')
  @HttpCode(HttpStatus.OK)
  @UseGuards(BookingEventHmacGuard)
  events(@Req() req: RawBodyRequest<FastifyRequest>): Promise<{ ok: true; status: BookingEventOutcome }> {
    return this.booking.handleEvent(parseBookingEvent(req.rawBody));
  }
}
