import { Controller, Get, UseGuards } from '@nestjs/common';
import { AuthUser } from '../common/security/access-token.service';
import { CurrentUser, JwtAuthGuard } from '../common/security/jwt-auth.guard';
import { AuthService, MeResponse } from './auth.service';

/** `GET /v1/me` (Bearer) — kontrak §3.2. Nomor HP hanya dikembalikan dalam bentuk ter-mask. */
@Controller('me')
@UseGuards(JwtAuthGuard)
export class MeController {
  constructor(private readonly auth: AuthService) {}

  @Get()
  me(@CurrentUser() user: AuthUser): Promise<MeResponse> {
    return this.auth.me(user);
  }
}
