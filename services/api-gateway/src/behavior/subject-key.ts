import { createHash, createHmac } from 'node:crypto';

/**
 * Pseudonim tracking (ADR-047 §3.1 no. 3, §3.3; docs/04 §28.6, L-152). Server TIDAK menyimpan device ID mentah.
 * - `device_key  = hex(HMAC-SHA256(behavior_device_pepper,  "efn:device:v1:" + device_id lowercase))`
 * - `subject_key = hex(HMAC-SHA256(behavior_subject_pepper, "efn:subject:v1:" + member_id lowercase))`
 * Dua pepper terpisah (AppConfig menolak nilai sama) → subject_key member tidak dapat dihitung dari device_key.
 * Prefix domain mencegah tabrakan bila kelak pepper yang sama dipakai untuk input lain.
 */
export function deviceKey(pepper: string, deviceId: string): string {
  return createHmac('sha256', pepper).update(`efn:device:v1:${deviceId.toLowerCase()}`, 'utf8').digest('hex');
}

export function memberSubjectKey(pepper: string, memberId: string): string {
  return createHmac('sha256', pepper).update(`efn:subject:v1:${memberId.toLowerCase()}`, 'utf8').digest('hex');
}

/**
 * UUID deterministik (format versi 8, RFC 9562 "custom") dari teks — dipakai untuk event turunan server
 * (`cart_abandon`) agar job yang berjalan ulang menghasilkan `event_id` yang sama (dedup staging).
 */
export function deterministicUuid(seed: string): string {
  const h = createHash('sha256').update(seed, 'utf8').digest();
  h[6] = (h[6]! & 0x0f) | 0x80; // versi 8
  h[8] = (h[8]! & 0x3f) | 0x80; // varian RFC
  const x = h.subarray(0, 16).toString('hex');
  return `${x.slice(0, 8)}-${x.slice(8, 12)}-${x.slice(12, 16)}-${x.slice(16, 20)}-${x.slice(20, 32)}`;
}
