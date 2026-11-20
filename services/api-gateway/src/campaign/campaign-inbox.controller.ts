import { Controller, Get, Header, Inject, Injectable, Logger, UseGuards } from '@nestjs/common';
import { AppConfig } from '../config/app-config';
import { DB, Db } from '../common/db/db';
import { Errors } from '../common/errors/api-error';
import { PiiCipher } from '../common/pii/crypto';
import { AuthUser } from '../common/security/access-token.service';
import { CurrentUser, JwtAuthGuard } from '../common/security/jwt-auth.guard';
import { decryptVoucherCode } from './campaign-sync.service';

export interface InboxItem {
  id: string;
  title: string;
  body: string;
  valid_to: string;
  created_at: string;
  voucher: { code: string; mode: 'anonymous_code' | 'public_code' } | null;
}

/**
 * Inbox/voucher in-app (kanal `inbox` ADR-047 §3.4). USULAN KONTRAK G50-A (belum ada di §28.8): `GET /v1/me/inbox`
 * (Bearer, `no-store`) → item kampanye yang masih berlaku + kode voucher milik member (didekripsi hanya untuk pemilik).
 * Kill switch `PROMO_CAMPAIGN_ENABLED` → 404.
 */
@Injectable()
export class CampaignInboxService {
  private readonly logger = new Logger(CampaignInboxService.name);

  constructor(
    private readonly config: AppConfig,
    @Inject(DB) private readonly db: Db,
    private readonly cipher: PiiCipher,
  ) {}

  async list(user: AuthUser): Promise<{ items: InboxItem[] }> {
    if (!this.config.promoCampaignEnabled) throw Errors.notFound();
    const res = await this.db.query<{
      id: string;
      title: string;
      body: string;
      valid_to: Date | string;
      created_at: Date | string;
      v_id: string | null;
      code_enc: Buffer | null;
      mode: 'anonymous_code' | 'public_code' | null;
      v_member: string | null;
    }>(
      `SELECT i.id::text AS id, i.title, i.body, i.valid_to, i.created_at, v.id::text AS v_id, v.code_enc, v.mode, v.member_id::text AS v_member
         FROM platform.member_inbox i
         LEFT JOIN platform.member_voucher v ON v.id = i.voucher_id
        WHERE i.member_id = $1 AND i.valid_to > now()
        ORDER BY i.created_at DESC LIMIT 50`,
      [user.memberId],
    );
    return {
      items: res.rows.map((r) => {
        let voucher: InboxItem['voucher'] = null;
        // anonymous_code hanya ditampilkan ke member yang ditugaskan (pertahanan berlapis selain join inbox)
        const owned = r.mode === 'public_code' || (r.mode === 'anonymous_code' && r.v_member === user.memberId);
        if (r.code_enc && r.mode && r.v_id && owned) {
          try {
            // SEC-G50-11: hanya AAD per baris (v2, terikat id voucher); tanpa cadangan v1
            voucher = { code: decryptVoucherCode(this.cipher, r.v_id, r.code_enc), mode: r.mode };
          } catch {
            this.logger.warn(`Kode voucher inbox ${r.id} tidak dapat didekripsi`);
          }
        }
        return {
          id: r.id,
          title: r.title,
          body: r.body,
          valid_to: new Date(r.valid_to).toISOString(),
          created_at: new Date(r.created_at).toISOString(),
          voucher,
        };
      }),
    };
  }
}

@Controller('me/inbox')
@UseGuards(JwtAuthGuard)
export class CampaignInboxController {
  constructor(private readonly inbox: CampaignInboxService) {}

  @Get()
  @Header('Cache-Control', 'no-store')
  list(@CurrentUser() user: AuthUser): Promise<{ items: InboxItem[] }> {
    return this.inbox.list(user);
  }
}
