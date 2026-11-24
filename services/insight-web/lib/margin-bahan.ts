/**
 * Halaman "Margin bahan" (/margin-bahan, G67, docs/04 §31): logika murni (tanpa I/O), diuji tests/margin-bahan.test.ts.
 *
 * Data = tiga mart bulanan hasil `efn_esb refresh-margin` (view `efn_insight.v_esb_ing_price_monthly`,
 * `v_esb_menu_cost_monthly`, `v_esb_ing_group_monthly`). Metode (keranjang tetap, Laspeyres):
 *  - indeks bahan idx = harga PO 4 bulan bergulir / harga bulan dasar (bulan dasar = 1,0);
 *  - basket_value(kelompok, bulan) = nilai keranjang tetap pada harga bulan itu; base_value = pada harga bulan dasar;
 *  - kontribusi kelompok = basket_value - base_value (Rp per bulan), dijumlahkan lintas brand/PT yang dipilih;
 *  - indeks kelompok/total = sum(basket_value) / sum(base_value) x 100.
 */

export const MB_TOP_GROUPS = 5;
export const MB_TOP_INGREDIENTS = 20;
export const MB_TOP_MENUS = 20;

export interface MbGroupRow {
  brand: string;
  company_code: string;
  month: string;
  ing_group: string;
  basket_value: number;
  base_value: number;
}

export interface MbPriceRow {
  brand: string;
  company_code: string;
  month: string;
  product_code: string;
  product_name: string;
  ing_group: string;
  uom: string;
  idx: number | null;
  /** Nilai pakai di resep (Rp/bulan, keranjang tetap pada harga dasar; sama setiap bulan). Rupiah juga untuk PO USD. */
  usage_value: number;
}

export interface MbMenuRow {
  brand: string;
  company_code: string;
  month: string;
  menu_name: string;
  menu_category: string;
  qty: number;
  net: number;
  cogs: number;
  bom_matched: number;
  cost_idx: number | null;
  leaf_coverage: number;
}

const sortedUnique = (xs: readonly string[]): string[] => Array.from(new Set(xs)).sort();

/** Bulan yang ada (format `YYYY-MM-DD`, tanggal 1), urut naik. */
export const monthsOf = (rows: readonly { month: string }[]): string[] => sortedUnique(rows.map((r) => r.month));

/** Indeks 100-based; null bila basis tidak positif. */
export const indexPct = (basket: number, base: number): number | null => (base > 0 ? (basket / base) * 100 : null);

/** Margin kotor % = (net - cogs) / net; null bila net <= 0. */
export const grossMarginPct = (net: number, cogs: number): number | null => (net > 0 ? ((net - cogs) / net) * 100 : null);

// ------------------------------------------------------------------ kelompok bahan

export interface GroupSeriesItem {
  name: string;
  /** Kontribusi (Rp per bulan) pada bulan terakhir. */
  contribution: number;
  /** Indeks 100-based per bulan (selaras `months`); null bila kelompok tak punya baris di bulan itu. */
  idx: (number | null)[];
}

export interface GroupSeries {
  months: string[];
  groups: GroupSeriesItem[];
  total: { idx: (number | null)[]; contribution: number };
  /** Jumlah kelompok yang tidak ikut grafik (di luar `top`). */
  hidden: number;
}

interface Acc {
  basket: number;
  base: number;
}

function groupMonthMap(rows: readonly MbGroupRow[]): Map<string, Map<string, Acc>> {
  const out = new Map<string, Map<string, Acc>>();
  for (const r of rows) {
    let byMonth = out.get(r.ing_group);
    if (!byMonth) out.set(r.ing_group, (byMonth = new Map()));
    const a = byMonth.get(r.month) ?? { basket: 0, base: 0 };
    a.basket += r.basket_value;
    a.base += r.base_value;
    byMonth.set(r.month, a);
  }
  return out;
}

/**
 * Seri indeks per kelompok. Kelompok yang ditampilkan = `top` kelompok dengan |kontribusi| terbesar pada bulan terakhir
 * (kenaikan maupun penurunan), urut menurun menurut |kontribusi|; selebihnya hanya masuk total.
 */
