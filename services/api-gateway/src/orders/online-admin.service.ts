import { Inject, Injectable, Logger } from '@nestjs/common';
import { randomUUID } from 'node:crypto';
import { QUEUES } from '../common/amqp/queues';
import { ODOO_ONLINE, OdooOnlineApi, QuoteLineIn } from '../odoo-rpc/odoo-online.types';
import { OnlineMetrics } from '../odoo-rpc/online-metrics';
import { partStatusFromOdoo, refundRequestedMessage } from './online.model';
import { ONLINE_REPO, OnlineRepo } from './online.repository';
import { OnlineSagaService } from './online-saga.service';

export class AdminError extends Error {}

export const ACTOR_RE = /^[A-Za-z0-9._@-]{2,64}$/;

export interface CsRefundInput {
  partId: string;
  refundId?: string;
  lines: QuoteLineIn[] | null;
  full: boolean;
  restock: boolean;
  reason: 'customer_cs' | 'ops_cancelled';
  actor: string;
  note: string;
}

/**
 * Operasi CS/ops lewat CLI (L-3, docs/04 §10.6.6): refund parsial/penuh, settle refund manual, resolve `review`.
 * Setiap aksi dicatat di `platform.online_admin_audit` (aktor, alasan, order, bagian, nominal) + baris log
 * `EFN_ONLINE_CS_AUDIT {json}` — tanpa PII pelanggan. Refund selalu terikat SATU bagian dan ≤ porsi − refund sebelumnya.
 */
@Injectable()
export class OnlineAdminService {
  private readonly logger = new Logger('OnlineAdmin');
  now: () => number = () => Date.now();

  constructor(
    @Inject(ONLINE_REPO) private readonly repo: OnlineRepo,
    @Inject(ODOO_ONLINE) private readonly odoo: OdooOnlineApi,
    private readonly saga: OnlineSagaService,
    private readonly metrics: OnlineMetrics,
  ) {}

  private checkActor(actor: string, reason: string): void {
    if (!ACTOR_RE.test(actor)) throw new AdminError('--actor tidak valid (2–64 karakter [A-Za-z0-9._@-])');
    if (reason.trim().length < 3 || reason.length > 200) throw new AdminError('--reason wajib 3–200 karakter');
  }

  private logAudit(entry: Record<string, string | number | null>): void {
    this.logger.log(`EFN_ONLINE_CS_AUDIT ${JSON.stringify(entry)}`);
  }

  async refund(input: CsRefundInput): Promise<{ refund_id: string; amount: number; status: string }> {
    this.checkActor(input.actor, input.note);
    if (input.full === (input.lines !== null && input.lines.length > 0)) throw new AdminError('pilih salah satu: --full atau --lines');
    const part = await this.repo.getPart(input.partId);
    if (!part) throw new AdminError('bagian tidak ditemukan');
    if (!['confirmed', 'ready', 'handed_over'].includes(part.status)) throw new AdminError(`status bagian ${part.status} tidak dapat direfund lewat CLI`);
    const order = await this.repo.getOrder(part.order_id);
    if (!order?.payment_intent_id) throw new AdminError('order tanpa pembayaran');
    const refundId = input.refundId ?? randomUUID();
    const res = await this.odoo.refund(part.channel_code, part.id, {
      idempotency_key: `online:refund:${refundId}`,
      ...(input.full ? { full: true } : { lines: input.lines ?? [] }),
      restock: input.restock,
      reason: input.reason,
    });
    const intentId = order.payment_intent_id;
    const out = await this.repo.tx(async (r) => {
      const p = await r.getPart(part.id, true);
      if (!p) throw new AdminError('bagian hilang');
      const open = (await r.refundsOfPart(p.id)).filter((x) => x.status !== 'failed' && x.odoo_refund_ref !== res.refund_ref);
      const committed = open.reduce((s, x) => s + (x.amount ?? 0), 0);
      const over = committed + res.amount > p.amount_payable;
      const rf = await r.insertRefund({
        id: refundId,
        part_id: p.id,
        payment_intent_id: intentId,
        kind: input.full ? 'full' : 'lines',
        reason: input.reason,
        lines: input.full ? null : input.lines,
        amount: res.amount,
        odoo_refund_ref: res.refund_ref,
        status: over ? 'failed' : 'odoo_done',
        requested_by: 'cs',
      });
      if (over) this.metrics.anomaly('refund_exceeds_portion', 'critical', { part_id: p.id, refund_id: rf.id, expected: p.amount_payable, actual: committed + res.amount });
      else {
        const m = refundRequestedMessage(rf.id);
        await r.enqueue(QUEUES.ORDER_REFUND_REQUESTED, { ...m }, m.idempotency_key);
      }
      await r.audit({
        action: 'refund',
        actor: input.actor,
        reason: input.note,
        order_id: p.order_id,
        part_id: p.id,
        refund_id: rf.id,
        amount: res.amount,
        outcome: over ? 'refund_exceeds_portion' : 'odoo_done',
      });
      return { refund_id: rf.id, amount: res.amount, status: rf.status };
    });
    this.metrics.refund(out.status);
    this.logAudit({ action: 'refund', actor: input.actor, part_id: part.id, refund_id: out.refund_id, amount: out.amount, outcome: out.status });
    return out;
  }

