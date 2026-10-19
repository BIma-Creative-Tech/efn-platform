/**
 * Poin EFN di sisi pelanggan (docs/04 §14.C poin online, §14.D.6 info hangus) — modul MURNI (client, Route Handler, test).
 *
 * Prinsip (§14.C.1): redeem = porsi pembayaran "Poin EFN" per bagian (per PT/kanal), BUKAN diskon. Nilai poin dihitung
 * server dari `points x point_value_idr`; klien hanya mengirim JUMLAH POIN per kanal. Perhitungan di sini hanya untuk
 * tampilan/UX — BFF/Odoo tetap sumber validasi (`insufficient_points`, `below_minimum`, `exceeds_max_pct`,
 * `points_cover_full_amount`).
 *
 * Teks hangus (poin akan hangus) adalah DRAF sampai Legal menyetujui syarat program (docs/04 §14.7 L-73, Q-F22-4).
 */
import { formatRupiah } from './format';
import { isChannelCode } from './catalog';

// ---------- quote.points (§14.C.2) ----------

export interface QuotePointsGroup {
  channel_code: string;
  redeem_allowed: boolean;
  min_points: number;
  max_points: number;
  max_value_idr: number;
}

export interface QuotePoints {
  /** Saldo tersedia = saldo − reservasi aktif. */
  balance_available: number;
  point_value_idr: number;
  groups: QuotePointsGroup[];
}

function isObj(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v);
}

function nonNegInt(v: unknown): number | null {
  return typeof v === 'number' && Number.isSafeInteger(v) && v >= 0 ? v : null;
}

/** Allowlist `quote.points`. Bentuk rusak -> null (UI tidak menawarkan poin; checkout tanpa poin tetap jalan). */
export function parseQuotePoints(v: unknown): QuotePoints | null {
  if (!isObj(v) || !Array.isArray(v.groups)) return null;
  const balance = nonNegInt(v.balance_available);
  const value = nonNegInt(v.point_value_idr);
  if (balance === null || value === null || value < 1) return null;
  const groups: QuotePointsGroup[] = [];
  for (const g of v.groups) {
    if (!isObj(g) || !isChannelCode(g.channel_code)) continue;
    if (groups.some((x) => x.channel_code === g.channel_code)) continue;
    const min = nonNegInt(g.min_points) ?? 0;
    const max = nonNegInt(g.max_points) ?? 0;
    groups.push({
      channel_code: g.channel_code,
      redeem_allowed: g.redeem_allowed === true && max > 0,
      min_points: Math.max(1, min),
      max_points: max,
      max_value_idr: nonNegInt(g.max_value_idr) ?? max * value,
    });
  }
  return { balance_available: balance, point_value_idr: value, groups };
}

/** True bila ada minimal satu kelompok yang boleh memakai poin. */
export function canRedeemAny(p: QuotePoints | null): p is QuotePoints {
  return p !== null && p.balance_available > 0 && p.groups.some((g) => g.redeem_allowed);
}

// ---------- alokasi poin per kanal ----------

export type PointsError = 'invalid' | 'not_allowed' | 'below_minimum' | 'exceeds_max' | 'cover_full';

export interface AllocationLine {
  channel_code: string;
  points: number;
  value_idr: number;
  cash_idr: number;
  error: PointsError | null;
}

export interface Allocation {
  /** Hanya kanal dengan input > 0 atau input tak valid. */
  lines: AllocationLine[];
  total_points: number;
  total_value_idr: number;
  /** Σ amount_payable semua kelompok − nilai poin. */
  total_cash_idr: number;
  insufficient_balance: boolean;
  valid: boolean;
  /** Badan `points_redeem` untuk `POST /v1/orders` (hanya entri valid > 0). */
  request: { channel_code: string; points: number }[];
}

/** '' -> null (tidak dipakai); bukan digit murni / terlalu panjang -> 'invalid'. */
export function parsePointsInput(raw: string): number | null | 'invalid' {
  const t = raw.trim();
  if (t === '') return null;
  if (!/^[0-9]{1,9}$/.test(t)) return 'invalid';
  return Number(t);
}

export interface PayableGroup {
  channel_code: string;
  amount_payable: number;
}

/**
 * Hitung alokasi dari isian pelanggan. `inputs`: channel_code -> teks. Kanal tanpa isian / 0 dilewati.
 * `valid=false` bila ada baris bermasalah atau Σ poin > saldo tersedia.
 */
