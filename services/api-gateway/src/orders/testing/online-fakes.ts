/**
 * Fake in-memory untuk test saga online (bukan runtime). `MemoryOnlineRepo` meniru semantik `PgOnlineRepo`:
 * transaksi = snapshot + restore saat error (rollback), indeks unik idempotensi, transisi bersyarat.
 */
import { randomUUID } from 'node:crypto';
import { StuckKind } from '../../odoo-rpc/online-metrics';
import { PaymentOperationUnsupportedError, PaymentStatus, RefundOutcome, RefundRequest } from '../../payments/payment-gateway';
import { OnlineIntent, OnlineIntentInput, OnlinePaymentsPort } from '../../payments/online-payments';
import { OrderPatch, OrderRow, OrderStatus, PartPatch, PartRow, PartStatus, PortionRow, RefundPatch, RefundRow, RefundStatus } from '../online.model';
import {
  AdminAuditRecord,
  NewOrder,
  NewPart,
  NewRefund,
  OnlineEventRecord,
  OnlineRepo,
  OrderExportRow,
  RetentionConfig,
  RetentionResult,
} from '../online.repository';
import { OnlineSagaService } from '../online-saga.service';

export interface MemState {
  orders: Map<string, OrderRow>;
  parts: Map<string, PartRow>;
  refunds: Map<string, RefundRow>;
  portions: PortionRow[];
  events: Map<string, OnlineEventRecord>;
  claims: Set<string>;
  outbox: { queue: string; payload: Record<string, unknown>; messageId: string }[];
  audits: AdminAuditRecord[];
  members: Map<string, string>;
  activeDeletes: Set<string>;
  intents: Map<string, OnlineIntent & { onlineOrderId: string }>;
}

function clone<T>(v: T): T {
  return structuredClone(v);
}

export class MemoryOnlineRepo implements OnlineRepo {
  state: MemState = {
    orders: new Map(),
    parts: new Map(),
    refunds: new Map(),
    portions: [],
    events: new Map(),
    claims: new Set(),
    outbox: [],
    audits: [],
    members: new Map(),
    activeDeletes: new Set(),
    intents: new Map(),
  };
  private depth = 0;
  now: () => number = () => Date.now();

  async tx<T>(fn: (r: OnlineRepo) => Promise<T>): Promise<T> {
    if (this.depth > 0) return fn(this);
    const snap = clone(this.state);
    this.depth += 1;
    try {
      return await fn(this);
    } catch (err) {
      this.state = snap;
      throw err;
    } finally {
      this.depth -= 1;
    }
  }

  private d(): Date {
    return new Date(this.now());
  }

  async lockMember(): Promise<void> {}
  async tryJobLock(): Promise<boolean> {
    return true;
  }
  async hasActiveDelete(memberId: string): Promise<boolean> {
    return this.state.activeDeletes.has(memberId);
  }
  async memberHashOf(memberId: string): Promise<string | null> {
    return this.state.members.get(memberId) ?? null;
  }

  async insertOrder(o: NewOrder): Promise<boolean> {
    if ([...this.state.orders.values()].some((x) => x.member_id === o.member_id && x.idempotency_key === o.idempotency_key)) return false;
    this.state.orders.set(o.id, { ...o, payment_intent_id: null, paid_at: null, closed_at: null, close_reason: null, created_at: this.d(), updated_at: this.d() });
    return true;
  }
  async findOrderByKey(memberId: string, key: string): Promise<OrderRow | null> {
    return clone([...this.state.orders.values()].find((x) => x.member_id === memberId && x.idempotency_key === key) ?? null);
  }
  async getOrder(id: string): Promise<OrderRow | null> {
    return clone(this.state.orders.get(id) ?? null);
  }
  async getOrderOfMember(id: string, memberId: string): Promise<OrderRow | null> {
    const o = this.state.orders.get(id);
    return o && o.member_id === memberId ? clone(o) : null;
  }
  async listOrdersOfMember(memberId: string, before: { createdAt: Date; id: string } | null, limit: number): Promise<OrderRow[]> {
    return [...this.state.orders.values()]
      .filter((o) => o.member_id === memberId)
      .sort((a, b) => b.created_at.getTime() - a.created_at.getTime() || (b.id < a.id ? -1 : 1))
      .filter((o) => !before || o.created_at.getTime() < before.createdAt.getTime() || (o.created_at.getTime() === before.createdAt.getTime() && o.id < before.id))
      .slice(0, limit)
      .map(clone);
  }
  async updateOrder(id: string, patch: OrderPatch): Promise<void> {
    const o = this.state.orders.get(id);
    if (o) Object.assign(o, patch, { updated_at: this.d() });
  }
  async transitionOrder(id: string, from: readonly OrderStatus[], to: OrderStatus, patch: OrderPatch = {}): Promise<boolean> {
    const o = this.state.orders.get(id);
    if (!o || !from.includes(o.status)) return false;
    Object.assign(o, patch, { status: to, updated_at: this.d() });
    return true;
  }
  async countOpenOrders(memberId: string): Promise<number> {
    return [...this.state.orders.values()].filter((o) => o.member_id === memberId && (o.status === 'created' || o.status === 'awaiting_payment')).length;
  }

