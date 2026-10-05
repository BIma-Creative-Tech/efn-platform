/**
 * Pemeriksaan boot (runtime Node saja). Konfigurasi transport BFF tidak aman (http tanpa izin eksplisit,
 * plaintext di production, CA `NODE_EXTRA_CA_CERTS` tidak terbaca) -> proses keluar kode 1, sehingga
 * container gagal start/healthcheck alih-alih jalan diam-diam (docs/04 §7.1: tidak ada fallback ke plaintext).
 */
import { readFileSync } from 'node:fs';
import { assertBffTransport } from './bff-config';

function readFileOrNull(path: string): string | null {
  try {
    return readFileSync(path, 'utf8');
  } catch {
    return null;
  }
}

export function verifyBffTransportAtBoot(): void {
  try {
    const t = assertBffTransport(process.env, readFileOrNull);
    for (const w of t.warnings) console.warn(`[bff] ${w}`);
    console.info(`[bff] transport ${t.plaintext ? 'PLAINTEXT (dev)' : 'TLS'} -> ${t.baseUrl}`);
  } catch (err) {
    console.error(`[bff] FATAL konfigurasi transport: ${err instanceof Error ? err.message : 'unknown'}`);
    process.exit(1);
  }
}
