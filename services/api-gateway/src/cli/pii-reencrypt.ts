/**
 * CLI re-encrypt kolom PII ke versi kunci aktif (U2, runbook `docs/runbooks/pii-key-rotation.md` langkah 4).
 *
 *   node dist/cli/pii-reencrypt.js [--dry-run] [--batch-size=500] [--columns=member.member_ref_enc,consent.ip_enc]
 *
 * Membaca konfigurasi & secret persis seperti BFF (`*_FILE` → /run/secrets, TLS verify-full ke PostgreSQL), memakai
 * user DB `efn_bff`. Key ring harus memuat kunci LAMA dan BARU; `PII_ACTIVE_KEY_VERSION` = versi baru.
 * Idempotent: menjalankan ulang setelah selesai = tidak ada perubahan. Exit 0 bila semua baris terbaca, 1 bila ada
 * baris yang gagal didekripsi (atau error), 2 untuk argumen salah. Output hanya angka.
 * Jalankan di container api-gateway: `docker compose exec api-gateway node dist/cli/pii-reencrypt.js --dry-run`.
 */
import { AppConfig } from '../config/app-config';
import { createPgPool } from '../common/db/pg.provider';
import { PgDb } from '../common/db/db';
import { PiiCipher } from '../common/pii/crypto';
import { findPiiColumns } from '../common/pii/pii-columns';
import { ColumnReport, reencryptColumn } from '../common/pii/reencrypt';

interface CliArgs {
  dryRun: boolean;
  batchSize: number | undefined;
  columns: string | undefined;
}

export function parseArgs(argv: readonly string[]): CliArgs {
  const out: CliArgs = { dryRun: false, batchSize: undefined, columns: undefined };
  for (const a of argv) {
    if (a === '--dry-run') out.dryRun = true;
    else if (a.startsWith('--batch-size=')) {
      const n = Number(a.slice('--batch-size='.length));
      if (!Number.isInteger(n) || n < 1 || n > 10_000) throw new Error('--batch-size harus 1..10000');
      out.batchSize = n;
    } else if (a.startsWith('--columns=')) out.columns = a.slice('--columns='.length);
    else throw new Error(`argumen tidak dikenal: ${a}`);
  }
  return out;
}

function line(r: ColumnReport): string {
  const before = Object.entries(r.before)
    .map(([v, n]) => `v${v}=${n}`)
    .join(' ');
  return `${r.column}: sebelum[${before || 'kosong'}] reencrypted=${r.reencrypted} skipped_concurrent=${r.skippedConcurrent} failed=${r.failed}`;
}

async function main(): Promise<number> {
  let args: CliArgs;
  try {
    args = parseArgs(process.argv.slice(2));
  } catch (err) {
    process.stderr.write(`${err instanceof Error ? err.message : String(err)}\n`);
    return 2;
  }
  const config = AppConfig.fromProcessEnv();
  const cipher = PiiCipher.fromConfig(config);
  const columns = findPiiColumns(args.columns);
  const pool = createPgPool(config);
  const db = new PgDb(pool);
  let failed = 0;
  try {
    process.stdout.write(`pii-reencrypt: versi aktif v${cipher.activeVersion}, kunci dimuat [${cipher.loadedVersions.join(',')}]${args.dryRun ? ' (dry-run)' : ''}\n`);
    for (const c of columns) {
      const r = await reencryptColumn(db, cipher, c, args.batchSize ?? config.piiReencryptBatchSize, args.dryRun);
      failed += r.failed;
      process.stdout.write(`${line(r)}\n`);
    }
  } finally {
    await pool.end().catch(() => undefined);
  }
  return failed > 0 ? 1 : 0;
}

if (require.main === module) {
  main()
    .then((code) => {
      process.exitCode = code;
    })
    .catch((err: unknown) => {
      process.stderr.write(`pii-reencrypt gagal: ${err instanceof Error ? err.message : String(err)}\n`);
      process.exitCode = 1;
    });
}
