import { Db, Queryable } from '../common/db/db';
import { claimMessage } from '../common/db/processed-message';
import { StuckKind } from '../odoo-rpc/online-metrics';
import {
  OrderPatch,
  OrderRow,
  OrderStatus,
  PartPatch,
  PartRow,
  PartStatus,
  PortionRow,
  RefundPatch,
  RefundRow,
  RefundStatus,
} from './online.model';

export const ONLINE_REPO = Symbol('ONLINE_REPO');

export type NewOrder = Omit<OrderRow, 'created_at' | 'updated_at' | 'paid_at' | 'closed_at' | 'close_reason' | 'payment_intent_id'>;
export type NewPart = Omit<
  PartRow,
  | 'created_at'
  | 'updated_at'
  | 'sale_order_name'
  | 'odoo_state'
  | 'last_seq'
  | 'refunded_amount'
  | 'confirmed_at'
  | 'ready_at'
  | 'handed_over_at'
  | 'cancelled_at'
  | 'cancel_reason'
>;
export type NewRefund = Omit<RefundRow, 'created_at' | 'updated_at' | 'settled_at' | 'provider_refund_ref' | 'failure_reason'>;

export interface OnlineEventRecord {
  part_id: string;
  source: 'odoo' | 'bff' | 'gateway';
  event: string;
  seq: number | null;
  idempotency_key: string;
  outcome: 'applied' | 'duplicate' | 'ignored_stale' | 'ignored_unknown_event';
  occurred_at: Date | null;
}

export interface AdminAuditRecord {
  action: 'refund' | 'refund_settle' | 'refund_approve' | 'resolve_cancel' | 'resolve_sync';
  actor: string;
  reason: string;
  order_id: string | null;
  part_id: string | null;
  refund_id: string | null;
  amount: number | null;
  outcome: string;
}

export interface RetentionConfig {
  pickupCodeMaxDays: number;
  memberLinkDays: number;
  eventRetentionDays: number;
}
export interface RetentionResult {
  pickupNulled: number;
  qrNulled: number;
  unlinked: number;
  eventsPurged: number;
}

export interface OrderExportRow {
  order: OrderRow;
  parts: PartRow[];
}

/**
 * Akses data kanal online (docs/04 §10.8). Implementasi: `PgOnlineRepo` (SQL parametris) dan `MemoryOnlineRepo`
 * (test). Semua tulis lintas-baris dijalankan di `tx` (satu transaksi PostgreSQL).
 */
export interface OnlineRepo {
  tx<T>(fn: (r: OnlineRepo) => Promise<T>): Promise<T>;

  lockMember(memberId: string): Promise<void>;
  /** Advisory lock job (xact) — `false` bila replika lain memegangnya. */
  tryJobLock(key: number): Promise<boolean>;
  hasActiveDelete(memberId: string): Promise<boolean>;
  memberHashOf(memberId: string): Promise<string | null>;

  insertOrder(o: NewOrder): Promise<boolean>;
  findOrderByKey(memberId: string, key: string): Promise<OrderRow | null>;
  getOrder(id: string, forUpdate?: boolean): Promise<OrderRow | null>;
  getOrderOfMember(id: string, memberId: string): Promise<OrderRow | null>;
  listOrdersOfMember(memberId: string, before: { createdAt: Date; id: string } | null, limit: number): Promise<OrderRow[]>;
  updateOrder(id: string, patch: OrderPatch): Promise<void>;
  /** UPDATE bersyarat status (`from`) → `to`; true bila berubah. */
  transitionOrder(id: string, from: readonly OrderStatus[], to: OrderStatus, patch?: OrderPatch): Promise<boolean>;
  countOpenOrders(memberId: string): Promise<number>;

  insertPart(p: NewPart): Promise<void>;
  getPart(id: string, forUpdate?: boolean): Promise<PartRow | null>;
  getParts(orderId: string): Promise<PartRow[]>;
  getPartsOfOrders(orderIds: readonly string[]): Promise<PartRow[]>;
  updatePart(id: string, patch: PartPatch): Promise<void>;