export function resolveAllocation(points: QuotePoints | null, groups: readonly PayableGroup[], inputs: Record<string, string>): Allocation {
  const totalPayable = groups.reduce((n, g) => n + g.amount_payable, 0);
  const empty: Allocation = { lines: [], total_points: 0, total_value_idr: 0, total_cash_idr: totalPayable, insufficient_balance: false, valid: true, request: [] };
  if (!points) return empty;

  const lines: AllocationLine[] = [];
  for (const g of groups) {
    const parsed = parsePointsInput(inputs[g.channel_code] ?? '');
    if (parsed === null || parsed === 0) continue;
    if (parsed === 'invalid') {
      lines.push({ channel_code: g.channel_code, points: 0, value_idr: 0, cash_idr: g.amount_payable, error: 'invalid' });
      continue;
    }
    const info = points.groups.find((x) => x.channel_code === g.channel_code);
    const value = parsed * points.point_value_idr;
    const cash = g.amount_payable - value;
    let error: PointsError | null = null;
    if (!info || !info.redeem_allowed) error = 'not_allowed';
    else if (parsed < info.min_points) error = 'below_minimum';
    else if (parsed > info.max_points) error = 'exceeds_max';
    else if (cash < 1) error = 'cover_full';
    lines.push({ channel_code: g.channel_code, points: parsed, value_idr: value, cash_idr: cash, error });
  }

  const good = lines.filter((l) => l.error === null);
  const totalPoints = good.reduce((n, l) => n + l.points, 0);
  const totalValue = good.reduce((n, l) => n + l.value_idr, 0);
  const insufficient = totalPoints > points.balance_available;
  return {
    lines,
    total_points: totalPoints,
    total_value_idr: totalValue,
    total_cash_idr: totalPayable - totalValue,
    insufficient_balance: insufficient,
    valid: lines.every((l) => l.error === null) && !insufficient,
    request: good.map((l) => ({ channel_code: l.channel_code, points: l.points })),
  };
}

/** Poin yang masih bisa dialokasikan ke `channel` (saldo dikurangi isian valid kanal lain), dibatasi `max_points` kanal. */
export function suggestMaxPoints(points: QuotePoints, groups: readonly PayableGroup[], inputs: Record<string, string>, channel: string): number {
  const info = points.groups.find((g) => g.channel_code === channel);
  if (!info || !info.redeem_allowed) return 0;
  let others = 0;
  for (const g of groups) {
    if (g.channel_code === channel) continue;
    const p = parsePointsInput(inputs[g.channel_code] ?? '');
    if (typeof p === 'number') others += p;
  }
  const room = Math.max(0, points.balance_available - others);
  const best = Math.min(info.max_points, room);
  return best >= info.min_points ? best : 0;
}

export function pointsErrorMessage(error: PointsError, info: QuotePointsGroup | undefined): string {
  switch (error) {
    case 'invalid':
      return 'Masukkan jumlah poin dengan angka bulat.';
    case 'not_allowed':
      return 'Poin tidak dapat dipakai untuk pesanan ini.';
    case 'below_minimum':
      return `Minimal ${info?.min_points ?? 1} poin untuk ditukarkan.`;
    case 'exceeds_max':
      return `Maksimal ${info?.max_points ?? 0} poin (${formatRupiah(info?.max_value_idr ?? 0)}) untuk pesanan ini.`;
    case 'cover_full':
      return 'Poin tidak boleh menutup seluruh pembayaran; sisakan minimal Rp1 untuk dibayar tunai.';
  }
}

export const INSUFFICIENT_BALANCE_MESSAGE = 'Total poin melebihi saldo yang tersedia.';

/**
 * Validasi ulang `points_redeem` di Route Handler (sebelum diteruskan ke BFF): maksimal satu entri per kanal, `points`
 * bilangan bulat > 0. Hanya dua field kontrak yang diteruskan. undefined/null/[] -> `undefined` (field dihilangkan);
 * bentuk salah -> null (400).
 */
export function sanitizePointsRedeem(v: unknown): { channel_code: string; points: number }[] | undefined | null {
  if (v === undefined || v === null) return undefined;
  if (!Array.isArray(v) || v.length > 10) return null;
  const out: { channel_code: string; points: number }[] = [];
  for (const e of v) {
    if (!isObj(e) || !isChannelCode(e.channel_code)) return null;
    const p = e.points;
    if (typeof p !== 'number' || !Number.isSafeInteger(p) || p < 1 || p > 1_000_000_000) return null;
    if (out.some((x) => x.channel_code === e.channel_code)) return null;
    out.push({ channel_code: e.channel_code, points: p });
  }
  return out.length > 0 ? out : undefined;
}

