/**
 * Consent (UU PDP) — docs/04 §8.4, docs/compliance/PDP_CONSENT.md. MURNI (server + client, diuji unit).
 *
 * Kontrak v3 (menang atas usulan PDP_CONSENT.md):
 *   GET  /v1/me/consents           -> [{ purpose: "location"|"marketing", granted, version, updated_at }]
 *   PUT  /v1/me/consents/:purpose  { granted, version } -> 200
 *
 * Teks persetujuan berversi dimiliki frontend (yang ditampilkan ke pengguna = yang dicatat). Versi dikirim
 * apa adanya; Route Handler menolak versi yang bukan versi terbaru (`409 stale_text`) agar halaman lama
 * tidak mencatat persetujuan atas teks yang berbeda.
 *
 * Teks & versi = DRAFT, menunggu DPO/Legal.
 */

export type ConsentPurpose = 'location' | 'marketing';
export const CONSENT_PURPOSES: readonly ConsentPurpose[] = ['location', 'marketing'] as const;

export interface ConsentText {
  purpose: ConsentPurpose;
  /** Versi teks; ubah setiap kali isi `body` berubah. Format: `<purpose>@<tanggal>`. */
  version: string;
  title: string;
  body: string;
  /** Dampak bila tidak diberikan/ditarik. */
  whenOff: string;
}

export const CONSENT_TEXTS: Readonly<Record<ConsentPurpose, ConsentText>> = {
  location: {
    purpose: 'location',
    version: 'location@2026-10-06',
    title: 'Lokasi perkiraan untuk Pantau Harga',
    body:
      'Saya mengizinkan EFN memproses lokasi perkiraan perangkat (dibulatkan ±11 meter) yang saya kirim bersama ' +
      'laporan Pantau Harga, untuk memverifikasi toko yang dilaporkan. Lokasi hanya diambil saat saya memilih ' +
      'menyertakannya pada laporan, tidak dilacak terus-menerus.',
    whenOff: 'Laporan tetap bisa dikirim tanpa lokasi; poin tidak dikurangi.',
  },
  marketing: {
    purpose: 'marketing',
    version: 'marketing@2026-10-06',
    title: 'Info promo lewat WhatsApp',
    body:
      'Saya bersedia menerima informasi promo dan penawaran dari brand EFN lewat WhatsApp ke nomor akun saya. ' +
      'Saya dapat berhenti kapan saja dari halaman ini.',
    whenOff: 'Anda tetap menerima pesan layanan (kode OTP, status laporan, poin).',
  },
};

export interface ConsentRecord {
  purpose: ConsentPurpose;
  granted: boolean;
  version: string | null;
  updated_at: string | null;
}

export function isConsentPurpose(v: unknown): v is ConsentPurpose {
  return v === 'location' || v === 'marketing';
}

/**
 * Normalisasi respons GET /v1/me/consents. Menerima array (kontrak) atau `{ items: [...] }` (toleran).
 * Purpose yang tidak ada di respons dianggap BELUM diberikan (default tidak dicentang).
 */
export function parseConsents(data: unknown): Record<ConsentPurpose, ConsentRecord> {
  const out: Record<ConsentPurpose, ConsentRecord> = {
    location: { purpose: 'location', granted: false, version: null, updated_at: null },
    marketing: { purpose: 'marketing', granted: false, version: null, updated_at: null },
  };
  const list: unknown = Array.isArray(data)
    ? data
    : data && typeof data === 'object' && Array.isArray((data as { items?: unknown }).items)
      ? (data as { items: unknown[] }).items
      : [];
  for (const item of list as unknown[]) {
    if (!item || typeof item !== 'object') continue;
    const r = item as Record<string, unknown>;
    if (!isConsentPurpose(r.purpose)) continue;
    out[r.purpose] = {
      purpose: r.purpose,
      granted: r.granted === true, // hanya boolean true yang dianggap persetujuan
      version: typeof r.version === 'string' ? r.version : null,
      updated_at: typeof r.updated_at === 'string' ? r.updated_at : null,
    };
  }
  return out;
}

/** Persetujuan aktif = granted true. BFF tetap otoritas (membuang koordinat bila tidak aktif). */
export function isConsentActive(records: Record<ConsentPurpose, ConsentRecord>, purpose: ConsentPurpose): boolean {
  return records[purpose].granted === true;
}

/** Persetujuan aktif tetapi atas teks versi lama -> UI meminta konfirmasi ulang. */
export function isConsentOutdated(record: ConsentRecord): boolean {
  return record.granted && record.version !== CONSENT_TEXTS[record.purpose].version;
}

export type ConsentUpdateValidation =
  | { ok: true; purpose: ConsentPurpose; granted: boolean; version: string }
  | { ok: false; status: 400 | 409; code: 'invalid_request' | 'stale_text'; message: string };

/** Validasi body PUT /api/me/consents `{ purpose, granted, version }` di Route Handler. */
export function validateConsentUpdate(body: unknown): ConsentUpdateValidation {
  if (!body || typeof body !== 'object' || Array.isArray(body)) {
    return { ok: false, status: 400, code: 'invalid_request', message: 'Permintaan tidak valid.' };
  }
  const b = body as Record<string, unknown>;
  if (!isConsentPurpose(b.purpose) || typeof b.granted !== 'boolean' || typeof b.version !== 'string') {
    return { ok: false, status: 400, code: 'invalid_request', message: 'Permintaan tidak valid.' };
  }
  const current = CONSENT_TEXTS[b.purpose].version;
  // Penarikan harus semudah pemberian: tidak pernah ditolak karena versi teks; dicatat dengan versi terbaru.
  if (!b.granted) return { ok: true, purpose: b.purpose, granted: false, version: current };
  if (b.version !== current) {
    return {
      ok: false,
      status: 409,
      code: 'stale_text',
      message: 'Teks persetujuan telah diperbarui. Muat ulang halaman lalu tinjau kembali.',
    };
  }
  return { ok: true, purpose: b.purpose, granted: b.granted, version: b.version };
}
