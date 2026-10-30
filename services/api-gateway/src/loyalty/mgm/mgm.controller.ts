import { Body, Controller, Get, HttpCode, HttpStatus, Param, Post, Put, RawBodyRequest, Req, Res, UseGuards } from '@nestjs/common';
import { FastifyReply, FastifyRequest } from 'fastify';
import { noStore } from '../../catalog/online-http';
import { Errors } from '../../common/errors/api-error';
import { RateLimiter } from '../../common/rate-limit/rate-limiter';
import { AuthUser } from '../../common/security/access-token.service';
import { CurrentUser, JwtAuthGuard } from '../../common/security/jwt-auth.guard';
import { ServiceKeyGuard } from '../../common/security/service-key.guard';
import { LoyaltyPolicySignatureGuard } from '../../pos/loyalty-policy.guard';
import { parseMgmPolicy } from './mgm-policy';
import { MgmPolicyService, MgmPublishResult } from './mgm-policy.service';
import { MgmReferralService, ReferralView } from './mgm-referral.service';

/** §20.2: batas per member (anti-enumerasi kode, R-B06). */
const RL = { claim: { limit: 10, window: 3600 }, read: { limit: 60, window: 3600 } };

/** Body klaim kode: `{ code }` saja (≤ 1 KB). */
export function parseClaimBody(body: unknown, req: RawBodyRequest<FastifyRequest>): unknown {
  const len = req.rawBody ? req.rawBody.length : Number(req.headers['content-length'] ?? 0);
  if (len > 1024) throw Errors.payloadTooLarge();
  if (typeof body !== 'object' || body === null || Array.isArray(body)) throw Errors.invalidRequest();
  const o = body as Record<string, unknown>;
  if (Object.keys(o).length !== 1 || !('code' in o)) throw Errors.invalidRequest();
  return o.code;
}

/** Referral member Super-App (docs/04 §20.2), Bearer, `no-store`. Kill switch `MGM_ENABLED` (503 `mgm_unavailable`). */
@Controller('me/referral')
@UseGuards(JwtAuthGuard)
export class MgmReferralController {
  constructor(
    private readonly referral: MgmReferralService,
    private readonly limiter: RateLimiter,
  ) {}

  @Get()
  async view(@CurrentUser() user: AuthUser, @Res({ passthrough: true }) reply: FastifyReply): Promise<ReferralView> {
    noStore(reply);
    this.referral.requireEnabled();
    await this.limiter.enforce(`mgm:read:m:${user.memberId}`, RL.read.limit, RL.read.window);
    return this.referral.view(user);
  }

  @Post('claim')
  @HttpCode(HttpStatus.OK)
  async claim(
    @Body() body: unknown,
    @CurrentUser() user: AuthUser,
    @Req() req: RawBodyRequest<FastifyRequest>,
    @Res({ passthrough: true }) reply: FastifyReply,
  ): Promise<{ status: 'linked'; linked_at: string }> {
    noStore(reply);
    this.referral.requireEnabled();
    const code = parseClaimBody(body, req);
    await this.limiter.enforce(`mgm:claim:m:${user.memberId}`, RL.claim.limit, RL.claim.window);
    return this.referral.claim(user, code);
  }
}

/**
 * §20.1: publikasi kebijakan MGM server Odoo → BFF (service key POS + `X-EFN-Signature`, sama dengan §13.1 no. 3).
 * Body divalidasi dari `rawBody` (byte yang sama dengan yang ditandatangani). Tidak bergantung `MGM_ENABLED`: kebijakan
 * boleh dipublikasikan sebelum fitur dinyalakan.
 */
@Controller('pos/loyalty')
@UseGuards(ServiceKeyGuard)
export class MgmPolicyController {
  constructor(private readonly policies: MgmPolicyService) {}

  @Put('mgm-policies/:company_code/:version')
  @HttpCode(HttpStatus.OK)
  @UseGuards(LoyaltyPolicySignatureGuard)
  publish(
    @Param('company_code') company: string,
    @Param('version') version: string,
    @Req() req: RawBodyRequest<FastifyRequest>,
  ): Promise<MgmPublishResult> {
    return this.policies.publish(parseMgmPolicy(req.rawBody, company, version));
  }
}
