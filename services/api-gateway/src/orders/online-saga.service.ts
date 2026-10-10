import { Inject, Injectable, Logger } from '@nestjs/common';
import { randomUUID } from 'node:crypto';
import { PermanentMessageError } from '../common/amqp/amqp.service';
import { QUEUES } from '../common/amqp/queues';
import { ODOO_ONLINE, OdooOnlineApi, OdooRpcError } from '../odoo-rpc/odoo-online.types';
import { OnlineMetrics } from '../odoo-rpc/online-metrics';
import { PaymentOperationUnsupportedError } from '../payments/payment-gateway';
import { ONLINE_PAYMENTS, OnlineIntentTransition, OnlinePaymentsPort } from '../payments/online-payments';
import {
  CompensateMessage,
  FINAL_ORDER,
  FINAL_REFUND,
  MONEY_OUT_REFUND,
  OnlineNotifyTemplate,
  OrderPaidMessage,
  OrderRow,
  PART_RANK,
  PartCancelReason,
  PartRow,
  PartStatus,
  RefundRequestedMessage,
  RefundRow,
  RefundStatus,
  UnpaidClosedMessage,
  canTransitionPart,
  compensateMessage,
  deriveOrderStatus,
  orderPaidMessage,
  refundRequestedMessage,
} from './online.model';
import { ONLINE_REPO, OnlineRepo } from './online.repository';

export const CONSUMER = {
  ORDER_PAID: 'bff.order-paid',
  UNPAID_CLOSED: 'bff.order-unpaid-closed',
  COMPENSATE: 'bff.order-compensate',
  REFUND: 'bff.order-refund',
} as const;

/** SEC-97: status refund yang boleh diklaim untuk dikirim ke gateway; klaim in-flight basi setelah 15 menit. */
const CLAIMABLE: readonly RefundStatus[] = ['requested', 'odoo_done', 'gateway_pending'];
const IN_FLIGHT_STALE_MS = 15 * 60_000;

export interface ApplyOptions {
  source: 'odoo' | 'bff';
  odooState?: string | null;
  seq?: number | null;
  cancelReason?: PartCancelReason;
}

/**
 * Orkestrator saga penjualan online (docs/04 §10.6). Semua langkah idempoten: status bagian monoton (§10.6.2), kunci
 * Odoo deterministik (`online:paid|cancel|refund…`, §10.6.4), pesan RabbitMQ diklaim di `processed_message` HANYA
 * setelah efeknya final (pesan ulang dari rekonsiliasi tetap diproses bila masih ada pekerjaan). Panggilan Odoo/gateway
 * TIDAK dilakukan di dalam transaksi DB; perubahan status dilakukan sesudahnya di transaksi pendek dengan kunci baris.
 * Error transien (jaringan/timeout/circuit) dilempar → nack/retry; penolakan bisnis final → ack + status bagian + anomali.
 */
@Injectable()
export class OnlineSagaService {
  private readonly logger = new Logger('OnlineSaga');
  now: () => number = () => Date.now();

  constructor(
    @Inject(ONLINE_REPO) private readonly repo: OnlineRepo,
    @Inject(ODOO_ONLINE) private readonly odoo: OdooOnlineApi,
    @Inject(ONLINE_PAYMENTS) private readonly payments: OnlinePaymentsPort,
    private readonly metrics: OnlineMetrics,
  ) {}

  // ───────────── primitif bersama ─────────────
  /** Transisi bagian monoton + timestamp + efek samping (notifikasi). Dipanggil di dalam transaksi `r`. */
  async applyPart(r: OnlineRepo, part: PartRow, to: PartStatus, o: ApplyOptions): Promise<boolean> {
    const seqPatch = o.seq !== undefined && o.seq !== null && o.seq > part.last_seq ? { last_seq: o.seq } : {};
    const statePatch = o.odooState ? { odoo_state: o.odooState.slice(0, 32) } : {};
    if (!canTransitionPart(part.status, to)) {
      if (to !== part.status) this.metrics.backward(o.source);
      if (Object.keys(seqPatch).length + Object.keys(statePatch).length > 0) await r.updatePart(part.id, { ...seqPatch, ...statePatch });
      return false;
    }
    const at = new Date(this.now());
    const patch: Parameters<OnlineRepo['updatePart']>[1] = { status: to, ...seqPatch, ...statePatch };
    if (to === 'confirmed') patch.confirmed_at = at;
    if (to === 'ready') patch.ready_at = at;
    if (to === 'handed_over') {
      patch.handed_over_at = at;
      patch.pickup_code_enc = null; // §10.3.6: di-NULL-kan saat final
    }
    // SEC-99: kode ambil dihapus saat bagian final / stok kurang (akan dibatalkan)
    if (to === 'stock_short') patch.pickup_code_enc = null;
    if (to === 'cancelled' || to === 'place_failed') {
      patch.cancelled_at = at;
      patch.cancel_reason = o.cancelReason ?? 'ops';
      patch.pickup_code_enc = null;
    }
    await r.updatePart(part.id, patch);
    this.metrics.part(part.channel_code, to);
    const order = await r.getOrder(part.order_id);
    if (order) {
      if (to === 'ready') await this.notify(r, order, part, 'online_order_ready');
      if (to === 'cancelled' && PART_RANK[part.status] >= 2) await this.notify(r, order, part, 'online_order_cancelled');
    }
    Object.assign(part, patch);
    return true;
  }

