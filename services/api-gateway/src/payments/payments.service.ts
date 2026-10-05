import { Inject, Injectable, Logger } from '@nestjs/common';
import { randomUUID } from 'node:crypto';
import { AppConfig } from '../config/app-config';
import { DB, Db, Queryable } from '../common/db/db';
import { claimMessage } from '../common/db/processed-message';
import { Errors } from '../common/errors/api-error';
import { KV_STORE, KvStore } from '../common/redis/kv-store';
import { CreatePaymentDto } from './dto/payment.dto';
import { MockGateway } from './gateways/mock.gateway';
import {
  GatewayName,
  IntentRef,
  PaymentGateway,
  PaymentOperationUnsupportedError,
  PaymentStatus,
  WebhookEvent,
  WebhookRequest,
} from './payment-gateway';

export const PAYMENT_GATEWAY = Symbol('PAYMENT_GATEWAY');

/** Holder lazy: adapter dibuat saat dipakai pertama (kegagalan config vendor → 503, bukan gagal start BFF). */
export interface PaymentGatewayHolder {
  get(): PaymentGateway | null;
}

export interface CreatePaymentResponse {
  payment_id: string;
  provider: GatewayName;
  status: PaymentStatus;
  qr_string: string | null;
  expires_at: string | null;
}

export interface PaymentStatusResponse {
  payment_id: string;
  status: PaymentStatus;
  paid_at: string | null;
  provider_ref: string | null;
  amount: number;
}

interface IntentRow {
  id: string;
  provider: GatewayName;
  provider_ref: string | null;
  order_ref: string;
  company_code: string;
  amount: string | number;
  status: PaymentStatus;
  qr_string: string | null;
  expires_at: Date | string | null;
  paid_at: Date | string | null;
}

const INTENT_COLUMNS = `id::text AS id, provider, provider_ref, order_ref, company_code, amount, status, qr_string, expires_at, paid_at`;

function iso(v: Date | string | null): string | null {
  return v === null ? null : (v instanceof Date ? v : new Date(v)).toISOString();
}

/**
 * Transisi status yang diizinkan (monoton): pending → status final apa pun; expired/cancelled/failed → paid
 * (pembayaran terlambat: uang sudah diterima, dicatat apa adanya + log peringatan untuk refund manual — refund QRIS
 * dicadangkan, docs/04 §8.8); `paid` final.
 */
export function canTransition(from: PaymentStatus, to: PaymentStatus): boolean {
  if (from === to || from === 'paid') return false;
  if (from === 'pending') return to !== 'pending';
  return to === 'paid';
}

/**
 * Payment intent QRIS (docs/04 §8.3). Dipanggil server Odoo (`efn_pos_payment_gateway`, service key).
 * - `create` idempotent per `idempotency_key` (Odoo: `pos:<order_uuid>:<payment_uuid>`): INSERT ... ON CONFLICT DO
 *   NOTHING dulu (baris `pending` tanpa QR), baru memanggil provider. Retry dengan key sama → intent yang sama;
 *   payload beda (order/amount/PT) → `409 idempotency_conflict`; panggilan paralel saat QR belum jadi →
 *   `409 payment_in_progress` (Odoo mengulang). Provider gagal → intent `failed` + `502 payment_provider_error`.
 * - Status final HANYA lewat webhook terverifikasi atau `getStatus` ke provider (dibatasi 1× per
 *   `PAYMENT_STATUS_REFRESH_SECONDS` per intent, key Redis `rl:paychk:<id>`), tidak pernah dari klien.
 * - Webhook: verifikasi adapter (signature/token + replay window bila ada) → idempotensi `processed_message`
 *   (`webhook.payments.<provider>`, event id) → transisi monoton dalam satu transaksi (`FOR UPDATE`).
 *   Nominal webhook ≠ intent → status TIDAK diubah, log error (review manual).
 */
@Injectable()
export class PaymentsService {
  private readonly logger = new Logger(PaymentsService.name);
  now: () => number = () => Date.now();

  constructor(
    private readonly config: AppConfig,
    @Inject(DB) private readonly db: Db,
    @Inject(KV_STORE) private readonly kv: KvStore,
    @Inject(PAYMENT_GATEWAY) private readonly holder: PaymentGatewayHolder,
  ) {}

  private gateway(): PaymentGateway {
    let gw: PaymentGateway | null;
    try {
      gw = this.holder.get();
    } catch (err) {
      this.logger.error(`Adapter payment tidak dapat dibuat: ${err instanceof Error ? err.message : String(err)}`);
      throw Errors.paymentUnavailable();
    }
    if (!gw) throw Errors.paymentUnavailable();
    return gw;
  }

