import { Body, Controller, Get, Header, HttpCode, HttpStatus, Post, Req } from '@nestjs/common';
import { FastifyRequest } from 'fastify';
import { AppConfig } from '../config/app-config';
import { Errors } from '../common/errors/api-error';
import { MemberService } from '../common/member/member.service';
import { clientIp } from '../common/net/client-ip';
import { AccessTokenService, AuthUser } from '../common/security/access-token.service';
import { BehaviorService, IngestResponse } from './behavior.service';

const BEARER_RE = /^Bearer\s+([A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]+)$/i;

/**
 * Bearer OPSIONAL (§28.6: publik atau dengan Bearer member). Header ada tetapi tidak valid → 401 (klien membuang token),
 * tidak diam-diam diturunkan menjadi anonim.
 */
export async function optionalMember(req: FastifyRequest, tokens: AccessTokenService, members: MemberService): Promise<AuthUser | null> {
  const header = req.headers.authorization;
  if (header === undefined) return null;
  const m = typeof header === 'string' ? BEARER_RE.exec(header.trim()) : null;
  if (!m) throw Errors.unauthorized();
  const claims = tokens.verify(m[1]!);
  if (!claims) throw Errors.unauthorized();
  const member = await members.findActive(claims.memberId);
  if (!member) throw Errors.unauthorized();
  return { memberId: member.id, memberHash: member.memberHash };
}

function rawBytes(req: FastifyRequest, body: unknown): number {
  const raw = (req as FastifyRequest & { rawBody?: Buffer | string }).rawBody;
  if (raw !== undefined) return typeof raw === 'string' ? Buffer.byteLength(raw) : raw.length;
  return Buffer.byteLength(JSON.stringify(body ?? null));
}

/** Event perilaku visitor (docs/04 §28.6, ADR-047 Proposed). */
@Controller('events')
export class BehaviorController {
  constructor(
    private readonly behavior: BehaviorService,
    private readonly tokens: AccessTokenService,
    private readonly members: MemberService,
  ) {}

  @Post('consent')
  @HttpCode(HttpStatus.ACCEPTED)
  @Header('Cache-Control', 'no-store')
  consent(@Body() body: unknown, @Req() req: FastifyRequest): Promise<{ recorded: boolean; granted?: boolean; disabled?: true }> {
    return this.behavior.recordDeviceConsent(body, clientIp(req));
  }

  @Post()
  @HttpCode(HttpStatus.ACCEPTED)
  @Header('Cache-Control', 'no-store')
  async ingest(@Body() body: unknown, @Req() req: FastifyRequest): Promise<IngestResponse> {
    const user = await optionalMember(req, this.tokens, this.members);
    return this.behavior.ingest(body, rawBytes(req, body), clientIp(req), user);
  }
}

export interface PublicConfig {
  features: { tracking: boolean; wishlist: boolean; restock_alert: boolean; price_alert: boolean; campaign_inbox: boolean };
  /** Versi teks banner consent `tracking` aktif (null = belum dikonfigurasi → banner tidak ditampilkan). */
  tracking_consent: { version: string } | null;
}

/**
 * `GET /v1/config` (§28.6): sakelar fitur untuk klien. Publik, tanpa data member. Tracking hanya `true` bila flag aktif
 * DAN teks consent `tracking` dikonfigurasi (tanpa teks, banner tidak bisa ditampilkan → klien tidak boleh mengirim).
 */
@Controller('config')
export class PublicConfigController {
  constructor(private readonly config: AppConfig) {}

  @Get()
  @Header('Cache-Control', 'public, max-age=60')
  get(): PublicConfig {
    let text: { version: string; textSha256: string } | null = null;
    try {
      text = this.config.consentActiveText('tracking');
    } catch {
      text = null;
    }
    const tracking = this.config.behaviorTrackingEnabled && text !== null;
    return {
      features: {
        tracking,
        wishlist: this.config.wishlistEnabled,
        restock_alert: this.config.restockAlertEnabled,
        price_alert: this.config.priceAlertEnabled,
        campaign_inbox: this.config.promoCampaignEnabled,
      },
      tracking_consent: tracking && text ? { version: text.version } : null,
    };
  }
}
