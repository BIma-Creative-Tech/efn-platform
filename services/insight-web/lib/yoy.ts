/**
 * Halaman "Tahun lalu vs sekarang" (/yoy, 24 Nov 2026): logika murni (tanpa I/O), diuji tests/yoy.test.ts.
 *
 * Aturan sebanding:
 *  - Pembanding = rentang yang sama digeser 364 hari (hari dalam minggu sama; 52 minggu).
 *  - Hanya pasangan cabang-hari yang data penjualannya sudah ditarik lengkap di KEDUA sisi (query yoy_*; hari yang
 *    belum lengkap dikeluarkan dari dua sisi dan dilaporkan sebagai celah).
 *  - Toko sebanding = berjualan di kedua periode dan sudah berjualan >= 28 hari sebelum awal periode tahun lalu.
 */
import { addDays, diffDays } from './dates';

export const YOY_SHIFT_DAYS = 364;
/** Rentang maksimum: lebih panjang dari ini membuat periode ini dan pembanding bertumpuk. */
export const YOY_MAX_DAYS = 364;
/** Bagian menu membaca baris penjualan (bukan agregat): dibatasi agar query tetap di bawah batas waktu/memori Insight. */
export const YOY_MENU_MAX_DAYS = 124;
/** Toko dianggap sebanding bila penjualan pertamanya paling lambat sekian hari sebelum awal periode tahun lalu. */
export const YOY_MATURE_DAYS = 28;

export function lyRange(from: string, to: string): { from: string; to: string } {
  return { from: addDays(from, -YOY_SHIFT_DAYS), to: addDays(to, -YOY_SHIFT_DAYS) };
}

/** Jumlah hari inklusif. */
export function spanDays(from: string, to: string): number {
  return diffDays(from, to) + 1;
}

export type BranchStatus = 'same' | 'new' | 'closed' | 'young';

export const STATUS_LABEL: Record<BranchStatus, string> = {
  same: 'Sebanding',
  new: 'Baru (belum ada tahun lalu)',
  closed: 'Tidak berjualan sekarang',
  young: 'Belum sebanding (baru buka tahun lalu)',
};

export interface BranchRow {
  brand: string;
  branch_id: number;
  branch_name: string;
  company_code: string;
  net_cur: number;
  net_ly: number;
  gross_cur: number;
  gross_ly: number;
  disc_cur: number;
  disc_ly: number;
  cogs_cur: number;
  cogs_ly: number;
  trx_cur: number;
  trx_ly: number;
  days_cur: number;
  days_ly: number;
  first_sale: string;
}

export function branchStatus(r: Pick<BranchRow, 'net_cur' | 'net_ly' | 'first_sale'>, lyFrom: string): BranchStatus {
  if (r.net_cur <= 0) return 'closed';
  if (r.net_ly <= 0) return 'new';
  const mature = r.first_sale !== '' && diffDays(r.first_sale, addDays(lyFrom, -YOY_MATURE_DAYS)) >= 0;
  return mature ? 'same' : 'young';
}

export interface Totals {
  net: number;
  gross: number;
  disc: number;
  cogs: number;
  trx: number;
}

export interface Side {
  cur: Totals;
  ly: Totals;
  branches: number;
}

const ZERO: Totals = { net: 0, gross: 0, disc: 0, cogs: 0, trx: 0 };

export function totals(rows: readonly BranchRow[]): Side {
  const cur = { ...ZERO };
  const ly = { ...ZERO };
  for (const r of rows) {
    cur.net += r.net_cur;
    cur.gross += r.gross_cur;
    cur.disc += r.disc_cur;
    cur.cogs += r.cogs_cur;
    cur.trx += r.trx_cur;
    ly.net += r.net_ly;
    ly.gross += r.gross_ly;
    ly.disc += r.disc_ly;
    ly.cogs += r.cogs_ly;
    ly.trx += r.trx_ly;
  }
  return { cur, ly, branches: rows.length };
}

export const pctChange = (cur: number, prev: number): number | null => (prev > 0 ? ((cur - prev) / prev) * 100 : null);
export const atv = (t: Totals): number | null => (t.trx > 0 ? t.net / t.trx : null);
export const marginPct = (t: Totals): number | null => (t.net > 0 ? (1 - t.cogs / t.net) * 100 : null);
export const discPct = (t: Totals): number | null => (t.gross > 0 ? (t.disc / t.gross) * 100 : null);

/**
 * Jembatan perubahan penjualan bersih: total = toko sebanding (efek jumlah transaksi + efek nilai per transaksi)
 * + toko baru + toko yang tidak berjualan sekarang + toko belum sebanding. Jumlah komponen = selisih total (tepat).
 */
