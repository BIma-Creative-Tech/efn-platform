/**
 * Konfigurasi transport Next.js -> BFF (docs/04_SERVICE_CONTRACTS.md §7.1, "enkripsi di setiap transaksi data").
 *
 * Modul murni (tanpa `server-only`) agar bisa diuji unit; pemeriksaan file CA disuntikkan lewat parameter.
 *
 * ## Aturan
 * - `BFF_INTERNAL_URL` (fallback `API_INTERNAL_URL`, default `https://api-gateway:3000`) WAJIB `https://`.
 * - `http://` hanya diterima bila `EFN_ALLOW_PLAINTEXT=true` DAN `NODE_ENV !== 'production'`.
 *   Tidak ada fallback diam-diam ke plaintext.
 * - URL tidak boleh memuat kredensial (`user:pass@`), query, atau fragment.
 * - `NODE_TLS_REJECT_UNAUTHORIZED=0` ditolak di production (mematikan verifikasi sertifikat).
 *
 * ## Kepercayaan CA internal: `NODE_EXTRA_CA_CERTS` (dipilih), bukan undici `Agent({ connect: { ca } })`
 * - `fetch` global Node 22 (undici bawaan) membuka koneksi lewat `tls.connect` tanpa opsi `ca`,
 *   sehingga memakai root store default Node = CA Mozilla + isi `NODE_EXTRA_CA_CERTS`. Env ini dibaca
 *   SEKALI saat proses start; verifikasi hostname tetap aktif (nama service `api-gateway` harus ada di SAN).
 * - Alternatif `dispatcher: new Agent(...)` butuh paket `undici` npm terpisah yang versinya harus cocok
 *   dengan undici bawaan Node (versi dispatcher yang tidak cocok dikenal memicu error runtime), dan opsi
 *   `dispatcher` bukan bagian API `fetch` yang di-patch Next.js. Jadi env lebih andal di sini.
 * - Kelemahan env: Node hanya mengeluarkan WARNING bila file tidak terbaca, lalu jalan terus. Karena itu
 *   `assertBffTransport()` memeriksa file CA sendiri dan gagal tertutup (dipanggil di `instrumentation.ts`
 *   saat boot dan sebelum request pertama di `lib/bff.ts`).
 */

export const DEFAULT_BFF_URL = 'https://api-gateway:3000';

export type Env = Record<string, string | undefined>;

export interface BffTransport {
  baseUrl: string;
  /** true bila plaintext diizinkan secara eksplisit (dev saja). */
  plaintext: boolean;
  /** Peringatan non-fatal (dev). Tidak memuat secret. */
  warnings: string[];
}

export class BffConfigError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'BffConfigError';
  }
}

/** Mengembalikan isi file atau null bila tidak terbaca. Disuntikkan agar modul ini tetap murni. */
export type ReadFile = (path: string) => string | null;

function isProduction(env: Env): boolean {
  return env.NODE_ENV === 'production' || env.APP_ENV === 'production';
}

/**
 * Validasi env transport BFF. Melempar `BffConfigError` bila tidak aman.
 * `readFile` opsional: bila tidak diberikan, pemeriksaan file CA dilewati (dipakai test URL saja).
 */
export function assertBffTransport(env: Env, readFile?: ReadFile): BffTransport {
  const prod = isProduction(env);
  const warnings: string[] = [];
  const raw = (env.BFF_INTERNAL_URL || env.API_INTERNAL_URL || DEFAULT_BFF_URL).trim();

  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    throw new BffConfigError('BFF_INTERNAL_URL bukan URL valid');
  }
  if (url.username || url.password) throw new BffConfigError('BFF_INTERNAL_URL tidak boleh memuat kredensial');
  if (url.search || url.hash) throw new BffConfigError('BFF_INTERNAL_URL tidak boleh memuat query/fragment');

  let plaintext = false;
  if (url.protocol === 'http:') {
    if (env.EFN_ALLOW_PLAINTEXT !== 'true') {
      throw new BffConfigError('BFF_INTERNAL_URL wajib https:// (http:// hanya dengan EFN_ALLOW_PLAINTEXT=true di non-production)');
    }
    if (prod) {
      throw new BffConfigError('EFN_ALLOW_PLAINTEXT=true ditolak saat NODE_ENV/APP_ENV=production');
    }
    plaintext = true;
    warnings.push('PERINGATAN: koneksi ke BFF PLAINTEXT (EFN_ALLOW_PLAINTEXT=true). Hanya untuk dev.');
  } else if (url.protocol !== 'https:') {
    throw new BffConfigError('BFF_INTERNAL_URL harus berskema https://');
  }

  if (env.NODE_TLS_REJECT_UNAUTHORIZED === '0') {
    if (prod) throw new BffConfigError('NODE_TLS_REJECT_UNAUTHORIZED=0 ditolak di production');
    warnings.push('PERINGATAN: NODE_TLS_REJECT_UNAUTHORIZED=0 mematikan verifikasi sertifikat.');
  }

  if (!plaintext && readFile) {
    const caPath = env.NODE_EXTRA_CA_CERTS;
    if (!caPath) {
      if (prod) throw new BffConfigError('NODE_EXTRA_CA_CERTS wajib di-set ke CA internal (mis. /run/secrets/efn_ca_crt)');
      warnings.push('NODE_EXTRA_CA_CERTS tidak di-set; sertifikat BFF harus dipercaya root store default.');
    } else {
      const pem = readFile(caPath);
      if (!pem || !pem.includes('-----BEGIN CERTIFICATE-----')) {
        throw new BffConfigError('NODE_EXTRA_CA_CERTS tidak terbaca atau bukan sertifikat PEM');
      }
    }
  }

  const path = url.pathname.replace(/\/+$/, '');
  return { baseUrl: `${url.origin}${path}`, plaintext, warnings };
}