  /** INSERT; bila bentrok indeks unik idempotensi → baris yang sudah ada. */
  insertRefund(r: NewRefund): Promise<RefundRow>;
  getRefund(id: string, forUpdate?: boolean): Promise<RefundRow | null>;
  updateRefund(id: string, patch: RefundPatch): Promise<void>;
  /**
   * SEC-97: klaim eksklusif atomik → `gateway_in_flight` dari `from` (atau `gateway_in_flight` basi sebelum `staleBefore`,
   * mis. proses mati di tengah). `null` bila sudah diklaim pihak lain / status lain.
   */
  claimRefund(id: string, from: readonly RefundStatus[], staleBefore: Date): Promise<RefundRow | null>;
  refundsOfPart(partId: string): Promise<RefundRow[]>;

  portionsOf(intentId: string): Promise<PortionRow[]>;

  insertEvent(e: OnlineEventRecord): Promise<boolean>;
  claim(consumer: string, key: string): Promise<boolean>;
  isClaimed(consumer: string, key: string): Promise<boolean>;
  enqueue(queue: string, payload: Record<string, unknown>, messageId: string): Promise<void>;
  audit(a: AdminAuditRecord): Promise<void>;

  // ───────────── job ─────────────
  /** `awaiting_payment` lewat `expires_at + grace` atau `created` lebih tua dari `createdBefore`. */
  expireCandidates(expiredBefore: Date, createdBefore: Date, limit: number): Promise<OrderRow[]>;
  partsInStatusBefore(statuses: readonly PartStatus[], updatedBefore: Date, limit: number): Promise<PartRow[]>;
  refundsInStatusBefore(statuses: readonly RefundStatus[], updatedBefore: Date, limit: number): Promise<RefundRow[]>;
  /** Order final belum dibayar (`expired|cancelled|failed`) yang masih punya bagian `placing|placed`. */
  closedOrdersWithOpenParts(updatedBefore: Date, limit: number): Promise<OrderRow[]>;
  stuckCounts(now: Date): Promise<Record<StuckKind, number>>;
  portionMismatches(limit: number): Promise<{ payment_id: string; expected: number; actual: number }[]>;
  refundOverruns(limit: number): Promise<{ part_id: string; refunded: number; portion: number }[]>;
  retention(now: Date, cfg: RetentionConfig): Promise<RetentionResult>;

  // ───────────── privasi (L-4) ─────────────
  memberOpenPaidParts(memberId: string): Promise<number>;
  memberUnpaidOrders(memberId: string): Promise<OrderRow[]>;
  exportOrders(memberId: string, limit: number): Promise<OrderExportRow[]>;
}

// ───────────── PostgreSQL ─────────────

const ORDER_COLS = `id::text AS id, member_id::text AS member_id, idempotency_key, request_sha256, quote_id::text AS quote_id, status,
  amount_payable, currency, payment_intent_id::text AS payment_intent_id, expires_at, paid_at, closed_at, close_reason, created_at, updated_at`;
const PART_COLS = `id::text AS id, order_id::text AS order_id, channel_code, company_code, outlet_code, fulfilment, status, lines, tax_groups,
  amount_total, amount_payable, quote_hash, sale_order_name, odoo_state, last_seq, pickup_code_enc, refunded_amount, confirmed_at, ready_at,
  handed_over_at, cancelled_at, cancel_reason, created_at, updated_at`;
const REFUND_COLS = `id::text AS id, part_id::text AS part_id, payment_intent_id::text AS payment_intent_id, kind, reason, lines, amount,
  odoo_refund_ref, provider_refund_ref, status, failure_reason, requested_by, created_at, updated_at, settled_at`;

type Raw = Record<string, unknown>;
const d = (v: unknown): Date | null => (v === null || v === undefined ? null : v instanceof Date ? v : new Date(String(v)));
const n = (v: unknown): number => Number(v);

