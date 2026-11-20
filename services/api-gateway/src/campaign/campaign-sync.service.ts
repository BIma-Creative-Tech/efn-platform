import { Inject, Injectable, Logger, OnModuleDestroy, OnModuleInit } from '@nestjs/common';
import { createHmac, randomUUID } from 'node:crypto';
import { AppConfig } from '../config/app-config';
import { DB, Db } from '../common/db/db';
import { OutboxService } from '../common/outbox/outbox.service';
import { PiiCipher, piiAad } from '../common/pii/crypto';
import { CampaignRequestedMessage, PROMO_CAMPAIGN_REQUESTED_QUEUE, campaignKey } from './campaign-message';
import { CLAIM_LIMIT, ClaimResult, ClaimedCampaign, ODOO_CAMPAIGN, OdooCampaignApi, OdooCampaignError } from './odoo-campaign';

/** AAD kolom v1 kode voucher (hanya dibaca sebagai cadangan; registry `pii-columns.ts`). */
export const VOUCHER_CODE_AAD = piiAad('member_voucher', 'code_enc');
/**
 * SEC-G50-11 (pola SEC-102): AAD per baris — ciphertext terikat `member_voucher.id`, sehingga `code_enc` tidak bisa
 * ditukar antar-baris lewat UPDATE DB. Sama dengan `rowAad` entri `member_voucher` di `pii-columns.ts`.
 */
export function voucherCodeAad(id: string): string {
  return `efn:member_voucher.code_enc:v2:${id.toLowerCase()}`;
}

/** Dekripsi kode voucher: v2 (per baris) lalu v1 (kolom) sebagai cadangan. Melempar bila keduanya gagal. */
export function decryptVoucherCode(cipher: PiiCipher, id: string, blob: Buffer): string {
  try {
    return cipher.decrypt(blob, voucherCodeAad(id));
  } catch {
    return cipher.decrypt(blob, VOUCHER_CODE_AAD);
  }
}
/** Retensi (PLACEHOLDER DPO/Finance): inbox 30 hari setelah berakhir, kode voucher 90 hari setelah berakhir. */
const INBOX_RETENTION_DAYS = 30;
const VOUCHER_RETENTION_DAYS = 90;

export function voucherCodeHash(pepper: Buffer, code: string): string {
  return createHmac('sha256', pepper).update(`efn:voucher_code:${code}`, 'utf8').digest('hex');
}

export interface SyncCompanyResult {
  claimed: number;
  stored: number;
  acked: number;
  rejected: number;
}

/**
 * Campaign-sync (docs/04 §28.7, L-156): klaim outbox Odoo `efn.campaign/efn_campaign_claim` per PT → dalam SATU
 * transaksi PG: `platform.campaign` (+ kode voucher terenkripsi untuk `anonymous_code`/`public_code`) + outbox
 * `promo.campaign.requested` (message_id = `campaign:<pt>:<id>:<rev>`) → commit → `efn_campaign_ack(ids)`.
 * Ack gagal → Odoo mengembalikan klaim ke `queued` setelah 10 menit; klaim ulang menemukan `campaign_key` yang sama →
 * tidak menulis/menerbitkan ulang, hanya ack. Baris yang gagal validasi TIDAK di-ack (Odoo: maks 5 percobaan → `failed`
 * + aktivitas marketing). Odoo tidak pernah publish ke RabbitMQ dan tidak menerima data member (L-155).
 * Kill switch `PROMO_CAMPAIGN_ENABLED`. Tidak pernah melempar.
 */
@Injectable()
export class CampaignSyncService implements OnModuleInit, OnModuleDestroy {
  private readonly logger = new Logger(CampaignSyncService.name);
  private timer: NodeJS.Timeout | null = null;
  private running = false;

  constructor(
    private readonly config: AppConfig,
    @Inject(DB) private readonly db: Db,
    @Inject(ODOO_CAMPAIGN) private readonly odoo: OdooCampaignApi,
    private readonly outbox: OutboxService,
    private readonly cipher: PiiCipher,
  ) {}

  onModuleInit(): void {
    if (!this.config.promoCampaignEnabled) return;
    this.timer = setInterval(() => void this.runOnce(), this.config.campaignSyncSeconds * 1000);
    this.timer.unref();
    setTimeout(() => void this.runOnce(), 10_000).unref();
  }

  onModuleDestroy(): void {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
  }

  async runOnce(): Promise<void> {
    if (this.running || !this.config.promoCampaignEnabled) return;
    this.running = true;
    try {
      for (const company of this.config.campaignCompanies) await this.syncCompany(company);
      await this.purge();
    } finally {
      this.running = false;
    }
  }

