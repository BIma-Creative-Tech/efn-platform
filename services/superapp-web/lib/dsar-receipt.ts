/**
 * R6 (docs/04 §8.13) — bukti status penghapusan akun yang tetap berlaku setelah sesi/akun hilang.
 * MURNI (tanpa server-only, diuji unit). Cookie-nya sendiri di-set di `lib/session.ts`.
 *
 * `receipt_token` dari BFF `DELETE /v1/me` = `v1.<exp_epoch_s>.<base64url(HMAC-SHA256)>` (opak bagi Next.js; format
 * hanya dicek longgar agar nilai sampah/raksasa tidak diteruskan ke BFF). Token TIDAK pernah dikirim ke JavaScript,
 * tidak dicetak ke log, hanya disimpan di cookie HttpOnly `efn_dsar_receipt` (Path `/api/dsar`, SameSite=Strict).
 */

export const DSAR_RECEIPT_COOKIE = 'efn_dsar_receipt';
export const DSAR_RECEIPT_PATH = '/api/dsar';
/** Batas atas umur cookie: 30 hari (kontrak §8.13). */
export const DSAR_RECEIPT_MAX_AGE_S = 30 * 86_400;

const RECEIPT_RE = /^v1\.(\d{1,12})\.[A-Za-z0-9_-]{16,128}$/;
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** Bentuk token masuk akal (bukan verifikasi — verifikasi HMAC hanya di BFF). */
export function isPlausibleReceipt(v: unknown): v is string {
  return typeof v === 'string' && v.length <= 160 && RECEIPT_RE.test(v);
}

/**
 * Max-Age cookie (detik) = min(30 hari, sisa umur token dari `<exp>`). null bila token sudah kedaluwarsa
 * (cookie tidak perlu disimpan). `exp` tidak terbaca -> 30 hari.
 */
export function receiptMaxAge(token: string, nowMs: number = Date.now()): number | null {
  const m = RECEIPT_RE.exec(token);
  const exp = m ? Number(m[1]) : NaN;
  if (!Number.isSafeInteger(exp)) return DSAR_RECEIPT_MAX_AGE_S;
  const remaining = exp - Math.floor(nowMs / 1000);
  if (remaining <= 0) return null;
  return Math.min(remaining, DSAR_RECEIPT_MAX_AGE_S);
}

export function isDsarRequestId(v: unknown): v is string {
  return typeof v === 'string' && UUID_RE.test(v);
}

export type DeletionStatus = 'scheduled' | 'processing' | 'completed' | 'cancelled';

export interface DeletionStatusView {
  id: string;
  status: DeletionStatus | 'unknown';
  requested_at: string | null;
  scheduled_at: string | null;
  completed_at: string | null;
  /** L-22 (G18): jumlah bagian pesanan online yang masih terbuka (0 bila tidak ada / BFF lama). */
  open_online_orders: number;
  /**
   * L-22 (G18): batas paling lambat erasure bila ada pesanan terbuka. `null` = BFF menyatakan tidak ada penundaan;
   * `undefined` (field tidak dikirim) = BFF lama -> UI boleh memakai cadangan (`?batas=`).
   */
  erasure_deadline?: string | null;
}

/**
 * Batas erasure yang ditampilkan di halaman status. Urutan sumber:
 * 1. respons status jalur receipt (`view.erasure_deadline`) bila field ada — termasuk `null` (tidak ada penundaan,
 *    mis. pesanan sudah selesai: cadangan lama TIDAK dipakai agar tidak menampilkan tanggal basi);
 * 2. item `GET /v1/me/requests` milik sesi (sumber server yang sama);
 * 3. query `?batas=` dari respons 202 (cadangan untuk BFF lama).
 * Status final (selesai/dibatalkan) -> tidak ada batas.
 */
