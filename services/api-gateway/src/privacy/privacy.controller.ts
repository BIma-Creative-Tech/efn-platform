import { Body, Controller, Delete, Get, HttpCode, HttpStatus, Param, Post, Put, Req, Res, UseGuards } from '@nestjs/common';
import { FastifyReply, FastifyRequest } from 'fastify';
import { clientIp } from '../common/net/client-ip';
import { AuthUser } from '../common/security/access-token.service';
import { CurrentUser, JwtAuthGuard, StepUpToken } from '../common/security/jwt-auth.guard';
import { ConsentService, ConsentState } from './consent.service';
import { parseConsentPurpose, parseReauth, parseSetConsent } from './dto/privacy.dto';
import { DSAR_RECEIPT_HEADER, DsarReceiptService } from './dsar-receipt.service';
import { DeleteAccountResponse, DsarRequestItem, DsarService } from './dsar.service';

/** Consent (docs/04 §8.4/§8.9), Bearer. */
@Controller('me/consents')
@UseGuards(JwtAuthGuard)
export class ConsentController {
  constructor(private readonly consents: ConsentService) {}

  @Get()
  list(@CurrentUser() user: AuthUser): Promise<ConsentState[]> {
    return this.consents.list(user.memberId);
  }

  @Put(':purpose')
  @HttpCode(HttpStatus.OK)
  set(@Param('purpose') purpose: string, @Body() body: unknown, @CurrentUser() user: AuthUser, @Req() req: FastifyRequest): Promise<ConsentState> {
    return this.consents.set(user.memberId, parseConsentPurpose(purpose), parseSetConsent(body), clientIp(req));
  }
}

/** DSAR (docs/04 §8.4/§8.9; PDP_DSAR_DESIGN §3), Bearer. `GET /v1/me` tetap di MeController. */
@Controller('me')
@UseGuards(JwtAuthGuard)
export class DsarController {
  constructor(private readonly dsar: DsarService) {}

  @Post('reauth')
  async reauth(
    @Body() body: unknown,
    @CurrentUser() user: AuthUser,
    @Req() req: FastifyRequest,
    @Res({ passthrough: true }) reply: FastifyReply,
  ): Promise<unknown> {
    const res = await this.dsar.reauth(user, parseReauth(body), clientIp(req));
    void reply.status(res.status);
    return res.body;
  }

  @Get('data')
  async data(@CurrentUser() user: AuthUser, @StepUpToken() stepUp: string | undefined): Promise<Record<string, unknown>> {
    this.dsar.requireStepUp(user, stepUp);
    return this.dsar.exportData(user);
  }

  @Delete()
  @HttpCode(HttpStatus.ACCEPTED)
  async remove(@CurrentUser() user: AuthUser, @StepUpToken() stepUp: string | undefined): Promise<DeleteAccountResponse> {
    // SEC-40: token step-up sekali pakai untuk hapus akun
    await this.dsar.consumeStepUp(user, stepUp);
    return this.dsar.requestDelete(user);
  }

  @Post('delete/cancel')
  @HttpCode(HttpStatus.OK)
  cancel(@CurrentUser() user: AuthUser): Promise<{ request_id: string; status: 'cancelled' }> {
    return this.dsar.cancelDelete(user);
  }

  @Get('requests')
  requests(@CurrentUser() user: AuthUser): Promise<{ items: DsarRequestItem[] }> {
    return this.dsar.listRequests(user);
  }
}

/**
 * R6 (docs/04 §8.13): status permintaan hapus akun TANPA sesi (akun bisa sudah terhapus). Otorisasi = header
 * `X-DSAR-Receipt` (receipt_token dari `DELETE /v1/me`). Tanpa JwtAuthGuard; rate limit per IP di service.
 * Semua kegagalan otorisasi → 404 generik. Respons tanpa PII.
 */
@Controller('dsar/requests')
export class DsarReceiptController {
  constructor(private readonly receipts: DsarReceiptService) {}

  @Get(':id')
  status(@Param('id') id: string, @Req() req: FastifyRequest): Promise<DsarRequestItem> {
    const raw = req.headers[DSAR_RECEIPT_HEADER];
    return this.receipts.status(id, typeof raw === 'string' ? raw : undefined, clientIp(req));
  }
}