function toOrder(r: Raw): OrderRow {
  return {
    id: String(r.id),
    member_id: r.member_id === null ? null : String(r.member_id),
    idempotency_key: String(r.idempotency_key),
    request_sha256: String(r.request_sha256),
    quote_id: String(r.quote_id),
    status: r.status as OrderStatus,
    amount_payable: n(r.amount_payable),
    currency: 'IDR',
    payment_intent_id: r.payment_intent_id === null ? null : String(r.payment_intent_id),
    expires_at: d(r.expires_at) as Date,
    paid_at: d(r.paid_at),
    closed_at: d(r.closed_at),
    close_reason: (r.close_reason ?? null) as OrderRow['close_reason'],
    created_at: d(r.created_at) as Date,
    updated_at: d(r.updated_at) as Date,
  };
}

function toPart(r: Raw): PartRow {
  return {
    id: String(r.id),
    order_id: String(r.order_id),
    channel_code: String(r.channel_code),
    company_code: String(r.company_code),
    outlet_code: String(r.outlet_code),
    fulfilment: r.fulfilment as PartRow['fulfilment'],
    status: r.status as PartStatus,
    lines: (r.lines ?? []) as PartRow['lines'],
    tax_groups: (r.tax_groups ?? []) as PartRow['tax_groups'],
    amount_total: n(r.amount_total),
    amount_payable: n(r.amount_payable),
    quote_hash: String(r.quote_hash),
    sale_order_name: (r.sale_order_name ?? null) as string | null,
    odoo_state: (r.odoo_state ?? null) as string | null,
    last_seq: n(r.last_seq),
    pickup_code_enc: (r.pickup_code_enc ?? null) as Buffer | null,
    refunded_amount: n(r.refunded_amount),
    confirmed_at: d(r.confirmed_at),
    ready_at: d(r.ready_at),
    handed_over_at: d(r.handed_over_at),
    cancelled_at: d(r.cancelled_at),
    cancel_reason: (r.cancel_reason ?? null) as PartRow['cancel_reason'],
    created_at: d(r.created_at) as Date,
    updated_at: d(r.updated_at) as Date,
  };
}

function toRefund(r: Raw): RefundRow {
  return {
    id: String(r.id),
    part_id: String(r.part_id),
    payment_intent_id: String(r.payment_intent_id),
    kind: r.kind as RefundRow['kind'],
    reason: r.reason as RefundRow['reason'],
    lines: (r.lines ?? null) as RefundRow['lines'],
    amount: r.amount === null || r.amount === undefined ? null : n(r.amount),
    odoo_refund_ref: (r.odoo_refund_ref ?? null) as string | null,
    provider_refund_ref: (r.provider_refund_ref ?? null) as string | null,
    status: r.status as RefundStatus,
    failure_reason: (r.failure_reason ?? null) as string | null,
    requested_by: r.requested_by as RefundRow['requested_by'],
    created_at: d(r.created_at) as Date,
    updated_at: d(r.updated_at) as Date,
    settled_at: d(r.settled_at),
  };
}

const ORDER_PATCH_COLS = new Set(['status', 'payment_intent_id', 'paid_at', 'closed_at', 'close_reason', 'amount_payable', 'expires_at']);
const PART_PATCH_COLS = new Set([
  'status',
  'sale_order_name',
  'odoo_state',
  'last_seq',
  'pickup_code_enc',
  'refunded_amount',
  'confirmed_at',
  'ready_at',
  'handed_over_at',
  'cancelled_at',
  'cancel_reason',
  'amount_total',
  'amount_payable',
]);
const REFUND_PATCH_COLS = new Set(['status', 'amount', 'odoo_refund_ref', 'provider_refund_ref', 'settled_at', 'failure_reason']);

/** SET klausa dari patch (nama kolom HANYA dari whitelist konstan, nilai selalu parameter). */
function setClause(patch: Record<string, unknown>, allowed: ReadonlySet<string>, offset: number): { sql: string; params: unknown[] } {
  const parts: string[] = [];
  const params: unknown[] = [];
  for (const [k, v] of Object.entries(patch)) {
    if (v === undefined) continue;
    if (!allowed.has(k)) throw new Error(`kolom patch tidak diizinkan: ${k}`);
    params.push(v instanceof Date ? v.toISOString() : v);
    parts.push(`${k} = $${offset + params.length}`);
  }
  parts.push('updated_at = now()');
  return { sql: parts.join(', '), params };
}

