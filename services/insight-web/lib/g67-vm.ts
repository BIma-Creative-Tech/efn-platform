/**
 * Logika murni halaman `/waste` dan `/fill-rate` (docs/04 §31.9-§31.10, G67): parsing filter khusus halaman (`uclass`,
 * `supplier`), label kelas pemakaian, agregasi mingguan, dan penyusun spesifikasi grafik. Tanpa I/O; dapat diuji.
 * Nilai Rupiah waste = HPP jurnal item (positif = biaya keluar); qty selalu per satuan (tidak dijumlah lintas satuan).
 */
import { fmtSpan, type CoverageRow } from './coverage';
import { diffDays } from './dates';
import type { ChartSpec, ColorToken } from './chart-spec';
import { summarize } from './chart-spec';
import { fmtDayShort } from './format';

type Raw = Record<string, string | string[] | undefined>;

// ---------- cakupan untuk keadaan kosong ----------

/**
 * Nama entitas di `v_esb_coverage` untuk tiga sumber G67 BELUM terverifikasi (W-A memperluas view; nama pasti ada di DDL 24-*.sql).
 * Dicocokkan longgar: entitas yang namanya memuat salah satu kata kunci. Tidak ada kecocokan -> null (kalimat cakupan tidak tampil,
 * keadaan kosong tetap jujur "belum tersedia pada rentang ini").
 */
export const COVERAGE_KEYS = {
  pnl: ['profit_loss', 'pnl'],
  usage: ['item_journal', 'journal'],
  fill: ['transfer_request', 'gtr', 'delivery_ref'],
} as const;

export function coverageHint(rows: readonly CoverageRow[] | null, keys: readonly string[]): string | null {
  if (rows === null) return null;
  const mine = rows.filter((r) => keys.some((k) => r.entity === k || r.entity.includes(k)) && r.first_day && r.last_day);
  if (mine.length === 0) return null;
  const first = mine.reduce((m, r) => (r.first_day < m ? r.first_day : m), mine[0].first_day);
  const last = mine.reduce((m, r) => (r.last_day > m ? r.last_day : m), mine[0].last_day);
  return `Data tersedia ${fmtSpan(first, last)}.`;
}

// ---------- kelas pemakaian (/waste) ----------

export const USAGE_CLASSES = ['waste', 'internal_use', 'rnd', 'other', 'unclassified'] as const;
export type UsageClass = (typeof USAGE_CLASSES)[number];
export const DEFAULT_USAGE_CLASSES: readonly UsageClass[] = ['waste', 'internal_use'];

export const USAGE_LABEL: Readonly<Record<UsageClass, string>> = {
  waste: 'Waste',
  internal_use: 'Pemakaian internal',
  rnd: 'R&D',
  other: 'Lainnya (bukan biaya outlet)',
  unclassified: 'Belum terklasifikasi',
};

const USAGE_COLOR: Readonly<Record<UsageClass, ColorToken>> = {
  waste: 'bad',
  internal_use: 'c1',
  rnd: 'c3',
  other: 'muted',
  unclassified: 'c5',
};

export const usageLabel = (c: string): string => (USAGE_LABEL as Record<string, string>)[c] ?? c;

/** Rentang lebih dari 62 hari -> tren per minggu (§31.10). */
export const WEEKLY_AFTER_DAYS = 62;
export const isWeekly = (from: string, to: string): boolean => diffDays(from, to) + 1 > WEEKLY_AFTER_DAYS;

function all(raw: Raw, key: string): string[] {
  const v = raw[key];
  if (v === undefined) return [];
  return (Array.isArray(v) ? v : [v]).flatMap((s) => s.split(',')).map((s) => s.trim()).filter((s) => s !== '');
}

/** `uclass`: himpunan bagian kelas yang dikenal (maks 5, urutan kanonik); tak sah/kosong -> bawaan waste + pemakaian internal. */
export function parseUsageClasses(raw: Raw): { uclass: UsageClass[]; adjusted: boolean } {
  const given = all(raw, 'uclass');
  if (given.length === 0) return { uclass: [...DEFAULT_USAGE_CLASSES], adjusted: false };
  const ok = USAGE_CLASSES.filter((c) => given.includes(c));
  const adjusted = ok.length !== new Set(given).size;
  return ok.length === 0 ? { uclass: [...DEFAULT_USAGE_CLASSES], adjusted: true } : { uclass: ok, adjusted };
}

