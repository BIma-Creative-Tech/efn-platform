import { Inject, Injectable, Logger, OnApplicationBootstrap, Optional } from '@nestjs/common';
import { AppConfig } from '../config/app-config';
import { CatalogService } from '../catalog/catalog.service';
import { AmqpService, MessageMeta, PermanentMessageError } from '../common/amqp/amqp.service';
import { DB, Db } from '../common/db/db';
import { claimMessage } from '../common/db/processed-message';
import { PiiCipher } from '../common/pii/crypto';
import type { PushSender } from '../promo/promo.service';
import {
  EMAIL_NOTIFIER,
  EmailNotifierPort,
  G50_PUSH_SENDER,
  G50_TEMPLATES,
  WA_UNOFFICIAL_NOTIFIER,
  WaUnofficialNotifierPort,
  formatIdr,
  sendPushToMembers,
} from '../campaign/channel-ports';
import { ALERT_PUBLISHERS, NOTIFY_PRICE_ALERT_QUEUE, NOTIFY_RESTOCK_QUEUE, parsePriceAlertMessage, parseRestockMessage } from './alert-message';
import { AlertChannel } from './dto/wishlist.dto';

export const ALERT_DISPATCHER_CONSUMER = 'bff.alert-dispatcher';
const MAX_ALERTS_PER_EVENT = 1000;
const WA_TTL_MS = 24 * 3600 * 1000;

export type DispatchOutcome =
  | { status: 'disabled' }
  | { status: 'duplicate' }
  | { status: 'dispatched'; alerts: number; push: number; email: number; wa: number };

interface AlertRow {
  id: string;
  member_id: string;
  channels: AlertChannel[];
}

/**
 * Alert-dispatcher (docs/04 §28.7–§28.8): consumer `notify.restock` & `notify.price_alert` → kirim ke member yang
 * meminta lewat kanal pilihannya: push (Expo, langsung), email (`EmailNotifier` BFF-B), WA unofficial
 * (`WaUnofficialNotifier` BFF-B; kategori `notify_restock` / `price_alert`, kedaluwarsa ≤ 24 jam). Consent + flag kanal
 * email/WA diperiksa notifier; push = pelaksanaan permintaan member (tanpa consent `marketing`).
 *
 * Idempotensi: klaim `processed_message` + penandaan alert (`sent_at` / `triggered_at`) dalam SATU transaksi, kirim
 * SETELAH commit → at-most-once per alert (alert "terkirim sekali", §28.8). Gagal kirim satu kanal dicatat tanpa PII,
 * kanal lain tetap jalan. Publisher wajib `efn-bff`.
 *
 * Catatan: kontrak §28.7 menyebut `notify.push` dipakai ulang untuk push alert; consumer `notify.push` saat ini adalah
 * WhatsApp Cloud API (bukan push perangkat) — push dikirim langsung lewat Expo (lihat README, usulan kontrak).
 */
@Injectable()
export class AlertDispatcherConsumer implements OnApplicationBootstrap {
  private readonly logger = new Logger(AlertDispatcherConsumer.name);

  constructor(
    private readonly amqp: AmqpService,
    @Inject(DB) private readonly db: Db,
    private readonly config: AppConfig,
    private readonly catalog: CatalogService,
    private readonly cipher: PiiCipher,
    @Optional() @Inject(EMAIL_NOTIFIER) private readonly email: EmailNotifierPort | null = null,
    @Optional() @Inject(WA_UNOFFICIAL_NOTIFIER) private readonly wa: WaUnofficialNotifierPort | null = null,
    @Optional() @Inject(G50_PUSH_SENDER) private readonly push: PushSender | null = null,
  ) {}

  async onApplicationBootstrap(): Promise<void> {
    if (!this.config.amqpConsumersEnabled) return;
    if (this.config.restockAlertEnabled) await this.amqp.consume(NOTIFY_RESTOCK_QUEUE, async (p, m) => void (await this.handleRestock(p, m)));
    if (this.config.priceAlertEnabled) await this.amqp.consume(NOTIFY_PRICE_ALERT_QUEUE, async (p, m) => void (await this.handlePrice(p, m)));
  }

  private assertPublisher(queue: string, meta: Pick<MessageMeta, 'userId'>): void {
    if (meta.userId === undefined || !ALERT_PUBLISHERS.includes(meta.userId)) {
      this.logger.warn(`${queue} ditolak: publisher ${meta.userId ?? '(tanpa user_id)'} tidak berhak`);
      throw new PermanentMessageError('publisher tidak diizinkan');
    }
  }

  async handleRestock(payload: unknown, meta: Pick<MessageMeta, 'userId'>): Promise<DispatchOutcome> {
    const msg = parseRestockMessage(payload);
    this.assertPublisher(NOTIFY_RESTOCK_QUEUE, meta);
    if (!this.config.restockAlertEnabled) return { status: 'disabled' };
    const alerts = await this.db.tx(async (q): Promise<AlertRow[] | null> => {
      if (!(await claimMessage(q, ALERT_DISPATCHER_CONSUMER, msg.idempotency_key))) return null;
      const res = await q.query<AlertRow>(
        `UPDATE platform.restock_alert a SET sent_at = now()
          WHERE a.id IN (SELECT r.id FROM platform.restock_alert r JOIN platform.member m ON m.id = r.member_id AND m.deleted_at IS NULL
                          WHERE r.channel_code = $1 AND r.outlet_code = $2 AND r.product_id = $3
                            AND r.sent_at IS NULL AND r.expires_at > now()
                          ORDER BY r.created_at LIMIT $4 FOR UPDATE OF r SKIP LOCKED)
          RETURNING a.id::text AS id, a.member_id::text AS member_id, a.channels`,
        [msg.channel_code, msg.outlet_code, msg.product_id, MAX_ALERTS_PER_EVENT],
      );
      return res.rows;
    });
    if (alerts === null) return { status: 'duplicate' };
    const name = await this.productName(msg.channel_code, msg.product_id);
    const outlet = await this.outletName(msg.channel_code, msg.outlet_code);
    return this.deliver(alerts, {
      kind: 'restock',
      push: { title: 'Stok tersedia lagi', body: `${name} sudah tersedia lagi di ${outlet}.`, data: { type: 'restock', channel: msg.channel_code, outlet: msg.outlet_code, product_id: msg.product_id } },
      template: G50_TEMPLATES.restock,
      params: { product_name: name, outlet_name: outlet },
    });
  }

