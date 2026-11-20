import { Inject, Injectable, Logger, OnApplicationBootstrap, Optional } from '@nestjs/common';
import { randomUUID } from 'node:crypto';
import { AppConfig } from '../config/app-config';
import { AmqpService, MessageMeta, PermanentMessageError } from '../common/amqp/amqp.service';
import { DB, Db, Queryable } from '../common/db/db';
import { PiiCipher } from '../common/pii/crypto';
import type { PushSender } from '../promo/promo.service';
import { CAMPAIGN_PUBLISHERS, PROMO_CAMPAIGN_REQUESTED_QUEUE, parseCampaignMessage } from './campaign-message';
import {
  EMAIL_NOTIFIER,
  EmailNotifierPort,
  G50_PUSH_SENDER,
  G50_TEMPLATES,
  WA_UNOFFICIAL_NOTIFIER,
  WaUnofficialNotifierPort,
  formatDateWib,
  sendPushToMembers,
} from './channel-ports';
import { CampaignPayload } from './odoo-campaign';

const PAGE = 500;
const WA_MAX_TTL_MS = 24 * 3600 * 1000;
const ZERO_UUID = '00000000-0000-0000-0000-000000000000';

export interface DispatchStats {
  delivered: number;
  skipped: number;
  push_sent: number;
  email_queued: number;
  wa_queued: number;
  stop_reason: string | null;
}
export type CampaignOutcome =
  | { status: 'duplicate' | 'disabled' | 'expired' }
  | { status: 'done'; stats: DispatchStats };

/** Klausa consent terbaru (pola PromoService): baris consent terbaru purpose tsb `granted = true`. */
const CONSENT = (purpose: 'marketing' | 'tracking'): string =>
  `(SELECT c.granted FROM platform.consent c WHERE c.member_id = m.id AND c.purpose = '${purpose}'
     ORDER BY c.created_at DESC, c.id DESC LIMIT 1) IS TRUE`;

/**
 * Campaign-dispatcher (docs/04 §28.7, ADR-047 §3.4, L-155..L-157, L-164): consumer `promo.campaign.requested` →
 * fan-out per anggota.
 *
 * Target: `segment` (dan `markdown` dengan segmen) = `behavior.segment_member` (subject_key) → `behavior.subject_member`
 * → member, syarat consent `tracking` (segmen dibangun dari perilaku) DAN `marketing`; `markdown` tanpa segmen = member
 * yang mem-wishlist produk itu + consent `marketing`. Member terhapus dilewati.
 *
 * Per member (transaksi per halaman 500): cap frekuensi (HANYA bila `CAMPAIGN_FREQUENCY_CAP_ENABLED`) → baris
 * `campaign_delivery` (PK kampanye × member = idempoten lintas redelivery) → voucher: `anonymous_code` tugaskan satu
 * kode pool (`member_voucher.member_id`), cap anggaran HANYA bila `CAMPAIGN_BUDGET_CAP_ENABLED` (Σ voucher × nilai ≤
 * `budget_idr`), pool habis → berhenti; `public_code` = kode bersama; `member_bound` = hanya notifikasi → inbox.
 * Setelah commit: push Expo (batch), email & WA unofficial lewat notifier BFF-B (cek flag + consent sendiri). Kode
 * voucher TIDAK dikirim lewat email/WA (hanya di inbox in-app). Lewat `valid_to` → `expired`, tidak dikirim.
 * Semantik: at-most-once per member per kanal (lebih baik terlewat daripada spam). Publisher wajib `efn-bff`.
 */
@Injectable()
export class CampaignDispatcherConsumer implements OnApplicationBootstrap {
  private readonly logger = new Logger(CampaignDispatcherConsumer.name);
  now: () => number = () => Date.now();

