import { Body, Controller, Get, HttpCode, HttpStatus, Post, Query, Req, UseGuards } from '@nestjs/common';
import { FastifyRequest } from 'fastify';
import { clientIp } from '../common/net/client-ip';
import { ServiceKeyGuard } from '../common/security/service-key.guard';
import {
  parseDiscountTokenConsume,
  parseMemberRefQuery,
  parsePosOtpRequest,
  parsePosOtpVerify,
} from './dto/pos.dto';
import { PosOtpRequestResponse, PosOtpVerifyResponse, PosService } from './pos.service';

/** Endpoint server-to-server Odoo → BFF (kontrak §3.1, §3.4). Prefix global `v1` → `/v1/pos/*`. */
@Controller('pos')
@UseGuards(ServiceKeyGuard)
export class PosController {
  constructor(private readonly pos: PosService) {}

  @Post('otp/request')
  @HttpCode(HttpStatus.ACCEPTED)
  requestOtp(@Body() body: unknown, @Req() req: FastifyRequest): Promise<PosOtpRequestResponse> {
    return this.pos.requestOtp(parsePosOtpRequest(body), clientIp(req));
  }

  @Post('otp/verify')
  @HttpCode(HttpStatus.OK)
  verifyOtp(@Body() body: unknown, @Req() req: FastifyRequest): Promise<PosOtpVerifyResponse> {
    return this.pos.verifyOtp(parsePosOtpVerify(body), clientIp(req));
  }

  @Post('discount-token/consume')
  @HttpCode(HttpStatus.OK)
  consume(@Body() body: unknown): Promise<{ ok: true }> {
    return this.pos.consumeToken(parseDiscountTokenConsume(body));
  }

  @Get('loyalty/balance')
  balance(@Query('member_ref') memberRef: unknown): Promise<{ points_balance: number }> {
    return this.pos.balance(parseMemberRefQuery(memberRef));
  }
}
