/**
 * Selesaikan bagian `review` (docs/04 §10.6.6):
 *
 *   node dist/cli/online-resolve.js --part=<part_id> --action=cancel|sync --actor=<petugas> --reason="<alasan>"
 *
 * `cancel` → `efn_online_cancel(reason=review)` → bagian `cancelled` + refund (nota kredit Odoo bila ada, selain itu
 * porsi penuh bila sudah dibayar). `sync` → tarik `efn_online_status` dan terapkan (mis. ops sudah mengonfirmasi di
 * Odoo). Teraudit. Exit 0 sukses, 1 gagal, 2 argumen salah.
 */
import { isUuid } from '../common/validation';
import { parseFlags, required, runCli } from './online-cli';

export function parseResolveArgs(argv: readonly string[]): { partId: string; action: 'cancel' | 'sync'; actor: string; note: string } {
  const f = parseFlags(argv, ['part', 'action', 'actor', 'reason']);
  const partId = required(f, 'part');
  if (!isUuid(partId)) throw new Error('--part harus UUID');
  const action = required(f, 'action');
  if (action !== 'cancel' && action !== 'sync') throw new Error('--action cancel|sync');
  return { partId: partId.toLowerCase(), action, actor: required(f, 'actor'), note: required(f, 'reason') };
}

if (require.main === module) {
  let a: ReturnType<typeof parseResolveArgs>;
  try {
    a = parseResolveArgs(process.argv.slice(2));
  } catch (err) {
    process.stderr.write(`${err instanceof Error ? err.message : String(err)}\n`);
    process.exit(2);
  }
  void runCli('online-resolve', (cli) => cli.admin.resolve(a.partId, a.action, a.actor, a.note));
}