  async insertPart(p: NewPart): Promise<void> {
    this.state.parts.set(p.id, {
      ...p,
      sale_order_name: null,
      odoo_state: null,
      last_seq: 0,
      refunded_amount: 0,
      confirmed_at: null,
      ready_at: null,
      handed_over_at: null,
      cancelled_at: null,
      cancel_reason: null,
      created_at: this.d(),
      updated_at: this.d(),
    });
  }
  async getPart(id: string): Promise<PartRow | null> {
    return clone(this.state.parts.get(id) ?? null);
  }
  async getParts(orderId: string): Promise<PartRow[]> {
    return [...this.state.parts.values()].filter((p) => p.order_id === orderId).map(clone);
  }
  async getPartsOfOrders(ids: readonly string[]): Promise<PartRow[]> {
    return [...this.state.parts.values()].filter((p) => ids.includes(p.order_id)).map(clone);
  }
  async updatePart(id: string, patch: PartPatch): Promise<void> {
    const p = this.state.parts.get(id);
    if (!p) return;
    if (patch.refunded_amount !== undefined && patch.refunded_amount > p.amount_payable) throw new Error('CHECK refunded_amount <= amount_payable');
    Object.assign(p, patch, { updated_at: this.d() });
  }

  async insertRefund(r: NewRefund): Promise<RefundRow> {
    const existing = [...this.state.refunds.values()].find(
      (x) =>
        x.id === r.id ||
        (r.odoo_refund_ref !== null && x.part_id === r.part_id && x.odoo_refund_ref === r.odoo_refund_ref) ||
        (r.odoo_refund_ref === null && r.requested_by === 'system' && x.requested_by === 'system' && x.odoo_refund_ref === null && x.part_id === r.part_id && x.reason === r.reason),
    );
    if (existing) return clone(existing);
    const row: RefundRow = { ...r, provider_refund_ref: null, created_at: this.d(), updated_at: this.d(), settled_at: null };
    this.state.refunds.set(r.id, row);
    return clone(row);
  }
  async getRefund(id: string): Promise<RefundRow | null> {
    return clone(this.state.refunds.get(id) ?? null);
  }
  async updateRefund(id: string, patch: RefundPatch): Promise<void> {
    const r = this.state.refunds.get(id);
    if (r) Object.assign(r, patch, { updated_at: this.d() });
  }
  async refundsOfPart(partId: string): Promise<RefundRow[]> {
    return [...this.state.refunds.values()].filter((r) => r.part_id === partId).map(clone);
  }
  async portionsOf(intentId: string): Promise<PortionRow[]> {
    return this.state.portions.filter((p) => p.payment_intent_id === intentId).map(clone);
  }

  async insertEvent(e: OnlineEventRecord): Promise<boolean> {
    if (this.state.events.has(e.idempotency_key)) return false;
    this.state.events.set(e.idempotency_key, e);
    return true;
  }
  async claim(consumer: string, key: string): Promise<boolean> {
    const k = `${consumer}|${key}`;
    if (this.state.claims.has(k)) return false;
    this.state.claims.add(k);
    return true;
  }
  async isClaimed(consumer: string, key: string): Promise<boolean> {
    return this.state.claims.has(`${consumer}|${key}`);
  }
  async enqueue(queue: string, payload: Record<string, unknown>, messageId: string): Promise<void> {
    this.state.outbox.push({ queue, payload: clone(payload), messageId });
  }
  async audit(a: AdminAuditRecord): Promise<void> {
    this.state.audits.push(a);
  }

