import { Body, Controller, Delete, Get, HttpCode, HttpStatus, Param, Post, Query, Req, Res, UseGuards } from '@nestjs/common';
import type { FastifyReply, FastifyRequest } from 'fastify';
import { AppConfig } from '../config/app-config';
import { Errors } from '../common/errors/api-error';
import { RateLimiter } from '../common/rate-limit/rate-limiter';
import { AuthUser } from '../common/security/access-token.service';
import { CurrentUser, JwtAuthGuard } from '../common/security/jwt-auth.guard';
import { limitByIp, limitByMember } from '../catalog/online-http';
import { PromoResponse, PromoService } from './promo.service';

/**
 * Promo Super-App (docs/04 §18.2). Publik: `GET /v1/promo`, `GET /v1/promo/images/:sha`. Member: token push.
 * Kill switch `PROMO_ENABLED=false` → feed kosong (200) agar klien tetap menampilkan hero lama; gambar 404.
 */
@Controller('promo')
export class PromoController {
  constructor(
    private readonly promo: PromoService,
    private readonly config: AppConfig,
    private readonly limiter: RateLimiter,
  ) {}

  @Get()
  async list(@Query('surface') surface: unknown, @Req() req: FastifyRequest, @Res({ passthrough: true }) reply: FastifyReply): Promise<PromoResponse> {
    if (surface !== 'web' && surface !== 'app') throw Errors.invalidRequest();
    await limitByIp(this.limiter, this.config, 'CATALOG', req);
    void reply.header('Cache-Control', 'public, max-age=60');
    return this.promo.list(surface);
  }

  @Get('images/:sha')
  async image(@Param('sha') sha: string, @Query('w') w: unknown, @Req() req: FastifyRequest, @Res() reply: FastifyReply): Promise<void> {
    await limitByIp(this.limiter, this.config, 'IMAGE', req);
    const width = w === '1600' ? 1600 : w === '1024' ? 1024 : null;
    if (width === null) throw Errors.invalidRequest();
    const data = await this.promo.image(sha, width);
    void reply
      .header('Content-Type', 'image/jpeg')
      .header('Cache-Control', 'public, max-age=31536000, immutable')
      .header('X-Content-Type-Options', 'nosniff')
      .send(data);
  }
}

@Controller('me/push-token')
@UseGuards(JwtAuthGuard)
export class PushTokenController {
  constructor(
    private readonly promo: PromoService,
    private readonly config: AppConfig,
    private readonly limiter: RateLimiter,
  ) {}

  @Post()
  @HttpCode(HttpStatus.NO_CONTENT)
  async register(@Body() body: unknown, @CurrentUser() user: AuthUser): Promise<void> {
    await limitByMember(this.limiter, this.config, 'ORDERS', user.memberId);
    const o = body && typeof body === 'object' && !Array.isArray(body) ? (body as Record<string, unknown>) : {};
    await this.promo.registerToken(user.memberId, o.token, o.platform);
  }

  @Delete()
  @HttpCode(HttpStatus.NO_CONTENT)
  async remove(@Body() body: unknown, @CurrentUser() user: AuthUser): Promise<void> {
    const o = body && typeof body === 'object' && !Array.isArray(body) ? (body as Record<string, unknown>) : {};
    await this.promo.removeTokens(user.memberId, o.token);
  }
}