export class PgOnlineRepo implements OnlineRepo {
  constructor(
    private readonly q: Queryable,
    private readonly db: Db | null = null,
  ) {}

  /** Dari koneksi pool (`Db`) membuka transaksi; dari klien transaksi (`Queryable`) menjalankan langsung (nested). */
  async tx<T>(fn: (r: OnlineRepo) => Promise<T>): Promise<T> {
    if (!this.db) return fn(this);
    return this.db.tx((q) => fn(new PgOnlineRepo(q, null)));
  }

  async lockMember(memberId: string): Promise<void> {
    await this.q.query(`SELECT pg_advisory_xact_lock(hashtext('efn:online:member:' || $1::text))`, [memberId]);
  }
  async tryJobLock(key: number): Promise<boolean> {
    const r = await this.q.query<{ ok: boolean }>('SELECT pg_try_advisory_xact_lock($1) AS ok', [key]);
    return r.rows[0]?.ok === true;
  }
  async hasActiveDelete(memberId: string): Promise<boolean> {
    const r = await this.q.query(
      `SELECT 1 FROM platform.dsar_request WHERE member_id = $1 AND type = 'delete' AND status IN ('scheduled', 'processing') LIMIT 1`,
      [memberId],
    );
    return (r.rowCount ?? 0) > 0;
  }
  async memberHashOf(memberId: string): Promise<string | null> {
    const r = await this.q.query<{ member_hash: string }>(
      `SELECT member_hash FROM platform.member WHERE id = $1 AND deleted_at IS NULL`,
      [memberId],
    );
    return r.rows[0]?.member_hash ?? null;
  }

  async insertOrder(o: NewOrder): Promise<boolean> {
    const r = await this.q.query(
      `INSERT INTO platform.online_order
         (id, member_id, idempotency_key, request_sha256, quote_id, status, amount_payable, currency, expires_at)
       VALUES ($1, $2, $3, $4, $5, $6, $7, 'IDR', $8)
       ON CONFLICT ON CONSTRAINT uq_online_order_member_key DO NOTHING
       RETURNING id`,
      [o.id, o.member_id, o.idempotency_key, o.request_sha256, o.quote_id, o.status, o.amount_payable, o.expires_at.toISOString()],
    );
    return (r.rowCount ?? 0) > 0;
  }
  async findOrderByKey(memberId: string, key: string): Promise<OrderRow | null> {
    const r = await this.q.query<Raw>(`SELECT ${ORDER_COLS} FROM platform.online_order WHERE member_id = $1 AND idempotency_key = $2`, [
      memberId,
      key,
    ]);
    return r.rows[0] ? toOrder(r.rows[0]) : null;
  }
  async getOrder(id: string, forUpdate = false): Promise<OrderRow | null> {
    const r = await this.q.query<Raw>(`SELECT ${ORDER_COLS} FROM platform.online_order WHERE id = $1${forUpdate ? ' FOR UPDATE' : ''}`, [id]);
    return r.rows[0] ? toOrder(r.rows[0]) : null;
  }
  async getOrderOfMember(id: string, memberId: string): Promise<OrderRow | null> {
    const r = await this.q.query<Raw>(`SELECT ${ORDER_COLS} FROM platform.online_order WHERE id = $1 AND member_id = $2`, [id, memberId]);
    return r.rows[0] ? toOrder(r.rows[0]) : null;
  }
  async listOrdersOfMember(memberId: string, before: { createdAt: Date; id: string } | null, limit: number): Promise<OrderRow[]> {
    const r = before
      ? await this.q.query<Raw>(
          `SELECT ${ORDER_COLS} FROM platform.online_order
            WHERE member_id = $1 AND (created_at, id) < ($2::timestamptz, $3::uuid)
            ORDER BY created_at DESC, id DESC LIMIT $4`,
          [memberId, before.createdAt.toISOString(), before.id, limit],
        )
      : await this.q.query<Raw>(
          `SELECT ${ORDER_COLS} FROM platform.online_order WHERE member_id = $1 ORDER BY created_at DESC, id DESC LIMIT $2`,
          [memberId, limit],
        );
    return r.rows.map(toOrder);
  }
  async updateOrder(id: string, patch: OrderPatch): Promise<void> {
    const s = setClause(patch, ORDER_PATCH_COLS, 1);
    await this.q.query(`UPDATE platform.online_order SET ${s.sql} WHERE id = $1`, [id, ...s.params]);
  }
  async transitionOrder(id: string, from: readonly OrderStatus[], to: OrderStatus, patch: OrderPatch = {}): Promise<boolean> {
    const s = setClause({ ...patch, status: to }, ORDER_PATCH_COLS, 2);
    const r = await this.q.query(`UPDATE platform.online_order SET ${s.sql} WHERE id = $1 AND status = ANY($2::text[])`, [id, [...from], ...s.params]);
    return (r.rowCount ?? 0) > 0;
  }
  async countOpenOrders(memberId: string): Promise<number> {
    const r = await this.q.query<{ n: string }>(
      `SELECT count(*) AS n FROM platform.online_order WHERE member_id = $1 AND status IN ('created', 'awaiting_payment')`,
      [memberId],
    );
    return Number(r.rows[0]?.n ?? 0);
  }

