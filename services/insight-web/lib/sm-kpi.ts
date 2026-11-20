/**
 * Perhitungan KPI supermarket dari jumlah mentah view (docs/04 §28.13.8c/§28.13.9) dan status warna dari ambang
 * efektif. Murni. Rasio selalu dihitung ulang dari jumlah mentah (bukan rata-rata rasio), dan penyebut 0 menghasilkan null
 * ("belum ada data"), tidak pernah 0 palsu.
 *
 * Basis uang (L-202): Omzet = termasuk PPN; margin/GMROI/waste% = tanpa PPN. Margin % TIDAK PERNAH dihitung dari omzet
 * termasuk PPN (fungsi marginPct hanya menerima net_sales tanpa PPN).
 */
import type { Status } from './thresholds';
import { fmtCountdown } from './sm-format';
import type { ProposalState } from './sm-parse';
import type { SmThresholds } from './sm-thresholds';

export const pct = (a: number, b: number): number | null => (b > 0 ? (a / b) * 100 : null);
export const pctChange = (cur: number, prev: number): number | null => (prev > 0 ? ((cur - prev) / prev) * 100 : null);
export const ppChange = (cur: number | null, prev: number | null): number | null => (cur === null || prev === null ? null : cur - prev);

/** Margin kotor % = gross_margin / net_sales (tanpa PPN). */
export const marginPct = (grossMargin: number, netSalesExclTax: number): number | null => pct(grossMargin, netSalesExclTax);
/** Cakupan HPP = net_sales_with_cost / net_sales (porsi omzet yang punya HPP). */
export const costCoveragePct = (withCost: number, netSales: number): number | null => pct(withCost, netSales);
export const oosRate = (oos: number, universe: number): number | null => pct(oos, universe);
export const wastePct = (waste: number, base: number): number | null => pct(waste, base);

/** WAPE = Σ|aktual − forecast| / Σ aktual; bias = (Σ forecast − Σ aktual) / Σ aktual (ADR-046). */
export function wapeBias(absErr: number, actual: number, forecast: number): { wape: number | null; bias: number | null } {
  if (!(actual > 0)) return { wape: null, bias: null };
  return { wape: (absErr / actual) * 100, bias: ((forecast - actual) / actual) * 100 };
}

// ---------------------------------------------------------------- GMROI
export interface GmroiMonthRow {
  /** "YYYY-MM-DD" (hari pertama bulan). */
  month: string;
  outletType: string;
  gm: number;
  stockDaily: number;
  stockMonthEnd: number;
  days: number;
}
export interface GmroiOpts {
  period: 'month' | 'quarter' | 'year';
  stockAvg: 'daily' | 'month_end';
  includeDc: boolean;
  annualize: boolean;
}
export interface GmroiResult {
  value: number | null;
  /** Bulan terakhir yang dipakai ("YYYY-MM-01"), atau null. */
  lastMonth: string | null;
  months: number;
}

/** Bulan kalender berikutnya dari "YYYY-MM-01". */
function nextMonth(m: string): string {
  const [y, mo] = m.split('-').map(Number);
  const d = new Date(Date.UTC(y, mo, 1));
  return d.toISOString().slice(0, 10);
}

/**
 * GMROI = Σ gross_margin / rata-rata nilai persediaan HPP, untuk bulan LENGKAP terakhir ≤ `to` (hari terakhir periode) dan
 * mundur selama periode (1/3/12 bulan). Bulan berjalan (belum lengkap) tidak dipakai: margin separuh bulan dibagi persediaan
 * separuh bulan tampak lebih kecil. `includeDc=false` membuang baris outlet_type 'dc' (penyebut tingkat PT tanpa DC).
 * annualize: × 365 / jumlah hari bulan yang dipakai. Tidak ada bulan lengkap / persediaan 0 -> value null ("Menunggu data").
 */
export function computeGmroi(rows: readonly GmroiMonthRow[], opts: GmroiOpts, to: string): GmroiResult {
  const byMonth = new Map<string, { gm: number; stock: number; days: number }>();
  for (const r of rows) {
    if (!opts.includeDc && r.outletType === 'dc') continue;
    const cur = byMonth.get(r.month) ?? { gm: 0, stock: 0, days: r.days };
    cur.gm += r.gm;
    cur.stock += opts.stockAvg === 'daily' ? r.stockDaily : r.stockMonthEnd;
    cur.days = Math.max(cur.days, r.days);
    byMonth.set(r.month, cur);
  }
  const complete = Array.from(byMonth.keys())
    .filter((m) => nextMonth(m) <= addOneDay(to))
    .sort();
  if (complete.length === 0) return { value: null, lastMonth: null, months: 0 };
  const want = opts.period === 'month' ? 1 : opts.period === 'quarter' ? 3 : 12;
  const used = complete.slice(-want);
  let gm = 0;
  let stock = 0;
  let days = 0;
  for (const m of used) {
    const x = byMonth.get(m) as { gm: number; stock: number; days: number };
    gm += x.gm;
    stock += x.stock;
    days += x.days;
  }
  const avgStock = stock / used.length;
  if (!(avgStock > 0)) return { value: null, lastMonth: used[used.length - 1], months: used.length };
  let v = gm / avgStock;
  if (opts.annualize && days > 0) v *= 365 / days;
  return { value: v, lastMonth: used[used.length - 1], months: used.length };
}

/** "YYYY-MM-DD" + 1 hari (sebuah bulan lengkap bila hari pertama bulan berikutnya ≤ to + 1). */
function addOneDay(iso: string): string {
  const [y, m, d] = iso.split('-').map(Number);
  return new Date(Date.UTC(y, m - 1, d + 1)).toISOString().slice(0, 10);
}

