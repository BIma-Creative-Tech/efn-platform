/**
 * W-18 (Q-P18): daftar kerja CS — penghapusan akun yang tertahan pesanan online & bagian terbuka tanpa tautan akun:
 *
 *   node dist/cli/online-erasure-pending.js   (operator = identitas OS/host; read-only, tanpa alasan)
 *
 * Output: `{ deferred: [{ request_id, requested_at, scheduled_at, erasure_deadline, escalation, part_ids }],
 * unlinked_open_parts: [{ part_id, order_id, status, company_code, outlet_code, member_unlinked_at }] }` — tanpa PII
 * (tanpa nomor HP, member_id, member_hash, kode ambil). Tindak lanjut: `online-refund --part=… --full` /
 * `online-resolve` / serah terima di Odoo setelah verifikasi lewat kanal lain. Exit 0 sukses, 1 gagal, 2 argumen salah.
 * Tidak membaca stdin (tidak ada alasan; tidak mengubah data).
 */
import { listErasurePending } from '../privacy/erasure-pending';
import { operatorIdentity, parseFlags, runCli } from './online-cli';

export function parseErasurePendingArgs(argv: readonly string[]): Record<string, never> {
  parseFlags(argv, []);
  return {};
}

if (require.main === module) {
  try {
    parseErasurePendingArgs(process.argv.slice(2));
    operatorIdentity();
  } catch (err) {
    process.stderr.write(`${err instanceof Error ? err.message : String(err)}\n`);
    process.exitCode = 2;
  }
  if (process.exitCode !== 2) {
    void runCli('online-erasure-pending', async (cli) => ({ ...(await listErasurePending(cli.db, cli.config.dsarOpenOrderMaxDeferDays)) }));
  }
}
