import { Body, Controller, Delete, Get, HttpCode, HttpStatus, Post, Put, RawBodyRequest, Req, Res, UseGuards } from '@nestjs/common';
import { FastifyReply, FastifyRequest } from 'fastify';
import { AppConfig } from '../config/app-config';
import { RateLimiter } from '../common/rate-limit/rate-limiter';
import { AuthUser } from '../common/security/access-token.service';
import { CurrentUser, JwtAuthGuard } from '../common/security/jwt-auth.guard';
import { asRecord } from '../common/validation';
import { Errors } from '../common/errors/api-error';
import { assertBodySize, limitByMember, noStore, requireOnline } from '../catalog/online-http';
import { Cart } from './cart-store';
import { CartService, CartWarning, parseCartPut } from './cart.service';
import { QuoteService, QuoteView } from './quote.service';

/** Cart & quote (docs/04 §10.3.3–10.3.4): Bearer member, `no-store`, kill switch `ONLINE_ENABLED`. */
@Controller('cart')
@UseGuards(JwtAuthGuard)
export class CartController {
  constructor(
    private readonly config: AppConfig,
    private readonly cart: CartService,
    private readonly quotes: QuoteService,
    private readonly limiter: RateLimiter,
  ) {}

  @Get()
  async get(@CurrentUser() user: AuthUser, @Res({ passthrough: true }) reply: FastifyReply): Promise<Cart> {
    requireOnline(this.config);
    noStore(reply);
    await limitByMember(this.limiter, this.config, 'ORDERS', user.memberId);
    return this.cart.get(user.memberId);
  }

  @Put()
  @HttpCode(HttpStatus.OK)
  async put(
    @Body() body: unknown,
    @CurrentUser() user: AuthUser,
    @Req() req: RawBodyRequest<FastifyRequest>,
    @Res({ passthrough: true }) reply: FastifyReply,
  ): Promise<Cart & { warnings: CartWarning[] }> {
    requireOnline(this.config);
    noStore(reply);
    assertBodySize(req);
    await limitByMember(this.limiter, this.config, 'CART', user.memberId);
    return this.cart.put(user.memberId, parseCartPut(body));
  }

  @Delete()
  @HttpCode(HttpStatus.NO_CONTENT)
  async clear(@CurrentUser() user: AuthUser, @Res({ passthrough: true }) reply: FastifyReply): Promise<void> {
    requireOnline(this.config);
    noStore(reply);
    await limitByMember(this.limiter, this.config, 'CART', user.memberId);
    await this.cart.clear(user.memberId);
  }

  @Post('quote')
  @HttpCode(HttpStatus.OK)
  async quote(
    @Body() body: unknown,
    @CurrentUser() user: AuthUser,
    @Req() req: RawBodyRequest<FastifyRequest>,
    @Res({ passthrough: true }) reply: FastifyReply,
  ): Promise<QuoteView> {
    requireOnline(this.config);
    noStore(reply);
    assertBodySize(req);
    if (body !== undefined && body !== null && Object.keys(asRecord(body)).length > 0) throw Errors.invalidRequest();
    await limitByMember(this.limiter, this.config, 'QUOTE', user.memberId);
    return this.quotes.quote(user.memberId);
  }
}
