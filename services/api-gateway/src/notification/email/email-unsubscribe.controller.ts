import { Body, Controller, HttpCode, HttpStatus, Post, Req } from '@nestjs/common';
import { FastifyRequest } from 'fastify';
import { clientIp } from '../../common/net/client-ip';
import { EmailUnsubscribeService, UnsubscribeResponse, parseUnsubscribeBody } from './email-unsubscribe.service';

/**
 * `POST /v1/email/unsubscribe` (docs/04 §28.9): publik, tanpa Bearer, rate limit per IP di service.
 * Token HANYA di body JSON (bukan URL/query — R2 docs/04 §8.13); halaman web membaca token dari fragmen `#t=`.
 */
@Controller('email/unsubscribe')
export class EmailUnsubscribeController {
  constructor(private readonly service: EmailUnsubscribeService) {}

  @Post()
  @HttpCode(HttpStatus.OK)
  unsubscribe(@Body() body: unknown, @Req() req: FastifyRequest): Promise<UnsubscribeResponse> {
    return this.service.unsubscribe(parseUnsubscribeBody(body), clientIp(req));
  }
}
