import { PermanentMessageError } from '../common/amqp/amqp.service';
import { CampaignPayload } from './odoo-campaign';

/** docs/04 §28.7: queue `promo.campaign.requested` (DLQ `.dlq`). Publisher = campaign-sync (outbox), consumer = dispatcher. */
export const PROMO_CAMPAIGN_REQUESTED_QUEUE = 'promo.campaign.requested';
export const CAMPAIGN_PUBLISHERS: readonly string[] = ['efn-bff'];

/** `campaign:<company_code>:<odoo_campaign_id>:<approval_rev>` (= `message_id` = `platform.campaign.campaign_key`). */
export function campaignKey(company: string, campaignId: number, approvalRev: number): string {
  return `campaign:${company}:${campaignId}:${approvalRev}`;
}
const KEY_RE = /^campaign:[A-Z][A-Z0-9]{1,9}:[0-9]{1,10}:[0-9]{1,6}$/;

/**
 * Payload antrean = payload kampanye tervalidasi TANPA kode voucher (kode disimpan terenkripsi di
 * `platform.member_voucher` dalam transaksi yang sama dengan outbox) + `schema_version` & `idempotency_key`.
 */
export interface CampaignRequestedMessage extends CampaignPayload {
  schema_version: 1;
  idempotency_key: string;
}

export function parseCampaignMessage(payload: unknown): CampaignRequestedMessage {
  if (typeof payload !== 'object' || payload === null || Array.isArray(payload)) throw new PermanentMessageError('payload bukan object');
  const o = payload as Record<string, unknown>;
  if (o.schema_version !== 1) throw new PermanentMessageError('schema_version tidak didukung');
  if (typeof o.idempotency_key !== 'string' || !KEY_RE.test(o.idempotency_key)) throw new PermanentMessageError('idempotency_key tidak valid');
  // Detail kampanye dibaca ulang dari `platform.campaign` (sumber kebenaran BFF); pesan hanya memicu.
  return o as unknown as CampaignRequestedMessage;
}
