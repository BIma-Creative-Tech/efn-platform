/**
 * Logika murni halaman Toko F&B (docs/04 §28.15.9, G52): hari acuan, pemilihan cabang yang dibatasi `brs` sesi, dan
 * pemisahan 14 hari menjadi pekan ini vs pekan lalu. Tanpa I/O agar bisa diuji.
 */
import { addDays, eachDay, isIsoDate } from './dates';

/** `<esb_brand>:<branch_id>` (format filter `branch` §26.9 = klaim `brs`). */
export const STORE_BRANCH_RE = /^(pb|wtz|bacha|imd|cup):[1-9][0-9]{0,9}$/;
export const MAX_STORE_BRANCH = 50;
/** Hari acuan tidak boleh lebih dari 366 hari ke belakang (sama dengan batas rentang filter global). */
export const MAX_DAY_BACK = 366;

export interface DailyRow {
  sales_date: string;
  net_sum: number;
  trx: number;
}

const finite = (n: unknown): number => {
  const v = Number(n ?? 0);
  return Number.isFinite(v) ? v : 0;
};

/**
 * Hari acuan: `requested` bila sah (ISO, <= kemarin WIB, <= 366 hari ke belakang), selain itu hari terakhir yang punya penjualan
 * dalam `rows` (<= kemarin), selain itu kemarin. `rows` = hasil `fstore_daily_vs_lastweek` dengan ref_day = kemarin
 * (14 hari terakhir). `adjusted` true bila `requested` diberikan tetapi tidak sah.
 */
export function pickStoreDay(
  requested: string | undefined,
  rows: readonly DailyRow[] | null,
  yesterday: string,
): { day: string; adjusted: boolean; explicit: boolean } {
  if (requested !== undefined && requested !== '') {
    if (isIsoDate(requested) && requested <= yesterday && requested >= addDays(yesterday, -MAX_DAY_BACK)) {
      return { day: requested, adjusted: false, explicit: true };
    }
    return { ...pickStoreDay(undefined, rows, yesterday), adjusted: true };
  }
  const withSales = (rows ?? []).filter((r) => r.sales_date <= yesterday && (finite(r.trx) > 0 || finite(r.net_sum) !== 0));
  const latest = withSales.reduce<string | null>((m, r) => (m === null || r.sales_date > m ? r.sales_date : m), null);
  return { day: latest ?? yesterday, adjusted: false, explicit: false };
}

/**
 * Cabang terpilih dari parameter `branch`: format sah, dibuang bila di luar `allowed` (= `brs` sesi ber-cabang; kosong =
 * tanpa batas), unik, maks 50. `adjusted` true bila ada nilai yang dibuang.
 */
export function parseStoreBranches(requested: readonly string[], allowed: readonly string[]): { branch: string[]; adjusted: boolean } {
  const allow = allowed.length > 0 ? new Set(allowed) : null;
  const seen = new Set<string>();
  let adjusted = false;
  for (const raw of requested) {
    const s = raw.trim().toLowerCase();
    if (s === '') continue;
    if (!STORE_BRANCH_RE.test(s) || Number(s.slice(s.indexOf(':') + 1)) > 4_294_967_295 || (allow !== null && !allow.has(s))) {
      adjusted = true;
      continue;
    }
    seen.add(s);
  }
  let branch = Array.from(seen).sort();
  if (branch.length > MAX_STORE_BRANCH) {
    branch = branch.slice(0, MAX_STORE_BRANCH);
    adjusted = true;
  }
  return { branch, adjusted };
}

export interface WeekSplit {
  /** Hari pekan ini: day-6 .. day (urut naik). */
  days: string[];
  /** Hari yang sama pekan lalu: day-13 .. day-7 (sejajar dengan `days`). */
  lastWeekDays: string[];
  net: (number | null)[];
  netLastWeek: (number | null)[];
  trx: (number | null)[];
  trxLastWeek: (number | null)[];
}

/** Jumlahkan baris (lintas brand/cabang) per tanggal; hari tanpa baris = null (bukan nol: data belum masuk). */
export function splitWeeks(rows: readonly DailyRow[], day: string): WeekSplit {
  const net = new Map<string, number>();
  const trx = new Map<string, number>();
  for (const r of rows) {
    net.set(r.sales_date, (net.get(r.sales_date) ?? 0) + finite(r.net_sum));
    trx.set(r.sales_date, (trx.get(r.sales_date) ?? 0) + finite(r.trx));
  }
  const days = eachDay(addDays(day, -6), day);
  const lastWeekDays = days.map((d) => addDays(d, -7));
  const pick = (m: Map<string, number>, ds: string[]): (number | null)[] => ds.map((d) => m.get(d) ?? null);
  return { days, lastWeekDays, net: pick(net, days), netLastWeek: pick(net, lastWeekDays), trx: pick(trx, days), trxLastWeek: pick(trx, lastWeekDays) };
}
