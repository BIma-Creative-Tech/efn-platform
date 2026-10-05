import { createHash, timingSafeEqual } from 'node:crypto';

/**
 * Perbandingan string waktu-konstan. Kedua sisi di-hash SHA-256 dulu sehingga panjangnya sama
 * (timingSafeEqual melempar bila panjang beda, dan panjang pun tidak boleh bocor).
 */
export function safeEqualString(a: string, b: string): boolean {
  const da = createHash('sha256').update(a, 'utf8').digest();
  const db = createHash('sha256').update(b, 'utf8').digest();
  return timingSafeEqual(da, db);
}

/** Perbandingan dua digest hex dengan panjang yang diharapkan (byte). */
export function safeEqualHex(a: string, b: string, expectedBytes = 32): boolean {
  const ba = Buffer.from(a, 'hex');
  const bb = Buffer.from(b, 'hex');
  if (ba.length !== expectedBytes || bb.length !== expectedBytes) return false;
  return timingSafeEqual(ba, bb);
}
