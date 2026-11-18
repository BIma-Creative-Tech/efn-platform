/**
 * Format angka/tanggal id-ID (docs/design/insight-web.md §1). Ditulis sendiri (tanpa Intl/ICU) agar
 * hasil deterministik di semua runtime: titik = pemisah ribuan, koma = desimal, "jt/M/T" untuk Rupiah ringkas.
 */
export type ValueFmt = 'idr' | 'idr-short' | 'int' | 'num' | 'pct' | 'pp';

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'Mei', 'Jun', 'Jul', 'Agu', 'Sep', 'Okt', 'Nov', 'Des'];
export const WEEKDAYS = ['Sen', 'Sel', 'Rab', 'Kam', 'Jum', 'Sab', 'Min'];

/** Angka id-ID: ribuan '.', desimal ','; desimal nol di ujung dibuang. */
export function fmtNumber(n: number, maxFrac = 0): string {
  if (!Number.isFinite(n)) return '-';
  const abs = Math.abs(n);
  const [intPart, frac = ''] = abs.toFixed(maxFrac).split('.');
  const d = frac.replace(/0+$/, '');
  const grouped = intPart.replace(/\B(?=(\d{3})+(?!\d))/g, '.');
  const out = d ? `${grouped},${d}` : grouped;
  return n < 0 && out !== '0' ? `-${out}` : out;
}

export function fmtInt(n: number): string {
  return fmtNumber(Math.round(n), 0);
}

/** Kuantitas: desimal maks 1 bila non-bulat. */
export function fmtQty(n: number): string {
  return fmtNumber(n, Number.isInteger(n) ? 0 : 1);
}

export function fmtIdr(n: number): string {
  if (!Number.isFinite(n)) return '-';
  const r = Math.round(n);
  return r < 0 ? `-Rp ${fmtNumber(-r)}` : `Rp ${fmtNumber(r)}`;
}

const UNITS: [number, string][] = [
  [1e12, 'T'],
  [1e9, 'M'],
  [1e6, 'jt'],
];

/** Rupiah ringkas: < 1 jt penuh; >= 1 jt "1,2 jt"; >= 1 miliar "M"; >= 1 triliun "T" (1 desimal, ",0" dibuang). */
export function fmtIdrShort(n: number): string {
  if (!Number.isFinite(n)) return '-';
  const abs = Math.abs(n);
  if (abs < 1e6) return fmtIdr(n);
  let idx = UNITS.findIndex(([u]) => abs >= u);
  let v = Math.round((abs / UNITS[idx][0]) * 10) / 10;
  if (v >= 1000 && idx > 0) {
    idx -= 1;
    v = Math.round((abs / UNITS[idx][0]) * 10) / 10;
  }
  const s = `Rp ${fmtNumber(v, 1)} ${UNITS[idx][1]}`;
  return n < 0 ? `-${s}` : s;
}

export function fmtPct(n: number): string {
  return `${fmtNumber(n, 1)}%`;
}

export function fmtPp(n: number): string {
  return `${fmtNumber(n, 1)} pp`;
}

/** Delta bertanda eksplisit: "+4,2%" / "-1,5 pp". */
export function fmtDelta(n: number, kind: 'pct' | 'pp'): string {
  const body = kind === 'pct' ? fmtPct(Math.abs(n)) : fmtPp(Math.abs(n));
  if (!Number.isFinite(n)) return '-';
  return `${n > 0 ? '+' : n < 0 ? '-' : ''}${body}`;
}

export function fmtValue(fmt: ValueFmt, n: number): string {
  switch (fmt) {
    case 'idr':
      return fmtIdr(n);
    case 'idr-short':
      return fmtIdrShort(n);
    case 'int':
      return fmtInt(n);
    case 'num':
      return fmtQty(n);
    case 'pct':
      return fmtPct(n);
    case 'pp':
      return fmtPp(n);
  }
}

/** "18 Nov 2026" dari "2026-11-18". */
export function fmtDate(iso: string): string {
  const [y, m, d] = iso.split('-').map(Number);
  return `${d} ${MONTHS[m - 1]} ${y}`;
}

/** "9 Nov". */
export function fmtDayShort(iso: string): string {
  const [, m, d] = iso.split('-').map(Number);
  return `${d} ${MONTHS[m - 1]}`;
}

/** "Nov 2026" dari tanggal ISO mana pun di bulan itu. */
export function fmtMonth(iso: string): string {
  const [y, m] = iso.split('-').map(Number);
  return `${MONTHS[m - 1]} ${y}`;
}

/** "1 Nov - 17 Nov 2026" (tahun sekali bila sama). */
export function fmtRange(from: string, to: string): string {
  const [fy] = from.split('-');
  const [ty] = to.split('-');
  return fy === ty ? `${fmtDayShort(from)} - ${fmtDate(to)}` : `${fmtDate(from)} - ${fmtDate(to)}`;
}

/** "3 jam lalu", "1 hari 2 jam lalu". */
export function fmtAge(hours: number): string {
  if (!Number.isFinite(hours) || hours < 0) return 'tidak diketahui';
  if (hours < 1) return 'kurang dari 1 jam lalu';
  const h = Math.floor(hours);
  if (h < 24) return `${h} jam lalu`;
  const days = Math.floor(h / 24);
  const rest = h % 24;
  return rest === 0 ? `${days} hari lalu` : `${days} hari ${rest} jam lalu`;
}

export function escapeHtml(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}
