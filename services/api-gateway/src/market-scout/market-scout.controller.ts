import { Controller, Get, HttpCode, HttpStatus, Param, Post, Req, UseGuards } from '@nestjs/common';
import { FastifyRequest } from 'fastify';
import { AppConfig } from '../config/app-config';
import { Errors } from '../common/errors/api-error';
import { AuthUser } from '../common/security/access-token.service';
import { CurrentUser, JwtAuthGuard } from '../common/security/jwt-auth.guard';
import { ReportStatusResponse, MarketScoutService, SubmitReportResponse } from './market-scout.service';
import { MultipartRequest, readReportForm } from './multipart-form';

/** `/v1/market-scout/*` (kontrak §3.3), Bearer. */
@Controller('market-scout')
@UseGuards(JwtAuthGuard)
export class MarketScoutController {
  constructor(
    private readonly service: MarketScoutService,
    private readonly config: AppConfig,
  ) {}

  @Post('reports')
  @HttpCode(HttpStatus.ACCEPTED)
  async create(@Req() req: FastifyRequest, @CurrentUser() user: AuthUser): Promise<SubmitReportResponse> {
    const mreq = req as unknown as Partial<MultipartRequest>;
    if (typeof mreq.isMultipart !== 'function' || typeof mreq.parts !== 'function' || !mreq.isMultipart()) {
      throw Errors.unsupportedMediaType();
    }
    const form = await readReportForm(mreq.parts(), { maxFileBytes: this.config.marketScoutMaxBytes });
    return this.service.submit(user, form);
  }

  @Get('reports/:id')
  get(@Param('id') id: string, @CurrentUser() user: AuthUser): Promise<ReportStatusResponse> {
    return this.service.get(user, id);
  }
}
