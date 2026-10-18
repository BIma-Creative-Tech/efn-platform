import { Inject, Injectable, Logger, Optional } from '@nestjs/common';
import { AppConfig } from '../config/app-config';
import { randomUUID } from 'node:crypto';
import { QUEUES } from '../common/amqp/queues';
import { ODOO_ONLINE, OdooOnlineApi, QuoteLineIn } from '../odoo-rpc/odoo-online.types';
import { OnlineMetrics } from '../odoo-rpc/online-metrics';
import { PartRow, RefundRow, orderHasPayment, partStatusFromOdoo, refundRequestedMessage } from './online.model';
import { ONLINE_REPO, OnlineRepo } from './online.repository';
import { OnlineSagaService } from './online-saga.service';

export class AdminError extends Error {}

/**
 * SEC-98 (L-16): aktor = identitas OS/host `<user>/<host>` yang diisi CLI dari lingkungan (wrapper host
 * `EFN_CS_OPERATOR`/`SUDO_USER`), BUKAN argumen bebas; tanpa `@` (bukan email karyawan).
 */
export const ACTOR_RE = /^[A-Za-z0-9._-]{2,64}(\/[A-Za-z0-9._-]{1,64})?$/;

/** SEC-107 (L-19c): peran dari wrapper host (`EFN_CS_ROLE`, grup efn-cs / efn-cs-approver), tidak bisa ditimpa pemanggil. */
export type CsRole = 'cs' | 'approver';
export interface Operator {
  actor: string;
  role: CsRole;
}
/** String polos = aktor berperan `cs` (hak paling kecil). */
export function toOperator(op: string | Operator): Operator {
  return typeof op === 'string' ? { actor: op, role: 'cs' } : op;
}

/**
 * SEC-108: alasan CS disimpan ke audit setelah pola HP/email disamarkan (alasan bisa tidak sengaja memuat PII pelanggan).
 */
