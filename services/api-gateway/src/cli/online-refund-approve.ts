/**
 * Persetujuan CS atas refund inisiatif Odoo (SEC-95, L-16 — pemisahan tugas): event `cancelled`/`refunded` dari Odoo atas
 * bagian berbayar TIDAK me-refund otomatis; refund berstatus `approval_required` sampai disetujui:
 *
 *   node dist/cli/online-refund-approve.js --refund=<refund_id> --reason="<alasan / no tiket>"   (operator = identitas OS/host)
 *
 * → `odoo_done` + outbox `order.refund.requested` (gateway → refund_settled). Porsi dicek ulang. Teraudit
 * (`online_admin_audit` aksi `refund_approve`). Exit 0 sukses, 1 gagal, 2 argumen salah.
 */
import { isUuid } from '../common/validation';
import { Operator } from '../orders/online-admin.service';
import { cliMain, parseFlags, reasonOf, required } from './online-cli';

export function parseApproveArgs(argv: readonly string[], op: Operator, stdinReason?: string): { refundId: string; operator: Operator; note: string } {
  const f = parseFlags(argv, ['refund', 'reason', 'reason-stdin']);
  const refundId = required(f, 'refund');
  if (!isUuid(refundId)) throw new Error('--refund harus UUID');
  return { refundId: refundId.toLowerCase(), operator: op, note: reasonOf(f, stdinReason) };
}

if (require.main === module) {
  void cliMain('online-refund-approve', process.argv.slice(2), parseApproveArgs, (cli, a) => cli.admin.approveRefund(a.refundId, a.operator, a.note));
}
