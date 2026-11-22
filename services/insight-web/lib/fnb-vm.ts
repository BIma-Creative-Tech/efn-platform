/**
 * View-model murni halaman F&B persona (docs/04 §28.15.9, G52): ketersediaan pembanding tahun lalu, persentase, dan
 * kalimat keterangan. Tanpa I/O agar bisa diuji.
 */
import { fmtDate } from './format';

export interface CoverageFirstDay {
  company_code: string;
  first_day: string;
}

export interface YoyStatus {
  /** true bila SEMUA PT dalam cakupan punya data penjualan sejak awal rentang tahun lalu. */
  available: boolean;
  /** PT yang data penjualannya baru mulai setelah awal rentang tahun lalu (atau tidak punya data sama sekali). */
  missing: string[];
  /** Kalimat untuk pengguna; null bila pembanding tersedia. */
  note: string | null;
  /** Per PT: pembanding tersedia? */
  forCode: (code: string) => boolean;
}

/**
 * Pembanding tahun lalu hanya bermakna bila `first_day <= yoyFrom` (§28.15.9 `fexec_yoy_coverage`). `rows` null = query cakupan
 * gagal: pembanding diperlakukan tidak tersedia (lebih baik "n/a" daripada persentase yang menyesatkan).
 */
export function yoyStatus(rows: readonly CoverageFirstDay[] | null, yoyFrom: string): YoyStatus {
  if (rows === null) {
    return {
      available: false,
      missing: [],
      note: 'Pembanding tahun lalu belum dapat diverifikasi (data cakupan gagal dimuat).',
      forCode: () => false,
    };
  }
  if (rows.length === 0) {
    return { available: false, missing: [], note: 'Data tahun lalu belum tersedia.', forCode: () => false };
  }
  const ok = new Map<string, boolean>();
  for (const r of rows) {
    // Satu PT bisa punya beberapa baris (per brand): cukup satu yang terlambat untuk menandai PT itu belum lengkap.
    ok.set(r.company_code, (ok.get(r.company_code) ?? true) && r.first_day <= yoyFrom);
  }
  const missing = Array.from(ok.entries()).filter(([, v]) => !v).map(([k]) => k).sort();
  const available = missing.length === 0;
  const earliest = rows.reduce((m, r) => (r.first_day > m ? r.first_day : m), rows[0].first_day);
  return {
    available,
    missing,
    note: available
      ? null
      : `Data tahun lalu belum tersedia untuk ${missing.join(', ')}: data penjualan baru ada sejak ${fmtDate(earliest)} (pembanding mulai ${fmtDate(yoyFrom)}).`,
    forCode: (code) => ok.get(code) === true,
  };
}

/** Persentase `a / b * 100`; null bila b <= 0 (bukan 0 atau Infinity). */
export const pctOfSafe = (a: number, b: number): number | null => (b > 0 && Number.isFinite(a) ? (a / b) * 100 : null);

/** Perubahan persen `(cur - prev) / prev * 100`; null bila pembanding tidak ada/nol. */
export const growthPct = (cur: number, prev: number): number | null => (prev > 0 && Number.isFinite(cur) ? ((cur - prev) / prev) * 100 : null);
