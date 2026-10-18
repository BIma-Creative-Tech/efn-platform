/**
 * Kode promo online (docs/04 §11.2, W-09/E-36). Satu sumber aturan bentuk untuk body klien (`POST /v1/cart/quote`)
 * dan hasil Odoo (`promo_applied`/`promo_rejected`).
 *
 * - Bentuk: `^[A-Za-z0-9_-]{1,40}$`, dinormalisasi HURUF BESAR, duplikat dibuang, maks 3 per quote.
 * - Kode TIDAK PERNAH di-log/di-metrik, baik asli maupun turunannya (hash/tag). SEC-132 (L-42): tag 8 heks SHA-256
 *   tanpa kunci dihapus — ruang kode ~32 bit bisa dibalik dengan brute force. Log & metrik hanya memuat JUMLAH.
 * - Pesan ke klien tidak membedakan penyebab kode tidak terpakai (tidak dikenal/kedaluwarsa/kuota/bukan brand ini).
 */
export const PROMO_CODE_RE = /^[A-Za-z0-9_-]{1,40}$/;
export const PROMO_CODES_MAX = 3;

export class PromoCodesInvalid extends Error {
  constructor() {
    super('promo_codes tidak valid');
    this.name = 'PromoCodesInvalid';
  }
}

/**
 * Normalisasi `promo_codes` dari klien: `undefined`/`null` → `[]`; selain array → invalid; > 3 elemen (sebelum dedupe,
 * agar array besar tidak pernah diproses) → invalid; elemen bukan string / tidak cocok regex → invalid.
 */
export function normalizePromoCodes(raw: unknown): string[] {
  if (raw === undefined || raw === null) return [];
  if (!Array.isArray(raw) || raw.length > PROMO_CODES_MAX) throw new PromoCodesInvalid();
  const out: string[] = [];
  for (const c of raw as unknown[]) {
    if (typeof c !== 'string' || !PROMO_CODE_RE.test(c)) throw new PromoCodesInvalid();
    const u = c.toUpperCase();
    if (!out.includes(u)) out.push(u);
  }
  return out;
}