// ---------------------------------------------------------------- fill rate DC
export interface FillSums {
  sumLeast: number;
  sumApproved: number;
  linesFull: number;
  lines: number;
}
/** Angka utama sesuai `fill.level`: 'qty' = Σ least(basis, approved)/Σ approved; 'line' = baris terpenuhi/baris. */
export function fillRate(s: FillSums, level: 'qty' | 'line'): number | null {
  return level === 'qty' ? pct(s.sumLeast, s.sumApproved) : pct(s.linesFull, s.lines);
}

// ---------------------------------------------------------------- status warna
const upIsBad = (v: number | null, warn: number, bad: number): Status => {
  if (v === null || !Number.isFinite(v)) return 'neutral';
  if (v > bad) return 'bad';
  if (v > warn) return 'warn';
  return 'good';
};

export const oosStatus = (p: number | null, t: SmThresholds): Status => upIsBad(p, t.oosWarn, t.oosBad);
export const wasteStatus = (p: number | null, t: SmThresholds): Status => upIsBad(p, t.wasteWarn, t.wasteBad);
export const wapeStatus = (p: number | null, t: SmThresholds): Status => upIsBad(p, t.wapeWarn, t.wapeBad);
export const biasStatus = (p: number | null, t: SmThresholds): Status => (p === null ? 'neutral' : upIsBad(Math.abs(p), t.biasWarn, t.biasBad));

/** Fill rate: ≥ good = Baik; ≥ warn = Perlu dicek; < warn = Bermasalah (naik = baik). */
export function fillStatus(p: number | null, t: SmThresholds): Status {
  if (p === null || !Number.isFinite(p)) return 'neutral';
  if (p >= t.fillGood) return 'good';
  if (p >= t.fillWarn) return 'warn';
  return 'bad';
}

/** PO terlambat (jumlah): < warn Baik; ≥ bad Bermasalah; di antaranya Perlu dicek. */
export function poLateStatus(n: number, t: SmThresholds): Status {
  if (n >= t.poLateBad) return 'bad';
  if (n >= t.poLateWarn) return 'warn';
  return 'good';
}

export interface PriceStatus {
  status: Status;
  label: string;
}
/**
 * Harga vs median pasar (CM-5): > warn "Di atas pasar" (Perlu dicek); > bad Bermasalah; < −below "Di bawah pasar" (info,
 * margin mungkin terbuang); sampel < minimum = tidak ditampilkan statusnya. Selalu ikon + kata (label disertakan).
 */
export function priceStatus(diffPct: number | null, sampleN: number, t: SmThresholds): PriceStatus {
  if (diffPct === null || !Number.isFinite(diffPct) || sampleN < t.priceMinSamples) return { status: 'neutral', label: 'Sampel belum cukup' };
  if (diffPct > t.priceBad) return { status: 'bad', label: 'Jauh di atas pasar' };
  if (diffPct > t.priceWarn) return { status: 'warn', label: 'Di atas pasar' };
  if (diffPct < -t.priceBelow) return { status: 'neutral', label: 'Di bawah pasar' };
  return { status: 'good', label: 'Sekitar pasar' };
}

// ---------------------------------------------------------------- usulan order (ST-1, DC-2)
export interface CutoffInfo {
  status: Status;
  /** Sisa milidetik ke cut-off (negatif = lewat); null bila cut-off tidak diketahui. */
  remainingMs: number | null;
  countdown: string;
  label: string;
  /** true = tidak boleh menyatakan Disetujui/Diusulkan (data > batas usang, §4.1 ST-1). */
  uncertain: boolean;
}

const STATE_LABEL: Record<ProposalState, string> = {
  draft: 'Draf',
  proposed: 'Diusulkan (belum disetujui)',
  approved: 'Disetujui',
  rejected: 'Ditolak',
  expired: 'Kedaluwarsa (tidak dikirim otomatis)',
  unknown: 'Tidak diketahui',
};
export const proposalStateLabel = (s: ProposalState): string => STATE_LABEL[s];

/**
 * Status usulan order (ST-1, spesifikasi §4.1): Disetujui = Baik; Ditolak = Perlu dicek; Kedaluwarsa = Bermasalah;
 * Draf/Diusulkan: batas ≤ `cutoffUrgentMin` menit = Bermasalah ("Segera"), selain itu Perlu dicek. Data usang (`stale`)
 * -> "Tidak pasti" tanpa menyatakan status.
 */
export function cutoffInfo(state: ProposalState, cutoffAt: Date | null, now: Date, t: SmThresholds, stale: boolean): CutoffInfo {
  const remainingMs = cutoffAt === null ? null : cutoffAt.getTime() - now.getTime();
  const countdown = remainingMs === null ? '' : fmtCountdown(remainingMs);
  if (stale) return { status: 'unknown', remainingMs, countdown, label: 'Tidak pasti', uncertain: true };
  let status: Status;
  let label = STATE_LABEL[state];
  switch (state) {
    case 'approved':
      status = 'good';
      break;
    case 'rejected':
      status = 'warn';
      break;
    case 'expired':
      status = 'bad';
      break;
    case 'draft':
    case 'proposed': {
      const urgent = remainingMs !== null && remainingMs <= t.cutoffUrgentMin * 60_000;
      status = urgent ? 'bad' : 'warn';
      if (urgent) label = `Segera: ${label}`;
      break;
    }
    default:
      status = 'unknown';
  }
  return { status, remainingMs, countdown, label, uncertain: false };
}