  /** Setelah `paid`, status order diturunkan dari bagian (§10.6.1). */
  async refreshOrder(r: OnlineRepo, orderId: string): Promise<void> {
    const order = await r.getOrder(orderId, true);
    if (!order) return;
    const parts = await r.getParts(orderId);
    const next = deriveOrderStatus(order.status, parts.map((p) => p.status));
    if (next === order.status) return;
    const closing = FINAL_ORDER.has(next);
    const ok = await r.transitionOrder(orderId, [order.status], next, closing ? { closed_at: new Date(this.now()) } : {});
    if (ok) this.metrics.order(next);
  }

  async notify(r: OnlineRepo, order: OrderRow, part: PartRow, template: OnlineNotifyTemplate): Promise<void> {
    if (!order.member_id) return; // tautan member sudah dilepas (retensi/erasure) → tidak ada penerima
    const hash = await r.memberHashOf(order.member_id);
    if (!hash) return;
    const key = `notify:${template}:${part.id}`;
    await r.enqueue(
      QUEUES.NOTIFY_PUSH,
      { schema_version: 2, idempotency_key: key, member_hash: hash, source: 'online_order', source_ref: part.id, template, params: {} },
      key,
    );
  }

  // ───────────── hook webhook payment (§10.7) ─────────────
  /** DI DALAM transaksi webhook: order `paid` + bagian `paying` + outbox `order.paid`, atau jalur bayar terlambat. */
  async onIntentTransition(r: OnlineRepo, info: OnlineIntentTransition): Promise<void> {
    if (info.amountMismatch) {
      this.metrics.mismatch('webhook');
      this.metrics.anomaly('amount_mismatch_webhook', 'critical', { order_id: info.onlineOrderId, payment_id: info.paymentId });
      return;
    }
    if (info.to !== 'paid') return;
    const order = await r.getOrder(info.onlineOrderId, true);
    if (!order) {
      this.metrics.anomaly('paid_intent_without_order', 'critical', { payment_id: info.paymentId });
      return;
    }
    if (order.payment_intent_id !== null && order.payment_intent_id !== info.paymentId) {
      this.metrics.anomaly('paid_foreign_intent', 'critical', { order_id: order.id, payment_id: info.paymentId });
      return;
    }
    const portions = await r.portionsOf(info.paymentId);
    if (order.status === 'created' || order.status === 'awaiting_payment') {
      const sum = portions.reduce((a, p) => a + p.amount, 0);
      if (portions.length === 0 || sum !== order.amount_payable) {
        await r.transitionOrder(order.id, [order.status], 'review', { paid_at: new Date(this.now()), payment_intent_id: info.paymentId });
        this.metrics.mismatch('portion');
        this.metrics.order('review');
        this.metrics.anomaly('portion_mismatch', 'critical', { order_id: order.id, payment_id: info.paymentId, expected: order.amount_payable, actual: sum });
        return;
      }
      await r.transitionOrder(order.id, [order.status], 'paid', { paid_at: new Date(this.now()), payment_intent_id: info.paymentId });
      this.metrics.order('paid');
      for (const part of await r.getParts(order.id)) {
        if (part.status === 'placed') await this.applyPart(r, part, 'paying', { source: 'bff' });
      }
      const msg = orderPaidMessage(order.id, info.paymentId);
      await r.enqueue(QUEUES.ORDER_PAID, { ...msg }, msg.idempotency_key);
      return;
    }
    if (order.status === 'expired' || order.status === 'cancelled' || order.status === 'failed') {
      // §10.6.5: bayar terlambat → refund porsi tiap bagian; TIDAK memanggil mark_paid; status order tidak berubah
      this.metrics.anomaly('late_payment', 'warning', { order_id: order.id, payment_id: info.paymentId });
      if (portions.length === 0) this.metrics.anomaly('late_payment_without_portion', 'critical', { order_id: order.id });
      for (const p of portions) {
        const m = compensateMessage(p.part_id, 'late_payment');
        await r.enqueue(QUEUES.ORDER_COMPENSATE, { ...m }, m.idempotency_key);
      }
    }
  }

