/**
 * Settle refund manual (docs/04 §10.6.6): gateway tidak mendukung refund → Finance mengembalikan dana manual lalu:
 *
 *   node dist/cli/online-refund-settle.js --refund=<refund_id> --provider-ref=<ref transfer> --reason="<alasan>"   (operator = identitas OS/host)
 *
 * → `gateway_done` (+ `refunded_amount` bagian) → `efn_online_refund_settled` (bila ada nota kredit Odoo) → `settled`.
 * Teraudit (`online_admin_audit`). Exit 0 sukses, 1 gagal, 2 argumen salah.
 */
import { isUuid } from '../common/validation';
import { Operator } from '../orders/online-admin.service';
import { cliMain, parseFlags, reasonOf, required } from './online-cli';

export function parseSettleArgs(argv: readonly string[], op: Operator, stdinReason?: string): { refundId: string; providerRef: string; operator: Operator; note: string } {
  const f = parseFlags(argv, ['refund', 'provider-ref', 'reason', 'reason-stdin']);
  const refundId = required(f, 'refund');
  if (!isUuid(refundId)) throw new Error('--refund harus UUID');
  return { refundId: refundId.toLowerCase(), providerRef: required(f, 'provider-ref'), operator: op, note: reasonOf(f, stdinReason) };
}

if (require.main === module) {
  void cliMain('online-refund-settle', process.argv.slice(2), parseSettleArgs, (cli, a) => cli.admin.settleManual(a.refundId, a.providerRef, a.operator, a.note));
}
