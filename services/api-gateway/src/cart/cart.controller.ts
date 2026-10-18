import { Body, Controller, Delete, Get, HttpCode, HttpStatus, Post, Put, RawBodyRequest, Req, Res, UseGuards } from '@nestjs/common';
import { FastifyReply, FastifyRequest } from 'fastify';
import { AppConfig } from '../config/app-config';
import { RateLimiter } from '../common/rate-limit/rate-limiter';
import { AuthUser } from '../common/security/access-token.service';
import { CurrentUser, JwtAuthGuard } from '../common/security/jwt-auth.guard';
import { assertBodySize, limitByIp, limitByMember, noStore, requireOnline } from '../catalog/online-http';
import { OnlineErrors } from '../odoo-rpc/online-errors';
import { PromoCodesInvalid, normalizePromoCodes } from '../odoo-rpc/promo-codes';
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

  /**
   * `POST /v1/cart/quote` body `{}` atau `{ "promo_codes": ["KODE1"] }` (G19 §11.2 no. 1). Format salah / kunci lain →
   * `400 invalid_request`. Quote berpromo dihitung juga di rate limit `PROMO_MEMBER` + `PROMO_IP` (→ `429 rate_limited`).
   */
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
    const promoCodes = parseQuoteBody(body);
    await limitByMember(this.limiter, this.config, 'QUOTE', user.memberId);
    if (promoCodes.length > 0) {
      await limitByMember(this.limiter, this.config, 'PROMO_MEMBER', user.memberId);
      await limitByIp(this.limiter, this.config, 'PROMO_IP', req);
    }
    return this.quotes.quote(user.memberId, promoCodes);
  }
}

/** Body quote §11.2 no. 1: hanya kunci `promo_codes` (opsional). Hasil: kode ternormalisasi (huruf besar, unik, ≤ 3). */
export function parseQuoteBody(body: unknown): string[] {
  if (body === undefined || body === null) return [];
  if (typeof body !== 'object' || Array.isArray(body)) throw OnlineErrors.invalidInput();
  const rec = body as Record<string, unknown>;
  if (Object.keys(rec).some((k) => k !== 'promo_codes')) throw OnlineErrors.invalidInput();
  try {
    return normalizePromoCodes(rec.promo_codes);
  } catch (err) {
    if (err instanceof PromoCodesInvalid) throw OnlineErrors.invalidInput();
    throw err;
  }
}
