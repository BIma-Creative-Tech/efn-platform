import { WA_TEMPLATES } from './contract';

/**
 * Teks pesan WA (Bahasa Indonesia). Setiap pesan WAJIB memuat cara berhenti "Balas STOP" (ADR-044 §3.8).
 * Teks final = Marketing + Legal; isi ini placeholder aman. Label pengirim dari `WA_UNOFFICIAL_SENDER_LABEL`
 * (profil nomor menyebut "Promo <brand>", ADR-044 §3.3).
 */
export const STOP_FOOTER = 'Balas STOP untuk berhenti menerima pesan promo ini.';

export function renderWaText(template: string, p: Readonly<Record<string, string>>, senderLabel: string): string {
  if (!Object.prototype.hasOwnProperty.call(WA_TEMPLATES, template)) throw new Error('template WA tidak dikenal');
  const lines: string[] = [`*${senderLabel}*`];
  switch (template) {
    case 'promo_generic':
      lines.push(p.title, '', p.body);
      if (p.voucher_code) lines.push(`Kode voucher: ${p.voucher_code}`);
      if (p.valid_to) lines.push(`Berlaku sampai ${p.valid_to}.`);
      break;
    case 'markdown_near_expiry':
      lines.push(`${p.product_name} sedang diskon di ${p.outlet_name}, stok terbatas.`);
      lines.push(p.valid_to ? `Berlaku sampai ${p.valid_to} atau selama persediaan ada.` : 'Berlaku selama persediaan ada.');
      break;
    case 'restock_available':
      lines.push(`${p.product_name} yang Anda tunggu sudah tersedia lagi di ${p.outlet_name}.`);
      break;
    case 'price_drop':
      lines.push(`Harga ${p.product_name} turun menjadi Rp${p.new_price_idr}. Harga dapat berubah sewaktu-waktu.`);
      break;
    default:
      throw new Error('template WA tidak dikenal');
  }
  lines.push('', STOP_FOOTER);
  return lines.join('\n');
}

/** Teks masuk yang dianggap permintaan berhenti (dinormalisasi NFKC, huruf besar, tanpa tanda baca). */
const STOP_WORDS = new Set(['STOP', 'BERHENTI', 'UNSUBSCRIBE', 'STOP PROMO', 'BERHENTI PROMO']);

export function isStopText(text: unknown): boolean {
  if (typeof text !== 'string' || text.length === 0 || text.length > 64) return false;
  const norm = text
    .normalize('NFKC')
    .toUpperCase()
    .replace(/[^A-Z ]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
  return STOP_WORDS.has(norm);
}