export function scrubReason(reason: string): string {
  return reason
    .replace(/[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/g, '[disamarkan]')
    .replace(/\+?\d[\d\s.-]{6,}\d/g, '[disamarkan]')
    .slice(0, 200);
}

export interface CsRefundInput {
  partId: string;
  refundId?: string;
  lines: QuoteLineIn[] | null;
  full: boolean;
  restock: boolean;
  reason: 'customer_cs' | 'ops_cancelled';
  actor: string;
  /** SEC-107: default `cs`. */
  role?: CsRole;
  note: string;
}

/**
 * SEC-95: apakah aktor penyetuju (`<user>/<host>`) = pembuat/pemicu refund. Dibandingkan bagian user (huruf kecil):
 * aktor CS `<user>/<host>` dan `odoo-login:<login>`. `odoo-uid:<n>` tidak bisa dipetakan ke user OS → tidak cocok.
 * Pembuat NULL (sistem/data lama) → false (tidak pernah menolak karena data lama).
 */
export function sameActor(initiatedBy: string | null, approver: string): boolean {
  if (!initiatedBy) return false;
  const user = (v: string): string => v.split('/')[0].toLowerCase();
  const approverUser = user(approver);
  if (initiatedBy.startsWith('odoo-login:')) return initiatedBy.slice('odoo-login:'.length).toLowerCase() === approverUser;
  if (initiatedBy.startsWith('odoo-uid:')) return false;
  return user(initiatedBy) === approverUser;
}

/** Refund yang belum gagal mengikat porsi (pre-check sebelum nota kredit Odoo, SEC-98). */
function committedOf(refunds: readonly RefundRow[], exceptId?: string): number {
  return refunds.filter((x) => x.id !== exceptId && x.status !== 'failed').reduce((s, x) => s + (x.amount ?? 0), 0);
}

/** Perkiraan nominal refund baris dari snapshot harga quote (pre-check; nominal final tetap dari nota kredit Odoo). */
export function estimateLinesAmount(part: PartRow, lines: readonly QuoteLineIn[]): number {
  let total = 0;
  for (const l of lines) {
    // G19: baris reward promo (harga ≤ 0) bukan baris yang bisa direfund; perkiraan memakai harga baris produk
    const orig = part.lines.find((x) => x.product_id === l.product_id && x.reward !== true);
    if (!orig || l.qty < 1 || l.qty > orig.qty) throw new AdminError(`baris produk ${l.product_id} tidak ada di bagian atau qty melebihi pesanan`);
    total += orig.price_unit_incl * l.qty;
  }
  return Math.round(total);
}

/**
 * Operasi CS/ops lewat CLI (L-3, L-16, docs/04 §10.6.6): refund parsial/penuh, persetujuan refund inisiatif Odoo,
 * settle refund manual, resolve `review`. Setiap aksi dicatat di `platform.online_admin_audit` + log
 * `EFN_ONLINE_CS_AUDIT {json}` — tanpa PII pelanggan. Refund selalu terikat SATU bagian dan ≤ porsi − refund lain.
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
    @Optional() private readonly config: AppConfig | null = null,
  ) {}

  private get approvalMinIdr(): number {
    return this.config ? this.config.onlineCsRefundApprovalMinIdr : 0;
  }

  private checkActor(actor: string, reason: string): void {
    if (!ACTOR_RE.test(actor)) throw new AdminError('identitas operator tidak valid (wrapper host wajib menyetel EFN_CS_OPERATOR)');
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
    // G20 §12.2 no. 8: order bayar di kasir yang sudah lunas boleh direfund (tanpa intent; uang keluar lewat refund POS)
    if (!order || !orderHasPayment(order)) throw new AdminError('order tanpa pembayaran');
    // SEC-98: cek porsi di bawah kunci bagian SEBELUM meminta nota kredit (Odoo memposting dokumen akuntansi)
    await this.repo.tx(async (r) => {
      const p = await r.getPart(part.id, true);
      if (!p) throw new AdminError('bagian hilang');
      const committed = committedOf(await r.refundsOfPart(p.id), input.refundId);
      const estimate = input.full ? p.amount_payable - committed : estimateLinesAmount(p, input.lines ?? []);
      if (estimate <= 0 || committed + estimate > p.amount_payable) {
        throw new AdminError(`refund melebihi sisa porsi bagian (sisa ${p.amount_payable - committed})`);
      }
    });
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
      const committed = committedOf((await r.refundsOfPart(p.id)).filter((x) => x.odoo_refund_ref !== res.refund_ref));
      const over = committed + res.amount > p.amount_payable;
      // SEC-107 (L-19c): refund CS ≥ ambang (default 0 = semua) menunggu approver; di bawah ambang langsung ke gateway
      const needsApproval = res.amount >= this.approvalMinIdr;
      const rf = await r.insertRefund({
        id: refundId,
        part_id: p.id,
        payment_intent_id: intentId,
        kind: input.full ? 'full' : 'lines',
        reason: input.reason,
        lines: input.full ? null : input.lines,
        amount: res.amount,
        odoo_refund_ref: res.refund_ref,
        status: over ? 'failed' : needsApproval ? 'approval_required' : 'odoo_done',
        requested_by: 'cs',
        initiated_by: input.actor,
      });
      if (over) {
        await r.updateRefund(rf.id, { failure_reason: 'refund_exceeds_portion' });
        this.metrics.anomaly('refund_exceeds_portion', 'critical', { part_id: p.id, refund_id: rf.id, expected: p.amount_payable, actual: committed + res.amount });
      } else if (!needsApproval) {
        const m = refundRequestedMessage(rf.id);
        await r.enqueue(QUEUES.ORDER_REFUND_REQUESTED, { ...m }, m.idempotency_key);
      }
      await r.audit({
        action: 'refund',
        actor: input.actor,
        reason: scrubReason(input.note),
        order_id: p.order_id,
        part_id: p.id,
        refund_id: rf.id,
        amount: res.amount,
        outcome: over ? 'refund_exceeds_portion' : rf.status,
      });
      return { refund_id: rf.id, amount: res.amount, status: over ? 'failed' : rf.status };
    });
    this.metrics.refund(out.status);
    this.logAudit({ action: 'refund', actor: input.actor, part_id: part.id, refund_id: out.refund_id, amount: out.amount, outcome: out.status });
    return out;
  }

  /** SEC-95 (L-16): persetujuan CS atas refund inisiatif Odoo (`approval_required` → `odoo_done` → gateway). */
  async approveRefund(refundId: string, operator: string | Operator, note: string): Promise<{ status: string }> {
    const { actor, role } = toOperator(operator);
    this.checkActor(actor, note);
    const out = await this.repo.tx(async (r) => {
      const rf = await r.getRefund(refundId, true);
      if (!rf) throw new AdminError('refund tidak ditemukan');
      if (rf.status !== 'approval_required') throw new AdminError(`status refund ${rf.status} tidak menunggu persetujuan`);
      const p = await r.getPart(rf.part_id, true);
      if (!p) throw new AdminError('bagian hilang');
      // SEC-107 (L-19c): hanya peran approver (grup host efn-cs-approver) — termasuk refund dari Odoo (odoo-uid)
      if (role !== 'approver') {
        await r.audit({ action: 'refund_approve', actor, reason: scrubReason(note), order_id: p.order_id, part_id: p.id, refund_id: rf.id, amount: rf.amount, outcome: 'rejected_role' });
        return 'rejected_role';
      }
      // SEC-95: empat mata — penyetuju ≠ pembuat/pemicu (pembuat tidak diketahui → tidak ditolak)
      if (sameActor(rf.initiated_by, actor)) {
        await r.audit({ action: 'refund_approve', actor, reason: scrubReason(note), order_id: p.order_id, part_id: p.id, refund_id: rf.id, amount: rf.amount, outcome: 'rejected_same_actor' });
        return 'rejected_same_actor';
      }
      const committed = committedOf((await r.refundsOfPart(p.id)).filter((x) => x.status !== 'approval_required'), rf.id);
      if ((rf.amount ?? 0) <= 0 || committed + (rf.amount ?? 0) > p.amount_payable) {
        await r.updateRefund(rf.id, { status: 'failed', failure_reason: 'refund_exceeds_portion' });
        await r.audit({ action: 'refund_approve', actor, reason: scrubReason(note), order_id: p.order_id, part_id: p.id, refund_id: rf.id, amount: rf.amount, outcome: 'refund_exceeds_portion' });
        return 'failed';
      }
      await r.updateRefund(rf.id, { status: 'odoo_done' });
      const m = refundRequestedMessage(rf.id);
      await r.enqueue(QUEUES.ORDER_REFUND_REQUESTED, { ...m }, m.idempotency_key);
      await r.audit({ action: 'refund_approve', actor, reason: scrubReason(note), order_id: p.order_id, part_id: p.id, refund_id: rf.id, amount: rf.amount, outcome: 'odoo_done' });
      return 'odoo_done';
    });
    this.logAudit({ action: 'refund_approve', actor, refund_id: refundId, outcome: out });
    if (out === 'rejected_role') throw new AdminError('persetujuan refund hanya untuk peran approver (grup efn-cs-approver)');
    if (out === 'rejected_same_actor') throw new AdminError('penyetuju tidak boleh sama dengan pembuat/pemicu refund (empat mata)');
    this.metrics.refund(out);
    return { status: out };
  }

  /** Dana dikembalikan manual oleh Finance (gateway tak mendukung refund) → `gateway_done` → settle Odoo → `settled`. */
  async settleManual(refundId: string, providerRef: string, operator: string | Operator, note: string): Promise<{ status: string }> {
    const { actor, role } = toOperator(operator);
    this.checkActor(actor, note);
    if (!/^[A-Za-z0-9._:/-]{3,128}$/.test(providerRef)) throw new AdminError('--provider-ref tidak valid');
    const rf = await this.repo.getRefund(refundId);
    if (!rf) throw new AdminError('refund tidak ditemukan');
    // SEC-112: settle manual mencatat dana keluar (+ pembayaran keluar Odoo) → hanya approver, bukan pembuat refund
    const denied = role !== 'approver' ? 'rejected_role' : sameActor(rf.initiated_by, actor) ? 'rejected_same_actor' : null;
    if (denied) {
      const p0 = await this.repo.getPart(rf.part_id);
      await this.repo.tx(async (r) =>
        r.audit({ action: 'refund_settle', actor, reason: scrubReason(note), order_id: p0?.order_id ?? null, part_id: rf.part_id, refund_id: rf.id, amount: rf.amount, outcome: denied }),
      );
      this.logAudit({ action: 'refund_settle', actor, refund_id: rf.id, outcome: denied });
      throw new AdminError(
        denied === 'rejected_role'
          ? 'settle manual refund hanya untuk peran approver (grup efn-cs-approver)'
          : 'penyetuju tidak boleh sama dengan pembuat/pemicu refund (empat mata)',
      );
    }
    if (!['manual_required', 'failed', 'gateway_pending', 'gateway_done'].includes(rf.status)) throw new AdminError(`status refund ${rf.status} tidak dapat di-settle manual`);
    // SEC-98: refund yang gagal karena melebihi porsi tidak boleh di-settle
    if (rf.failure_reason === 'refund_exceeds_portion') throw new AdminError('refund ditolak karena melebihi porsi; tidak dapat di-settle');
    const part = await this.repo.getPart(rf.part_id);
    if (!part) throw new AdminError('bagian hilang');
    if (rf.status !== 'gateway_done') {
      const out = (await this.repo.refundsOfPart(part.id)).filter(
        (x) => x.id !== rf.id && ['gateway_in_flight', 'gateway_pending', 'gateway_done', 'settled'].includes(x.status),
      );
      const committed = out.reduce((s, x) => s + (x.amount ?? 0), 0);
      if ((rf.amount ?? 0) + committed > part.amount_payable) throw new AdminError('refund melebihi porsi bagian');
    }
    await this.saga.markGatewayDone(rf.id, providerRef);
    const msg = refundRequestedMessage(rf.id);
    // Langsung lanjutkan (refund_settled Odoo + settled); idempoten dengan consumer
    const outcome = await this.saga.handleRefundRequested({ ...msg, idempotency_key: `${msg.idempotency_key}:manual` });
    const fresh = await this.repo.getRefund(rf.id);
    await this.repo.tx(async (r) =>
      r.audit({ action: 'refund_settle', actor, reason: scrubReason(note), order_id: part.order_id, part_id: part.id, refund_id: rf.id, amount: rf.amount, outcome: fresh?.status ?? outcome }),
    );
    this.logAudit({ action: 'refund_settle', actor, refund_id: rf.id, outcome: fresh?.status ?? outcome });
    return { status: fresh?.status ?? 'unknown' };
  }

  /** Bagian `review`: `cancel` → kompensasi review (batal Odoo + refund); `sync` → tarik state Odoo dan terapkan. */
  async resolve(partId: string, action: 'cancel' | 'sync', operator: string | Operator, note: string): Promise<{ status: string }> {
    const { actor, role } = toOperator(operator);
    this.checkActor(actor, note);
    // SEC-107: batal bagian review memicu refund → hanya approver
    if (action === 'cancel' && role !== 'approver') throw new AdminError('resolve --action=cancel (memicu refund) hanya untuk peran approver');
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
        reason: scrubReason(note),
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