  constructor(
    private readonly amqp: AmqpService,
    @Inject(DB) private readonly db: Db,
    private readonly config: AppConfig,
    private readonly cipher: PiiCipher,
    @Optional() @Inject(EMAIL_NOTIFIER) private readonly email: EmailNotifierPort | null = null,
    @Optional() @Inject(WA_UNOFFICIAL_NOTIFIER) private readonly wa: WaUnofficialNotifierPort | null = null,
    @Optional() @Inject(G50_PUSH_SENDER) private readonly push: PushSender | null = null,
  ) {}

  async onApplicationBootstrap(): Promise<void> {
    if (!this.config.amqpConsumersEnabled || !this.config.promoCampaignEnabled) return;
    await this.amqp.consume(PROMO_CAMPAIGN_REQUESTED_QUEUE, async (payload, meta) => {
      await this.handle(payload, meta);
    });
  }

  async handle(payload: unknown, meta: Pick<MessageMeta, 'userId'>): Promise<CampaignOutcome> {
    const msg = parseCampaignMessage(payload);
    if (meta.userId === undefined || !CAMPAIGN_PUBLISHERS.includes(meta.userId)) {
      this.logger.warn(`promo.campaign.requested ditolak: publisher ${meta.userId ?? '(tanpa user_id)'} tidak berhak`);
      throw new PermanentMessageError('publisher tidak diizinkan');
    }
    const key = msg.idempotency_key;
    const start = await this.db.tx(async (q) => {
      const r = await q.query<{ status: string; payload: CampaignPayload }>(
        `SELECT status, payload FROM platform.campaign WHERE campaign_key = $1 FOR UPDATE`,
        [key],
      );
      const row = r.rows[0];
      if (!row) throw new PermanentMessageError('kampanye tidak ditemukan'); // outbox menjamin baris ada
      if (row.status !== 'queued' && row.status !== 'dispatching') return { outcome: 'duplicate' as const };
      if (!this.config.promoCampaignEnabled) {
        await q.query(`UPDATE platform.campaign SET status = 'disabled', updated_at = now() WHERE campaign_key = $1`, [key]);
        return { outcome: 'disabled' as const };
      }
      if (Date.parse(row.payload.valid_to) <= this.now()) {
        await q.query(`UPDATE platform.campaign SET status = 'expired', updated_at = now() WHERE campaign_key = $1`, [key]);
        return { outcome: 'expired' as const };
      }
      await q.query(`UPDATE platform.campaign SET status = 'dispatching', updated_at = now() WHERE campaign_key = $1`, [key]);
      return { outcome: 'go' as const, payload: row.payload };
    });
    if (start.outcome !== 'go') return { status: start.outcome };
    const stats = await this.fanOut(key, start.payload);
    await this.db.query(`UPDATE platform.campaign SET status = 'done', stats = $2::jsonb, updated_at = now() WHERE campaign_key = $1`, [
      key,
      JSON.stringify(stats),
    ]);
    this.logger.log(
      `Kampanye ${key}: ${stats.delivered} terkirim, ${stats.skipped} dilewati, push ${stats.push_sent}, email ${stats.email_queued}, wa ${stats.wa_queued}` +
        (stats.stop_reason ? ` (berhenti: ${stats.stop_reason})` : ''),
    );
    return { status: 'done', stats };
  }

  /** Halaman target (keyset `member_id`). */
  async targets(q: Queryable, p: CampaignPayload, after: string): Promise<string[]> {
    if (p.segment_run_id && p.segment_code) {
      const r = await q.query<{ member_id: string }>(
        `SELECT m.id::text AS member_id FROM behavior.segment_member s
           JOIN behavior.subject_member sm ON sm.subject_key = s.subject_key
           JOIN platform.member m ON m.id = sm.member_id AND m.deleted_at IS NULL
          WHERE s.run_id = $1::uuid AND s.segment_code = $2 AND m.id > $3::uuid
            AND ${CONSENT('marketing')} AND ${CONSENT('tracking')}
          ORDER BY m.id LIMIT $4`,
        [p.segment_run_id, p.segment_code, after, PAGE],
      );
      return r.rows.map((x) => x.member_id);
    }
    if (p.kind === 'markdown' && p.markdown) {
      const r = await q.query<{ member_id: string }>(
        `SELECT DISTINCT m.id AS member_id FROM platform.member_wishlist w
           JOIN platform.member m ON m.id = w.member_id AND m.deleted_at IS NULL
          WHERE w.product_id = ANY($1::int[]) AND ($2::text IS NULL OR w.channel_code = $2) AND m.id > $3::uuid
            AND ${CONSENT('marketing')}
          ORDER BY m.id LIMIT $4`,
        [p.markdown.product_ids, p.markdown.channel_code, after, PAGE],
      );
      return r.rows.map((x) => String(x.member_id));
    }
    return [];
  }

