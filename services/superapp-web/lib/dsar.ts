/**
 * DSAR (hak subjek data, UU PDP) — docs/04 §8.4, docs/compliance/PDP_DSAR_DESIGN.md §3/§5.
 * MURNI (server + client, diuji unit): parsing respons BFF, label status, validasi tautan unduh.
 *
 * Bentuk respons yang TIDAK dirinci kontrak diperlakukan toleran (field opsional) dan dicatat sebagai
 * asumsi di laporan Gelombang 5.
 */
import { openOrdersCount } from './dsar-receipt';

export type DsarType = 'access' | 'rectify' | 'delete' | 'phone_change' | 'object';

export interface DsarRequest {
  id: string;
  type: string;
  status: string;
  requested_at: string | null;
  scheduled_at: string | null;
  completed_at: string | null;
  /** ASUMSI: tautan unduh (presigned, TTL 24 jam) untuk export besar yang sudah siap. */
  download_url: string | null;
  /** L-22 (G18): jumlah bagian pesanan online terbuka (hapus akun); 0 bila tidak ada. */
  open_online_orders: number;
  /** L-22 (G18): batas paling lambat erasure bila ada pesanan terbuka; null bila tidak ada. */
  erasure_deadline: string | null;
}

export interface ExportAccepted {
  request_id: string;
  download_url: string | null;
  expires_at: string | null;
}

export interface DeleteAcceptedParsed {
  request_id: string;
  scheduled_at: string | null;
  points_forfeited: number;
  /** G18 W-18: jumlah bagian pesanan online yang belum selesai saat permintaan (0 = tanpa penundaan). */
  open_online_orders: number;
  /** G18 W-18: batas paling lambat erasure bila ada pesanan terbuka; null bila tidak ada penundaan. */
  erasure_deadline: string | null;
}

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const CHALLENGE_RE = /^[0-9a-fA-F-]{8,64}$/;
const CODE_RE = /^\d{6}$/;

export function isUuid(v: unknown): v is string {
  return typeof v === 'string' && UUID_RE.test(v);
}

export function isValidChallengeId(v: unknown): v is string {
  return typeof v === 'string' && CHALLENGE_RE.test(v);
}

export function isValidOtpCode(v: unknown): v is string {
  return typeof v === 'string' && CODE_RE.test(v);
}

function str(v: unknown, max = 256): string | null {
  return typeof v === 'string' && v.length > 0 && v.length <= max ? v : null;
}

/**
 * Tautan unduh export hanya diterima bila `https:` tanpa kredensial di URL. Mencegah `javascript:`/`data:`
 * atau tautan ke skema lain dirender sebagai href walau BFF terkompromi/salah konfigurasi.
 */
export function safeDownloadUrl(v: unknown): string | null {
  if (typeof v !== 'string' || v.length > 4096) return null;
  let u: URL;
  try {
    u = new URL(v);
  } catch {
    return null;
  }
  if (u.protocol !== 'https:') return null;
  if (u.username || u.password) return null;
  return u.toString();
}

/** GET /v1/me/requests -> array (kontrak) atau `{ items: [...] }` (toleran). Item rusak dibuang. */
export function parseDsarRequests(data: unknown): DsarRequest[] {
  const list: unknown = Array.isArray(data)
    ? data
    : data && typeof data === 'object' && Array.isArray((data as { items?: unknown }).items)
      ? (data as { items: unknown[] }).items
      : [];
  const out: DsarRequest[] = [];
  for (const item of list as unknown[]) {
    if (!item || typeof item !== 'object') continue;
    const r = item as Record<string, unknown>;
    const id = str(r.id) ?? str(r.request_id);
    const type = str(r.type, 32);
    const status = str(r.status, 32);
    if (!id || !type || !status) continue;
    out.push({
      id,
      type,
      status,
      requested_at: str(r.requested_at) ?? str(r.created_at),
      scheduled_at: str(r.scheduled_at),
      completed_at: str(r.completed_at),
      download_url: safeDownloadUrl(r.download_url),
      // L-22 (G18): aditif; BFF lama -> 0 / null.
      open_online_orders: openOrdersCount(r.open_online_orders),
      erasure_deadline: isoDateOrNull(r.erasure_deadline),
    });
  }
  // Terbaru di atas.
  return out.sort((a, b) => (b.requested_at ?? '').localeCompare(a.requested_at ?? ''));
}