export interface Bridge {
  ly: number;
  trxEffect: number;
  atvEffect: number;
  newStores: number;
  closedStores: number;
  youngStores: number;
  cur: number;
}

export function bridge(rows: readonly BranchRow[], lyFrom: string): Bridge {
  const by = (s: BranchStatus) => rows.filter((r) => branchStatus(r, lyFrom) === s);
  const same = totals(by('same'));
  const atvLy = atv(same.ly) ?? 0;
  const trxEffect = (same.cur.trx - same.ly.trx) * atvLy;
  const atvEffect = same.cur.net - same.ly.net - trxEffect;
  const delta = (s: BranchStatus) => {
    const t = totals(by(s));
    return t.cur.net - t.ly.net;
  };
  const all = totals(rows);
  return {
    ly: all.ly.net,
    trxEffect,
    atvEffect,
    newStores: delta('new'),
    closedStores: delta('closed'),
    youngStores: delta('young'),
    cur: all.cur.net,
  };
}

// ------------------------------------------------------------------ harian

export interface DailyRow {
  day: string;
  brand: string;
  branch_id: number;
  net_cur: number;
  net_ly: number;
  trx_cur: number;
  trx_ly: number;
}

/** Seri harian (hari periode ini; nilai tahun lalu dipetakan ke hari padanannya) untuk himpunan cabang tertentu. */
export function dailySeries(
  rows: readonly DailyRow[],
  days: readonly string[],
  keep: (r: DailyRow) => boolean,
): { netCur: (number | null)[]; netLy: (number | null)[]; trxCur: (number | null)[]; trxLy: (number | null)[] } {
  const idx = new Map(days.map((d, i) => [d, i]));
  const blank = (): (number | null)[] => days.map(() => null);
  const out = { netCur: blank(), netLy: blank(), trxCur: blank(), trxLy: blank() };
  const add = (arr: (number | null)[], i: number, v: number) => {
    arr[i] = (arr[i] ?? 0) + v;
  };
  for (const r of rows) {
    const i = idx.get(r.day);
    if (i === undefined || !keep(r)) continue;
    add(out.netCur, i, r.net_cur);
    add(out.netLy, i, r.net_ly);
    add(out.trxCur, i, r.trx_cur);
    add(out.trxLy, i, r.trx_ly);
  }
  return out;
}

// ------------------------------------------------------------------ celah data

export interface GapRow {
  day: string;
  miss_ly: number;
  miss_cur: number;
}

/** Kalimat celah: hari yang sebagian/seluruh cabangnya dikeluarkan karena data salah satu sisi belum lengkap. */
export function gapNotes(rows: readonly GapRow[], fmtDay: (d: string) => string): string[] {
  return rows.map((g) => {
    const parts: string[] = [];
    if (g.miss_cur > 0) parts.push(`${g.miss_cur} cabang belum ditarik untuk tahun ini`);
    if (g.miss_ly > 0) parts.push(`${g.miss_ly} cabang belum ditarik untuk tahun lalu`);
    return `${fmtDay(g.day)}: ${parts.join('; ')}`;
  });
}

// ------------------------------------------------------------------ menu & promo

export interface MenuRow {
  menu_name: string;
  category: string;
  qty_paid_cur: number;
  qty_paid_ly: number;
  gross_paid_cur: number;
  gross_paid_ly: number;
  disc_paid_cur: number;
  disc_paid_ly: number;
  cogs_paid_cur: number;
  cogs_paid_ly: number;
  qty_free_cur: number;
  qty_free_ly: number;
  cogs_free_cur: number;
  cogs_free_ly: number;
  qty_pkg_cur: number;
  qty_pkg_ly: number;
  cogs_pkg_cur: number;
  cogs_pkg_ly: number;
  pkg_gross_cur: number;
  pkg_gross_ly: number;
  pkg_disc_cur: number;
  pkg_disc_ly: number;
}

const div = (a: number, b: number): number | null => (b > 0 ? a / b : null);

/** Harga list per unit (tanpa pajak) dari baris berbayar; null bila menu tidak terjual berbayar di sisi itu. */
export const listPrice = (m: MenuRow, side: 'cur' | 'ly'): number | null =>
  side === 'cur' ? div(m.gross_paid_cur, m.qty_paid_cur) : div(m.gross_paid_ly, m.qty_paid_ly);

export const cogsUnit = (m: MenuRow, side: 'cur' | 'ly'): number | null =>
  side === 'cur' ? div(m.cogs_paid_cur, m.qty_paid_cur) : div(m.cogs_paid_ly, m.qty_paid_ly);