  async insertPart(p: NewPart): Promise<void> {
    await this.q.query(
      `INSERT INTO platform.online_order_part
         (id, order_id, channel_code, company_code, outlet_code, fulfilment, status, lines, tax_groups, amount_total, amount_payable,
          quote_hash, pickup_code_enc)
       VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9::jsonb, $10, $11, $12, $13)`,
      [
        p.id,
        p.order_id,
        p.channel_code,
        p.company_code,
        p.outlet_code,
        p.fulfilment,
        p.status,
        JSON.stringify(p.lines),
        JSON.stringify(p.tax_groups),
        p.amount_total,
        p.amount_payable,
        p.quote_hash,
        p.pickup_code_enc,
      ],
    );
  }
  async getPart(id: string, forUpdate = false): Promise<PartRow | null> {
    const r = await this.q.query<Raw>(`SELECT ${PART_COLS} FROM platform.online_order_part WHERE id = $1${forUpdate ? ' FOR UPDATE' : ''}`, [id]);
    return r.rows[0] ? toPart(r.rows[0]) : null;
  }
  async getParts(orderId: string): Promise<PartRow[]> {
    const r = await this.q.query<Raw>(`SELECT ${PART_COLS} FROM platform.online_order_part WHERE order_id = $1 ORDER BY channel_code`, [orderId]);
    return r.rows.map(toPart);
  }
  async getPartsOfOrders(orderIds: readonly string[]): Promise<PartRow[]> {
    if (orderIds.length === 0) return [];
    const r = await this.q.query<Raw>(
      `SELECT ${PART_COLS} FROM platform.online_order_part WHERE order_id = ANY($1::uuid[]) ORDER BY channel_code`,
      [[...orderIds]],
    );
    return r.rows.map(toPart);
  }
  async updatePart(id: string, patch: PartPatch): Promise<void> {
    const s = setClause(patch, PART_PATCH_COLS, 1);
    await this.q.query(`UPDATE platform.online_order_part SET ${s.sql} WHERE id = $1`, [id, ...s.params]);
  }

