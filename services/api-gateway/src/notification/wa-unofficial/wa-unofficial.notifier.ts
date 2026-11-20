import { Inject, Injectable, Logger, OnModuleInit } from '@nestjs/common';
import { DB, Db } from '../../common/db/db';
import { PII_AAD, PiiCipher } from '../../common/pii/crypto';
import { isMemberRef, maskMsisdn, memberHash } from '../../common/pii/pii';
import { KV_STORE, KvStore } from '../../common/redis/kv-store';
import { isUuid } from '../../common/validation';
import { AppConfig } from '../../config/app-config';
import { isMarketingGranted } from '../email/marketing-consent';
import { WaUnofficialConfig } from './wa-unofficial.config';
import {
  NotifyWaMessage,
  WA_MAX_EXPIRATION_MS,
  WA_SOURCE_REF_RE,
  WA_SOURCE_RE,
  WA_TEMPLATES,
  WaCategory,
  WaContractError,
  isWaCategory,
  normalizeWaParams,
  waIdempotencyKey,
  waToHash,
} from './wa-unofficial.contract';
import { WA_QUEUE_PUBLISHER, WaQueuePublisher } from './wa-unofficial.publisher';

export interface WaEnqueueInput {
  memberId: string;
  category: WaCategory;
  source: string;
  sourceRef: string;
  template: string;
  params: Record<string, string | number>;
  expiresAt: Date;
}

export type WaEnqueueResult = 'queued' | 'skipped_disabled' | 'skipped_no_consent' | 'skipped_no_phone';

/** Kunci Redis penanda kanal tidak tersedia (diisi consumer hasil saat `banned`/`disconnected`, ADR-044 §3.7). */
export const WA_CHANNEL_DOWN_KEY = 'waun:down';

/**
 * Antarmuka untuk dispatcher BFF-A: WA tidak resmi HANYA untuk promo & notify-me (ADR-044 §3.1, L-158).
 *
 * Gerbang: flag (`WA_UNOFFICIAL_ENABLED=true`, production wajib `WA_UNOFFICIAL_RISK_ACCEPTANCE_REF`, kunci hash ada)
 * → kanal tidak sedang `banned/disconnected` → member aktif → consent `marketing` (semua kategori; Q-SM25 default)
 * → nomor HP (= `member_ref`, sudah terverifikasi OTP saat login; hanya nomor Indonesia).
 * Flag mati → TIDAK publish apa pun (dilewati, bukan diantre).
 *
 * Nomor didekripsi dari `platform.member.member_ref_enc` hanya untuk pesan ini, integritas dicek terhadap
 * `member_hash`, lalu dikirim sebagai `to_e164` (pengecualian L-159) dengan `to_hash` HMAC. Baris
 * `wa_unofficial_outbound` + pemetaan `wa_unofficial_recipient` (to_hash → member, untuk STOP) ditulis dalam transaksi
 * yang sama; publish gagal → rollback. Idempoten per `wa:<source>:<source_ref>:<to_hash>`.
 * `expiresAt` dipangkas ke ≤ 24 jam; yang sudah lewat → `WaContractError` (kesalahan pemanggil).
 */
@Injectable()
export class WaUnofficialNotifier implements OnModuleInit {
  private readonly logger = new Logger(WaUnofficialNotifier.name);
  now: () => number = () => Date.now();

  constructor(
    private readonly config: WaUnofficialConfig,
    private readonly appConfig: AppConfig,
    @Inject(DB) private readonly db: Db,
    private readonly cipher: PiiCipher,
    @Inject(KV_STORE) private readonly kv: KvStore,
    @Inject(WA_QUEUE_PUBLISHER) private readonly publisher: WaQueuePublisher,
  ) {}

  onModuleInit(): void {
    const reason = this.config.disabledReason;
    if (reason === 'production_without_risk_ref') {
      this.logger.error(
        'WA_UNOFFICIAL_ENABLED=true di APP_ENV=production tanpa WA_UNOFFICIAL_RISK_ACCEPTANCE_REF → kanal WA unofficial DIMATIKAN (ADR-044, Q-SM14)',
      );
    } else if (reason === 'hash_key_missing') {
      this.logger.error('WA_UNOFFICIAL_ENABLED=true tetapi wa_unofficial_hash_key belum dikonfigurasi → kanal dimatikan');
    } else if (reason === null) {
      this.logger.warn('Kanal WA unofficial AKTIF (ADR-044 Proposed; hanya promo & notify-me)');
    }
  }

