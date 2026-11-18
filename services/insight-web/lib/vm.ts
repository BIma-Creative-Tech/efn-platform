/**
 * Helper murni untuk menyusun view-model halaman (delta, rasio, "Lainnya", seri harian). Dapat diuji.
 */
import { brandColor, brandName } from './brands';
import type { ChartSeries } from './chart-spec';
import { addDays, diffDays } from './dates';

export const pctDelta = (cur: number, prev: number): number | null => (prev > 0 ? ((cur - prev) / prev) * 100 : null);
export const ppDelta = (cur: number | null, prev: number | null): number | null => (cur === null || prev === null ? null : cur - prev);
export const ratio = (a: number, b: number): number | null => (b > 0 ? a / b : null);
export const pctOf = (a: number, b: number): number | null => (b > 0 ? (a / b) * 100 : null);

/** Ambil `max` teratas; sisanya digabung ke "Lainnya" (dijumlah). Masukan sudah terurut menurun. */
export function withOther<T extends { label: string; value: number }>(items: T[], max: number): { label: string; value: number }[] {
  if (items.length <= max) return items.map(({ label, value }) => ({ label, value }));
  const head = items.slice(0, max - 1).map(({ label, value }) => ({ label, value }));
  const rest = items.slice(max - 1).reduce((s, x) => s + x.value, 0);
  return [...head, { label: 'Lainnya', value: rest }];
}

export interface DailyRow {
  sales_date: string;
  brand: string;
  net: number;
  trx: number;
}

/**
 * Seri tren harian: satu garis per brand untuk periode ini + garis putus "Periode lalu" (total) yang digeser ke
 * sumbu periode ini. `metric` menentukan nilai (net | trx | avg = net/trx total; avg hanya total).
 */
export function trendSeries(rows: DailyRow[], from: string, to: string, metric: 'net' | 'trx' | 'avg'): { days: string[]; series: ChartSeries[] } {
  const len = diffDays(from, to) + 1;
  const days: string[] = [];
  for (let i = 0; i < len; i += 1) days.push(addDays(from, i));
  const idx = new Map(days.map((d, i) => [d, i]));

  const brands = Array.from(new Set(rows.filter((r) => r.sales_date >= from).map((r) => r.brand))).sort();
  const perBrand = new Map<string, { net: number[]; trx: number[] }>();
  for (const b of brands) perBrand.set(b, { net: Array(len).fill(0), trx: Array(len).fill(0) });
  const total = { net: Array<number>(len).fill(0), trx: Array<number>(len).fill(0) };
  const prev = { net: Array<number>(len).fill(0), trx: Array<number>(len).fill(0) };

  for (const r of rows) {
    if (r.sales_date >= from) {
      const i = idx.get(r.sales_date);
      if (i === undefined) continue;
      const pb = perBrand.get(r.brand);
      if (pb) {
        pb.net[i] += r.net;
        pb.trx[i] += r.trx;
      }
      total.net[i] += r.net;
      total.trx[i] += r.trx;
    } else {
      const i = idx.get(addDays(r.sales_date, len));
      if (i === undefined) continue;
      prev.net[i] += r.net;
      prev.trx[i] += r.trx;
    }
  }

  const pick = (s: { net: number[]; trx: number[] }): (number | null)[] =>
    s.net.map((n, i) => (metric === 'net' ? n : metric === 'trx' ? s.trx[i] : s.trx[i] > 0 ? n / s.trx[i] : null));

  const series: ChartSeries[] = [];
  if (metric === 'avg' || brands.length <= 1) {
    series.push({ name: brands.length === 1 && metric !== 'avg' ? brandName(brands[0]) : 'Total', data: pick(total), color: brands.length === 1 ? brandColor(brands[0]) : 'accent' });
  } else {
    for (const b of brands.slice(0, 6)) {
      const pb = perBrand.get(b);
      if (pb) series.push({ name: brandName(b), data: pick(pb), color: brandColor(b) });
    }
  }
  series.push({ name: 'Periode lalu (total)', data: pick(prev), color: 'muted', dashed: true });
  return { days, series };
}
