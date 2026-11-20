/**
 * Format tampilan khusus halaman supermarket (docs/design/insight-supermarket.md §6.4). Murni dan deterministik
 * (tanpa Intl/ICU), melengkapi lib/format.ts. Semua waktu "hari ini"/jam memakai WIB (UTC+7).
 */
import { isoWeekday } from './dates';
import { fmtDate, fmtNumber, WEEKDAYS } from './format';
import { K_ANON } from './sm-defaults';

const WIB_MS = 7 * 3_600_000;
const pad2 = (n: number): string => String(n).padStart(2, '0');
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'Mei', 'Jun', 'Jul', 'Agu', 'Sep', 'Okt', 'Nov', 'Des'];

/** "14:45 WIB" (titik dua; Q-INS-S11 menunggu keputusan akhir). */
export function fmtClockWib(d: Date): string {
  const w = new Date(d.getTime() + WIB_MS);
  return `${pad2(w.getUTCHours())}:${pad2(w.getUTCMinutes())} WIB`;
}

/** Tanggal WIB "YYYY-MM-DD" dari instan. */
export function wibDateOf(d: Date): string {
  return new Date(d.getTime() + WIB_MS).toISOString().slice(0, 10);
}

/** "14:45 WIB" bila hari ini (WIB) relatif ke `now`, selain itu "20 Nov 14:45 WIB". */
export function fmtStampWib(d: Date, now: Date): string {
  if (wibDateOf(d) === wibDateOf(now)) return fmtClockWib(d);
  const w = new Date(d.getTime() + WIB_MS);
  return `${w.getUTCDate()} ${MONTHS[w.getUTCMonth()]} ${fmtClockWib(d)}`;
}

/** "Kam, 21 Nov 2026" dari "2026-11-21". */
export function fmtDeliveryDate(iso: string): string {
  return `${WEEKDAYS[isoWeekday(iso) - 1]}, ${fmtDate(iso)}`;
}

/** Umur relatif dari menit: "12 menit lalu", "3 jam lalu", "2 hari lalu". Negatif/NaN -> "baru saja". */
export function fmtAgeMin(min: number): string {
  if (!Number.isFinite(min) || min < 1) return 'baru saja';
  const m = Math.floor(min);
  if (m < 60) return `${m} menit lalu`;
  const h = Math.floor(m / 60);
  if (h < 48) return `${h} jam lalu`;
  return `${Math.floor(h / 24)} hari lalu`;
}

/** Hitung mundur cut-off dari sisa milidetik: "tersisa 3 j 15 m", "tersisa 42 m", "tersisa < 1 m", "batas lewat 12 m". */
export function fmtCountdown(remainingMs: number): string {
  if (!Number.isFinite(remainingMs)) return '';
  if (remainingMs <= 0) {
    const over = Math.floor(-remainingMs / 60_000);
    if (over < 1) return 'batas baru saja lewat';
    return over >= 60 ? `batas lewat ${Math.floor(over / 60)} j ${over % 60} m` : `batas lewat ${over} m`;
  }
  const total = Math.floor(remainingMs / 60_000);
  if (total < 1) return 'tersisa < 1 m';
  if (total < 60) return `tersisa ${total} m`;
  return `tersisa ${Math.floor(total / 60)} j ${total % 60} m`;
}

/** Sisa umur lot: 0 = "ED hari ini"; negatif = "lewat 2 hari"; positif = "sisa 3 hari". */
export function fmtDaysLeft(n: number): string {
  if (!Number.isFinite(n)) return '-';
  const d = Math.trunc(n);
  if (d === 0) return 'ED hari ini';
  return d < 0 ? `lewat ${-d} hari` : `sisa ${d} hari`;
}

/** GMROI "2,8x" (1 desimal). */
export function fmtRatioX(n: number): string {
  return `${fmtNumber(n, 1)}x`;
}

/** Cakupan stok "4,5 hari". */
export function fmtCover(n: number): string {
  return `${fmtNumber(n, 1)} hari`;
}

/**
 * Hitungan kecil di bawah k-anonim (default k = 10) ditekan menjadi teks "< 10", bukan 0 (§6.4/§6.6). null = tidak tersedia.
 */
export function fmtSuppressed(n: number | null, k: number = K_ANON): string {
  if (n === null || !Number.isFinite(n)) return '-';
  return n < k ? `< ${k}` : fmtNumber(Math.round(n), 0);
}

export const isSuppressed = (n: number | null, k: number = K_ANON): boolean => n !== null && Number.isFinite(n) && n < k;

/** Persen satu desimal dengan koma; null -> "n/a" (jangan "0" untuk data yang belum ada). */
export function fmtPctOrNa(n: number | null): string {
  return n === null || !Number.isFinite(n) ? 'n/a' : `${fmtNumber(n, 1)}%`;
}
