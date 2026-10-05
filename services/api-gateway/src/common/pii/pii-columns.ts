import { PII_AAD } from './crypto';

/**
 * Daftar SEMUA kolom `*_enc` di skema `platform` (docs/04 §7.2, §8.2, §8.4). Dipakai oleh job/CLI re-encrypt
 * (rotasi kunci, U2) dan pemeriksaan key ring saat start. Kolom baru terenkripsi WAJIB ditambahkan di sini.
 *
 * Nama tabel/kolom/pk adalah konstanta kode (bukan input) sehingga aman dirangkai ke teks SQL.
 */
export interface PiiColumn {
  table: string;
  column: string;
  pk: string;
  aad: string;
}

export const PII_COLUMNS: readonly PiiColumn[] = [
  { table: 'price_hunter_report', column: 'user_enc', pk: 'id', aad: PII_AAD.PRICE_HUNTER_REPORT_USER },
  { table: 'loyalty_ledger', column: 'member_ref_enc', pk: 'id', aad: PII_AAD.LOYALTY_LEDGER_MEMBER_REF },
  { table: 'otp_audit', column: 'ip_enc', pk: 'id', aad: PII_AAD.OTP_AUDIT_IP },
  { table: 'member', column: 'member_ref_enc', pk: 'id', aad: PII_AAD.MEMBER_REF },
  { table: 'consent', column: 'ip_enc', pk: 'id', aad: PII_AAD.CONSENT_IP },
];

const IDENT_RE = /^[a-z_][a-z0-9_]*$/;

/** Pertahanan tambahan: identifier harus snake_case sederhana sebelum dirangkai ke SQL. */
export function assertPiiColumn(c: PiiColumn): void {
  if (![c.table, c.column, c.pk].every((s) => IDENT_RE.test(s))) throw new Error('identifier kolom PII tidak valid');
}

export function findPiiColumns(filter: string | undefined): PiiColumn[] {
  if (!filter) return [...PII_COLUMNS];
  const wanted = new Set(filter.split(',').map((s) => s.trim()).filter((s) => s.length > 0));
  const out = PII_COLUMNS.filter((c) => wanted.has(`${c.table}.${c.column}`));
  if (out.length !== wanted.size) throw new Error(`kolom tidak dikenal; pilihan: ${PII_COLUMNS.map((c) => `${c.table}.${c.column}`).join(', ')}`);
  return out;
}