export interface MenuView {
  menu_name: string;
  category: string;
  qty_cur: number;
  qty_ly: number;
  price_ly: number | null;
  price_cur: number | null;
  price_chg: number | null;
  net_price_cur: number | null;
  cogs_ly: number | null;
  cogs_cur: number | null;
  cogs_chg: number | null;
  fc_ly: number | null;
  fc_cur: number | null;
  fc_pp: number | null;
  free_share_cur: number | null;
  rev_cur: number;
}

/** Baris tabel "Harga jual vs COGS" (hanya menu dengan penjualan berbayar di periode ini). */
export function menuView(rows: readonly MenuRow[]): MenuView[] {
  const out: MenuView[] = [];
  for (const m of rows) {
    if (m.qty_paid_cur <= 0) continue;
    const pl = listPrice(m, 'ly');
    const pc = listPrice(m, 'cur');
    const cl = cogsUnit(m, 'ly');
    const cc = cogsUnit(m, 'cur');
    const fcl = cl !== null && pl ? (cl / pl) * 100 : null;
    const fcc = cc !== null && pc ? (cc / pc) * 100 : null;
    const units = m.qty_paid_cur + m.qty_free_cur;
    out.push({
      menu_name: m.menu_name,
      category: m.category,
      qty_cur: m.qty_paid_cur,
      qty_ly: m.qty_paid_ly,
      price_ly: pl,
      price_cur: pc,
      price_chg: pl !== null && pc !== null ? pctChange(pc, pl) : null,
      net_price_cur: div(m.gross_paid_cur - m.disc_paid_cur, m.qty_paid_cur),
      cogs_ly: cl,
      cogs_cur: cc,
      cogs_chg: cl !== null && cc !== null ? pctChange(cc, cl) : null,
      fc_ly: fcl,
      fc_cur: fcc,
      fc_pp: fcl !== null && fcc !== null ? fcc - fcl : null,
      free_share_cur: units > 0 ? (m.qty_free_cur / units) * 100 : null,
      rev_cur: m.gross_paid_cur - m.disc_paid_cur,
    });
  }
  return out;
}

export interface PromoSide {
  /** Omzet pada harga list (a la carte berbayar + nilai list komponen paket + nilai list item gratis). */
  listValue: number;
  discount: number;
  bundle: number;
  free: number;
  freeCogs: number;
  pkgRevenue: number;
  pkgShare: number | null;
  depth: number | null;
  /** Nilai list komponen/item gratis tanpa harga pembanding (menu tidak terjual berbayar di kedua sisi). */
  unpriced: number;
}

/**
 * Kedalaman promo per sisi. Harga list menu = harga berbayar di sisi itu; bila tidak ada, harga sisi lain; bila tidak ada
 * juga, unit dihitung sebagai `unpriced` (nilai tidak ditebak).
 *  - diskon eksplisit = diskon baris berbayar + diskon header paket
 *  - diskon bundel    = nilai list komponen paket - harga paket (tidak tercatat di kolom diskon ESB)
 *  - item gratis      = qty gratis x harga list
 */
export function promo(rows: readonly MenuRow[], side: 'cur' | 'ly'): PromoSide {
  const other = side === 'cur' ? 'ly' : 'cur';
  let paidGross = 0;
  let discount = 0;
  let compList = 0;
  let pkgGross = 0;
  let free = 0;
  let freeCogs = 0;
  let unpriced = 0;
  for (const m of rows) {
    const p = listPrice(m, side) ?? listPrice(m, other);
    const s = side === 'cur';
    paidGross += s ? m.gross_paid_cur : m.gross_paid_ly;
    discount += (s ? m.disc_paid_cur : m.disc_paid_ly) + (s ? m.pkg_disc_cur : m.pkg_disc_ly);
    pkgGross += s ? m.pkg_gross_cur : m.pkg_gross_ly;
    freeCogs += s ? m.cogs_free_cur : m.cogs_free_ly;
    const qPkg = s ? m.qty_pkg_cur : m.qty_pkg_ly;
    const qFree = s ? m.qty_free_cur : m.qty_free_ly;
    if (p === null) {
      unpriced += qPkg + qFree;
      continue;
    }
    compList += qPkg * p;
    free += qFree * p;
  }
  // Diskon header paket sudah masuk `discount`; bundel = selisih nilai list komponen dengan harga paket (sebelum diskon).
  const bundle = Math.max(0, compList - pkgGross);
  const listValue = paidGross + compList + free;
  return {
    listValue,
    discount,
    bundle,
    free,
    freeCogs,
    pkgRevenue: pkgGross,
    pkgShare: paidGross + pkgGross > 0 ? (pkgGross / (paidGross + pkgGross)) * 100 : null,
    depth: listValue > 0 ? ((discount + bundle + free) / listValue) * 100 : null,
    unpriced,
  };
}