export function groupSeries(rows: readonly MbGroupRow[], top = MB_TOP_GROUPS): GroupSeries {
  const months = monthsOf(rows);
  const last = months[months.length - 1];
  const by = groupMonthMap(rows);
  const contrib = (a: Acc | undefined) => (a ? a.basket - a.base : 0);
  const all = Array.from(by.entries()).map(([name, m]) => ({
    name,
    contribution: contrib(m.get(last)),
    idx: months.map((mo) => {
      const a = m.get(mo);
      return a ? indexPct(a.basket, a.base) : null;
    }),
  }));
  all.sort((a, b) => Math.abs(b.contribution) - Math.abs(a.contribution) || a.name.localeCompare(b.name));
  const totalIdx = months.map((mo) => {
    let basket = 0;
    let base = 0;
    for (const m of by.values()) {
      const a = m.get(mo);
      if (a) {
        basket += a.basket;
        base += a.base;
      }
    }
    return indexPct(basket, base);
  });
  const totalContribution = all.reduce((s, g) => s + g.contribution, 0);
  return {
    months,
    groups: all.slice(0, top),
    total: { idx: totalIdx, contribution: totalContribution },
    hidden: Math.max(0, all.length - top),
  };
}

export interface GroupContribution {
  ing_group: string;
  /** Nilai keranjang pada harga bulan dasar dan bulan terakhir (Rp per bulan). */
  base_value: number;
  basket_value: number;
  contribution: number;
  idx: number | null;
  /** Porsi dari total perubahan keranjang (%); null bila total perubahan nol. */
  share: number | null;
}

/** Tabel kontribusi kelompok pada bulan terakhir, urut menurut |kontribusi|. */
export function groupContributions(rows: readonly MbGroupRow[]): GroupContribution[] {
  const months = monthsOf(rows);
  const last = months[months.length - 1];
  const acc = new Map<string, Acc>();
  for (const r of rows) {
    if (r.month !== last) continue;
    const a = acc.get(r.ing_group) ?? { basket: 0, base: 0 };
    a.basket += r.basket_value;
    a.base += r.base_value;
    acc.set(r.ing_group, a);
  }
  const total = Array.from(acc.values()).reduce((s, a) => s + (a.basket - a.base), 0);
  return Array.from(acc.entries())
    .map(([ing_group, a]) => ({
      ing_group,
      base_value: a.base,
      basket_value: a.basket,
      contribution: a.basket - a.base,
      idx: indexPct(a.basket, a.base),
      share: total !== 0 ? ((a.basket - a.base) / total) * 100 : null,
    }))
    .sort((a, b) => Math.abs(b.contribution) - Math.abs(a.contribution) || a.ing_group.localeCompare(b.ing_group));
}

// ------------------------------------------------------------------ bahan

export interface IngredientImpact {
  brand: string;
  company_code: string;
  product_code: string;
  product_name: string;
  ing_group: string;
  uom: string;
  /** Bulan harga terakhir yang punya indeks. */
  last_month: string;
  /** Perubahan indeks terhadap bulan dasar (%) = (idx - 1) x 100. */
  idx_chg: number;
  /** Nilai pakai di resep (Rp per bulan, keranjang tetap). */
  usage_value: number;
  /** Dampak = (idx - 1) x nilai pakai (Rp per bulan); positif = biaya naik. */
  impact: number;
}

/** `top` bahan dengan |dampak| terbesar. Bahan tanpa indeks (tak ada harga PO di bulan dasar) tidak ikut. */
export function ingredientImpacts(rows: readonly MbPriceRow[], top = MB_TOP_INGREDIENTS): IngredientImpact[] {
  const by = new Map<string, MbPriceRow[]>();
  for (const r of rows) {
    const k = `${r.brand}|${r.company_code}|${r.product_code}|${r.uom}`;
    const list = by.get(k);
    if (list) list.push(r);
    else by.set(k, [r]);
  }
  const out: IngredientImpact[] = [];
  for (const list of by.values()) {
    let latest: MbPriceRow | null = null;
    for (const r of list) {
      if (r.idx !== null && (latest === null || r.month > latest.month)) latest = r;
    }
    if (latest === null || latest.idx === null) continue;
    const usage = latest.usage_value;
    out.push({
      brand: latest.brand,
      company_code: latest.company_code,
      product_code: latest.product_code,
      product_name: latest.product_name,
      ing_group: latest.ing_group,
      uom: latest.uom,
      last_month: latest.month,
      idx_chg: (latest.idx - 1) * 100,
      usage_value: usage,
      impact: (latest.idx - 1) * usage,
    });
  }
  out.sort((a, b) => Math.abs(b.impact) - Math.abs(a.impact) || a.product_name.localeCompare(b.product_name));
  return out.slice(0, top);
}