  async create(dto: CreatePaymentDto): Promise<CreatePaymentResponse> {
    const gw = this.gateway();
    const id = randomUUID();
    const expiresAt = new Date(this.now() + this.config.paymentQrTtlSeconds * 1000);
    const ins = await this.db.query<{ id: string }>(
      `INSERT INTO platform.payment_intent
         (id, provider, order_ref, company_code, amount, currency, method, status, expires_at, idempotency_key)
       VALUES ($1, $2, $3, $4, $5, $6, $7, 'pending', $8, $9)
       ON CONFLICT (idempotency_key) DO NOTHING
       RETURNING id::text AS id`,
      [id, gw.name, dto.orderRef, dto.companyCode, dto.amount, dto.currency, dto.method, expiresAt.toISOString(), dto.idempotencyKey],
    );
    if (!ins.rows[0]) return this.replayCreate(dto, gw.name);

    try {
      const r = await gw.createQris({
        paymentId: id,
        orderRef: dto.orderRef,
        amount: dto.amount,
        currency: dto.currency,
        companyCode: dto.companyCode,
        expiresAt,
      });
      await this.db.query(
        `UPDATE platform.payment_intent SET provider_ref = $2, qr_string = $3, expires_at = $4, updated_at = now() WHERE id = $1`,
        [id, r.providerRef, r.qrString, r.expiresAt.toISOString()],
      );
      this.logger.log(`Payment intent ${id} dibuat (${gw.name}, ${dto.companyCode})`);
      return { payment_id: id, provider: gw.name, status: 'pending', qr_string: r.qrString, expires_at: r.expiresAt.toISOString() };
    } catch (err) {
      await this.db
        .query(`UPDATE platform.payment_intent SET status = 'failed', updated_at = now() WHERE id = $1 AND status = 'pending'`, [id])
        .catch(() => undefined);
      this.logger.error(`Provider ${gw.name} gagal membuat QR untuk ${id}: ${err instanceof Error ? err.message : String(err)}`);
      throw Errors.paymentProviderError();
    }
  }

  private async replayCreate(dto: CreatePaymentDto, provider: GatewayName): Promise<CreatePaymentResponse> {
    const res = await this.db.query<IntentRow & { idempotency_key: string }>(
      `SELECT ${INTENT_COLUMNS} FROM platform.payment_intent WHERE idempotency_key = $1`,
      [dto.idempotencyKey],
    );
    const row = res.rows[0];
    if (!row) throw Errors.serviceUnavailable();
    if (row.order_ref !== dto.orderRef || Number(row.amount) !== dto.amount || row.company_code !== dto.companyCode || row.provider !== provider) {
      throw Errors.conflict('idempotency_conflict', 'idempotency_key sudah dipakai untuk pembayaran lain.');
    }
    if (row.status === 'pending' && row.qr_string === null) {
      throw Errors.conflict('payment_in_progress', 'Pembayaran sedang dibuat. Coba lagi sebentar.');
    }
    return { payment_id: row.id, provider: row.provider, status: row.status, qr_string: row.qr_string, expires_at: iso(row.expires_at) };
  }

  async get(paymentId: string): Promise<PaymentStatusResponse> {
    let row = await this.load(paymentId);
    if (row.status === 'pending') {
      const gw = this.holder.get();
      if (gw && gw.name === row.provider && (await this.kv.setNxEx(`rl:paychk:${row.id}`, '1', this.config.paymentStatusRefreshSeconds))) {
        try {
          const st = await gw.getStatus(this.ref(row));
          if (st.status !== 'pending') {
            await this.db.tx(async (q) => {
              await this.transition(q, row.id, { status: st.status, paidAt: st.paidAt, amount: st.amount }, 'get_status');
            });
            row = await this.load(paymentId);
          }
        } catch (err) {
          this.logger.warn(`getStatus ${row.id} gagal: ${err instanceof Error ? err.message : String(err)}`);
        }
      }
    }
    return this.statusResponse(row);
  }

  async cancel(paymentId: string): Promise<{ status: PaymentStatus }> {
    const row = await this.load(paymentId);
    if (row.status !== 'pending') return { status: row.status };
    const gw = this.gateway();
    let target: PaymentStatus = 'cancelled';
    try {
      target = (await gw.cancel(this.ref(row))).status;
    } catch (err) {
      if (!(err instanceof PaymentOperationUnsupportedError)) {
        this.logger.error(`Cancel ${row.id} di provider gagal: ${err instanceof Error ? err.message : String(err)}`);
        throw Errors.paymentProviderError();
      }
      this.logger.warn(`Provider ${gw.name} tidak mendukung cancel; ${row.id} ditandai cancelled lokal`);
    }
    if (target === 'pending') target = 'cancelled';
    await this.db.tx(async (q) => {
      await this.transition(q, row.id, { status: target, paidAt: null, amount: null }, 'cancel');
    });
    return { status: (await this.load(paymentId)).status };
  }

