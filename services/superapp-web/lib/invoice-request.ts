/**
 * Faktur pajak atas nama pembeli (docs/04 §13.5, L-63, W-06) — logika murni, dipakai klien dan Route Handler.
 *
 * - Validasi di klien hanya untuk UX; BFF tetap sumber validasi (NPWP/NIK 16 digit, nama 2–100, alamat 5–255, email).
 * - Isi form TIDAK PERNAH disimpan di storage/URL/log/analytics (K-36-7); modul ini tidak punya efek samping.
 * - Hanya keranjang yang semua bagiannya kanal PT PPN (GL, BCR) yang menawarkan opsi ini.
 */
import { allPpnCompanies } from './brands';

export type InvoiceBuyerType = 'npwp' | 'nik' | 'passport';

export interface InvoiceRequestInput {
  buyer_type: InvoiceBuyerType;
  tax_id: string;
  name: string;
  address: string;
  email: string;
}

export type InvoiceField = keyof InvoiceRequestInput;
export type InvoiceErrors = Partial<Record<InvoiceField, string>>;

export const BUYER_TYPE_LABEL: Record<InvoiceBuyerType, string> = {
  npwp: 'NPWP',
  nik: 'NIK',
  passport: 'Paspor',
};

export const TAX_ID_LABEL: Record<InvoiceBuyerType, string> = {
  npwp: 'Nomor NPWP (16 digit)',
  nik: 'Nomor NIK (16 digit)',
  passport: 'Nomor paspor',
};

export const EMPTY_INVOICE_FORM: InvoiceRequestInput = { buyer_type: 'npwp', tax_id: '', name: '', address: '', email: '' };

export const INVOICE_NOT_AVAILABLE_MESSAGE = 'Faktur atas nama belum tersedia untuk pesanan ini';

export const INVOICE_PRIVACY_NOTICE =
  'Data ini hanya dipakai untuk menerbitkan faktur pajak atas nama Anda. Data tidak disimpan di perangkat ini dan dihapus dari layar setelah pesanan dibuat. Faktur pajak disimpan sesuai aturan perpajakan (10 tahun).';

// Selaras dengan BFF `invoice-request.ts` dan Odoo `efn_online_common.EMAIL_RE` (lokal ≤ 64, TLD ≥ 2).
const EMAIL_RE = /^[^\s@]{1,64}@[^\s@]+\.[^\s@]{2,}$/;

/** Hapus pemisah umum (titik, strip, spasi) pada NPWP/NIK; paspor hanya dipangkas dan dijadikan huruf besar. */
export function normalizeTaxId(type: InvoiceBuyerType, raw: string): string {
  if (type === 'passport') return raw.trim().toUpperCase();
  return raw.replace(/[\s.\-]/g, '');
}

/** Kesalahan per kolom (kosong = valid). Pesan Bahasa Indonesia, tidak memuat nilai yang diketik. */
export function validateInvoiceRequest(input: InvoiceRequestInput): InvoiceErrors {
  const errors: InvoiceErrors = {};
  if (input.buyer_type !== 'npwp' && input.buyer_type !== 'nik' && input.buyer_type !== 'passport') {
    errors.buyer_type = 'Pilih jenis identitas.';
  } else {
    const id = normalizeTaxId(input.buyer_type, input.tax_id);
    if (input.buyer_type === 'passport') {
      if (!/^[A-Z0-9]{5,20}$/.test(id)) errors.tax_id = 'Nomor paspor 5–20 huruf atau angka.';
    } else if (!/^\d{16}$/.test(id)) {
      errors.tax_id = `${BUYER_TYPE_LABEL[input.buyer_type]} harus 16 digit angka.`;
    }
  }
  const name = typeof input.name === 'string' ? input.name.trim() : '';
  if (name.length < 2 || name.length > 100) errors.name = 'Nama 2–100 karakter.';
  const address = typeof input.address === 'string' ? input.address.trim() : '';
  if (address.length < 5 || address.length > 255) errors.address = 'Alamat 5–255 karakter.';
  const email = typeof input.email === 'string' ? input.email.trim() : '';
  if (email.length > 254 || !EMAIL_RE.test(email)) errors.email = 'Alamat email tidak valid.';
  return errors;
}

/** Bentuk kiriman ke BFF: tepat lima field, sudah dinormalkan. Panggil hanya setelah validasi lolos. */
export function buildInvoiceRequest(input: InvoiceRequestInput): InvoiceRequestInput {
  return {
    buyer_type: input.buyer_type,
    tax_id: normalizeTaxId(input.buyer_type, input.tax_id),
    name: input.name.trim(),
    address: input.address.trim(),
    email: input.email.trim(),
  };
}

/**
 * Parse `invoice_request` dari body tak tepercaya (Route Handler). Mengembalikan null bila bukan objek lima field
 * bertipe string yang lolos validasi; field lain dibuang (tidak diteruskan).
 */
export function parseInvoiceRequest(raw: unknown): InvoiceRequestInput | null {
  if (raw === null || typeof raw !== 'object' || Array.isArray(raw)) return null;
  const o = raw as Record<string, unknown>;
  const { buyer_type, tax_id, name, address, email } = o;
  if (typeof tax_id !== 'string' || typeof name !== 'string' || typeof address !== 'string' || typeof email !== 'string') return null;
  if (buyer_type !== 'npwp' && buyer_type !== 'nik' && buyer_type !== 'passport') return null;
  const input: InvoiceRequestInput = { buyer_type, tax_id, name, address, email };
  if (Object.keys(validateInvoiceRequest(input)).length > 0) return null;
  return buildInvoiceRequest(input);
}

/** Opsi faktur hanya ditawarkan bila SEMUA bagian keranjang adalah kanal PT PPN (GL/BCR). */
export function invoiceRequestAvailable(companyCodes: readonly string[]): boolean {
  return allPpnCompanies(companyCodes);
}
