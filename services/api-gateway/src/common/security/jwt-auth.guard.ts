import { CanActivate, ExecutionContext, Injectable, createParamDecorator } from '@nestjs/common';
import { FastifyRequest } from 'fastify';
import { Errors } from '../errors/api-error';
import { MemberService } from '../member/member.service';
import { AccessTokenService, AuthUser } from './access-token.service';

export type AuthenticatedRequest = FastifyRequest & { user?: AuthUser };

const BEARER_RE = /^Bearer\s+([A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]+)$/i;
/** Header token step-up DSAR (docs/04 §8.9; dipakai `DELETE /v1/me` dan `GET /v1/me/data`). */
export const STEP_UP_HEADER = 'x-step-up-token';

/**
 * Guard `Authorization: Bearer <jwt>` untuk endpoint pelanggan (Super-App via Next.js server-side).
 * v3 (§8.2): `sub` = member.id; guard memastikan member masih aktif (`deleted_at IS NULL`) sehingga token akun yang
 * sedang/selesai dihapus langsung ditolak, lalu mengisi `req.user = { memberId, memberHash }`.
 */
@Injectable()
export class JwtAuthGuard implements CanActivate {
  constructor(
    private readonly tokens: AccessTokenService,
    private readonly members: MemberService,
  ) {}

  async canActivate(ctx: ExecutionContext): Promise<boolean> {
    const req = ctx.switchToHttp().getRequest<AuthenticatedRequest>();
    const header = req.headers.authorization;
    const match = typeof header === 'string' ? BEARER_RE.exec(header.trim()) : null;
    if (!match) throw Errors.unauthorized();
    const claims = this.tokens.verify(match[1]);
    if (!claims) throw Errors.unauthorized();
    const member = await this.members.findActive(claims.memberId);
    if (!member) throw Errors.unauthorized();
    req.user = { memberId: member.id, memberHash: member.memberHash };
    return true;
  }
}

export const CurrentUser = createParamDecorator((_data: unknown, ctx: ExecutionContext): AuthUser => {
  const req = ctx.switchToHttp().getRequest<AuthenticatedRequest>();
  if (!req.user) throw Errors.unauthorized();
  return req.user;
});

/** Nilai header step-up (string tunggal) atau undefined. */
export const StepUpToken = createParamDecorator((_data: unknown, ctx: ExecutionContext): string | undefined => {
  const req = ctx.switchToHttp().getRequest<FastifyRequest>();
  const v = req.headers[STEP_UP_HEADER];
  return typeof v === 'string' && v.length > 0 && v.length <= 4096 ? v : undefined;
});