  async enqueue(input: WaEnqueueInput): Promise<WaEnqueueResult> {
    if (!this.config.enabled) return 'skipped_disabled';
    if (!isWaCategory(input.category)) throw new WaContractError('category WA di luar allowlist');
    const tpl = WA_TEMPLATES[input.template];
    if (!tpl || !Object.prototype.hasOwnProperty.call(WA_TEMPLATES, input.template)) throw new WaContractError('template WA tidak dikenal');
    if (tpl.category !== input.category) throw new WaContractError('template WA tidak sesuai category');
    if (typeof input.source !== 'string' || !WA_SOURCE_RE.test(input.source)) throw new WaContractError('source WA tidak valid');
    if (typeof input.sourceRef !== 'string' || !WA_SOURCE_REF_RE.test(input.sourceRef)) throw new WaContractError('sourceRef WA tidak valid');
    const params = normalizeWaParams(input.template, input.params ?? {});
    const nowMs = this.now();
    const expMs = input.expiresAt instanceof Date ? input.expiresAt.getTime() : Number.NaN;
    if (!Number.isFinite(expMs) || expMs <= nowMs + 1000) throw new WaContractError('expiresAt sudah lewat atau tidak valid');
    const expiresAt = Math.min(expMs, nowMs + WA_MAX_EXPIRATION_MS);

    if ((await this.kv.get(WA_CHANNEL_DOWN_KEY)) !== null) return 'skipped_disabled';
    if (!isUuid(input.memberId)) return 'skipped_no_phone';
    const memberId = input.memberId.toLowerCase();
    const hashKey = this.config.hashKey;
    if (!hashKey) return 'skipped_disabled';

    let masked = '';
    const result = await this.db.tx(async (q): Promise<WaEnqueueResult> => {
      const res = await q.query<{ member_hash: string; member_ref_enc: Buffer | null }>(
        `SELECT member_hash, member_ref_enc FROM platform.member WHERE id = $1 AND deleted_at IS NULL`,
        [memberId],
      );
      const row = res.rows[0];
      if (!row) return 'skipped_no_phone';
      if (!(await isMarketingGranted(q, memberId))) return 'skipped_no_consent';
      if (!row.member_ref_enc) return 'skipped_no_phone';
      const toE164 = this.cipher.decrypt(row.member_ref_enc, PII_AAD.MEMBER_REF);
      if (!isMemberRef(toE164) || memberHash(this.appConfig.memberHashPepper, toE164) !== row.member_hash) return 'skipped_no_phone';
      masked = maskMsisdn(toE164);
      const toHash = waToHash(hashKey, toE164);
      const key = waIdempotencyKey(input.source, input.sourceRef, toHash);
      await q.query(
        `INSERT INTO platform.wa_unofficial_recipient (to_hash, member_id, last_queued_at)
         VALUES ($1, $2, now())
         ON CONFLICT (to_hash) DO UPDATE SET member_id = EXCLUDED.member_id, last_queued_at = now()`,
        [toHash, memberId],
      );
      const ins = await q.query<{ idempotency_key: string }>(
        `INSERT INTO platform.wa_unofficial_outbound
           (idempotency_key, member_id, to_hash, category, source, source_ref, template, expires_at)
         VALUES ($1, $2, $3, $4, $5, $6, $7, to_timestamp($8 / 1000.0))
         ON CONFLICT (idempotency_key) DO NOTHING
         RETURNING idempotency_key`,
        [key, memberId, toHash, input.category, input.source, input.sourceRef, input.template, expiresAt],
      );
      if ((ins.rowCount ?? 0) === 0) return 'queued'; // sudah diantre sebelumnya
      const msg: NotifyWaMessage = {
        schema_version: 1,
        idempotency_key: key,
        to_e164: toE164,
        to_hash: toHash,
        category: input.category,
        template: input.template,
        params,
        expires_at: new Date(expiresAt).toISOString(),
      };
      await this.publisher.publish(msg, key, Math.max(1000, Math.floor(expiresAt - nowMs)));
      return 'queued';
    });
    if (result === 'queued') this.logger.log(`notify.wa_unofficial ${input.category}/${input.template} → ${masked || '(sudah diantre)'}: queued`);
    return result;
  }
}
