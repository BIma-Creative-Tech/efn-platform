import { PII_AAD, piiAad } from './crypto';

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
  /**
   * SEC-102: AAD per baris (mis. terikat id). Bila ada, re-encrypt menulis dengan AAD ini dan membaca dengan AAD ini
   * lalu `aad` (format lama) sebagai cadangan.
   */
  rowAad?: (pk: string) => string;
}

export const PII_COLUMNS: readonly PiiColumn[] = [
  { table: 'price_hunter_report', column: 'user_enc', pk: 'id', aad: PII_AAD.PRICE_HUNTER_REPORT_USER },
  { table: 'loyalty_ledger', column: 'member_ref_enc', pk: 'id', aad: PII_AAD.LOYALTY_LEDGER_MEMBER_REF },
  { table: 'otp_audit', column: 'ip_enc', pk: 'id', aad: PII_AAD.OTP_AUDIT_IP },
  { table: 'member', column: 'member_ref_enc', pk: 'id', aad: PII_AAD.MEMBER_REF },
  { table: 'consent', column: 'ip_enc', pk: 'id', aad: PII_AAD.CONSENT_IP },
  // L-13 (docs/04 §10.17): kode ambil online ikut rotasi/re-encrypt & cek kunci pensiun saat start
  {
    table: 'online_order_part',
    column: 'pickup_code_enc',
    pk: 'id',
    aad: PII_AAD.ONLINE_PICKUP_CODE,
    rowAad: (pk) => `efn:online_order_part.pickup_code_enc:v2:${pk.toLowerCase()}`,
  },
  // G20 §12.2 no. 3: kode bayar di kasir (diperlakukan seperti kode ambil)
  {
    table: 'online_order_part',
    column: 'counter_code_enc',
    pk: 'id',
    aad: PII_AAD.ONLINE_COUNTER_CODE,
    rowAad: (pk) => `efn:online_order_part.counter_code_enc:v2:${pk.toLowerCase()}`,
  },
  // G27 §18.2: token push Expo (pengenal perangkat = data pribadi)
  { table: 'push_token', column: 'token_enc', pk: 'id', aad: PII_AAD.PUSH_TOKEN },
  // G50 (docs/04 §28.7/§28.9): alamat email member (ADR-048) dan kode voucher kampanye yang ditugaskan BFF.
  // SEC-G50-11 (pola SEC-102): AAD v2 terikat member_id (= notification/email/email.constants.ts memberEmailAad);
  // `aad` v1 = format lama, dibaca re-encrypt sebagai cadangan lalu ditulis ulang ke v2.
  {
    table: 'member_email',
    column: 'email_enc',
    pk: 'member_id',
    aad: piiAad('member_email', 'email_enc'),
    rowAad: (pk) => `efn:member_email.email_enc:v2:${pk.toLowerCase()}`,
  },
  // SEC-G50-11 (pola SEC-102): kode voucher bearer terikat id baris; v1 kolom hanya dibaca (cadangan)
  {
    table: 'member_voucher',
    column: 'code_enc',
    pk: 'id',
    aad: piiAad('member_voucher', 'code_enc'),
    rowAad: (pk) => `efn:member_voucher.code_enc:v2:${pk.toLowerCase()}`,
  },
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
