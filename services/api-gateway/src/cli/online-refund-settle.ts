/**
 * Settle refund manual (docs/04 §10.6.6): gateway tidak mendukung refund → Finance mengembalikan dana manual lalu:
 *
 *   node dist/cli/online-refund-settle.js --refund=<refund_id> --provider-ref=<ref transfer> --reason="<alasan>"   (operator = identitas OS/host)
 *
 * → `gateway_done` (+ `refunded_amount` bagian) → `efn_online_refund_settled` (bila ada nota kredit Odoo) → `settled`.
 * Teraudit (`online_admin_audit`). Exit 0 sukses, 1 gagal, 2 argumen salah.
 */
import { isUuid } from '../common/validation';
import { operatorIdentity, parseFlags, required, runCli } from './online-cli';

export function parseSettleArgs(argv: readonly string[], actor: string = operatorIdentity()): { refundId: string; providerRef: string; actor: string; note: string } {
  const f = parseFlags(argv, ['refund', 'provider-ref', 'reason']);
  const refundId = required(f, 'refund');
  if (!isUuid(refundId)) throw new Error('--refund harus UUID');
  return { refundId: refundId.toLowerCase(), providerRef: required(f, 'provider-ref'), actor, note: required(f, 'reason') };
}

if (require.main === module) {
  let a: ReturnType<typeof parseSettleArgs>;
  try {
    a = parseSettleArgs(process.argv.slice(2));
  } catch (err) {
    process.stderr.write(`${err instanceof Error ? err.message : String(err)}\n`);
    process.exit(2);
  }
  void runCli('online-refund-settle', (cli) => cli.admin.settleManual(a.refundId, a.providerRef, a.actor, a.note));
}
