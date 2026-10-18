import { randomInt } from 'node:crypto';
import { PII_AAD, PiiCipher, PiiDecryptError } from '../common/pii/crypto';
import { isUuid } from '../common/validation';

/**
 * Kode ambil (docs/04 §10.2, §10.3.6): 8 karakter dari alfabet tanpa huruf/angka ambigu (31 simbol, ≈ 39,6 bit),
 * `crypto.randomInt`, tampil `ABCD-EFGH`. RAHASIA: disimpan BFF sebagai ciphertext AES-256-GCM (key ring
 * `pii_encryption_key`, perlu ditampilkan ulang ke pemilik), dikirim sekali ke Odoo (yang menyimpan hash KDF), tidak
 * pernah di log/event/metrik/URL.
 *
 * SEC-102 (L-16): AAD terikat baris — `efn:online_order_part.pickup_code_enc:v2:<part_id>` — sehingga ciphertext yang
 * disalin ke bagian lain gagal diautentikasi. Baris lama (AAD kolom v1, `PII_AAD.ONLINE_PICKUP_CODE`) tetap terbaca
 * (`legacy: true`) lalu ditulis ulang ke format v2 oleh pemanggil.
 */
export const PICKUP_ALPHABET = 'ABCDEFGHJKMNPQRSTUVWXYZ23456789';
/** AAD lama (v1, per kolom) — hanya untuk membaca baris sebelum SEC-102. */
export const PICKUP_CODE_AAD_LEGACY = PII_AAD.ONLINE_PICKUP_CODE;
export const PICKUP_CODE_RE = /^[ABCDEFGHJKMNPQRSTUVWXYZ23456789]{8}$/;

export function pickupCodeAad(partId: string): string {
  if (!isUuid(partId)) throw new Error('part_id tidak valid untuk AAD kode ambil');
  return `efn:online_order_part.pickup_code_enc:v2:${partId.toLowerCase()}`;
}

export function generatePickupCode(): string {
  let s = '';
  for (let i = 0; i < 8; i += 1) s += PICKUP_ALPHABET[randomInt(PICKUP_ALPHABET.length)];
  return s;
}

export function formatPickupCode(code: string): string {
  return `${code.slice(0, 4)}-${code.slice(4)}`;
}

export function encryptPickupCode(cipher: PiiCipher, code: string, partId: string): Buffer {
  if (!PICKUP_CODE_RE.test(code)) throw new Error('kode ambil tidak valid');
  return cipher.encrypt(code, pickupCodeAad(partId));
}

/**
 * G20 §12.2 no. 3 (L-50, L-56): kode BAYAR di kasir — dibuat BFF (`generatePickupCode`: `crypto.randomInt`, alfabet
 * §10.2, 8 karakter) di transaksi checkout, disimpan terenkripsi SEBELUM `efn_online_place`, dikirim sekali ke Odoo
 * (`order.counter_code`, Odoo menyimpan hash). Bukan kode ambil (tidak bisa dipakai menyerahkan barang; selalu berbeda).
 * AAD terikat baris `efn:online_order_part.counter_code_enc:v2:<part_id>`; tidak pernah di log/event/metrik/URL.
 */
export function counterCodeAad(partId: string): string {
  if (!isUuid(partId)) throw new Error('part_id tidak valid untuk AAD kode bayar');
  return `efn:online_order_part.counter_code_enc:v2:${partId.toLowerCase()}`;
}

export function encryptCounterCode(cipher: PiiCipher, code: string, partId: string): Buffer {
  if (!PICKUP_CODE_RE.test(code)) throw new Error('kode bayar tidak valid');
  return cipher.encrypt(code, counterCodeAad(partId));
}

export function decryptCounterCode(cipher: PiiCipher, enc: Buffer | Uint8Array, partId: string): string {
  return cipher.decrypt(enc, counterCodeAad(partId));
}

/** v2 (terikat `part_id`) dulu; gagal → AAD lama v1 (`legacy: true`). Keduanya gagal → `PiiDecryptError`. */
export function decryptPickupCode(cipher: PiiCipher, enc: Buffer | Uint8Array, partId: string): { code: string; legacy: boolean } {
  try {
    return { code: cipher.decrypt(enc, pickupCodeAad(partId)), legacy: false };
  } catch (err) {
    if (!(err instanceof PiiDecryptError)) throw err;
  }
  return { code: cipher.decrypt(enc, PICKUP_CODE_AAD_LEGACY), legacy: true };
}
