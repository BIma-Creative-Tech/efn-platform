import { Errors } from '../common/errors/api-error';
import { InvoiceBuyerProfile } from '../odoo-rpc/odoo-online.types';

/**
 * G21 docs/04 §13.5 (W-06/E-39, L-63, K-36-7): `invoice_request` opsional pada `POST /v1/orders`.
 *
 * Validasi BFF (sumber kebenaran; frontend hanya UX): NPWP 16 digit, NIK 16 digit, paspor 5–20 huruf/angka (ASUMSI —
 * sama dengan `superapp-web/lib/invoice-request.ts`, kontrak tidak merinci), nama 2–100, alamat 5–255, email RFC dasar
 * (≤ 254). Tepat lima field (field lain → 400). Pesan error generik — TIDAK PERNAH memuat nilai yang dikirim.
 *
 * BFF TIDAK menyimpan isi: tidak masuk DB (termasuk `request_sha256` — hanya penanda boolean "ada permintaan faktur"),
 * outbox, Redis, maupun log. Diteruskan sekali ke Odoo `efn_online_set_invoice_party` lalu dilepas.
 */
export const INVOICE_REQUEST_KEYS = ['buyer_type', 'tax_id', 'name', 'address', 'email'] as const;
/** §13.5: hanya kanal PT PPN. PT F&B (PB, BCF, WTZ §16.2; PB1) dan PT lain → `422 invoice_not_available`. */
export const INVOICE_COMPANIES: readonly string[] = ['GL', 'BCR'];

// Selaras dengan Odoo `efn_online_common.EMAIL_RE` (lokal ≤ 64, TLD ≥ 2): tanpa ini email lolos BFF tetapi ditolak Odoo
// (invalid_input) sehingga faktur atas nama diam-diam jatuh ke CS.
const EMAIL_RE = /^[^\s@]{1,64}@[^\s@]+\.[^\s@]{2,}$/;
/** Kontrol & karakter format (termasuk pemisah baris/arah teks) ditolak di nama/alamat — cegah injeksi ke dokumen pajak. */
// eslint-disable-next-line no-control-regex -- sengaja mencocokkan karakter kontrol untuk DITOLAK
const CONTROL_RE = /[\u0000-\u001f\u007f-\u009f\u200b-\u200f\u2028-\u202e\u2066-\u2069]/;

function text(v: unknown, min: number, max: number): string {
  if (typeof v !== 'string') throw Errors.invalidRequest();
  const s = v.normalize('NFC').trim().replace(/ {2,}/g, ' ');
  if ([...s].length < min || [...s].length > max || CONTROL_RE.test(s)) throw Errors.invalidRequest();
  return s;
}

/** `undefined`/`null` → null (tidak ada permintaan faktur). Bentuk salah → 400 `invalid_request`. */
export function parseInvoiceRequest(v: unknown): InvoiceBuyerProfile | null {
  if (v === undefined || v === null) return null;
  if (typeof v !== 'object' || Array.isArray(v)) throw Errors.invalidRequest();
  const o = v as Record<string, unknown>;
  if (Object.keys(o).some((k) => !(INVOICE_REQUEST_KEYS as readonly string[]).includes(k))) throw Errors.invalidRequest();
  const type = o.buyer_type;
  if (type !== 'npwp' && type !== 'nik' && type !== 'passport') throw Errors.invalidRequest();
  if (typeof o.tax_id !== 'string' || o.tax_id.length > 40) throw Errors.invalidRequest();
  let taxId: string;
  if (type === 'passport') {
    taxId = o.tax_id.trim().toUpperCase();
    if (!/^[A-Z0-9]{5,20}$/.test(taxId)) throw Errors.invalidRequest();
  } else {
    taxId = o.tax_id.replace(/[\s.-]/g, '');
    if (!/^\d{16}$/.test(taxId)) throw Errors.invalidRequest();
  }
  const name = text(o.name, 2, 100);
  const address = text(o.address, 5, 255);
  if (typeof o.email !== 'string') throw Errors.invalidRequest();
  const email = o.email.trim();
  if (email.length > 254 || !EMAIL_RE.test(email) || CONTROL_RE.test(email)) throw Errors.invalidRequest();
  return { buyer_type: type, tax_id: taxId, name, address, email };
}
