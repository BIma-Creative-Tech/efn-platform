import { createCipheriv, createDecipheriv, randomBytes } from 'node:crypto';

/**
 * AES-256-GCM untuk material at rest di volume sesi (ADR-044 §3.5): auth state Baileys + state lokal (dedup, kuota,
 * opt-out). Kunci 32 byte dari `/run/secrets/wa_unofficial_session_key`.
 *
 * Format: 0x01 (versi) ‖ IV 12 byte acak ‖ ciphertext ‖ tag 16 byte. AAD = nama logis berkas, sehingga isi satu berkas
 * tidak bisa dipindah ke berkas lain (mis. kunci pre-key ditukar ke creds).
 */
const VERSION = 0x01;
const IV_BYTES = 12;
const TAG_BYTES = 16;

export class AeadError extends Error {}

export function seal(key: Buffer, plaintext: Buffer, aad: string): Buffer {
  if (key.length !== 32) throw new AeadError('kunci sesi harus 32 byte');
  const iv = randomBytes(IV_BYTES);
  const c = createCipheriv('aes-256-gcm', key, iv, { authTagLength: TAG_BYTES });
  c.setAAD(Buffer.from(aad, 'utf8'));
  const ct = Buffer.concat([c.update(plaintext), c.final()]);
  return Buffer.concat([Buffer.from([VERSION]), iv, ct, c.getAuthTag()]);
}

export function open(key: Buffer, blob: Buffer, aad: string): Buffer {
  if (key.length !== 32) throw new AeadError('kunci sesi harus 32 byte');
  if (blob.length < 1 + IV_BYTES + TAG_BYTES || blob[0] !== VERSION) throw new AeadError('berkas sesi rusak atau versi tidak dikenal');
  const iv = blob.subarray(1, 1 + IV_BYTES);
  const tag = blob.subarray(blob.length - TAG_BYTES);
  const ct = blob.subarray(1 + IV_BYTES, blob.length - TAG_BYTES);
  try {
    const d = createDecipheriv('aes-256-gcm', key, iv, { authTagLength: TAG_BYTES });
    d.setAAD(Buffer.from(aad, 'utf8'));
    d.setAuthTag(tag);
    return Buffer.concat([d.update(ct), d.final()]);
  } catch {
    throw new AeadError('autentikasi berkas sesi gagal (kunci salah atau berkas dimodifikasi)');
  }
}