/** GET /v1/me/data -> 202 (export besar, diproses async). */
export function parseExportAccepted(data: unknown): ExportAccepted | null {
  if (!data || typeof data !== 'object') return null;
  const r = data as Record<string, unknown>;
  const id = str(r.request_id);
  if (!id) return null;
  return { request_id: id, download_url: safeDownloadUrl(r.download_url), expires_at: str(r.expires_at) };
}

/** DELETE /v1/me -> 202 { request_id, scheduled_at, points_forfeited, open_online_orders, erasure_deadline } (G18 W-18). */
export function parseDeleteAccepted(data: unknown): DeleteAcceptedParsed | null {
  if (!data || typeof data !== 'object') return null;
  const r = data as Record<string, unknown>;
  const id = str(r.request_id);
  if (!id) return null;
  const pts = typeof r.points_forfeited === 'number' && Number.isFinite(r.points_forfeited) ? r.points_forfeited : 0;
  const open =
    typeof r.open_online_orders === 'number' && Number.isFinite(r.open_online_orders)
      ? Math.max(0, Math.trunc(r.open_online_orders))
      : 0;
  return {
    request_id: id,
    scheduled_at: str(r.scheduled_at),
    points_forfeited: Math.max(0, Math.trunc(pts)),
    open_online_orders: open,
    erasure_deadline: open > 0 ? isoDateOrNull(r.erasure_deadline) : null,
  };
}

const ISO_RE = /^\d{4}-\d{2}-\d{2}T[0-9:.]+(Z|[+-]\d{2}:\d{2})$/;

/** String tanggal-waktu ISO 8601 yang benar-benar bisa di-parse (untuk tampilan); selain itu null. */
export function isoDateOrNull(v: unknown): string | null {
  if (typeof v !== 'string' || v.length > 40 || !ISO_RE.test(v)) return null;
  return Number.isNaN(Date.parse(v)) ? null : v;
}

// ---------- G18 W-18 (Q-P18): hapus akun saat ada pesanan online terbuka ----------

/** Nama parameter konfirmasi di BFF (`DELETE /v1/me?confirm_open_orders=true`). */
export const OPEN_ORDERS_CONFIRM_PARAM = 'confirm_open_orders';
/** Plafon penundaan erasure (Q-P18). BFF mengirim nilai efektif di `error.max_defer_days`. */
export const MAX_DEFER_DAYS_CAP = 14;

export interface OpenOrdersConflict {
  open_parts: number;
  max_defer_days: number;
}

/**
 * `409 { error: { code: "open_online_orders", open_parts, max_defer_days, confirm_param } }` -> info untuk UI.
 * Kode lain / bentuk rusak -> null. `max_defer_days` dibatasi 1..14 (di luar itu -> 14).
 */
export function parseOpenOrdersConflict(body: unknown): OpenOrdersConflict | null {
  if (!body || typeof body !== 'object') return null;
  const e = (body as { error?: Record<string, unknown> }).error;
  if (!e || e.code !== 'open_online_orders') return null;
  const parts =
    typeof e.open_parts === 'number' && Number.isFinite(e.open_parts) ? Math.max(0, Math.trunc(e.open_parts)) : 0;
  const raw = typeof e.max_defer_days === 'number' && Number.isInteger(e.max_defer_days) ? e.max_defer_days : MAX_DEFER_DAYS_CAP;
  return { open_parts: parts, max_defer_days: raw >= 1 && raw <= MAX_DEFER_DAYS_CAP ? raw : MAX_DEFER_DAYS_CAP };
}

/**
 * Penjelasan untuk pelanggan — DRAF, teks final menunggu persetujuan Legal (lihat README).
 * Isi wajib (L-4/W-18): ada pesanan terbuka, kode ambil tidak lagi tampil di aplikasi, penghapusan paling lambat N hari.
 */
export function openOrdersExplanation(c: OpenOrdersConflict): string {
  const jumlah = c.open_parts > 0 ? `${c.open_parts} pesanan online` : 'pesanan online';
  return (
    `Masih ada ${jumlah} yang belum selesai (sudah dibayar atau sedang disiapkan). ` +
    'Anda bisa mengambil pesanan atau meminta pembatalan lewat layanan pelanggan terlebih dahulu, lalu mengulangi penghapusan. ' +
    `Bila tetap menghapus akun sekarang, akun dihapus paling lambat ${c.max_defer_days} hari sejak permintaan ini; ` +
    'pesanan yang belum selesai akan diselesaikan layanan pelanggan tanpa tautan ke akun Anda, dan kode ambil tidak lagi tampil di aplikasi.'
  );
}

