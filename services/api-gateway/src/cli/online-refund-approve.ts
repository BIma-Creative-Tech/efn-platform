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
import { operatorIdentity, parseFlags, required, runCli } from './online-cli';

export function parseApproveArgs(argv: readonly string[], actor: string = operatorIdentity()): { refundId: string; actor: string; note: string } {
  const f = parseFlags(argv, ['refund', 'reason']);
  const refundId = required(f, 'refund');
  if (!isUuid(refundId)) throw new Error('--refund harus UUID');
  return { refundId: refundId.toLowerCase(), actor, note: required(f, 'reason') };
}

if (require.main === module) {
  let a: ReturnType<typeof parseApproveArgs>;
  try {
    a = parseApproveArgs(process.argv.slice(2));
  } catch (err) {
    process.stderr.write(`${err instanceof Error ? err.message : String(err)}\n`);
    process.exit(2);
  }
  void runCli('online-refund-approve', (cli) => cli.admin.approveRefund(a.refundId, a.actor, a.note));
}
