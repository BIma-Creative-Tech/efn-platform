/**
 * View-model murni untuk kartu supermarket (tanpa I/O): menyusun jumlah mentah dari view menjadi nilai tampilan.
 * Aturan: penyebut 0 -> null ("belum ada data", bukan 0), rasio dihitung dari jumlah mentah, nilai di bawah k-anonim ditekan.
 */
import { fmtSuppressed } from './sm-format';
import { marginPct, pct, pctChange, wastePct, wasteStatus } from './sm-kpi';
import type { CampaignRow, ExpiryDisplay, ExpiryLot, ProposalRow } from './sm-parse';
import { toNum, toNumOrNull, type Raw } from './sm-parse';
import { K_ANON } from './sm-defaults';
import type { SmThresholds } from './sm-thresholds';
import type { Status } from './thresholds';
import { worst } from './thresholds';

// ---------------------------------------------------------------- ST-1 / DC-2
/** Usulan terdekat penuh; sisanya terlipat ("Pengiriman berikutnya lainnya"). Masukan sudah terurut (tanggal kirim, cut-off). */
export function pickProposals(rows: readonly ProposalRow[]): { primary: ProposalRow | null; others: ProposalRow[] } {
  return { primary: rows[0] ?? null, others: rows.slice(1) };
}

/** Kelompok pengiriman berikutnya (tanggal kirim terdekat ≥ today) untuk DC-2. */
export function nextDeliveryGroup(rows: readonly ProposalRow[], today: string): { date: string | null; rows: ProposalRow[] } {
  const future = rows.filter((r) => r.deliveryDate >= today);
  if (future.length === 0) return { date: null, rows: [] };
  const date = future.reduce((a, r) => (r.deliveryDate < a ? r.deliveryDate : a), future[0].deliveryDate);
  return { date, rows: future.filter((r) => r.deliveryDate === date) };
}

// ---------------------------------------------------------------- ST-2 / CM-1 / CM-4
export interface ExpirySummary {
  counts: Record<ExpiryDisplay, number>;
  total: number;
  valueCost: number;
}
export function summarizeExpiry(lots: readonly ExpiryLot[]): ExpirySummary {
  const counts: Record<ExpiryDisplay, number> = { kedaluwarsa: 0, markdown_sekarang: 0, markdown_aktif: 0, pantau: 0 };
  let valueCost = 0;
  for (const l of lots) {
    counts[l.display] += 1;
    valueCost += l.valueCost;
  }
  return { counts, total: lots.length, valueCost };
}

/** Ringkasan dari baris agregat `sm_cm_expiry_totals` (display_status, lots_n, value_sum). */
export function summarizeExpiryTotals(rows: readonly Raw[]): ExpirySummary {
  const counts: Record<ExpiryDisplay, number> = { kedaluwarsa: 0, markdown_sekarang: 0, markdown_aktif: 0, pantau: 0 };
  let total = 0;
  let valueCost = 0;
  for (const r of rows) {
    const k = typeof r.display_status === 'string' ? r.display_status : '';
    const n = toNum(r.lots_n);
    const key: ExpiryDisplay = k === 'kedaluwarsa' || k === 'markdown_sekarang' || k === 'markdown_aktif' ? k : 'pantau';
    counts[key] += n;
    total += n;
    valueCost += toNum(r.value_sum);
  }
  return { counts, total, valueCost };
}

/** Selisih jumlah lot hari ini vs kemarin ("+2 dari kemarin"); null bila salah satu hari tidak ada (riwayat mulai sejak job aktif). */
export function expiryDelta(rows: readonly Raw[], today: string, yesterday: string): { today: number | null; yesterday: number | null; delta: number | null } {
  let t: number | null = null;
  let y: number | null = null;
  for (const r of rows) {
    if (r.day === today) t = toNum(r.lots_n);
    else if (r.day === yesterday) y = toNum(r.lots_n);
  }
  return { today: t, yesterday: y, delta: t !== null && y !== null ? t - y : null };
}

// ---------------------------------------------------------------- ST-4 / EX-1 / EX-5
export interface WasteReason {
  name: string;
  value: number;
  sharePct: number | null;
}
export interface WasteVm {
  wasteCur: number;
  wastePrev: number;
  pctCur: number | null;
  pctPrev: number | null;
  reasons: WasteReason[];
}

