import { Body, Controller, Get, HttpCode, HttpStatus, Post, UseGuards } from '@nestjs/common';
import { ServiceKeyGuard } from '../common/security/service-key.guard';
import { parsePosEarn, parsePosReverse, parseRedeemCommit, parseRedeemRelease, parseRedeemReserve } from './dto/pos-loyalty.dto';
import {
  LoyaltyParamsResponse,
  PosEarnResponse,
  PosLoyaltyService,
  PosReverseResponse,
  RedeemCommitResponse,
  RedeemReserveResponse,
} from './pos-loyalty.service';

/**
 * Loyalty POS Fase 1 (docs/04 §9.2–9.4), server Odoo → BFF (service key). Prefix global `v1` → `/v1/pos/loyalty/*`.
 * R2: semua identitas (member_ref, token, order) hanya di body POST — tidak ada @Param/@Query.
 * `POST /v1/pos/loyalty/balance` tetap di `PosController` (§8.13).
 */
@Controller('pos/loyalty')
@UseGuards(ServiceKeyGuard)
export class PosLoyaltyController {
  constructor(private readonly loyalty: PosLoyaltyService) {}

  /** Parameter §9.1 yang dipakai BFF (Odoo menampilkan nilai poin & menghitung eligible_amount dengan nilai yang sama). */
  @Get('params')
  params(): LoyaltyParamsResponse {
    return this.loyalty.params();
  }

  @Post('earn')
  @HttpCode(HttpStatus.OK)
  earn(@Body() body: unknown): Promise<PosEarnResponse> {
    return this.loyalty.earn(parsePosEarn(body));
  }

  @Post('redeem/reserve')
  @HttpCode(HttpStatus.OK)
  reserve(@Body() body: unknown): Promise<RedeemReserveResponse> {
    return this.loyalty.reserve(parseRedeemReserve(body));
  }

  @Post('redeem/commit')
  @HttpCode(HttpStatus.OK)
  commit(@Body() body: unknown): Promise<RedeemCommitResponse> {
    return this.loyalty.commit(parseRedeemCommit(body));
  }

  @Post('redeem/release')
  @HttpCode(HttpStatus.OK)
  release(@Body() body: unknown): Promise<{ ok: true }> {
    return this.loyalty.release(parseRedeemRelease(body));
  }

  @Post('reverse')
  @HttpCode(HttpStatus.OK)
  reverse(@Body() body: unknown): Promise<PosReverseResponse> {
    return this.loyalty.reverse(parsePosReverse(body));
  }
}