// ---------- info poin hangus (§14.D.6) ----------

export interface PointsExpiry {
  inactive_since: string | null;
  expires_on: string | null;
  notice_sent: boolean;
}

/** `YYYY-MM-DD` (atau ISO yang diawali tanggal) valid -> `YYYY-MM-DD`; selain itu null. */
export function normalizeDate(v: unknown): string | null {
  if (typeof v !== 'string') return null;
  const m = /^(\d{4})-(\d{2})-(\d{2})(?:$|T)/.exec(v.trim());
  if (!m) return null;
  const y = Number(m[1]);
  const mo = Number(m[2]);
  const d = Number(m[3]);
  const t = new Date(Date.UTC(y, mo - 1, d));
  if (t.getUTCFullYear() !== y || t.getUTCMonth() !== mo - 1 || t.getUTCDate() !== d) return null;
  return `${m[1]}-${m[2]}-${m[3]}`;
}

export function parsePointsExpiry(v: unknown): PointsExpiry | null {
  if (!isObj(v)) return null;
  return {
    inactive_since: normalizeDate(v.inactive_since),
    expires_on: normalizeDate(v.expires_on),
    notice_sent: v.notice_sent === true,
  };
}

/** Tanggal kalender (tanpa zona waktu) dalam format Indonesia, mis. "12 Januari 2027". Tanggal rusak -> ''. */
export function formatDateId(date: string): string {
  const n = normalizeDate(date);
  if (!n) return '';
  const [y, m, d] = n.split('-').map(Number) as [number, number, number];
  return new Intl.DateTimeFormat('id-ID', { dateStyle: 'long', timeZone: 'UTC' }).format(new Date(Date.UTC(y, m - 1, d)));
}

const WIB_OFFSET_MS = 7 * 3_600_000;

/** Selisih hari kalender (zona Asia/Jakarta) dari "hari ini" ke `date`. Negatif = sudah lewat. */
export function daysUntil(date: string, nowMs: number): number | null {
  const n = normalizeDate(date);
  if (!n) return null;
  const [y, m, d] = n.split('-').map(Number) as [number, number, number];
  const today = new Date(nowMs + WIB_OFFSET_MS);
  const todayUtc = Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate());
  return Math.round((Date.UTC(y, m - 1, d) - todayUtc) / 86_400_000);
}

export interface ExpiryNotice {
  points: number;
  dateLabel: string;
  daysLeft: number;
  urgent: boolean;
  text: string;
}

/**
 * Info "poin akan hangus": hanya bila `expires_on` terisi (BFF hanya mengisinya bila ≤ 60 hari lagi) DAN saldo > 0.
 * Jumlah yang hangus = seluruh saldo (hangus karena tidak aktif menghanguskan semua lot, §14.D.3).
 */
export function expiryNotice(expiry: PointsExpiry | null, balance: number, nowMs: number): ExpiryNotice | null {
  if (!expiry || !expiry.expires_on || !Number.isFinite(balance) || balance <= 0) return null;
  const days = daysUntil(expiry.expires_on, nowMs);
  if (days === null) return null;
  const dateLabel = formatDateId(expiry.expires_on);
  const when = days <= 0 ? 'hari ini' : days === 1 ? 'besok' : `pada ${dateLabel} (${days} hari lagi)`;
  const n = new Intl.NumberFormat('id-ID').format(balance);
  return {
    points: balance,
    dateLabel,
    daysLeft: Math.max(0, days),
    urgent: days <= 7,
    text: `${n} poin Anda akan hangus ${when} bila akun tetap tidak aktif. Berbelanja di EFN sebelum tanggal itu menunda hangusnya poin.`,
  };
}

/** `expiryNotice` dengan jam sekarang (dipanggil dari Server Component tanpa `Date.now()` di badan render). */
export function currentExpiryNotice(expiry: PointsExpiry | null, balance: number): ExpiryNotice | null {
  return expiryNotice(expiry, balance, Date.now());
}

// ---------- pesan error poin dari BFF (checkout) ----------

/** Kode yang berarti isian poin harus diperbaiki pelanggan -> klien menghitung ulang quote (saldo/batas bisa berubah). */
export const POINTS_REFRESH_CODES: readonly string[] = ['insufficient_points', 'below_minimum', 'exceeds_max_pct', 'points_cover_full_amount'];