  async expireCandidates(expiredBefore: Date, createdBefore: Date, limit: number): Promise<OrderRow[]> {
    return [...this.state.orders.values()]
      .filter((o) => (o.status === 'awaiting_payment' && o.expires_at < expiredBefore) || (o.status === 'created' && o.created_at < createdBefore))
      .slice(0, limit)
      .map(clone);
  }
  async partsInStatusBefore(statuses: readonly PartStatus[], before: Date, limit: number): Promise<PartRow[]> {
    return [...this.state.parts.values()].filter((p) => statuses.includes(p.status) && p.updated_at < before).slice(0, limit).map(clone);
  }
  async refundsInStatusBefore(statuses: readonly RefundStatus[], before: Date, limit: number): Promise<RefundRow[]> {
    return [...this.state.refunds.values()].filter((r) => statuses.includes(r.status) && r.updated_at < before).slice(0, limit).map(clone);
  }
  async closedOrdersWithOpenParts(before: Date, limit: number): Promise<OrderRow[]> {
    return [...this.state.orders.values()]
      .filter(
        (o) =>
          ['expired', 'cancelled', 'failed'].includes(o.status) &&
          o.paid_at === null &&
          o.updated_at < before &&
          [...this.state.parts.values()].some((p) => p.order_id === o.id && (p.status === 'placing' || p.status === 'placed')),
      )
      .slice(0, limit)
      .map(clone);
  }
  async stuckCounts(now: Date): Promise<Record<StuckKind, number>> {
    const parts = [...this.state.parts.values()];
    const ago = (ms: number): number => now.getTime() - ms;
    return {
      placing: parts.filter((p) => p.status === 'placing' && p.updated_at.getTime() < ago(120_000)).length,
      paying: parts.filter((p) => p.status === 'paying' && p.updated_at.getTime() < ago(600_000)).length,
      awaiting_payment: [...this.state.orders.values()].filter((o) => o.status === 'awaiting_payment' && o.expires_at.getTime() < ago(300_000)).length,
      refund: [...this.state.refunds.values()].filter((r) => !['settled', 'failed', 'manual_required'].includes(r.status) && r.created_at.getTime() < ago(86_400_000)).length,
      refund_attention: [...this.state.refunds.values()].filter((r) => r.status === 'failed' || r.status === 'manual_required').length,
    };
  }
  async portionMismatches(): Promise<{ payment_id: string; expected: number; actual: number }[]> {
    return [...this.state.intents.values()]
      .map((i) => ({ payment_id: i.paymentId, expected: i.amount, actual: this.state.portions.filter((p) => p.payment_intent_id === i.paymentId).reduce((s, p) => s + p.amount, 0) }))
      .filter((x) => x.expected !== x.actual);
  }
  async refundOverruns(): Promise<{ part_id: string; refunded: number; portion: number }[]> {
    return [];
  }
  async retention(now: Date, cfg: RetentionConfig): Promise<RetentionResult> {
    let pickupNulled = 0;
    for (const p of this.state.parts.values()) {
      if (!p.pickup_code_enc) continue;
      const old = p.ready_at !== null && p.ready_at.getTime() < now.getTime() - cfg.pickupCodeMaxDays * 86_400_000;
      if (['handed_over', 'cancelled', 'place_failed'].includes(p.status) || old) {
        p.pickup_code_enc = null;
        pickupNulled += 1;
      }
    }
    let unlinked = 0;
    for (const o of this.state.orders.values()) {
      if (o.member_id && o.closed_at && o.closed_at.getTime() < now.getTime() - cfg.memberLinkDays * 86_400_000) {
        o.member_id = null;
        unlinked += 1;
      }
    }
    return { pickupNulled, qrNulled: 0, unlinked, eventsPurged: 0 };
  }
  async memberOpenPaidParts(memberId: string): Promise<number> {
    return [...this.state.parts.values()].filter(
      (p) => this.state.orders.get(p.order_id)?.member_id === memberId && ['paying', 'confirmed', 'ready', 'review'].includes(p.status),
    ).length;
  }
  async memberUnpaidOrders(memberId: string): Promise<OrderRow[]> {
    return [...this.state.orders.values()].filter((o) => o.member_id === memberId && (o.status === 'created' || o.status === 'awaiting_payment')).map(clone);
  }
  async exportOrders(memberId: string, limit: number): Promise<OrderExportRow[]> {
    const orders = await this.listOrdersOfMember(memberId, null, limit);
    return Promise.all(orders.map(async (o) => ({ order: o, parts: await this.getParts(o.id) })));
  }
}