  /** Dana dikembalikan manual oleh Finance (gateway tak mendukung refund) → `gateway_done` → settle Odoo → `settled`. */
  async settleManual(refundId: string, providerRef: string, actor: string, note: string): Promise<{ status: string }> {
    this.checkActor(actor, note);
    if (!/^[A-Za-z0-9._:/-]{3,128}$/.test(providerRef)) throw new AdminError('--provider-ref tidak valid');
    const rf = await this.repo.getRefund(refundId);
    if (!rf) throw new AdminError('refund tidak ditemukan');
    if (!['manual_required', 'failed', 'gateway_pending', 'gateway_done'].includes(rf.status)) throw new AdminError(`status refund ${rf.status} tidak dapat di-settle manual`);
    const part = await this.repo.getPart(rf.part_id);
    if (!part) throw new AdminError('bagian hilang');
    if ((rf.amount ?? 0) + part.refunded_amount > part.amount_payable && rf.status !== 'gateway_done') {
      throw new AdminError('refund melebihi porsi bagian');
    }
    await this.saga.markGatewayDone(rf.id, providerRef);
    const msg = refundRequestedMessage(rf.id);
    // Langsung lanjutkan (refund_settled Odoo + settled); idempoten dengan consumer
    const outcome = await this.saga.handleRefundRequested({ ...msg, idempotency_key: `${msg.idempotency_key}:manual` });
    const fresh = await this.repo.getRefund(rf.id);
    await this.repo.tx(async (r) =>
      r.audit({ action: 'refund_settle', actor, reason: note, order_id: part.order_id, part_id: part.id, refund_id: rf.id, amount: rf.amount, outcome: fresh?.status ?? outcome }),
    );
    this.logAudit({ action: 'refund_settle', actor, refund_id: rf.id, outcome: fresh?.status ?? outcome });
    return { status: fresh?.status ?? 'unknown' };
  }

  /** Bagian `review`: `cancel` → kompensasi review (batal Odoo + refund); `sync` → tarik state Odoo dan terapkan. */
  async resolve(partId: string, action: 'cancel' | 'sync', actor: string, note: string): Promise<{ status: string }> {
    this.checkActor(actor, note);
    const part = await this.repo.getPart(partId);
    if (!part) throw new AdminError('bagian tidak ditemukan');
    if (action === 'cancel') {
      if (part.status !== 'review') throw new AdminError('hanya bagian berstatus review yang dapat dibatalkan lewat resolve');
      await this.saga.handleCompensate({ schema_version: 1, idempotency_key: `order.compensate:${part.id}:review`, part_id: part.id, reason: 'review' });
    } else {
      const rows = await this.odoo.status(part.channel_code, [part.id]);
      const row = rows.find((x) => x.platform_order_ref === part.id);
      const target = row ? partStatusFromOdoo(row.state) : null;
      if (target) {
        await this.repo.tx(async (r) => {
          const p = await r.getPart(part.id, true);
          if (p && (await this.saga.applyPart(r, p, target, { source: 'bff', odooState: row?.state, seq: row?.seq ?? null }))) {
            await this.saga.refreshOrder(r, p.order_id);
          }
        });
      }
    }
    const fresh = await this.repo.getPart(partId);
    await this.repo.tx(async (r) =>
      r.audit({
        action: action === 'cancel' ? 'resolve_cancel' : 'resolve_sync',
        actor,
        reason: note,
        order_id: part.order_id,
        part_id: part.id,
        refund_id: null,
        amount: null,
        outcome: fresh?.status ?? 'unknown',
      }),
    );
    this.logAudit({ action: `resolve_${action}`, actor, part_id: part.id, outcome: fresh?.status ?? 'unknown' });
    return { status: fresh?.status ?? 'unknown' };
  }
}