  async insertRefund(r: NewRefund): Promise<RefundRow> {
    const ins = await this.q.query<Raw>(
      `INSERT INTO platform.online_refund
         (id, part_id, payment_intent_id, kind, reason, lines, amount, odoo_refund_ref, status, requested_by)
       VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7, $8, $9, $10)
       ON CONFLICT DO NOTHING
       RETURNING ${REFUND_COLS}`,
      [r.id, r.part_id, r.payment_intent_id, r.kind, r.reason, r.lines === null ? null : JSON.stringify(r.lines), r.amount, r.odoo_refund_ref, r.status, r.requested_by],
    );
    if (ins.rows[0]) return toRefund(ins.rows[0]);
    const existing =
      r.odoo_refund_ref !== null
        ? await this.q.query<Raw>(`SELECT ${REFUND_COLS} FROM platform.online_refund WHERE part_id = $1 AND odoo_refund_ref = $2`, [
            r.part_id,
            r.odoo_refund_ref,
          ])
        : await this.q.query<Raw>(
            `SELECT ${REFUND_COLS} FROM platform.online_refund
              WHERE (id = $1) OR (part_id = $2 AND reason = $3 AND requested_by = 'system' AND odoo_refund_ref IS NULL)
              ORDER BY created_at LIMIT 1`,
            [r.id, r.part_id, r.reason],
          );
    if (!existing.rows[0]) throw new Error('refund bentrok tetapi tidak ditemukan');
    return toRefund(existing.rows[0]);
  }
  async getRefund(id: string, forUpdate = false): Promise<RefundRow | null> {
    const r = await this.q.query<Raw>(`SELECT ${REFUND_COLS} FROM platform.online_refund WHERE id = $1${forUpdate ? ' FOR UPDATE' : ''}`, [id]);
    return r.rows[0] ? toRefund(r.rows[0]) : null;
  }
  async updateRefund(id: string, patch: RefundPatch): Promise<void> {
    const s = setClause(patch, REFUND_PATCH_COLS, 1);
    await this.q.query(`UPDATE platform.online_refund SET ${s.sql} WHERE id = $1`, [id, ...s.params]);
  }
  async claimRefund(id: string, from: readonly RefundStatus[], staleBefore: Date): Promise<RefundRow | null> {
    const r = await this.q.query<Raw>(
      `UPDATE platform.online_refund SET status = 'gateway_in_flight', updated_at = now()
        WHERE id = $1 AND (status = ANY($2::text[]) OR (status = 'gateway_in_flight' AND updated_at < $3))
        RETURNING ${REFUND_COLS}`,
      [id, [...from], staleBefore.toISOString()],
    );
    return r.rows[0] ? toRefund(r.rows[0]) : null;
  }
  async refundsOfPart(partId: string): Promise<RefundRow[]> {
    const r = await this.q.query<Raw>(`SELECT ${REFUND_COLS} FROM platform.online_refund WHERE part_id = $1 ORDER BY created_at`, [partId]);
    return r.rows.map(toRefund);
  }

  async portionsOf(intentId: string): Promise<PortionRow[]> {
    const r = await this.q.query<Raw>(
      `SELECT payment_intent_id::text AS payment_intent_id, part_id::text AS part_id, channel_code, company_code, amount
         FROM platform.payment_intent_portion WHERE payment_intent_id = $1`,
      [intentId],
    );
    return r.rows.map((x) => ({
      payment_intent_id: String(x.payment_intent_id),
      part_id: String(x.part_id),
      channel_code: String(x.channel_code),
      company_code: String(x.company_code),
      amount: n(x.amount),
    }));
  }

  async insertEvent(e: OnlineEventRecord): Promise<boolean> {
    const r = await this.q.query(
      `INSERT INTO platform.online_order_event (part_id, source, event, seq, idempotency_key, outcome, occurred_at)
       VALUES ($1, $2, $3, $4, $5, $6, $7)
       ON CONFLICT (idempotency_key) DO NOTHING`,
      [e.part_id, e.source, e.event, e.seq, e.idempotency_key, e.outcome, e.occurred_at ? e.occurred_at.toISOString() : null],
    );
    return (r.rowCount ?? 0) > 0;
  }
  claim(consumer: string, key: string): Promise<boolean> {
    return claimMessage(this.q, consumer, key);
  }
  async isClaimed(consumer: string, key: string): Promise<boolean> {
    const r = await this.q.query(`SELECT 1 FROM platform.processed_message WHERE consumer = $1 AND idempotency_key = $2`, [consumer, key]);
    return (r.rowCount ?? 0) > 0;
  }
  async enqueue(queue: string, payload: Record<string, unknown>, messageId: string): Promise<void> {
    await this.q.query(`INSERT INTO platform.outbox (queue, payload, message_id) VALUES ($1, $2::jsonb, $3)`, [queue, JSON.stringify(payload), messageId]);
  }
  async audit(a: AdminAuditRecord): Promise<void> {
    await this.q.query(
      `INSERT INTO platform.online_admin_audit (action, actor, reason, order_id, part_id, refund_id, amount, outcome)
       VALUES ($1, $2, $3, $4, $5, $6, $7, $8)`,
      [a.action, a.actor, a.reason, a.order_id, a.part_id, a.refund_id, a.amount, a.outcome],
    );
  }

