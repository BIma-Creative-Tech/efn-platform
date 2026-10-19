import { Body, Controller, Delete, Get, HttpCode, HttpStatus, Param, Post, RawBodyRequest, Req, Res, UseGuards } from '@nestjs/common';
import { FastifyReply, FastifyRequest } from 'fastify';
import { clientIp } from '../common/net/client-ip';
import { ServiceKeyGuard } from '../common/security/service-key.guard';
import { assertBodySize } from '../catalog/online-http';
import { FnbEventHmacGuard } from './fnb-event.guard';
import { FnbEventOutcome, FnbPosService, OpenSessionView, parseFnbEvent, parseOpenSession } from './fnb-pos.service';
import { TABLE_SESSION_HEADER } from './fnb-token';
import { FnbRequestContext, FnbService, MenuView, SessionView, SubmitView } from './fnb.service';

function ctx(req: FastifyRequest): FnbRequestContext {
  // Token HANYA dari header (A.2): query `?ts=` / path tidak pernah dibaca. Nilai tidak di-log di mana pun.
  const raw = req.headers[TABLE_SESSION_HEADER];
  return { tableToken: typeof raw === 'string' ? raw : undefined, authorization: req.headers.authorization, ip: clientIp(req) };
}
function noStore(reply: FastifyReply): void {
  void reply.header('Cache-Control', 'no-store');
}

/**
 * API publik QR meja `/v1/fnb/*` (docs/04 §14.A.3) — lewat router Traefik `api.`; header `X-Table-Session` wajib, Bearer
 * opsional (klaim poin: wajib). Body ≤ 16 KB. `Cache-Control: no-store` kecuali menu (`private, max-age=60`).
 */
@Controller('fnb')
export class FnbController {
  constructor(private readonly fnb: FnbService) {}

  @Get('session')
  async session(@Req() req: FastifyRequest, @Res({ passthrough: true }) reply: FastifyReply): Promise<SessionView> {
    noStore(reply);
    return this.fnb.session(ctx(req));
  }

  @Get('menu')
  async menu(@Req() req: FastifyRequest, @Res({ passthrough: true }) reply: FastifyReply): Promise<MenuView> {
    const out = await this.fnb.menu(ctx(req));
    void reply.header('Cache-Control', 'private, max-age=60');
    return out;
  }

  @Post('orders')
  @HttpCode(HttpStatus.ACCEPTED)
  async submit(@Body() body: unknown, @Req() req: RawBodyRequest<FastifyRequest>, @Res({ passthrough: true }) reply: FastifyReply): Promise<SubmitView> {
    noStore(reply);
    assertBodySize(req);
    return this.fnb.submit(ctx(req), req.headers['idempotency-key'], body);
  }

  @Get('orders')
  async list(@Req() req: FastifyRequest, @Res({ passthrough: true }) reply: FastifyReply): Promise<unknown> {
    noStore(reply);
    return this.fnb.list(ctx(req));
  }

  @Get('orders/:submission_id')
  async one(@Param('submission_id') id: string, @Req() req: FastifyRequest, @Res({ passthrough: true }) reply: FastifyReply): Promise<unknown> {
    noStore(reply);
    return this.fnb.one(ctx(req), id);
  }

  @Post('session/points-claim')
  @HttpCode(HttpStatus.OK)
  async claim(@Req() req: RawBodyRequest<FastifyRequest>, @Res({ passthrough: true }) reply: FastifyReply): Promise<{ claimed: true }> {
    noStore(reply);
    assertBodySize(req);
    return this.fnb.claim(ctx(req));
  }

  @Delete('session/points-claim')
  @HttpCode(HttpStatus.NO_CONTENT)
  async unclaim(@Req() req: FastifyRequest, @Res({ passthrough: true }) reply: FastifyReply): Promise<void> {
    noStore(reply);
    await this.fnb.unclaim(ctx(req));
  }
}

/**
 * Odoo → BFF (docs/04 §14.A.4, L-76): `/v1/pos/fnb/table-sessions` (mTLS + service key) dan `/v1/pos/fnb/events`
 * (+ HMAC `odoo_fnb_event_hmac`, replay 300 s). In-cluster saja (Traefik mengecualikan `/v1/pos`).
 */
@Controller('pos/fnb')
@UseGuards(ServiceKeyGuard)
export class FnbPosController {
  constructor(private readonly pos: FnbPosService) {}

  @Post('table-sessions')
  @HttpCode(HttpStatus.CREATED)
  async open(@Body() body: unknown, @Req() req: RawBodyRequest<FastifyRequest>, @Res({ passthrough: true }) reply: FastifyReply): Promise<OpenSessionView> {
    noStore(reply);
    assertBodySize(req);
    return this.pos.openSession(parseOpenSession(body));
  }

  @Post('events')
  @HttpCode(HttpStatus.OK)
  @UseGuards(FnbEventHmacGuard)
  events(@Req() req: RawBodyRequest<FastifyRequest>): Promise<{ ok: true; status: FnbEventOutcome }> {
    return this.pos.handleEvent(parseFnbEvent(req.rawBody));
  }
}