// ------------------------------------------------------------------ menu

export interface MenuCostChange {
  brand: string;
  company_code: string;
  menu_name: string;
  menu_category: string;
  last_month: string;
  /** Kenaikan indeks biaya menu terhadap bulan dasar (%) = (cost_idx - 1) x 100. */
  cost_chg: number;
  gm_base: number | null;
  gm_last: number | null;
  /** Selisih margin kotor (poin persentase), null bila salah satu sisi tak terhitung. */
  gm_pp: number | null;
  /** Porsi nilai daun resep yang punya harga PO (%). */
  coverage: number;
}

/**
 * `top` menu ber-BOM dengan kenaikan `cost_idx` terbesar (urut menurun). Per menu: baris bulan terakhir yang punya cost_idx
 * vs baris bulan paling awal yang punya cost_idx; margin kotor = (net - cogs) / net pada dua baris itu.
 */
export function menuCostChanges(rows: readonly MbMenuRow[], top = MB_TOP_MENUS): MenuCostChange[] {
  const by = new Map<string, MbMenuRow[]>();
  for (const r of rows) {
    if (r.bom_matched !== 1 || r.cost_idx === null) continue;
    const k = `${r.brand}|${r.company_code}|${r.menu_name}`;
    const list = by.get(k);
    if (list) list.push(r);
    else by.set(k, [r]);
  }
  const out: MenuCostChange[] = [];
  for (const list of by.values()) {
    const sorted = [...list].sort((a, b) => a.month.localeCompare(b.month));
    const first = sorted[0];
    const last = sorted[sorted.length - 1];
    const gmB = grossMarginPct(first.net, first.cogs);
    const gmL = grossMarginPct(last.net, last.cogs);
    out.push({
      brand: last.brand,
      company_code: last.company_code,
      menu_name: last.menu_name,
      menu_category: last.menu_category,
      last_month: last.month,
      cost_chg: ((last.cost_idx as number) - 1) * 100,
      gm_base: gmB,
      gm_last: gmL,
      gm_pp: gmB !== null && gmL !== null ? gmL - gmB : null,
      coverage: last.leaf_coverage * 100,
    });
  }
  out.sort((a, b) => b.cost_chg - a.cost_chg || a.menu_name.localeCompare(b.menu_name));
  return out.slice(0, top);
}

// ------------------------------------------------------------------ KPI

export interface MbKpis {
  baseMonth: string | null;
  lastMonth: string | null;
  /** Indeks biaya bahan total bulan terakhir (100 = bulan dasar). */
  costIdx: number | null;
  /** Margin kotor ESB (semua menu) bulan dasar dan bulan terakhir (%). */
  gmBase: number | null;
  gmLast: number | null;
  /** Porsi COGS menu yang punya BOM pada bulan terakhir (%). */
  bomCoverage: number | null;
}

function monthMargin(rows: readonly MbMenuRow[], month: string | undefined): number | null {
  if (month === undefined) return null;
  let net = 0;
  let cogs = 0;
  for (const r of rows) {
    if (r.month !== month) continue;
    net += r.net;
    cogs += r.cogs;
  }
  return grossMarginPct(net, cogs);
}

export function kpis(menus: readonly MbMenuRow[], groups: readonly MbGroupRow[]): MbKpis {
  const mMonths = monthsOf(menus);
  const gMonths = monthsOf(groups);
  const lastMenu = mMonths[mMonths.length - 1];
  const lastGroup = gMonths[gMonths.length - 1];
  let basket = 0;
  let base = 0;
  for (const r of groups) {
    if (r.month !== lastGroup) continue;
    basket += r.basket_value;
    base += r.base_value;
  }
  let cogsAll = 0;
  let cogsBom = 0;
  for (const r of menus) {
    if (r.month !== lastMenu) continue;
    cogsAll += r.cogs;
    if (r.bom_matched === 1) cogsBom += r.cogs;
  }
  return {
    baseMonth: mMonths[0] ?? gMonths[0] ?? null,
    lastMonth: lastMenu ?? lastGroup ?? null,
    costIdx: lastGroup === undefined ? null : indexPct(basket, base),
    gmBase: monthMargin(menus, mMonths[0]),
    gmLast: monthMargin(menus, lastMenu),
    bomCoverage: cogsAll > 0 ? (cogsBom / cogsAll) * 100 : null,
  };
}
