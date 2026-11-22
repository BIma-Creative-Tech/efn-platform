/**
 * Utilitas tanggal ISO (YYYY-MM-DD) berbasis UTC; murni, tanpa Intl. Hari "kemarin" dihitung dalam WIB (UTC+7):
 * data harian lengkap hanya sampai H-1 (docs/04 §26.9).
 */
const ISO_RE = /^(\d{4})-(\d{2})-(\d{2})$/;
const DAY_MS = 86_400_000;

export function isIsoDate(s: unknown): s is string {
  if (typeof s !== 'string') return false;
  const m = ISO_RE.exec(s);
  if (!m) return false;
  const d = new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3])));
  return d.toISOString().slice(0, 10) === s;
}

function toMs(iso: string): number {
  const m = ISO_RE.exec(iso);
  if (!m) throw new Error('tanggal bukan ISO');
  return Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
}

export function addDays(iso: string, n: number): string {
  return new Date(toMs(iso) + n * DAY_MS).toISOString().slice(0, 10);
}

/** Tambah `n` tahun; 29 Feb pada tahun tujuan non-kabisat menjadi 28 Feb (bukan 1 Mar). */
export function addYears(iso: string, n: number): string {
  const m = ISO_RE.exec(iso);
  if (!m) throw new Error('tanggal bukan ISO');
  const y = Number(m[1]) + n;
  const mo = Number(m[2]) - 1;
  const lastDay = new Date(Date.UTC(y, mo + 1, 0)).getUTCDate();
  return new Date(Date.UTC(y, mo, Math.min(Number(m[3]), lastDay))).toISOString().slice(0, 10);
}

/** Tanggal 1 pada bulan yang sama dengan `iso`, digeser `n` bulan (n boleh negatif). */
export function startOfMonthShift(iso: string, n: number): string {
  const m = ISO_RE.exec(iso);
  if (!m) throw new Error('tanggal bukan ISO');
  return new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1 + n, 1)).toISOString().slice(0, 10);
}

/** Selisih hari b - a. */
export function diffDays(a: string, b: string): number {
  return Math.round((toMs(b) - toMs(a)) / DAY_MS);
}

/** Tanggal WIB (UTC+7) dari instan `now`. */
export function wibToday(now: Date): string {
  return new Date(now.getTime() + 7 * 3_600_000).toISOString().slice(0, 10);
}

export function wibYesterday(now: Date): string {
  return addDays(wibToday(now), -1);
}

export function eachDay(from: string, to: string): string[] {
  const out: string[] = [];
  for (let d = from; d <= to; d = addDays(d, 1)) out.push(d);
  return out;
}

/** Hari dalam minggu ISO: 1 = Senin ... 7 = Minggu. */
export function isoWeekday(iso: string): number {
  const d = new Date(toMs(iso)).getUTCDay();
  return d === 0 ? 7 : d;
}