  // ───────────── consumer order.paid ─────────────
  async handleOrderPaid(msg: OrderPaidMessage): Promise<'done' | 'pending' | 'duplicate'> {
    if (await this.repo.isClaimed(CONSUMER.ORDER_PAID, msg.idempotency_key)) return 'duplicate';
    const order = await this.repo.getOrder(msg.order_id);
    if (!order) throw new PermanentMessageError('order tidak ada');
    if (order.payment_intent_id !== msg.payment_id) throw new PermanentMessageError('payment_id bukan intent order');
    const intent = await this.payments.getIntent(msg.payment_id);
    if (!intent || intent.status !== 'paid') throw new PermanentMessageError('intent belum paid');
    const portions = new Map((await this.repo.portionsOf(msg.payment_id)).map((p) => [p.part_id, p]));
    const paidAt = (intent.paidAt ?? order.paid_at ?? new Date(this.now())).toISOString();
    for (const part of (await this.repo.getParts(order.id)).filter((p) => p.status === 'paying')) {
      const portion = portions.get(part.id);
      if (!portion || portion.amount !== part.amount_payable) {
        this.metrics.mismatch('portion');
        this.metrics.anomaly('portion_mismatch', 'critical', { order_id: order.id, part_id: part.id });
        await this.repo.tx(async (r) => this.withPart(r, part.id, (p) => this.applyPart(r, p, 'review', { source: 'bff' })));
        continue;
      }
      let res;
      try {
        res = await this.odoo.markPaid(part.channel_code, part.id, {
          payment_ref: msg.payment_id,
          amount_paid: portion.amount,
          paid_at: paidAt,
          idempotency_key: `online:paid:${part.id}:${msg.payment_id}`,
        });
      } catch (err) {
        if (err instanceof OdooRpcError && (err.kind === 'business' || err.kind === 'invalid_response' || err.kind === 'not_found')) {
          this.metrics.anomaly('mark_paid_rejected', 'critical', { order_id: order.id, part_id: part.id, code: err.code ?? err.kind });
          await this.repo.tx(async (r) => this.withPart(r, part.id, (p) => this.applyPart(r, p, 'review', { source: 'bff' })));
          continue;
        }
        throw err; // transien → retry (bagian yang sudah selesai tidak diproses ulang)
      }
      await this.repo.tx(async (r) =>
        this.withPart(r, part.id, async (p) => {
          if (p.status !== 'paying') return;
          const state = res.refund_required ? 'stock_short' : res.order_state;
          if (state === 'confirmed') {
            await this.applyPart(r, p, 'confirmed', { source: 'bff', odooState: res.order_state });
          } else if (state === 'stock_short' || state === 'cancelled') {
            // stok kurang / SO sudah dibatalkan cron Odoo (refund_required) → batal + refund penuh porsi (§10.6.6)
            await this.applyPart(r, p, 'stock_short', { source: 'bff', odooState: res.order_state });
            const m = compensateMessage(p.id, 'stock_short');
            await r.enqueue(QUEUES.ORDER_COMPENSATE, { ...m }, m.idempotency_key);
          } else if (state === 'review') {
            this.metrics.anomaly('mark_paid_review', 'critical', { order_id: order.id, part_id: p.id });
            await this.applyPart(r, p, 'review', { source: 'bff', odooState: res.order_state });
          }
          // state lain (`paid`): tetap `paying`; rekonsiliasi menerbitkan ulang order.paid
        }),
      );
    }
    return this.repo.tx(async (r) => {
      await this.refreshOrder(r, order.id);
      const left = (await r.getParts(order.id)).some((p) => p.status === 'paying');
      if (left) return 'pending';
      await r.claim(CONSUMER.ORDER_PAID, msg.idempotency_key);
      return 'done';
    });
  }

  private async withPart<T>(r: OnlineRepo, partId: string, fn: (p: PartRow) => Promise<T>): Promise<T | undefined> {
    const p = await r.getPart(partId, true);
    return p ? fn(p) : undefined;
  }

