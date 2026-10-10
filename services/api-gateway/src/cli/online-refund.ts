/**
 * Refund CS online (L-3, docs/04 §10.6.6) — teraudit (`platform.online_admin_audit` + log `EFN_ONLINE_CS_AUDIT`).
 *
 *   node dist/cli/online-refund.js --part=<part_id> (--full | --lines=<product_id>:<qty>,...) \
 *     --reason="<alasan>" [--kind=customer_cs|ops_cancelled] [--restock] [--refund-id=<uuid untuk mengulang>]
 *
 * `efn_online_refund` (Odoo, sinkron, nominal = nota kredit) → `online_refund(odoo_done)` → outbox
 * `order.refund.requested` → BFF refund gateway → `refund_settled`. Ulangi dengan `--refund-id` yang dicetak bila
 * jaringan putus (idempoten `online:refund:<refund_id>`). Exit 0 sukses, 1 gagal, 2 argumen salah.
 */
import { isUuid } from '../common/validation';
import { Operator } from '../orders/online-admin.service';
import { cliMain, parseFlags, reasonOf, required } from './online-cli';

export function parseRefundArgs(argv: readonly string[], op: Operator, stdinReason?: string): {
  partId: string;
  refundId?: string;
  lines: { product_id: number; qty: number }[] | null;
  full: boolean;
  restock: boolean;
  reason: 'customer_cs' | 'ops_cancelled';
  actor: string;
  role: Operator['role'];
  note: string;
} {
  // SEC-98: tidak ada --actor (identitas dari OS/host)
  const f = parseFlags(argv, ['part', 'full', 'lines', 'reason', 'reason-stdin', 'kind', 'restock', 'refund-id']);
  const partId = required(f, 'part');
  if (!isUuid(partId)) throw new Error('--part harus UUID');
  const refundId = f.get('refund-id');
  if (refundId !== undefined && !isUuid(refundId)) throw new Error('--refund-id harus UUID');
  const full = f.get('full') === 'true';
  let lines: { product_id: number; qty: number }[] | null = null;
  if (f.has('lines')) {
    const raw = required(f, 'lines');
    if (!/^\d{1,10}:\d{1,4}(,\d{1,10}:\d{1,4}){0,49}$/.test(raw)) throw new Error('--lines format <product_id>:<qty>,...');
    lines = raw.split(',').map((x) => {
      const [p, q] = x.split(':').map(Number);
      if (p < 1 || q < 1) throw new Error('--lines berisi nilai tidak valid');
      return { product_id: p, qty: q };
    });
  }
  if (full === (lines !== null)) throw new Error('pilih salah satu: --full atau --lines');
  const kind = f.get('kind') ?? 'customer_cs';
  if (kind !== 'customer_cs' && kind !== 'ops_cancelled') throw new Error('--kind customer_cs|ops_cancelled');
  return {
    partId: partId.toLowerCase(),
    ...(refundId ? { refundId: refundId.toLowerCase() } : {}),
    lines,
    full,
    restock: f.get('restock') === 'true',
    reason: kind,
    actor: op.actor,
    role: op.role,
    note: reasonOf(f, stdinReason),
  };
}

if (require.main === module) {
  void cliMain('online-refund', process.argv.slice(2), parseRefundArgs, (cli, a) => cli.admin.refund(a));
}