  async handlePrice(payload: unknown, meta: Pick<MessageMeta, 'userId'>): Promise<DispatchOutcome> {
    const msg = parsePriceAlertMessage(payload);
    this.assertPublisher(NOTIFY_PRICE_ALERT_QUEUE, meta);
    if (!this.config.priceAlertEnabled) return { status: 'disabled' };
    const alerts = await this.db.tx(async (q): Promise<AlertRow[] | null> => {
      if (!(await claimMessage(q, ALERT_DISPATCHER_CONSUMER, msg.idempotency_key))) return null;
      const res = await q.query<AlertRow>(
        `UPDATE platform.price_alert a SET triggered_at = now()
          WHERE a.id IN (SELECT r.id FROM platform.price_alert r JOIN platform.member m ON m.id = r.member_id AND m.deleted_at IS NULL
                          WHERE r.channel_code = $1 AND r.product_id = $2 AND r.triggered_at IS NULL AND r.expires_at > now()
                            AND r.target_price_idr >= $3
                          ORDER BY r.created_at LIMIT $4 FOR UPDATE OF r SKIP LOCKED)
          RETURNING a.id::text AS id, a.member_id::text AS member_id, a.channels`,
        [msg.channel_code, msg.product_id, msg.new_price_idr, MAX_ALERTS_PER_EVENT],
      );
      return res.rows;
    });
    if (alerts === null) return { status: 'duplicate' };
    const name = await this.productName(msg.channel_code, msg.product_id);
    return this.deliver(alerts, {
      kind: 'price_alert',
      push: { title: 'Harga turun', body: `${name} kini Rp${formatIdr(msg.new_price_idr)}.`, data: { type: 'price_alert', channel: msg.channel_code, product_id: msg.product_id } },
      template: G50_TEMPLATES.priceAlert,
      params: { product_name: name, new_price_idr: formatIdr(msg.new_price_idr) },
    });
  }

  /** Nama publik outlet dari salinan kanal (fallback kode outlet). */
  private async outletName(channel: string, outletCode: string): Promise<string> {
    try {
      const o = (await this.catalog.channel(channel))?.outlets.find((x) => x.outlet_code === outletCode);
      return (o?.public_name ?? outletCode).replace(/[\u0000-\u001f\u007f]/g, ' ').slice(0, 80);
    } catch {
      return outletCode;
    }
  }

  private async productName(channel: string, productId: number): Promise<string> {
    try {
      const p = (await this.catalog.productsByIds(channel, [productId])).get(productId);
      return (p?.name ?? 'Produk').replace(/[\u0000-\u001f\u007f]/g, ' ').slice(0, 60);
    } catch {
      return 'Produk';
    }
  }

  private async deliver(
    alerts: AlertRow[],
    spec: { kind: 'restock' | 'price_alert'; push: { title: string; body: string; data: Record<string, unknown> }; template: string; params: Record<string, string | number> },
  ): Promise<DispatchOutcome> {
    const out = { status: 'dispatched' as const, alerts: alerts.length, push: 0, email: 0, wa: 0 };
    const pushMembers = [...new Set(alerts.filter((a) => a.channels.includes('push')).map((a) => a.member_id))];
    if (pushMembers.length > 0) {
      const r = await sendPushToMembers(this.db, this.cipher, this.config.redisKeyPepper, this.push, pushMembers, spec.push).catch(() => ({ tokens: 0, sent: 0 }));
      out.push = r.sent;
    }
    for (const a of alerts) {
      if (a.channels.includes('email') && this.email) {
        try {
          const r = await this.email.enqueue({ memberId: a.member_id, source: spec.kind, sourceRef: a.id, template: spec.template, params: spec.params });
          if (r === 'queued') out.email += 1;
        } catch (err) {
          this.logger.warn(`Email ${spec.kind} alert ${a.id} gagal diantrekan: ${err instanceof Error ? err.message : 'error'}`);
        }
      }
      if (a.channels.includes('wa_unofficial') && this.wa) {
        try {
          const r = await this.wa.enqueue({
            memberId: a.member_id,
            category: spec.kind === 'restock' ? 'notify_restock' : 'price_alert',
            source: spec.kind,
            sourceRef: a.id,
            template: spec.template,
            params: spec.params,
            expiresAt: new Date(Date.now() + WA_TTL_MS),
          });
          if (r === 'queued') out.wa += 1;
        } catch (err) {
          this.logger.warn(`WA ${spec.kind} alert ${a.id} gagal diantrekan: ${err instanceof Error ? err.message : 'error'}`);
        }
      }
    }
    this.logger.log(`${spec.kind}: ${out.alerts} alert, push ${out.push}, email ${out.email}, wa ${out.wa}`);
    return out;
  }
}