  // ───────────── consumer order.unpaid_closed ─────────────
  async handleUnpaidClosed(msg: UnpaidClosedMessage): Promise<'done' | 'duplicate'> {
    if (await this.repo.isClaimed(CONSUMER.UNPAID_CLOSED, msg.idempotency_key)) return 'duplicate';
    const order = await this.repo.getOrder(msg.order_id);
    if (!order) throw new PermanentMessageError('order tidak ada');
    if (order.paid_at !== null || !['expired', 'cancelled', 'failed'].includes(order.status)) {
      // Order sudah dibayar (webhook menang) — SO tidak boleh dibatalkan di jalur ini
      return this.repo.tx(async (r) => {
        await r.claim(CONSUMER.UNPAID_CLOSED, msg.idempotency_key);
        return 'done' as const;
      });
    }
    for (const part of (await this.repo.getParts(order.id)).filter((p) => p.status === 'placing' || p.status === 'placed')) {
      try {
        await this.odoo.cancel(part.channel_code, part.id, msg.reason, `online:cancel:${part.id}`);
      } catch (err) {
        if (err instanceof OdooRpcError && err.kind === 'business') {
          this.metrics.anomaly('unpaid_cancel_rejected', 'critical', { order_id: order.id, part_id: part.id, code: err.code });
          continue;
        }
        throw err;
      }
      await this.repo.tx(async (r) =>
        this.withPart(r, part.id, (p) =>
          this.applyPart(r, p, p.status === 'placing' ? 'place_failed' : 'cancelled', { source: 'bff', cancelReason: msg.reason, odooState: 'cancelled' }),
        ),
      );
    }
    return this.repo.tx(async (r) => {
      await r.claim(CONSUMER.UNPAID_CLOSED, msg.idempotency_key);
      return 'done' as const;
    });
  }

  // ───────────── consumer order.compensate ─────────────
  async handleCompensate(msg: CompensateMessage): Promise<'done' | 'duplicate'> {
    if (await this.repo.isClaimed(CONSUMER.COMPENSATE, msg.idempotency_key)) return 'duplicate';
    const part = await this.repo.getPart(msg.part_id);
    if (!part) throw new PermanentMessageError('bagian tidak ada');
    const order = await this.repo.getOrder(part.order_id);
    if (!order?.payment_intent_id) throw new PermanentMessageError('order tanpa intent');
    const intentId = order.payment_intent_id;
    const portion = (await this.repo.portionsOf(intentId)).find((p) => p.part_id === part.id);

    let odooRefund: { refund_ref: string; amount: number } | null = null;
    if (msg.reason !== 'late_payment' && part.status !== 'cancelled') {
      try {
        const res = await this.odoo.cancel(part.channel_code, part.id, msg.reason, `online:cancel:${part.id}`);
        odooRefund = res.refund;
      } catch (err) {
        if (err instanceof OdooRpcError && err.kind === 'business') {
          this.metrics.anomaly('compensate_cancel_rejected', 'critical', { part_id: part.id, code: err.code });
          return this.claimed(CONSUMER.COMPENSATE, msg.idempotency_key);
        }
        throw err;
      }
    }

    return this.repo.tx(async (r) => {
      const p = await r.getPart(part.id, true);
      if (!p) return 'done' as const;
      if (msg.reason !== 'late_payment') {
        await this.applyPart(r, p, 'cancelled', { source: 'bff', cancelReason: msg.reason, odooState: 'cancelled' });
      }
      const paid = order.paid_at !== null || msg.reason === 'late_payment';
      let refund: RefundRow | null = null;
      if (odooRefund) {
        refund = await r.insertRefund({
          id: randomUUID(),
          part_id: p.id,
          payment_intent_id: intentId,
          kind: 'full',
          reason: msg.reason === 'review' ? 'review' : 'stock_short',
          lines: null,
          amount: odooRefund.amount,
          odoo_refund_ref: odooRefund.refund_ref,
          status: 'odoo_done',
          requested_by: 'system',
        });
      } else if (paid && portion) {
        const amount = portion.amount - p.refunded_amount;
        if (amount > 0) {
          refund = await r.insertRefund({
            id: randomUUID(),
            part_id: p.id,
            payment_intent_id: intentId,
            kind: msg.reason === 'late_payment' ? 'late_payment' : 'full',
            reason: msg.reason,
            lines: null,
            amount,
            odoo_refund_ref: null,
            status: 'requested',
            requested_by: 'system',
          });
        }
      } else if (paid) {
        this.metrics.anomaly('compensate_without_portion', 'critical', { part_id: p.id });
      }
      if (refund && !FINAL_REFUND.has(refund.status)) {
        this.metrics.refund(refund.status);
        const m = refundRequestedMessage(refund.id);
        await r.enqueue(QUEUES.ORDER_REFUND_REQUESTED, { ...m }, m.idempotency_key);
      }
      await this.refreshOrder(r, p.order_id);
      await r.claim(CONSUMER.COMPENSATE, msg.idempotency_key);
      return 'done' as const;
    });
  }

