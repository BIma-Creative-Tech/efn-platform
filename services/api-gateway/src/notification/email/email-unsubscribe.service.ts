import { Inject, Injectable, Logger } from '@nestjs/common';
import { isIP } from 'node:net';
import { DB, Db } from '../../common/db/db';
import { ApiError, Errors } from '../../common/errors/api-error';
import { PII_AAD, PiiCipher } from '../../common/pii/crypto';
import { redisKeyHash } from '../../common/pii/pii';
import { RateLimiter } from '../../common/rate-limit/rate-limiter';
import { asRecord } from '../../common/validation';
import { AppConfig } from '../../config/app-config';
import { EmailConfig } from './email.config';
import { withdrawMarketing } from './marketing-consent';
import { UNSUBSCRIBE_TOKEN_MAX_LENGTH, verifyUnsubscribeToken } from './unsubscribe-token';

export interface UnsubscribeResponse {
  status: 'unsubscribed';
}

export function unsubscribeInvalid(): ApiError {
  return ApiError.of(400, 'unsubscribe_token_invalid', 'Tautan berhenti berlangganan tidak valid, kedaluwarsa, atau sudah dipakai.');
}

/** Body `POST /v1/email/unsubscribe`: `{ "token": "eu1...." }` (field lain ditolak). */
export function parseUnsubscribeBody(body: unknown): string {
  const o = asRecord(body);
  for (const k of Object.keys(o)) if (k !== 'token') throw Errors.invalidRequest();
  const t = o.token;
  if (typeof t !== 'string' || t.length === 0 || t.length > UNSUBSCRIBE_TOKEN_MAX_LENGTH) throw Errors.invalidRequest();
  return t;
}

/**
 * Berhenti berlangganan email (docs/04 §28.9). Publik (tanpa Bearer), rate limit per IP
 * (`EMAIL_UNSUBSCRIBE_RATE_PER_MIN`, key Redis = HMAC IP, SEC-24).
 *
 * Token sekali pakai: `INSERT platform.email_unsubscribe_used (jti)` + penarikan consent `marketing` dalam SATU transaksi
 * → token yang dipakai ulang (atau dua klik bersamaan) ditolak 400 generik. Semua kegagalan token (tanda tangan salah,
 * kedaluwarsa, sudah dipakai) memberi respons yang sama. Respons tidak memuat identitas member.
 * Berfungsi walau `EMAIL_ENABLED=false` (hak berhenti tidak bergantung pada flag kirim); kunci belum ada → 503.
 */
@Injectable()
export class EmailUnsubscribeService {
  private readonly logger = new Logger(EmailUnsubscribeService.name);
  now: () => number = () => Date.now();

  constructor(
    private readonly config: EmailConfig,
    private readonly appConfig: AppConfig,
    @Inject(DB) private readonly db: Db,
    private readonly cipher: PiiCipher,
    private readonly limiter: RateLimiter,
  ) {}

  async unsubscribe(token: string, ip: string | null): Promise<UnsubscribeResponse> {
    await this.limiter.enforce(`email:unsub-ip:${redisKeyHash(this.appConfig.redisKeyPepper, ip ?? 'unknown')}`, this.config.unsubscribeRatePerMin, 60);
    let key: Buffer | null;
    try {
      key = this.config.unsubscribeKey;
    } catch {
      key = null;
    }
    if (!key) throw Errors.serviceUnavailable('unsubscribe_unavailable');
    const claims = verifyUnsubscribeToken(key, token, this.now());
    if (!claims) throw unsubscribeInvalid();
    const ipEnc = ip && isIP(ip) ? this.cipher.encrypt(ip, PII_AAD.CONSENT_IP) : null;
    await this.db.tx(async (q) => {
      const used = await q.query<{ jti: string }>(
        `INSERT INTO platform.email_unsubscribe_used (jti, expires_at)
         VALUES ($1, to_timestamp($2))
         ON CONFLICT (jti) DO NOTHING
         RETURNING jti::text AS jti`,
        [claims.jti, claims.exp],
      );
      if ((used.rowCount ?? 0) === 0) throw unsubscribeInvalid();
      const withdrawn = await withdrawMarketing(q, this.appConfig, claims.memberId, 'email_unsubscribe', ipEnc);
      this.logger.log(`berhenti berlangganan email diproses (consent marketing ${withdrawn ? 'dicabut' : 'sudah tidak aktif'})`);
    });
    return { status: 'unsubscribed' };
  }
}