  private async fanOut(key: string, p: CampaignPayload): Promise<DispatchStats> {
    const stats: DispatchStats = { delivered: 0, skipped: 0, push_sent: 0, email_queued: 0, wa_queued: 0, stop_reason: null };
    const freqCap = this.config.campaignFrequencyCapEnabled
      ? Math.min(this.config.campaignMaxPerMemberPerWeek, p.cap_per_member_week ?? Number.MAX_SAFE_INTEGER)
      : null;
    const budgetOn = this.config.campaignBudgetCapEnabled && p.voucher_mode === 'anonymous_code' && p.budget_idr !== null && p.voucher_value_idr !== null;
    if (this.config.campaignBudgetCapEnabled && p.voucher_mode === 'anonymous_code' && p.budget_idr !== null && p.voucher_value_idr === null) {
      this.logger.warn(`Kampanye ${key}: cap anggaran aktif tetapi voucher_value_idr kosong → cap anggaran tidak dapat ditegakkan`);
    }
    let assigned = 0;
    if (budgetOn) {
      const c = await this.db.query<{ n: string | number }>(
        `SELECT count(*) AS n FROM platform.member_voucher WHERE campaign_key = $1 AND assigned_at IS NOT NULL`,
        [key],
      );
      assigned = Number(c.rows[0]?.n ?? 0);
    }
    let publicVoucherId: string | null = null;
    if (p.voucher_mode === 'public_code') {
      const r = await this.db.query<{ id: string }>(`SELECT id::text AS id FROM platform.member_voucher WHERE campaign_key = $1 AND mode = 'public_code' LIMIT 1`, [key]);
      publicVoucherId = r.rows[0]?.id ?? null;
    }
    const validTo = new Date(p.valid_to);
    let after = ZERO_UUID;
    for (;;) {
      const page = await this.targets(this.db, p, after);
      if (page.length === 0) break;
      after = page[page.length - 1]!;
      const deliver: string[] = [];
      await this.db.tx(async (q) => {
        for (const memberId of page) {
          if (stats.stop_reason) break;
          if (freqCap !== null) {
            const f = await q.query<{ n: string | number }>(
              `SELECT count(*) AS n FROM platform.campaign_delivery
                WHERE member_id = $1 AND status = 'delivered' AND campaign_key <> $2 AND created_at > now() - interval '7 days'`,
              [memberId, key],
            );
            if (Number(f.rows[0]?.n ?? 0) >= freqCap) {
              await this.skip(q, key, memberId, 'frequency_cap', stats);
              continue;
            }
          }
          const ins = await q.query(
            `INSERT INTO platform.campaign_delivery (campaign_key, member_id, status) VALUES ($1, $2, 'delivered')
             ON CONFLICT (campaign_key, member_id) DO NOTHING RETURNING member_id`,
            [key, memberId],
          );
          if ((ins.rowCount ?? 0) === 0) continue; // sudah diproses (redelivery)
          let voucherId: string | null = publicVoucherId;
          if (p.voucher_mode === 'anonymous_code') {
            if (budgetOn && (assigned + 1) * (p.voucher_value_idr as number) > (p.budget_idr as number)) {
              await q.query(`UPDATE platform.campaign_delivery SET status = 'skipped', reason = 'budget_cap' WHERE campaign_key = $1 AND member_id = $2`, [key, memberId]);
              stats.skipped += 1;
              stats.stop_reason = 'budget_cap';
              break;
            }
            const v = await q.query<{ id: string }>(
              `UPDATE platform.member_voucher SET member_id = $2, assigned_at = now()
                WHERE id = (SELECT id FROM platform.member_voucher WHERE campaign_key = $1 AND mode = 'anonymous_code' AND assigned_at IS NULL
                             ORDER BY created_at, id LIMIT 1 FOR UPDATE SKIP LOCKED)
                RETURNING id::text AS id`,
              [key, memberId],
            );
            voucherId = v.rows[0]?.id ?? null;
            if (!voucherId) {
              await q.query(`UPDATE platform.campaign_delivery SET status = 'skipped', reason = 'voucher_exhausted' WHERE campaign_key = $1 AND member_id = $2`, [key, memberId]);
              stats.skipped += 1;
              stats.stop_reason = 'voucher_exhausted';
              break;
            }
            assigned += 1;
          }
          if (p.channels.includes('inbox')) {
            await q.query(
              `INSERT INTO platform.member_inbox (id, member_id, campaign_key, title, body, voucher_id, valid_to)
               VALUES ($1, $2, $3, $4, $5, $6, $7) ON CONFLICT (member_id, campaign_key) DO NOTHING`,
              [randomUUID(), memberId, key, p.message.title, p.message.body, voucherId, p.valid_to],
            );
          }
          await q.query(`UPDATE platform.campaign_delivery SET voucher_id = $3 WHERE campaign_key = $1 AND member_id = $2`, [key, memberId, voucherId]);
          deliver.push(memberId);
          stats.delivered += 1;
        }
      });
      await this.sendChannels(key, p, validTo, deliver, stats);
      if (stats.stop_reason) break;
    }
    return stats;
  }