// ---------- pemasok (/fill-rate) ----------

export const MAX_SUPPLIERS = 10;
const SUPPLIER_RE = /^(pb|wtz|bacha|imd|cup):(\d{1,10})$/;

/** `supplier` = `<brand>:<branch_id>` (maks 10, unik); entri tak sah dibuang (adjusted). */
export function parseSuppliers(raw: Raw): { supplier: string[]; adjusted: boolean } {
  const given = Array.from(new Set(all(raw, 'supplier').map((s) => s.toLowerCase())));
  const ok = given.filter((s) => {
    const m = SUPPLIER_RE.exec(s);
    return m !== null && Number(m[2]) <= 4_294_967_295;
  });
  const cut = ok.slice(0, MAX_SUPPLIERS);
  return { supplier: cut, adjusted: cut.length !== given.length };
}

// ---------- rasio ----------

export const pctSafe = (num: number, den: number): number | null => (den > 0 && Number.isFinite(num) && Number.isFinite(den) ? (num / den) * 100 : null);

// ---------- grafik /waste ----------

export interface WasteTrendRow {
  bucket: string;
  usage_class: string;
  value_sum: number;
}

/** Batang bertumpuk per kelas; sumbu = hari (atau minggu mulai Senin bila `weekly`). */
export function wasteTrendSpec(rows: readonly WasteTrendRow[], weekly: boolean): ChartSpec {
  const buckets = Array.from(new Set(rows.map((r) => r.bucket))).sort();
  const classes = USAGE_CLASSES.filter((c) => rows.some((r) => r.usage_class === c));
  const at = new Map(rows.map((r) => [`${r.bucket}|${r.usage_class}`, r.value_sum]));
  const cats = buckets.map((b) => (weekly ? `Mgg ${fmtDayShort(b)}` : fmtDayShort(b)));
  const totals = buckets.map((b) => classes.reduce((s, c) => s + (at.get(`${b}|${c}`) ?? 0), 0));
  return {
    id: 'waste-trend',
    kind: 'bar',
    fmt: 'idr',
    axisFmt: 'idr-short',
    categories: cats,
    series: classes.map((c) => ({
      name: usageLabel(c),
      data: buckets.map((b) => at.get(`${b}|${c}`) ?? 0),
      color: USAGE_COLOR[c],
      stack: 'usage',
    })),
    summary: summarize(weekly ? 'Nilai pemakaian per minggu' : 'Nilai pemakaian per hari', cats, totals, 'idr-short', true),
  };
}

// ---------- grafik /fill-rate ----------

export interface WeeklyFillRow {
  week_start: string;
  fill_rate_line: number | null;
  pct_lines_full: number | null;
}

export function fillRateTrendSpec(rows: readonly WeeklyFillRow[]): ChartSpec {
  const cats = rows.map((r) => `Mgg ${fmtDayShort(r.week_start)}`);
  const line = rows.map((r) => r.fill_rate_line);
  return {
    id: 'fr-trend',
    kind: 'line',
    fmt: 'pct',
    categories: cats,
    series: [
      { name: 'Fill rate outlet (baris)', data: line, color: 'c1' },
      { name: '% baris penuh', data: rows.map((r) => r.pct_lines_full), color: 'c2', dashed: true },
    ],
    summary: summarize('Fill rate outlet (baris) per minggu', cats, line, 'pct', false),
  };
}

/** Batang horizontal fill rate per outlet (terendah dulu), dibatasi `limit` baris agar terbaca. */
export function fillRateByOutletSpec(rows: readonly { label: string; value: number | null }[], limit = 15): ChartSpec {
  const shown = rows.filter((r) => r.value !== null).slice(0, limit);
  const cats = shown.map((r) => r.label);
  const vals = shown.map((r) => r.value);
  return {
    id: 'fr-outlet',
    kind: 'barH',
    fmt: 'pct',
    categories: cats,
    // Tanpa ambang warna: target fill rate belum ditetapkan klien (Q-ESB31), jadi tidak ada baik/buruk yang diklaim.
    series: [{ name: 'Fill rate outlet (baris)', data: vals, color: 'c1' }],
    summary: summarize(`Fill rate outlet (baris) (${shown.length} terendah)`, cats, vals, 'pct', false),
  };
}
