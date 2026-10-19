import { Controller, Get, HttpCode, HttpStatus, Param, Put, Query, RawBodyRequest, Req, UseGuards } from '@nestjs/common';
import { FastifyRequest } from 'fastify';
import { ServiceKeyGuard } from '../common/security/service-key.guard';
import { parseLoyaltyPolicy } from '../loyalty/loyalty-policy';
import { LoyaltyPolicyService, PublishResult } from '../loyalty/loyalty-policy.service';
import { LoyaltyAccrualResponse, LoyaltyAccrualService } from './loyalty-accrual.service';
import { LoyaltyPolicySignatureGuard } from './loyalty-policy.guard';

/**
 * G21 docs/04 §13.1 no. 3 & 6 (L-61): keuangan poin per PT, server Odoo → BFF (mTLS + `X-EFN-Service-Key` POS).
 * - `PUT  /v1/pos/loyalty/policies/{company_code}/{version}` — publikasi kebijakan (outbox Odoo) + `X-EFN-Signature`.
 * - `GET  /v1/pos/loyalty/accruals?company_code=&date=` — agregat akrual harian (cron Odoo 02:00 WIB).
 * Body PUT divalidasi dari `rawBody` (byte yang sama dengan yang ditandatangani).
 */
@Controller('pos/loyalty')
@UseGuards(ServiceKeyGuard)
export class PosLoyaltyFinanceController {
  constructor(
    private readonly policies: LoyaltyPolicyService,
    private readonly accruals: LoyaltyAccrualService,
  ) {}

  @Put('policies/:company_code/:version')
  @HttpCode(HttpStatus.OK)
  @UseGuards(LoyaltyPolicySignatureGuard)
  publish(
    @Param('company_code') company: string,
    @Param('version') version: string,
    @Req() req: RawBodyRequest<FastifyRequest>,
  ): Promise<PublishResult> {
    return this.policies.publish(parseLoyaltyPolicy(req.rawBody, company, version));
  }

  @Get('accruals')
  accrual(@Query('company_code') company: unknown, @Query('date') date: unknown): Promise<LoyaltyAccrualResponse> {
    return this.accruals.accrual(company, date);
  }
}