const TYPE_LABELS: Record<string, string> = {
  access: 'Unduh data',
  rectify: 'Perbaikan data',
  delete: 'Hapus akun',
  phone_change: 'Ganti nomor HP',
  object: 'Keberatan',
};

const STATUS_LABELS: Record<string, string> = {
  received: 'Diterima',
  verified: 'Terverifikasi',
  scheduled: 'Dijadwalkan (masa tenggang)',
  pending: 'Menunggu',
  processing: 'Diproses',
  in_progress: 'Diproses',
  ready: 'Siap diunduh',
  done: 'Selesai',
  completed: 'Selesai',
  cancelled: 'Dibatalkan',
  rejected: 'Ditolak',
  expired: 'Kedaluwarsa',
};

export function dsarTypeLabel(type: string): string {
  return Object.prototype.hasOwnProperty.call(TYPE_LABELS, type) ? TYPE_LABELS[type]! : type;
}

export function dsarStatusLabel(status: string): string {
  return Object.prototype.hasOwnProperty.call(STATUS_LABELS, status) ? STATUS_LABELS[status]! : status;
}

/** Status hapus akun yang masih dalam masa tenggang dan dapat dibatalkan. */
const CANCELLABLE = new Set(['received', 'verified', 'scheduled', 'pending']);

export function isCancellableDeletion(req: DsarRequest, now: Date = new Date()): boolean {
  if (req.type !== 'delete' || !CANCELLABLE.has(req.status)) return false;
  if (!req.scheduled_at) return true;
  const t = Date.parse(req.scheduled_at);
  return Number.isNaN(t) ? true : t > now.getTime();
}

export function findPendingDeletion(reqs: DsarRequest[], now: Date = new Date()): DsarRequest | null {
  return reqs.find((r) => isCancellableDeletion(r, now)) ?? null;
}

/** Sisa hari (dibulatkan ke atas) sampai `iso`; null bila tidak valid; 0 bila sudah lewat. */
export function daysUntil(iso: string | null, now: Date = new Date()): number | null {
  if (!iso) return null;
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return null;
  return Math.max(0, Math.ceil((t - now.getTime()) / 86_400_000));
}

/** Format tanggal Indonesia, zona Asia/Jakarta. null/invalid -> "-". */
export function formatDateId(iso: string | null): string {
  if (!iso) return '-';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '-';
  return new Intl.DateTimeFormat('id-ID', { dateStyle: 'long', timeStyle: 'short', timeZone: 'Asia/Jakarta' }).format(d);
}

/** Nama berkas unduhan export: `data-saya-efn-YYYYMMDD.json` (tanggal Jakarta). */
export function exportFilename(now: Date = new Date()): string {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Jakarta',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(now); // 2026-10-06
  return `data-saya-efn-${parts.replace(/-/g, '')}.json`;
}

/** Konfirmasi ketik ulang sebelum hapus akun (dicek client DAN Route Handler). */
export const DELETE_CONFIRM_WORD = 'HAPUS';

export function isDeleteConfirmed(body: unknown): boolean {
  return (
    !!body &&
    typeof body === 'object' &&
    (body as { confirm?: unknown }).confirm === DELETE_CONFIRM_WORD &&
    (body as { acknowledge_points?: unknown }).acknowledge_points === true
  );
}

/** Masa tenggang yang DITAMPILKAN sebelum konfirmasi (perkiraan); tanggal pasti dari `scheduled_at` BFF. */
export function parseGraceDays(raw: string | undefined): number {
  if (raw === undefined || raw.trim() === '') return 7;
  const n = Number(raw);
  return Number.isInteger(n) && n >= 0 && n <= 60 ? n : 7;
}

/** Query `?batas=` di halaman status penghapusan: batas erasure dari respons 202 (GET status BFF belum memuatnya). */
export const DEFER_DEADLINE_PARAM = 'batas';

/**
 * Tautan halaman status penghapusan. `erasure_deadline` (bukan data pribadi, hanya tanggal) ikut di query karena
 * `GET /v1/dsar/requests/:id` belum mengembalikannya — nilai hanya untuk tampilan, divalidasi ulang di halaman.
 */
export function deletionStatusHref(requestId: string, erasureDeadline: string | null): string {
  const base = `/akun/penghapusan/${encodeURIComponent(requestId)}`;
  const d = isoDateOrNull(erasureDeadline);
  return d ? `${base}?${DEFER_DEADLINE_PARAM}=${encodeURIComponent(d)}` : base;
}