  private async claimed(consumer: string, key: string): Promise<'done'> {
    await this.repo.tx(async (r) => r.claim(consumer, key));
    return 'done';
  }

  // ───────────── consumer order.refund.requested ─────────────
  /**
   * Refund ke gateway lalu `efn_online_refund_settled` (bila ada dokumen Odoo). Σ refund bagian ≤ porsi (dicek di sini;
   * DB juga memaksa `refunded_amount <= amount_payable`). Gateway tidak mendukung → `manual_required` (+ alert),
   * gagal → `failed` (+ alert), pending → `gateway_pending` (rekonsiliasi menerbitkan ulang).
   */
  async handleRefundRequested(msg: RefundRequestedMessage): Promise<'done' | 'pending' | 'duplicate'> {
    if (await this.repo.isClaimed(CONSUMER.REFUND, msg.idempotency_key)) return 'duplicate';
    let refund = await this.repo.getRefund(msg.refund_id);
    if (!refund) throw new PermanentMessageError('refund tidak ada');
    const part = await this.repo.getPart(refund.part_id);
    if (!part) throw new PermanentMessageError('bagian tidak ada');
    if (refund.status === 'approval_required') return 'pending'; // SEC-95: menunggu persetujuan CS (CLI)

    if (CLAIMABLE.includes(refund.status) || refund.status === 'gateway_in_flight') {
      // SEC-97: klaim eksklusif atomik + cek porsi (termasuk refund in-flight) di bawah kunci bagian, SEBELUM gateway
      const stale = new Date(this.now() - IN_FLIGHT_STALE_MS);
      const token = randomUUID(); // SEC-106: CAS penulisan pasca-gateway
      let before: RefundStatus = 'odoo_done';
      const claim = await this.repo.tx(async (r) => {
        const p = await r.getPart(part.id, true);
        if (!p) return null;
        const cur = await r.getRefund(msg.refund_id, true);
        if (!cur) return null;
        // status asal untuk pelepasan klaim; klaim basi direbut → turunkan dari data yang ada (bukan selalu odoo_done)
        before = cur.status !== 'gateway_in_flight' ? cur.status : cur.provider_refund_ref ? 'gateway_pending' : cur.odoo_refund_ref ? 'odoo_done' : 'requested';
        const mine = await r.claimRefund(msg.refund_id, CLAIMABLE, stale, token);
        if (!mine) return null;
        const amount = mine.amount ?? 0;
        const out = (await r.refundsOfPart(p.id)).filter((x) => x.id !== mine.id && MONEY_OUT_REFUND.includes(x.status));
        const committed = out.reduce((s, x) => s + (x.amount ?? 0), 0);
        if (amount <= 0 || committed + amount > p.amount_payable) {
          await r.updateRefundIfClaimed(mine.id, token, { status: 'failed', failure_reason: 'refund_exceeds_portion' });
          this.metrics.mismatch('portion');
          this.metrics.anomaly('refund_exceeds_portion', 'critical', {
            refund_id: mine.id,
            part_id: p.id,
            expected: p.amount_payable,
            actual: committed + amount,
          });
          return 'rejected' as const;
        }
        return mine;
      });
      if (claim === null) return 'pending'; // diklaim consumer/replika lain
      if (claim === 'rejected') {
        this.metrics.refund('failed');
        return this.claimed(CONSUMER.REFUND, msg.idempotency_key);
      }
      let res;
      try {
        res = await this.payments.refund(claim.payment_intent_id, { refundId: claim.id, amount: claim.amount ?? 0, reason: claim.reason });
      } catch (err) {
        if (err instanceof PaymentOperationUnsupportedError) {
          if (!(await this.casWrite(msg.refund_id, token, { status: 'manual_required' }))) return 'pending';
          this.metrics.refund('manual_required');
          this.metrics.anomaly('refund_manual_required', 'warning', { refund_id: msg.refund_id, part_id: part.id });
          return this.claimed(CONSUMER.REFUND, msg.idempotency_key);
        }
        // transien: lepas klaim agar bisa dicoba ulang (idempotensi provider per refundId)
        await this.casWrite(msg.refund_id, token, { status: before });
        throw err;
      }
      if (res.status === 'pending') {
        const ref = res.providerRefundRef;
        if (!(await this.casWrite(msg.refund_id, token, { status: 'gateway_pending', provider_refund_ref: ref }))) return 'pending';
        this.metrics.refund('gateway_pending');
        return 'pending';
      }
      if (res.status === 'failed') {
        if (!(await this.casWrite(msg.refund_id, token, { status: 'failed', failure_reason: 'gateway_failed' }))) return 'pending';
        this.metrics.refund('failed');
        this.metrics.anomaly('refund_gateway_failed', 'critical', { refund_id: msg.refund_id, part_id: part.id });
        return this.claimed(CONSUMER.REFUND, msg.idempotency_key);
      }
      if (!(await this.markGatewayDone(msg.refund_id, res.providerRefundRef ?? `gw:${msg.refund_id}`, token))) return 'pending';
      refund = await this.repo.getRefund(msg.refund_id);
      if (!refund) throw new PermanentMessageError('refund hilang');
    }

    if (refund.status === 'gateway_done') {
      if (refund.odoo_refund_ref) {
        await this.odoo.refundSettled(part.channel_code, refund.odoo_refund_ref, {
          provider_refund_ref: refund.provider_refund_ref ?? `gw:${refund.id}`,
          settled_at: new Date(this.now()).toISOString(),
          ok: true,
          idempotency_key: `online:refund_settled:${refund.id}`,
        });
      }
      await this.repo.tx(async (r) => r.updateRefund(msg.refund_id, { status: 'settled', settled_at: new Date(this.now()) }));
      this.metrics.refund('settled');
      return this.claimed(CONSUMER.REFUND, msg.idempotency_key);
    }
    if (refund.status === 'failed' || refund.status === 'manual_required' || refund.status === 'settled') {
      return this.claimed(CONSUMER.REFUND, msg.idempotency_key);
    }
    return 'pending';
  }

