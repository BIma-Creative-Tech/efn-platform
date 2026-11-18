/**
 * Log JSON satu baris. Hanya nilai skalar bertipe sederhana; tidak ada fungsi untuk mencatat token, tanda tangan,
 * cookie, atau header `Cookie` (docs/04 §26.4). Pemanggil hanya mengirim kode hasil, sub, jti, durasi, referensi.
 */
export type LogFields = Record<string, string | number | boolean | null>;

export function log(level: 'info' | 'warn' | 'error', event: string, fields: LogFields = {}): void {
  const line = JSON.stringify({ t: new Date().toISOString(), level, svc: 'insight-web', event, ...fields });
  if (level === 'error') console.error(line);
  else if (level === 'warn') console.warn(line);
  else console.info(line);
}

/** Referensi korelasi singkat untuk kartu galat (tanpa detail SQL/host). */
export function newRef(): string {
  return Array.from(crypto.getRandomValues(new Uint8Array(4)), (b) => b.toString(16).padStart(2, '0')).join('');
}
