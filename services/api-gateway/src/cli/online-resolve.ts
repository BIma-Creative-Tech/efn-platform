/**
 * Selesaikan bagian `review` (docs/04 §10.6.6):
 *
 *   node dist/cli/online-resolve.js --part=<part_id> --action=cancel|sync --reason="<alasan>"   (operator = identitas OS/host)
 *
 * `cancel` → `efn_online_cancel(reason=review)` → bagian `cancelled` + refund (nota kredit Odoo bila ada, selain itu
 * porsi penuh bila sudah dibayar). `sync` → tarik `efn_online_status` dan terapkan (mis. ops sudah mengonfirmasi di
 * Odoo). Teraudit. Exit 0 sukses, 1 gagal, 2 argumen salah.
 */
import { isUuid } from '../common/validation';
import { Operator } from '../orders/online-admin.service';
import { cliMain, parseFlags, reasonOf, required } from './online-cli';

export function parseResolveArgs(argv: readonly string[], op: Operator, stdinReason?: string): { partId: string; action: 'cancel' | 'sync'; operator: Operator; note: string } {
  const f = parseFlags(argv, ['part', 'action', 'reason', 'reason-stdin']);
  const partId = required(f, 'part');
  if (!isUuid(partId)) throw new Error('--part harus UUID');
  const action = required(f, 'action');
  if (action !== 'cancel' && action !== 'sync') throw new Error('--action cancel|sync');
  return { partId: partId.toLowerCase(), action, operator: op, note: reasonOf(f, stdinReason) };
}

if (require.main === module) {
  void cliMain('online-resolve', process.argv.slice(2), parseResolveArgs, (cli, a) => cli.admin.resolve(a.partId, a.action, a.operator, a.note));
}