  /**
   * `gateway_done` + `refunded_amount` bagian (satu transaksi, sekali per refund). Dipakai consumer & CLI settle manual.
   * SEC-97: melebihi porsi → TIDAK dipotong diam-diam; refund `failed` (`refund_exceeds_portion`) + anomali critical.
   */
  /** SEC-106: penulisan pasca-gateway bersyarat token; klaim hilang (direbut) → anomali, tidak menimpa. */
  private async casWrite(refundId: string, token: string, patch: Parameters<OnlineRepo['updateRefund']>[1]): Promise<boolean> {
    const ok = await this.repo.tx(async (r) => r.updateRefundIfClaimed(refundId, token, patch));
    if (!ok) this.metrics.anomaly('refund_claim_lost', 'warning', { refund_id: refundId });
    return ok;
  }

  async markGatewayDone(refundId: string, providerRef: string, token?: string): Promise<boolean> {
    return this.repo.tx(async (r) => {
      const rf = await r.getRefund(refundId, true);
      if (!rf || rf.status === 'gateway_done' || rf.status === 'settled') return false;
      if (token !== undefined && (rf.claim_token !== token || rf.status !== 'gateway_in_flight')) {
        this.metrics.anomaly('refund_claim_lost', 'warning', { refund_id: refundId });
        return false;
      }
      const p = await r.getPart(rf.part_id, true);
      if (!p) return false;
      const amount = rf.amount ?? 0;
      if (p.refunded_amount + amount > p.amount_payable) {
        await r.updateRefund(refundId, { status: 'failed', failure_reason: 'refund_exceeds_portion', provider_refund_ref: providerRef.slice(0, 128), claim_token: null });
        this.metrics.refund('failed');
        this.metrics.anomaly('refund_exceeds_portion', 'critical', {
          refund_id: refundId,
          part_id: p.id,
          expected: p.amount_payable,
          actual: p.refunded_amount + amount,
        });
        return false;
      }
      await r.updateRefund(refundId, { status: 'gateway_done', provider_refund_ref: providerRef.slice(0, 128), claim_token: null });
      await r.updatePart(p.id, { refunded_amount: p.refunded_amount + amount });
      this.metrics.refund('gateway_done');
      const order = await r.getOrder(p.order_id);
      if (order) await this.notify(r, order, p, 'online_order_refunded');
      return true;
    });
  }
}
