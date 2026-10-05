import { Body, Controller, Get, Header, HttpCode, HttpStatus, Post, Req } from '@nestjs/common';
import { FastifyRequest } from 'fastify';
import { clientIp } from '../common/net/client-ip';
import { AltchaChallenge } from './altcha.service';
import { AuthOtpRequestResponse, AuthOtpVerifyResponse, AuthService } from './auth.service';
import { parseAuthOtpRequest, parseAuthOtpVerify } from './dto/auth.dto';

/** `/v1/auth/*` — publik (dipanggil Next.js server-side / widget ALTCHA), dilindungi rate limit + ALTCHA. */
@Controller('auth')
export class AuthController {
  constructor(private readonly auth: AuthService) {}

  /** docs/04 §8.1: challenge ALTCHA (format JSON yang dipahami widget `altcha` sebagai `challengeurl`). */
  @Get('challenge')
  @Header('Cache-Control', 'no-store')
  challenge(@Req() req: FastifyRequest): Promise<AltchaChallenge> {
    return this.auth.challenge(clientIp(req));
  }

  @Post('otp/request')
  @HttpCode(HttpStatus.ACCEPTED)
  requestOtp(@Body() body: unknown, @Req() req: FastifyRequest): Promise<AuthOtpRequestResponse> {
    return this.auth.requestOtp(parseAuthOtpRequest(body), clientIp(req));
  }

  @Post('otp/verify')
  @HttpCode(HttpStatus.OK)
  verifyOtp(@Body() body: unknown, @Req() req: FastifyRequest): Promise<AuthOtpVerifyResponse> {
    return this.auth.verifyOtp(parseAuthOtpVerify(body), clientIp(req));
  }
}