export function buildWasteVm(
  sums: { waste_cur?: unknown; waste_prev?: unknown } | undefined,
  base: { cur: number | null; prev: number | null },
  reasonRows: readonly Raw[],
): WasteVm {
  const wasteCur = toNum(sums?.waste_cur);
  const wastePrev = toNum(sums?.waste_prev);
  const total = reasonRows.reduce((s, r) => s + toNum(r.value_sum), 0);
  const reasons = reasonRows
    .map((r) => ({ name: typeof r.reason_name === 'string' && r.reason_name !== '' ? r.reason_name : 'Lainnya', value: toNum(r.value_sum) }))
    .filter((r) => r.value > 0)
    .slice(0, 4)
    .map((r) => ({ ...r, sharePct: pct(r.value, total) }));
  return {
    wasteCur,
    wastePrev,
    pctCur: base.cur === null ? null : wastePct(wasteCur, base.cur),
    pctPrev: base.prev === null ? null : wastePct(wastePrev, base.prev),
    reasons,
  };
}

/** Penyebut Waste % sesuai `waste.basis`; 'cogs' hanya untuk persona yang boleh membaca margin (exec, category). */
export function wasteBase(
  basis: string,
  canReadMargin: boolean,
  net: { net_cur?: unknown; net_prev?: unknown } | undefined,
  cogs: { cogs_cur?: unknown; cogs_prev?: unknown } | undefined,
): { cur: number | null; prev: number | null } {
  if (basis === 'cogs') {
    if (!canReadMargin) return { cur: null, prev: null };
    return { cur: toNumOrNull(cogs?.cogs_cur), prev: toNumOrNull(cogs?.cogs_prev) };
  }
  return { cur: toNumOrNull(net?.net_cur), prev: toNumOrNull(net?.net_prev) };
}

// ---------------------------------------------------------------- EX-3
export interface OutletRow {
  id: number;
  name: string;
  revCur: number;
  revDeltaPct: number | null;
  sharePct: number | null;
  marginPct: number | null;
  wastePct: number | null;
  yoyPct: number | null;
  status: Status;
}

/** Penurunan omzet ≥ ambang ini vs periode lalu = Perlu dicek (spesifikasi EX-3: "delta Omzet < −5%"). */
export const OUTLET_DROP_PCT = -5;

export function buildOutletRows(
  sales: readonly Raw[],
  margin: readonly Raw[],
  waste: readonly Raw[],
  names: ReadonlyMap<number, string>,
  t: SmThresholds,
): OutletRow[] {
  const mById = new Map(margin.map((r) => [toNum(r.warehouse_id), r]));
  const wById = new Map(waste.map((r) => [toNum(r.warehouse_id), r]));
  const total = sales.reduce((s, r) => s + toNum(r.rev_cur), 0);
  const rows: OutletRow[] = sales
    .filter((r) => toNum(r.warehouse_id) > 0 && (toNum(r.rev_cur) > 0 || toNum(r.rev_prev) > 0))
    .map((r) => {
      const id = toNum(r.warehouse_id);
      const m = mById.get(id);
      const w = wById.get(id);
      const net = toNum(r.net_cur);
      const mp = m ? marginPct(toNum(m.gm_cur), toNum(m.net_cur)) : null;
      const wp = w ? wastePct(toNum(w.waste_cur), net) : null;
      const delta = pctChange(toNum(r.rev_cur), toNum(r.rev_prev));
      const yoy = toNum(r.rev_yoy) > 0 ? pctChange(toNum(r.rev_cur), toNum(r.rev_yoy)) : null;
      const parts: Status[] = [];
      if (wp !== null) parts.push(wasteStatus(wp, t));
      if (delta !== null && delta <= OUTLET_DROP_PCT) parts.push('warn');
      return {
        id,
        name: names.get(id) ?? `Outlet #${id}`,
        revCur: toNum(r.rev_cur),
        revDeltaPct: delta,
        sharePct: pct(toNum(r.rev_cur), total),
        marginPct: mp,
        wastePct: wp,
        yoyPct: yoy,
        status: parts.length === 0 ? 'neutral' : worst(parts),
      };
    });
  return rows.sort((a, b) => b.revCur - a.revCur);
}

// ---------------------------------------------------------------- CM-2
export interface HeatVm {
  /** Kolom = outlet. */
  cols: string[];
  /** Baris = kategori. */
  rows: string[];
  /** [xIndexOutlet, yIndexKategori, OOS%|null] */
  cells: [number, number, number | null][];
}