  private async skip(q: Queryable, key: string, memberId: string, reason: string, stats: DispatchStats): Promise<void> {
    const r = await q.query(
      `INSERT INTO platform.campaign_delivery (campaign_key, member_id, status, reason) VALUES ($1, $2, 'skipped', $3)
       ON CONFLICT (campaign_key, member_id) DO NOTHING`,
      [key, memberId, reason],
    );
    if ((r.rowCount ?? 0) > 0) stats.skipped += 1;
  }

  private async sendChannels(key: string, p: CampaignPayload, validTo: Date, members: string[], stats: DispatchStats): Promise<void> {
    if (members.length === 0) return;
    if (p.channels.includes('push')) {
      const r = await sendPushToMembers(this.db, this.cipher, this.config.redisKeyPepper, this.push, members, {
        title: p.message.title,
        body: p.message.body,
        data: { type: 'campaign', company_code: p.company_code, campaign_id: p.campaign_id },
      }).catch(() => ({ tokens: 0, sent: 0 }));
      stats.push_sent += r.sent;
    }
    const params = { title: p.message.title, body: p.message.body, valid_to: formatDateWib(p.valid_to) };
    const waExpires = new Date(Math.min(validTo.getTime(), this.now() + WA_MAX_TTL_MS));
    for (const memberId of members) {
      if (p.channels.includes('email') && this.email) {
        try {
          if ((await this.email.enqueue({ memberId, source: 'campaign', sourceRef: key, template: G50_TEMPLATES.campaign, params })) === 'queued') stats.email_queued += 1;
        } catch (err) {
          this.logger.warn(`Email kampanye ${key} gagal diantrekan: ${err instanceof Error ? err.message : 'error'}`);
        }
      }
      if (p.channels.includes('wa_unofficial') && this.wa) {
        try {
          const r = await this.wa.enqueue({ memberId, category: 'promo', source: 'campaign', sourceRef: key, template: G50_TEMPLATES.campaign, params, expiresAt: waExpires });
          if (r === 'queued') stats.wa_queued += 1;
        } catch (err) {
          this.logger.warn(`WA kampanye ${key} gagal diantrekan: ${err instanceof Error ? err.message : 'error'}`);
        }
      }
    }
  }
}