  async expireCandidates(expiredBefore: Date, createdBefore: Date, limit: number): Promise<OrderRow[]> {
    const r = await this.q.query<Raw>(
      `SELECT ${ORDER_COLS} FROM platform.online_order
        WHERE (status = 'awaiting_payment' AND expires_at < $1) OR (status = 'created' AND created_at < $2)
        ORDER BY expires_at LIMIT $3`,
      [expiredBefore.toISOString(), createdBefore.toISOString(), limit],
    );
    return r.rows.map(toOrder);
  }
  async partsInStatusBefore(statuses: readonly PartStatus[], updatedBefore: Date, limit: number): Promise<PartRow[]> {
    const r = await this.q.query<Raw>(
      `SELECT ${PART_COLS} FROM platform.online_order_part WHERE status = ANY($1::text[]) AND updated_at < $2 ORDER BY updated_at LIMIT $3`,
      [[...statuses], updatedBefore.toISOString(), limit],
    );
    return r.rows.map(toPart);
  }
  async refundsInStatusBefore(statuses: readonly RefundStatus[], updatedBefore: Date, limit: number): Promise<RefundRow[]> {
    const r = await this.q.query<Raw>(
      `SELECT ${REFUND_COLS} FROM platform.online_refund WHERE status = ANY($1::text[]) AND updated_at < $2 ORDER BY updated_at LIMIT $3`,
      [[...statuses], updatedBefore.toISOString(), limit],
    );
    return r.rows.map(toRefund);
  }
  async closedOrdersWithOpenParts(updatedBefore: Date, limit: number): Promise<OrderRow[]> {
    const r = await this.q.query<Raw>(
      `SELECT ${ORDER_COLS} FROM platform.online_order o
        WHERE o.status IN ('expired', 'cancelled', 'failed') AND o.paid_at IS NULL AND o.updated_at < $1
          AND EXISTS (SELECT 1 FROM platform.online_order_part p WHERE p.order_id = o.id AND p.status IN ('placing', 'placed'))
        ORDER BY o.updated_at LIMIT $2`,
      [updatedBefore.toISOString(), limit],
    );
    return r.rows.map(toOrder);
  }
  async stuckCounts(now: Date): Promise<Record<StuckKind, number>> {
    const r = await this.q.query<{ placing: string; paying: string; awaiting: string; refund: string; attention: string }>(
      `SELECT
         (SELECT count(*) FROM platform.online_order_part WHERE status = 'placing' AND updated_at < $1::timestamptz - interval '2 minutes') AS placing,
         (SELECT count(*) FROM platform.online_order_part WHERE status = 'paying' AND updated_at < $1::timestamptz - interval '10 minutes') AS paying,
         (SELECT count(*) FROM platform.online_order WHERE status = 'awaiting_payment' AND expires_at < $1::timestamptz - interval '5 minutes') AS awaiting,
         (SELECT count(*) FROM platform.online_refund WHERE status NOT IN ('settled', 'failed', 'manual_required') AND created_at < $1::timestamptz - interval '24 hours') AS refund,
         (SELECT count(*) FROM platform.online_refund WHERE status IN ('failed', 'manual_required', 'approval_required')) AS attention`,
      [now.toISOString()],
    );
    const row = r.rows[0];
    return {
      placing: Number(row?.placing ?? 0),
      paying: Number(row?.paying ?? 0),
      awaiting_payment: Number(row?.awaiting ?? 0),
      refund: Number(row?.refund ?? 0),
      refund_attention: Number(row?.attention ?? 0),
    };
  }
  async portionMismatches(limit: number): Promise<{ payment_id: string; expected: number; actual: number }[]> {
    const r = await this.q.query<{ payment_id: string; expected: string; actual: string }>(
      `SELECT i.id::text AS payment_id, i.amount AS expected, COALESCE(SUM(p.amount), 0) AS actual
         FROM platform.payment_intent i
         LEFT JOIN platform.payment_intent_portion p ON p.payment_intent_id = i.id
        WHERE i.purpose = 'online' AND i.created_at > now() - interval '7 days'
        GROUP BY i.id, i.amount
       HAVING COALESCE(SUM(p.amount), 0) <> i.amount
        LIMIT $1`,
      [limit],
    );
    return r.rows.map((x) => ({ payment_id: x.payment_id, expected: Number(x.expected), actual: Number(x.actual) }));
  }
  async refundOverruns(limit: number): Promise<{ part_id: string; refunded: number; portion: number }[]> {
    const r = await this.q.query<{ part_id: string; refunded: string; portion: string }>(
      `SELECT p.id::text AS part_id, COALESCE(SUM(r.amount), 0) AS refunded, p.amount_payable AS portion
         FROM platform.online_order_part p
         JOIN platform.online_refund r ON r.part_id = p.id AND r.status IN ('gateway_done', 'settled')
        GROUP BY p.id, p.amount_payable
       HAVING COALESCE(SUM(r.amount), 0) > p.amount_payable
        LIMIT $1`,
      [limit],
    );
    return r.rows.map((x) => ({ part_id: x.part_id, refunded: Number(x.refunded), portion: Number(x.portion) }));
  }
  async retention(now: Date, cfg: RetentionConfig): Promise<RetentionResult> {
    const ts = now.toISOString();
    const pickup = await this.q.query(
      `UPDATE platform.online_order_part SET pickup_code_enc = NULL, updated_at = now()
        WHERE pickup_code_enc IS NOT NULL
          AND (status IN ('handed_over', 'cancelled', 'place_failed', 'stock_short')
               OR (status IN ('confirmed', 'ready', 'review')
                   AND COALESCE(ready_at, confirmed_at, updated_at) < $1::timestamptz - make_interval(days => $2::int)))`,
      [ts, cfg.pickupCodeMaxDays],
    );
    const qr = await this.q.query(
      `UPDATE platform.payment_intent SET qr_string = NULL, updated_at = now()
        WHERE purpose = 'online' AND qr_string IS NOT NULL AND status <> 'pending' AND updated_at < $1::timestamptz - interval '1 day'`,
      [ts],
    );
    const unlink = await this.q.query(
      `UPDATE platform.online_order SET member_id = NULL, member_unlinked_at = now(), updated_at = now()
        WHERE member_id IS NOT NULL AND closed_at IS NOT NULL AND closed_at < $1::timestamptz - make_interval(days => $2::int)`,
      [ts, cfg.memberLinkDays],
    );
    const events = await this.q.query(
      `DELETE FROM platform.online_order_event
        WHERE id IN (SELECT id FROM platform.online_order_event
                      WHERE received_at < $1::timestamptz - make_interval(days => $2::int) LIMIT 5000)`,
      [ts, cfg.eventRetentionDays],
    );
    return {
      pickupNulled: pickup.rowCount ?? 0,
      qrNulled: qr.rowCount ?? 0,
      unlinked: unlink.rowCount ?? 0,
      eventsPurged: events.rowCount ?? 0,
    };
  }

  async memberOpenPaidParts(memberId: string): Promise<number> {
    const r = await this.q.query<{ n: string }>(
      `SELECT count(*) AS n FROM platform.online_order_part p JOIN platform.online_order o ON o.id = p.order_id
        WHERE o.member_id = $1 AND p.status IN ('paying', 'confirmed', 'ready', 'review')`,
      [memberId],
    );
    return Number(r.rows[0]?.n ?? 0);
  }
  async memberUnpaidOrders(memberId: string): Promise<OrderRow[]> {
    const r = await this.q.query<Raw>(
      `SELECT ${ORDER_COLS} FROM platform.online_order WHERE member_id = $1 AND status IN ('created', 'awaiting_payment')`,
      [memberId],
    );
    return r.rows.map(toOrder);
  }
  async exportOrders(memberId: string, limit: number): Promise<OrderExportRow[]> {
    const orders = await this.listOrdersOfMember(memberId, null, limit);
    const parts = await this.getPartsOfOrders(orders.map((o) => o.id));
    return orders.map((o) => ({ order: o, parts: parts.filter((p) => p.order_id === o.id) }));
  }
}