/** Heatmap OOS rate kategori × outlet; sel tanpa SKU di universe = null ("n/a", bukan 0%). Maks `maxRows` kategori terburuk. */
export function buildOosHeat(
  matrix: readonly Raw[],
  catNames: ReadonlyMap<number, string>,
  whNames: ReadonlyMap<number, string>,
  maxRows = 15,
): HeatVm {
  const cats = new Map<number, { oos: number; uni: number }>();
  const whs = new Set<number>();
  for (const r of matrix) {
    const c = toNum(r.categ_id);
    const w = toNum(r.warehouse_id);
    if (w <= 0) continue;
    whs.add(w);
    const cur = cats.get(c) ?? { oos: 0, uni: 0 };
    cur.oos += toNum(r.oos_sum);
    cur.uni += toNum(r.uni_sum);
    cats.set(c, cur);
  }
  const catIds = Array.from(cats.entries())
    .sort((a, b) => (pct(b[1].oos, b[1].uni) ?? 0) - (pct(a[1].oos, a[1].uni) ?? 0))
    .slice(0, maxRows)
    .map(([id]) => id);
  const whIds = Array.from(whs).sort((a, b) => a - b);
  const cells: [number, number, number | null][] = [];
  for (const r of matrix) {
    const y = catIds.indexOf(toNum(r.categ_id));
    const x = whIds.indexOf(toNum(r.warehouse_id));
    if (x < 0 || y < 0) continue;
    cells.push([x, y, pct(toNum(r.oos_sum), toNum(r.uni_sum))]);
  }
  return {
    cols: whIds.map((id) => whNames.get(id) ?? `Outlet #${id}`),
    rows: catIds.map((id) => catNames.get(id) ?? `Kategori #${id}`),
    cells,
  };
}

// ---------------------------------------------------------------- MK-1 / MK-2 / MK-4
export interface FunnelStep {
  label: string;
  count: number | null;
  /** Hitungan < k: tampil "< 10", tidak diekstrapolasi (§6.6). */
  suppressed: boolean;
  text: string;
  pctOfFirst: number | null;
  pctOfPrev: number | null;
  biggestDrop: boolean;
}

/**
 * Funnel 4 tahap dari jumlah Σ perangkat-hari (Q-SM41): Lihat produk -> Masukkan keranjang -> Mulai checkout -> Beli.
 * Tahap < k ditekan dan tidak ikut perhitungan persentase. "Penurunan terbesar" = tahap dengan % dari tahap sebelumnya terendah.
 */
export function buildFunnel(c: { view: number; cart: number; chk: number; buy: number }, k: number = K_ANON): FunnelStep[] {
  const raw: [string, number][] = [
    ['Lihat produk', c.view],
    ['Masukkan ke keranjang', c.cart],
    ['Mulai checkout', c.chk],
    ['Beli', c.buy],
  ];
  const steps: FunnelStep[] = raw.map(([label, n], i) => {
    const suppressed = n < k;
    const first = raw[0][1] >= k ? raw[0][1] : null;
    const prev = i > 0 && raw[i - 1][1] >= k ? raw[i - 1][1] : null;
    return {
      label,
      count: suppressed ? null : n,
      suppressed,
      text: fmtSuppressed(n, k),
      pctOfFirst: suppressed || first === null ? null : pct(n, first),
      pctOfPrev: suppressed || prev === null ? null : pct(n, prev),
      biggestDrop: false,
    };
  });
  let worstIdx = -1;
  steps.forEach((s, i) => {
    if (i === 0 || s.pctOfPrev === null) return;
    if (worstIdx < 0 || s.pctOfPrev < (steps[worstIdx].pctOfPrev as number)) worstIdx = i;
  });
  if (worstIdx > 0) steps[worstIdx].biggestDrop = true;
  return steps;
}

export interface CampaignTotals {
  used: number;
  issued: number;
  delivered: number;
  discount: number;
  /** Biaya voucher / anggaran (%) hanya dari kampanye yang punya anggaran; null bila tidak ada anggaran (Q-SM24). */
  costVsBudgetPct: number | null;
}

export function campaignTotals(rows: readonly CampaignRow[]): CampaignTotals {
  const budgeted = rows.filter((r) => r.budgetIdr !== null && r.budgetIdr > 0);
  const budget = budgeted.reduce((s, r) => s + (r.budgetIdr as number), 0);
  const spent = budgeted.reduce((s, r) => s + r.discountIdr, 0);
  return {
    used: rows.reduce((s, r) => s + r.vouchersUsed, 0),
    issued: rows.reduce((s, r) => s + r.vouchersIssued, 0),
    delivered: rows.reduce((s, r) => s + r.delivered, 0),
    discount: rows.reduce((s, r) => s + r.discountIdr, 0),
    costVsBudgetPct: budget > 0 ? (spent / budget) * 100 : null,
  };
}