export function resolveErasureDeadline(
  view: Pick<DeletionStatusView, 'status' | 'erasure_deadline'>,
  sessionDeadline: string | null,
  queryDeadline: string | null,
): string | null {
  if (view.status === 'completed' || view.status === 'cancelled') return null;
  if (view.erasure_deadline !== undefined) return view.erasure_deadline;
  return sessionDeadline ?? queryDeadline;
}

/** Bilangan bulat ≥ 0 dari BFF; selain itu 0 (klien lama/field rusak). Dibatasi agar tidak menampilkan angka aneh. */
export function openOrdersCount(v: unknown): number {
  return typeof v === 'number' && Number.isInteger(v) && v >= 0 ? Math.min(v, 999) : 0;
}

const KNOWN: ReadonlySet<string> = new Set(['scheduled', 'processing', 'completed', 'cancelled']);

function isoOrNull(v: unknown): string | null {
  if (typeof v !== 'string' || v.length === 0 || v.length > 64) return null;
  return Number.isNaN(Date.parse(v)) ? null : v;
}

/**
 * `GET /v1/dsar/requests/:id` -> `{ id, type, status, requested_at, scheduled_at, completed_at, open_online_orders,
 * erasure_deadline }` (dua field terakhir: L-22, aditif — BFF lama tidak mengirimnya -> 0 / null).
 * Hanya field allowlist yang diteruskan ke browser; `type` wajib `delete` dan `id` wajib sama dengan yang diminta.
 */
export function parseDeletionStatus(data: unknown, expectedId: string): DeletionStatusView | null {
  if (!data || typeof data !== 'object') return null;
  const r = data as Record<string, unknown>;
  if (typeof r.id !== 'string' || r.id.toLowerCase() !== expectedId.toLowerCase()) return null;
  if (r.type !== 'delete') return null;
  if (typeof r.status !== 'string') return null;
  return {
    id: r.id.toLowerCase(),
    status: KNOWN.has(r.status) ? (r.status as DeletionStatus) : 'unknown',
    requested_at: isoOrNull(r.requested_at),
    scheduled_at: isoOrNull(r.scheduled_at),
    completed_at: isoOrNull(r.completed_at),
    open_online_orders: openOrdersCount(r.open_online_orders),
    // Field ada -> ISO tervalidasi atau null; field tidak ada (BFF lama) -> undefined (tidak ikut di JSON).
    ...('erasure_deadline' in r ? { erasure_deadline: isoOrNull(r.erasure_deadline) } : {}),
  };
}

const LABELS: Record<DeletionStatusView['status'], string> = {
  scheduled: 'Dijadwalkan (masa tenggang)',
  processing: 'Sedang dihapus',
  completed: 'Selesai dihapus',
  cancelled: 'Dibatalkan',
  unknown: 'Sedang diperbarui',
};

const DESCRIPTIONS: Record<DeletionStatusView['status'], string> = {
  scheduled:
    'Akun belum dihapus. Anda masih dapat membatalkan dengan masuk kembali sebelum tanggal penghapusan.',
  processing: 'Data pribadi Anda sedang dihapus dari semua sistem EFN. Proses ini tidak dapat dibatalkan lagi.',
  completed:
    'Data pribadi Anda telah dihapus. Catatan transaksi yang wajib disimpan menurut hukum tidak lagi terhubung dengan identitas Anda.',
  cancelled: 'Permintaan hapus akun dibatalkan. Akun Anda tetap aktif.',
  unknown: 'Status permintaan sedang diperbarui. Silakan muat ulang beberapa saat lagi.',
};

export function deletionStatusLabel(s: DeletionStatusView['status']): string {
  return LABELS[s];
}

export function deletionStatusDescription(s: DeletionStatusView['status']): string {
  return DESCRIPTIONS[s];
}

/** Tombol batalkan hanya bila status `scheduled` DAN sesi masih valid (dicek server). */
export function canCancelDeletion(view: Pick<DeletionStatusView, 'status'>, sessionValid: boolean): boolean {
  return sessionValid && view.status === 'scheduled';
}
