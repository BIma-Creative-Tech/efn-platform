import { randomInt } from 'node:crypto';
import { PII_AAD, PiiCipher } from '../common/pii/crypto';

/**
 * Kode ambil (docs/04 §10.2, §10.3.6): 8 karakter dari alfabet tanpa huruf/angka ambigu (31 simbol, ≈ 39,6 bit),
 * `crypto.randomInt`, tampil `ABCD-EFGH`. RAHASIA: disimpan BFF sebagai ciphertext AES-256-GCM (perlu ditampilkan ulang
 * ke pemilik), dikirim sekali ke Odoo (yang menyimpan hash KDF), tidak pernah di log/event/metrik/URL.
 */
export const PICKUP_ALPHABET = 'ABCDEFGHJKMNPQRSTUVWXYZ23456789';
/** L-13: terdaftar di `common/pii/pii-columns.ts` (rotasi key ring §7.2). */
export const PICKUP_CODE_AAD = PII_AAD.ONLINE_PICKUP_CODE;
export const PICKUP_CODE_RE = /^[ABCDEFGHJKMNPQRSTUVWXYZ23456789]{8}$/;

export function generatePickupCode(): string {
  let s = '';
  for (let i = 0; i < 8; i += 1) s += PICKUP_ALPHABET[randomInt(PICKUP_ALPHABET.length)];
  return s;
}

export function formatPickupCode(code: string): string {
  return `${code.slice(0, 4)}-${code.slice(4)}`;
}

export function encryptPickupCode(cipher: PiiCipher, code: string): Buffer {
  if (!PICKUP_CODE_RE.test(code)) throw new Error('kode ambil tidak valid');
  return cipher.encrypt(code, PICKUP_CODE_AAD);
}

export function decryptPickupCode(cipher: PiiCipher, enc: Buffer): string {
  return cipher.decrypt(enc, PICKUP_CODE_AAD);
}