  async syncCompany(company: string): Promise<SyncCompanyResult | null> {
    let claim: ClaimResult;
    try {
      claim = await this.odoo.claim(company, CLAIM_LIMIT);
    } catch (err) {
      this.logger.warn(`Klaim kampanye PT ${company} gagal: ${err instanceof OdooCampaignError ? `odoo_${err.kind}` : 'error'}`);
      return null;
    }
    for (const r of claim.rejected) {
      this.logger.warn(`Outbox kampanye PT ${company} #${r.outboxId ?? '?'} ditolak validasi BFF (${r.reason}); tidak di-ack`);
    }
    const ackIds: number[] = [];
    let stored = 0;
    for (const item of claim.items) {
      try {
        if (await this.store(company, item)) stored += 1;
        ackIds.push(item.outboxId);
      } catch (err) {
        this.logger.warn(`Simpan kampanye PT ${company} #${item.payload.campaign_id} gagal: ${err instanceof Error ? err.message : 'db'}`);
      }
    }
    let acked = 0;
    if (ackIds.length > 0) {
      try {
        await this.odoo.ack(company, ackIds);
        acked = ackIds.length;
      } catch (err) {
        // Aman: klaim ulang setelah 10 menit → campaign_key sudah ada → hanya ack
        this.logger.warn(`Ack kampanye PT ${company} gagal (${ackIds.length} baris): ${err instanceof OdooCampaignError ? `odoo_${err.kind}` : 'error'}`);
      }
    }
    if (claim.items.length + claim.rejected.length > 0) {
      this.logger.log(`Kampanye PT ${company}: ${claim.items.length} diklaim, ${stored} baru, ${acked} di-ack, ${claim.rejected.length} ditolak`);
    }
    return { claimed: claim.items.length, stored, acked, rejected: claim.rejected.length };
  }

  /** @returns true bila kampanye baru (ditulis + diterbitkan), false bila sudah ada (klaim ulang). */
  async store(company: string, item: ClaimedCampaign): Promise<boolean> {
    const p = item.payload;
    const key = campaignKey(company, p.campaign_id, item.approvalRev);
    return this.db.tx(async (q) => {
      const ins = await q.query(
        `INSERT INTO platform.campaign (campaign_key, company_code, odoo_campaign_id, approval_rev, kind, voucher_mode, payload, valid_to)
         VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8)
         ON CONFLICT (campaign_key) DO NOTHING RETURNING campaign_key`,
        [key, company, p.campaign_id, item.approvalRev, p.kind, p.voucher_mode, JSON.stringify(p), p.valid_to],
      );
      if ((ins.rowCount ?? 0) === 0) return false;
      if (item.codes.length > 0 && (p.voucher_mode === 'anonymous_code' || p.voucher_mode === 'public_code')) {
        const pepper = this.config.redisKeyPepper;
        const ids: string[] = [];
        const encs: Buffer[] = [];
        const hashes: string[] = [];
        for (const code of item.codes) {
          const id = randomUUID();
          ids.push(id);
          encs.push(this.cipher.encrypt(code, voucherCodeAad(id)));
          hashes.push(voucherCodeHash(pepper, code));
        }
        for (let i = 0; i < ids.length; i += 1000) {
          await q.query(
            `INSERT INTO platform.member_voucher (id, campaign_key, mode, code_enc, code_hash, valid_to)
             SELECT t.id, $1, $2, t.enc, t.hash, $3 FROM unnest($4::uuid[], $5::bytea[], $6::text[]) AS t(id, enc, hash)
             ON CONFLICT (code_hash) DO NOTHING`,
            [key, p.voucher_mode, p.valid_to, ids.slice(i, i + 1000), encs.slice(i, i + 1000), hashes.slice(i, i + 1000)],
          );
        }
      }
      const msg: CampaignRequestedMessage = { schema_version: 1, idempotency_key: key, ...p };
      await this.outbox.enqueue(q, PROMO_CAMPAIGN_REQUESTED_QUEUE, { ...msg }, key);
      return true;
    });
  }

  private async purge(): Promise<void> {
    try {
      await this.db.query(`DELETE FROM platform.member_inbox WHERE valid_to < now() - make_interval(days => $1::int)`, [INBOX_RETENTION_DAYS]);
      await this.db.query(`DELETE FROM platform.member_voucher WHERE valid_to < now() - make_interval(days => $1::int)`, [VOUCHER_RETENTION_DAYS]);
    } catch (err) {
      this.logger.warn(`Retensi inbox/voucher gagal: ${err instanceof Error ? err.message : 'db'}`);
    }
  }
}