/**
 * Fake port payment: intent di `repo.state.intents`, porsi di `repo.state.portions`. `pay()` meniru webhook
 * terverifikasi (transisi + hook saga di dalam transaksi repo).
 */
export class FakeOnlinePayments implements OnlinePaymentsPort {
  refundMode: 'succeeded' | 'pending' | 'failed' | 'unsupported' = 'succeeded';
  failCreate = false;
  readonly refunds: { paymentId: string; req: RefundRequest }[] = [];
  saga: OnlineSagaService | null = null;

  constructor(private readonly repo: MemoryOnlineRepo) {}

  async createOnlineIntent(input: OnlineIntentInput): Promise<OnlineIntent> {
    if (this.failCreate) throw new Error('gateway down');
    const existing = [...this.repo.state.intents.values()].find((i) => i.onlineOrderId === input.orderId);
    if (existing) return { ...existing };
    const id = randomUUID();
    const intent = { paymentId: id, provider: 'mock' as const, status: 'pending' as PaymentStatus, qrString: `EFN-MOCK-QRIS|${id}`, expiresAt: input.expiresAt, paidAt: null, amount: input.amount, onlineOrderId: input.orderId };
    this.repo.state.intents.set(id, intent);
    for (const p of input.portions) {
      this.repo.state.portions.push({ payment_intent_id: id, part_id: p.partId, channel_code: p.channelCode, company_code: p.companyCode, amount: p.amount });
    }
    return { ...intent };
  }
  async getIntent(id: string): Promise<OnlineIntent | null> {
    const i = this.repo.state.intents.get(id);
    return i ? { ...i } : null;
  }
  async cancelIntent(id: string): Promise<PaymentStatus> {
    const i = this.repo.state.intents.get(id);
    if (!i) throw new Error('intent tidak ada');
    if (i.status === 'pending') i.status = 'cancelled';
    return i.status;
  }
  async refund(paymentId: string, req: RefundRequest): Promise<RefundOutcome> {
    this.refunds.push({ paymentId, req });
    if (this.refundMode === 'unsupported') throw new PaymentOperationUnsupportedError('tidak didukung');
    if (this.refundMode === 'succeeded') return { status: 'succeeded', providerRefundRef: `rf_${req.refundId}` };
    return { status: this.refundMode, providerRefundRef: null };
  }
  async simulate(id: string, status: Exclude<PaymentStatus, 'pending'>): Promise<unknown> {
    return this.transition(id, status, null);
  }

  /** Webhook terverifikasi: transisi monoton intent + hook saga dalam satu transaksi. */
  async transition(id: string, to: PaymentStatus, amount: number | null): Promise<void> {
    await this.repo.tx(async (r) => {
      const i = this.repo.state.intents.get(id);
      if (!i || i.status === 'paid' || i.status === to) return;
      if (i.status !== 'pending' && to !== 'paid') return;
      const mismatch = to === 'paid' && amount !== null && amount !== i.amount;
      if (!mismatch) {
        const from = i.status;
        i.status = to;
        if (to === 'paid') i.paidAt = new Date(this.repo.now());
        await this.saga?.onIntentTransition(r, { paymentId: id, onlineOrderId: i.onlineOrderId, from, to, amountMismatch: false });
      } else {
        await this.saga?.onIntentTransition(r, { paymentId: id, onlineOrderId: i.onlineOrderId, from: i.status, to, amountMismatch: true });
      }
    });
  }
  pay(id: string, amount: number | null = null): Promise<void> {
    return this.transition(id, 'paid', amount);
  }
}