  /** `POST /v1/webhooks/payments/:provider`. Provider tak dikenal / bukan provider aktif → 404. */
  async handleWebhook(provider: string, req: WebhookRequest): Promise<{ ok: true }> {
    const gw = this.holder.get();
    if (!gw || gw.name !== provider) throw Errors.notFound();
    const v = gw.verifyWebhook(req, Math.floor(this.now() / 1000));
    if (!v.ok) {
      this.logger.warn(`Webhook ${provider} ditolak: ${v.reason}`);
      throw Errors.invalidSignature();
    }
    let ev: WebhookEvent;
    try {
      ev = gw.parseWebhook(req.rawBody as Buffer);
    } catch {
      throw Errors.invalidRequest();
    }
    await this.db.tx(async (q) => {
      if (!(await claimMessage(q, `webhook.payments.${provider}`, ev.eventId))) {
        this.logger.log(`Webhook ${provider} duplikat diabaikan`);
        return;
      }
      const res = await q.query<{ id: string }>(
        `SELECT id::text AS id FROM platform.payment_intent WHERE provider = $1 AND provider_ref = $2`,
        [provider, ev.providerRef],
      );
      const id = res.rows[0]?.id;
      if (!id) {
        this.logger.warn(`Webhook ${provider} untuk provider_ref tidak dikenal diabaikan`);
        return;
      }
      await this.transition(q, id, ev, 'webhook');
    });
    return { ok: true };
  }

  /**
   * Dev/staging saja (ditolak deployment production & provider ≠ mock → 404): simulasikan webhook mock bertanda tangan.
   * G6 QA: memakai `isProductionDeployment` (APP_ENV) agar UAT staging (image production, `APP_ENV=staging`, mock)
   * bisa menjalankan skenario sukses/expired/late-paid; production tetap 404 (mock juga ditolak saat start).
   */
  async simulate(paymentId: string, status: Exclude<PaymentStatus, 'pending'>): Promise<PaymentStatusResponse> {
    const gw = this.holder.get();
    if (this.config.isProductionDeployment || !gw || gw.name !== 'mock') throw Errors.notFound();
    const row = await this.load(paymentId);
    if (!row.provider_ref) throw Errors.conflict('payment_in_progress');
    const nowSec = Math.floor(this.now() / 1000);
    const req = MockGateway.buildWebhook(
      this.config.webhookPaymentSecret,
      {
        event_id: `sim_${randomUUID()}`,
        provider_ref: row.provider_ref,
        status,
        amount: Number(row.amount),
        paid_at: status === 'paid' ? new Date(this.now()).toISOString() : null,
      },
      nowSec,
    );
    await this.handleWebhook('mock', req);
    return this.statusResponse(await this.load(paymentId));
  }

  private async transition(
    q: Queryable,
    id: string,
    ev: Pick<WebhookEvent, 'status' | 'paidAt' | 'amount'>,
    via: 'webhook' | 'get_status' | 'cancel',
  ): Promise<void> {
    const res = await q.query<IntentRow>(`SELECT ${INTENT_COLUMNS} FROM platform.payment_intent WHERE id = $1 FOR UPDATE`, [id]);
    const row = res.rows[0];
    if (!row || !canTransition(row.status, ev.status)) return;
    if (ev.status === 'paid' && ev.amount !== null && Math.abs(ev.amount - Number(row.amount)) > 0.005) {
      this.logger.error(`Nominal ${via} untuk ${id} tidak sama dengan intent — status tidak diubah (review manual)`);
      return;
    }
    if (ev.status === 'paid' && row.status !== 'pending') {
      this.logger.warn(`Pembayaran terlambat untuk ${id} (status ${row.status} → paid via ${via}); periksa refund manual`);
    }
    const paidAt = ev.status === 'paid' ? (ev.paidAt ?? new Date(this.now())).toISOString() : null;
    await q.query(
      `UPDATE platform.payment_intent SET status = $2, paid_at = COALESCE($3::timestamptz, paid_at), updated_at = now()
        WHERE id = $1 AND status = $4`,
      [id, ev.status, paidAt, row.status],
    );
    this.logger.log(`Payment ${id}: ${row.status} → ${ev.status} (${via})`);
  }

  private async load(paymentId: string): Promise<IntentRow> {
    const res = await this.db.query<IntentRow>(`SELECT ${INTENT_COLUMNS} FROM platform.payment_intent WHERE id = $1`, [paymentId]);
    const row = res.rows[0];
    if (!row) throw Errors.notFound();
    return row;
  }

  private ref(row: IntentRow): IntentRef {
    return {
      paymentId: row.id,
      providerRef: row.provider_ref,
      amount: Number(row.amount),
      expiresAt: row.expires_at === null ? null : new Date(row.expires_at),
    };
  }

  private statusResponse(row: IntentRow): PaymentStatusResponse {
    return {
      payment_id: row.id,
      status: row.status,
      paid_at: iso(row.paid_at),
      provider_ref: row.provider_ref,
      amount: Number(row.amount),
    };
  }
}
