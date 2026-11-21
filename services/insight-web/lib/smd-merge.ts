/**
 * Penggabungan data ESB + SMD untuk halaman F&B (G51 B1). Murni (tanpa I/O) agar bisa diuji. ESB dan SMD ditarik terpisah
 * (chQuery scope v1, chQuerySm scope sig2) lalu digabung per kode PT di sini.
 *
 * Basis uang (WAJIB diberi label di UI): omzet SMD = TERMASUK PPN (sama dengan dashboard supermarket L-200/L-202); penjualan bersih
 * brand ESB = sebelum PB1/service. HPP% memakai penyebut tanpa pajak di kedua sisi (net SMD tanpa PPN dari `sm_sales_net`).
 */
import { codeName } from './brands';
import type { PtSelection } from './filters';
import { toNum, type Raw } from './sm-parse';
import type { ChartSeries, ColorToken } from './chart-spec';
import type { ValueFmt } from './format';
import type { ColumnDef } from './table-defs';
import { pctOf, trendSeries, type DailyRow } from './vm';

export type Cell = string | number | boolean | null;
export type MRow = Record<string, Cell>;

export const NA_SMD = 'Tidak berlaku untuk SMD';
export const SMD_BASIS = 'termasuk PPN';
/** Kode brand seri SMD pada chart/trend (lib/brands.ts BRAND_INFO.smd). */
export const SMD_BRAND = 'smd';

/** SMD ikut dihitung? `needs`: filter ESB-saja yang tidak punya padanan di SMD (kategori menu / produk FIN) mengecualikan SMD. */
export function smdActive(sel: PtSelection, filters: { cat: readonly string[]; product: readonly string[] }, needs: 'cat' | 'product' | null): boolean {
  if (!sel.smd) return false;
  return needs === null || filters[needs].length === 0;
}

/** Catatan di UI bila SMD dipilih tetapi tidak ikut karena filter ESB-saja aktif; null bila tidak relevan. */
export function smdExcludedNote(sel: PtSelection, filters: { cat: readonly string[]; product: readonly string[] }, needs: 'cat' | 'product' | null): string | null {
  if (!sel.smd || needs === null || filters[needs].length === 0) return null;
  return `Filter ${needs === 'cat' ? 'kategori menu' : 'produk FIN'} hanya berlaku untuk brand F&B; SMD tidak ikut dihitung.`;
}

/** Ringkasan cakupan untuk kartu F&B-only: 'only-smd' -> "Tidak berlaku"; 'mixed' -> hanya brand ESB + catatan; 'esb' -> biasa. */
export type EsbOnlyMode = 'esb' | 'mixed' | 'only-smd';
export function esbOnlyMode(sel: PtSelection): EsbOnlyMode {
  if (!sel.esb) return 'only-smd';
  return sel.smd ? 'mixed' : 'esb';
}
export const MIXED_NOTE = 'Hanya memuat brand F&B (ESB); SMD tidak ikut dalam kartu ini.';

/**
 * Opsi cabang SMD (daftar Cabang di FilterBar) dari baris `sm_warehouses`: hanya outlet (DC tidak menjual), nilai `sm:<warehouse_id>`
 * agar tidak bentrok dengan id cabang ESB, kode PT `SMD`.
 */
export function smdBranchOptions(rows: readonly Record<string, unknown>[]): { value: string; label: string; group: string; code: string }[] {
  const out: { value: string; label: string; group: string; code: string }[] = [];
  const seen = new Set<number>();
  for (const r of rows) {
    const id = Number(r.warehouse_id);
    if (!Number.isSafeInteger(id) || id <= 0 || seen.has(id) || r.outlet_type !== 'outlet') continue;
    seen.add(id);
    const name = typeof r.warehouse_name === 'string' && r.warehouse_name !== '' ? r.warehouse_name : `Outlet #${id}`;
    out.push({ value: `sm:${id}`, label: Number(r.active) === 0 ? `${name} (nonaktif)` : name, group: codeName('SMD'), code: 'SMD' });
  }
  return out;
}

// ---------------------------------------------------------------- dua sisi (ESB + SMD) dengan kegagalan sebagian

/** Sama bentuknya dengan `Result` di lib/loaders.ts (tipe struktural; modul ini tetap murni). */
export type Res<T> = { ok: true; data: T } | { ok: false; ref: string; reason?: 'rate_limited' };

export interface MergedSides<A, B> {
  /** Data sisi F&B (null = tidak aktif atau gagal). */
  esb: A | null;
  smd: B | null;
  /** Catatan bila tepat satu sisi aktif gagal sementara sisi lain berhasil (docs/04 §28.14.1 butir 4). */
  note: string | null;
  /** Semua sisi aktif gagal -> kartu galat. */
  fail: { ref: string; reason?: 'rate_limited' } | null;
}

export function mergeSides<A, B>(esbOn: boolean, esb: Res<A> | null, smdOn: boolean, smd: Res<B> | null): MergedSides<A, B> {
  const e = esbOn && esb ? esb : null;
  const s = smdOn && smd ? smd : null;
  const eOk = e !== null && e.ok;
  const sOk = s !== null && s.ok;
  const out: MergedSides<A, B> = { esb: e && e.ok ? e.data : null, smd: s && s.ok ? s.data : null, note: null, fail: null };
  if (!eOk && !sOk) {
    const bad = (e && !e.ok ? e : null) ?? (s && !s.ok ? s : null);
    if (bad && !bad.ok) out.fail = bad.reason ? { ref: bad.ref, reason: bad.reason } : { ref: bad.ref };
    return out;
  }
  if (e !== null && !e.ok) out.note = 'Data F&B tidak tersedia saat ini; angka hanya mencakup SMD.';
  else if (s !== null && !s.ok) out.note = 'Data SMD tidak tersedia saat ini; angka hanya mencakup brand F&B.';
  return out;
}

/** Semua hasil harus berhasil (kartu yang butuh beberapa query SM sekaligus); gagal -> hasil gagal pertama. */
export function allOk<T>(rs: readonly Res<T>[]): Res<T[]> {
  const data: T[] = [];
  for (const r of rs) {
    if (!r.ok) return r;
    data.push(r.data);
  }
  return { ok: true, data };
}

export const MIXED_BASIS_NOTE =
  'Total gabungan berbasis campuran: brand F&B tanpa PB1/service charge, SMD termasuk PPN. Nilai per brand tidak terpengaruh. Subtotal per segmen (F&B dan Supermarket) ditampilkan terpisah di bawah total.';

// ---------------------------------------------------------------- segmen (total + subtotal, keputusan pemilik 21 Nov 2026, Q-SM46)

export type SegKind = 'fb' | 'sm';
export const SEG_FB = 'F&B';
export const SEG_SM = 'Supermarket';
export const SEG_NAME_SM_SERIES = 'Supermarket (SMD)';
export const segLabel = (k: SegKind): string => (k === 'fb' ? SEG_FB : SEG_SM);
/** Kelas CSS lencana segmen (globals.css `.seg-badge`); teks selalu ada, warna hanya penegas. */
export const segBadgeClass = (label: string): string => (label === SEG_SM ? 'seg-badge seg-badge--sm' : 'seg-badge');

/** Tambah kolom teks `segment` pada tiap baris (hanya dipakai pada mode campuran). */
export function tagSegment(rows: readonly MRow[], kind: SegKind): MRow[] {
  return rows.map((r) => ({ ...r, segment: segLabel(kind) }));
}

/** Sisipkan kolom "Segmen" tepat setelah kolom pertama; tidak mengubah array masukan. */
export function withSegmentColumn(columns: readonly ColumnDef[]): ColumnDef[] {
  const seg: ColumnDef = { key: 'segment', header: 'Segmen', fmt: 'segment', align: 'left' };
  return [...columns.slice(0, 1), seg, ...columns.slice(1)];
}

/** Penjumlahan kolom: null bila tidak ada satu pun nilai numerik (mis. MDR SMD = n/a, bukan 0). */
function sumKey(rows: readonly MRow[], key: string): number | null {
  let any = false;
  let s = 0;
  for (const r of rows) {
    const v = r[key];
    // Baris ESB dari ClickHouse bisa membawa angka sebagai string numerik.
    const n = typeof v === 'number' ? v : typeof v === 'string' && v.trim() !== '' ? Number(v) : Number.NaN;
    if (Number.isFinite(n)) {
      any = true;
      s += n;
    }
  }
  return any ? s : null;
}

export interface FooterSpec {
  /** Kolom label baris (kolom pertama tabel). */
  labelKey: string;
  /** Kolom numerik yang dijumlahkan. */
  sumKeys: readonly string[];
  /** Kolom turunan (rasio, rata-rata) dihitung dari jumlah; kind 'total' = gabungan. */
  derive?: (kind: SegKind | 'total', sums: MRow) => MRow;
  /** Label baris total gabungan; undefined = tanpa baris total. */
  totalLabel?: string;
}

/**
 * Baris footer tabel campuran: "Subtotal F&B", "Subtotal Supermarket", lalu total gabungan (bila kedua segmen ada).
 * Segmen tanpa baris dilewati. Baris footer tidak ikut disortir/dipaginasi (DataTable `footerRows`).
 */
export function segmentFooter(esb: readonly MRow[], smd: readonly MRow[], spec: FooterSpec): MRow[] {
  const build = (rows: readonly MRow[], kind: SegKind | 'total', label: string, segment: string | null): MRow => {
    const sums: MRow = {};
    for (const k of spec.sumKeys) sums[k] = sumKey(rows, k);
    return { [spec.labelKey]: label, segment, ...sums, ...(spec.derive ? spec.derive(kind, sums) : {}) };
  };
  const out: MRow[] = [];
  if (esb.length > 0) out.push(build(esb, 'fb', `Subtotal ${SEG_FB}`, SEG_FB));
  if (smd.length > 0) out.push(build(smd, 'sm', `Subtotal ${SEG_SM}`, SEG_SM));
  if (spec.totalLabel && esb.length > 0 && smd.length > 0) out.push(build([...esb, ...smd], 'total', spec.totalLabel, null));
  return out;
}

export interface KpiPart {
  label: string;
  value: number | null;
  fmt?: ValueFmt;
  note?: string;
}

/** Rincian "F&B" / "Supermarket" di bawah nilai KPI (mode campuran). `smNote` mis. "termasuk PPN". */
export function segParts(fb: number | null, sm: number | null, o: { fmt?: ValueFmt; fbNote?: string; smNote?: string } = {}): KpiPart[] {
  const mk = (label: string, value: number | null, note?: string): KpiPart => {
    const p: KpiPart = { label, value };
    if (o.fmt) p.fmt = o.fmt;
    if (note) p.note = note;
    return p;
  };
  return [mk(SEG_FB, fb, o.fbNote), mk(SEG_SM, sm, o.smNote)];
}

/** Subtotal kontribusi PT per segmen (baris `company_code === 'SMD'` = Supermarket). */
export function ptShareSubtotals(rows: readonly { company_code: string; net: number }[]): { fb: number; sm: number } {
  let fb = 0;
  let sm = 0;
  for (const r of rows) {
    if (r.company_code === 'SMD') sm += r.net;
    else fb += r.net;
  }
  return { fb, sm };
}

/**
 * Satu seri tren dari sekumpulan baris harian (subtotal segmen). `rows` dianggap satu kelompok; nama & warna ditimpa.
 * Metrik 'avg' tidak dipakai pada mode campuran.
 */
export function segmentTrendSeries(rows: readonly DailyRow[], from: string, to: string, metric: 'net' | 'trx' | 'avg', name: string, color: ColorToken): ChartSeries {
  const t = trendSeries(
    rows.map((r) => ({ ...r, brand: '_segment' })),
    from,
    to,
    metric,
  );
  return { ...t.series[0], name, color };
}

// ---------------------------------------------------------------- KPI Ringkasan

export interface KpiParts {
  /** Omzet pembuka KPI "Penjualan": ESB = penjualan bersih; SMD = termasuk PPN. */
  rev: number;
  revPrev: number;
  trx: number;
  trxPrev: number;
  cogs: number;
  cogsPrev: number;
  /** Penyebut HPP (penjualan bersih TANPA pajak). */
  base: number;
  basePrev: number;
}

export const ZERO_PARTS: KpiParts = { rev: 0, revPrev: 0, trx: 0, trxPrev: 0, cogs: 0, cogsPrev: 0, base: 0, basePrev: 0 };

export function esbParts(k: { net_cur?: unknown; net_prev?: unknown; trx_cur?: unknown; trx_prev?: unknown; cogs_cur?: unknown; cogs_prev?: unknown } | null | undefined): KpiParts {
  if (!k) return ZERO_PARTS;
  return {
    rev: toNum(k.net_cur),
    revPrev: toNum(k.net_prev),
    trx: toNum(k.trx_cur),
    trxPrev: toNum(k.trx_prev),
    cogs: toNum(k.cogs_cur),
    cogsPrev: toNum(k.cogs_prev),
    base: toNum(k.net_cur),
    basePrev: toNum(k.net_prev),
  };
}

/** Hari `>= from` = periode ini; sisanya (rentang pembanding) = periode lalu. */
export function splitDaily(rows: readonly Raw[], from: string): { rev: number; revPrev: number; trx: number; trxPrev: number } {
  const o = { rev: 0, revPrev: 0, trx: 0, trxPrev: 0 };
  for (const r of rows) {
    const cur = String(r.day) >= from;
    if (cur) {
      o.rev += toNum(r.rev_sum);
      o.trx += toNum(r.orders_sum);
    } else {
      o.revPrev += toNum(r.rev_sum);
      o.trxPrev += toNum(r.orders_sum);
    }
  }
  return o;
}

/** `net` = baris `sm_sales_net` (net_cur/net_prev tanpa PPN); `cogs` = baris `sm_cogs_sums`. */
export function smdParts(daily: readonly Raw[], from: string, net: Raw | undefined, cogs: Raw | undefined): KpiParts {
  const d = splitDaily(daily, from);
  return {
    rev: d.rev,
    revPrev: d.revPrev,
    trx: d.trx,
    trxPrev: d.trxPrev,
    cogs: toNum(cogs?.cogs_cur),
    cogsPrev: toNum(cogs?.cogs_prev),
    base: toNum(net?.net_cur),
    basePrev: toNum(net?.net_prev),
  };
}

export function sumParts(a: KpiParts, b: KpiParts): KpiParts {
  return {
    rev: a.rev + b.rev,
    revPrev: a.revPrev + b.revPrev,
    trx: a.trx + b.trx,
    trxPrev: a.trxPrev + b.trxPrev,
    cogs: a.cogs + b.cogs,
    cogsPrev: a.cogsPrev + b.cogsPrev,
    base: a.base + b.base,
    basePrev: a.basePrev + b.basePrev,
  };
}

export const hppPct = (p: KpiParts): { cur: number | null; prev: number | null } => ({ cur: pctOf(p.cogs, p.base), prev: pctOf(p.cogsPrev, p.basePrev) });

/** Baris tren harian seri SMD (brand `smd`) dari `sm_fb_daily`; `trendSeries` (lib/vm.ts) menggambar satu garis per brand. */
export function smdTrendRows(rows: readonly Raw[]): DailyRow[] {
  return rows.map((r) => ({ sales_date: String(r.day), brand: SMD_BRAND, net: toNum(r.rev_sum), trx: toNum(r.orders_sum) }));
}

// ---------------------------------------------------------------- peringkat cabang & kontribusi PT

/**
 * Baris peringkat cabang SMD (kolom tabel 'branch-rank'). Omzet termasuk PPN ditulis di label agar tidak tertukar dengan
 * penjualan bersih ESB. HPP% = (net tanpa PPN - margin kotor) / net tanpa PPN dari `sm_ex_by_outlet_margin`.
 */
export function smdBranchRows(outlets: readonly Raw[], margins: readonly Raw[], names: ReadonlyMap<number, string>): MRow[] {
  const mg = new Map(margins.map((m) => [toNum(m.warehouse_id), m]));
  return outlets.map((o) => {
    const id = toNum(o.warehouse_id);
    const rev = toNum(o.rev_cur);
    const trx = toNum(o.orders_cur);
    const m = mg.get(id);
    const net = toNum(m?.net_cur);
    const hpp = m && net > 0 ? ((net - toNum(m.gm_cur)) / net) * 100 : null;
    return {
      branch_key: `sm:${id}`,
      branch_label: `${names.get(id) ?? `Outlet #${id}`} (${SMD_BASIS})`,
      company_code: 'SMD',
      net_sales: rev,
      trx,
      avg_trx: trx > 0 ? rev / trx : null,
      hpp_pct: hpp,
    };
  });
}

export function mergeBranchRank(esb: readonly MRow[], smd: readonly MRow[]): MRow[] {
  return [...esb, ...smd].sort((a, b) => Number(b.net_sales ?? 0) - Number(a.net_sales ?? 0));
}

export function mergePtShare(esb: readonly { company_code: string; net: number }[], smdRev: number | null): { company_code: string; net: number }[] {
  const rows = [...esb];
  if (smdRev !== null) rows.push({ company_code: 'SMD', net: smdRev });
  return rows.sort((a, b) => b.net - a.net);
}

// ---------------------------------------------------------------- Penjualan & Menu

export interface SalesSums {
  net: number;
  netPrev: number;
  gross: number;
  grossPrev: number;
  disc: number;
  discPrev: number;
  qty: number;
  qtyPrev: number;
  /** Margin kotor = pembilang; penyebut = penjualan bersih tanpa pajak. */
  gm: number;
  gmPrev: number;
  base: number;
  basePrev: number;
}

export const ZERO_SALES: SalesSums = { net: 0, netPrev: 0, gross: 0, grossPrev: 0, disc: 0, discPrev: 0, qty: 0, qtyPrev: 0, gm: 0, gmPrev: 0, base: 0, basePrev: 0 };

export function esbSalesSums(k: Record<string, unknown> | null | undefined): SalesSums {
  if (!k) return ZERO_SALES;
  return {
    net: toNum(k.net_cur),
    netPrev: toNum(k.net_prev),
    gross: toNum(k.gross_cur),
    grossPrev: toNum(k.gross_prev),
    disc: toNum(k.discount_cur),
    discPrev: toNum(k.discount_prev),
    qty: toNum(k.qty_cur),
    qtyPrev: toNum(k.qty_prev),
    gm: toNum(k.net_cur) - toNum(k.cogs_cur),
    gmPrev: toNum(k.net_prev) - toNum(k.cogs_prev),
    base: toNum(k.net_cur),
    basePrev: toNum(k.net_prev),
  };
}

/** `daily` = sm_fb_daily; `kpi` = sm_fb_sales_kpi; `margin` = sm_ex_margin (net_cur/gm_cur tanpa PPN). */
export function smdSalesSums(daily: readonly Raw[], from: string, kpi: Raw | undefined, margin: Raw | undefined): SalesSums {
  const d = splitDaily(daily, from);
  return {
    net: d.rev,
    netPrev: d.revPrev,
    gross: toNum(kpi?.gross_cur),
    grossPrev: toNum(kpi?.gross_prev),
    disc: toNum(kpi?.disc_cur),
    discPrev: toNum(kpi?.disc_prev),
    qty: toNum(kpi?.qty_cur),
    qtyPrev: toNum(kpi?.qty_prev),
    gm: toNum(margin?.gm_cur),
    gmPrev: toNum(margin?.gm_prev),
    base: toNum(margin?.net_cur),
    basePrev: toNum(margin?.net_prev),
  };
}

export function addSales(a: SalesSums, b: SalesSums): SalesSums {
  return {
    net: a.net + b.net,
    netPrev: a.netPrev + b.netPrev,
    gross: a.gross + b.gross,
    grossPrev: a.grossPrev + b.grossPrev,
    disc: a.disc + b.disc,
    discPrev: a.discPrev + b.discPrev,
    qty: a.qty + b.qty,
    qtyPrev: a.qtyPrev + b.qtyPrev,
    gm: a.gm + b.gm,
    gmPrev: a.gmPrev + b.gmPrev,
    base: a.base + b.base,
    basePrev: a.basePrev + b.basePrev,
  };
}

/** Rata-rata penjualan per hari dalam minggu (indeks 0 = Senin). ESB `avg_net` dijumlah dengan rata-rata SMD (rev / hari). */
export function mergeWeekday(esb: readonly { dow: number | string; avg_net: number | string }[], smd: readonly Raw[]): (number | null)[] {
  const out: (number | null)[] = [];
  for (let dow = 1; dow <= 7; dow += 1) {
    const e = esb.find((r) => Number(r.dow) === dow);
    const s = smd.find((r) => toNum(r.dow) === dow);
    const sDays = toNum(s?.days_n);
    const eVal = e ? toNum(e.avg_net) : null;
    const sVal = s && sDays > 0 ? toNum(s.rev_sum) / sDays : null;
    out.push(eVal === null && sVal === null ? null : (eVal ?? 0) + (sVal ?? 0));
  }
  return out;
}

export function mergeHeat(
  esb: readonly { week: string; dow: number | string; net: number | string }[],
  smd: readonly Raw[],
): { week: string; dow: number; net: number }[] {
  const m = new Map<string, { week: string; dow: number; net: number }>();
  const add = (week: string, dow: number, net: number): void => {
    const k = `${week}|${dow}`;
    const cur = m.get(k);
    if (cur) cur.net += net;
    else m.set(k, { week, dow, net });
  };
  for (const r of esb) add(String(r.week), Number(r.dow), toNum(r.net));
  for (const r of smd) add(String(r.week_key), toNum(r.dow), toNum(r.rev_sum));
  return Array.from(m.values());
}

export interface CatItem {
  label: string;
  net: number;
  qty: number;
  margin: number | null;
  /** Hanya kategori menu ESB yang bisa dijadikan filter `cat`. */
  drillable: boolean;
  /** Segmen asal (warna batang: F&B c1, Supermarket c7). */
  segment: SegKind;
}

export function mergeCategories(
  esb: readonly { category: string; net_sales: number | string; qty_sold: number | string; margin_pct: number | string | null }[],
  smd: readonly Raw[],
): CatItem[] {
  const items: CatItem[] = esb.map((r) => ({
    label: r.category === '' ? 'Tanpa kategori' : r.category,
    net: toNum(r.net_sales),
    qty: toNum(r.qty_sold),
    margin: r.margin_pct === null ? null : toNum(r.margin_pct),
    drillable: r.category !== '',
    segment: 'fb',
  }));
  for (const r of smd) {
    const name = typeof r.category_name === 'string' && r.category_name !== '' ? r.category_name : `Kategori #${toNum(r.categ_id)}`;
    items.push({ label: `SMD: ${name}`, net: toNum(r.rev_sum), qty: toNum(r.qty_sum), margin: null, drillable: false, segment: 'sm' });
  }
  return items;
}

// ---------------------------------------------------------------- Pembayaran

// `method_type` di mart = pos_payment_method.payment_method_type Odoo 19 (ETL efn_etl/pos.py): none | terminal | qr_code.
// Kunci cash/bank/pay_later (field `type` lama) dipertahankan sebagai cadangan. Rincian tunai/QRIS/debit ada di nama metode.
export const SMD_PAY_TYPE: Readonly<Record<string, string>> = {
  none: 'Manual (tunai / transfer / kartu)',
  terminal: 'Terminal kartu (EDC)',
  qr_code: 'QR code',
  cash: 'Tunai',
  bank: 'Bank / non-tunai',
  pay_later: 'Bayar nanti',
};
export const smdPayType = (t: unknown): string => {
  const k = typeof t === 'string' ? t : '';
  return SMD_PAY_TYPE[k] ?? (k === '' ? 'Tidak diketahui' : k);
};

/** Kolom tabel 'pay-methods'. SMD tidak punya MDR di mart: nilai kosong (n/a), bukan 0. */
export function smdPayMethodRows(matrix: readonly Raw[]): MRow[] {
  const m = new Map<string, { type: string; method: string; amount: number; n: number }>();
  for (const r of matrix) {
    const type = smdPayType(r.method_type);
    const method = typeof r.method_name === 'string' && r.method_name !== '' ? r.method_name : 'Tidak diketahui';
    const k = `${type}|${method}`;
    const cur = m.get(k) ?? { type, method, amount: 0, n: 0 };
    cur.amount += toNum(r.amount_sum);
    cur.n += toNum(r.pay_n);
    m.set(k, cur);
  }
  return Array.from(m.values()).map((v) => ({
    payment_type: `SMD - ${v.type}`,
    method: v.method,
    cnt: v.n,
    amount_total: v.amount,
    mdr_total: null,
    mdr_pct: null,
    avg_amount: v.n > 0 ? v.amount / v.n : null,
  }));
}

/** Kolom tabel 'pay-branches'; metode dominan = metode bernilai terbesar di outlet itu. */
export function smdPayBranchRows(matrix: readonly Raw[], names: ReadonlyMap<number, string>): MRow[] {
  const by = new Map<number, { amount: number; top: string; topAmount: number }>();
  for (const r of matrix) {
    const id = toNum(r.warehouse_id);
    const amt = toNum(r.amount_sum);
    const cur = by.get(id) ?? { amount: 0, top: '-', topAmount: -1 };
    cur.amount += amt;
    if (amt > cur.topAmount) {
      cur.topAmount = amt;
      cur.top = typeof r.method_name === 'string' && r.method_name !== '' ? r.method_name : 'Tidak diketahui';
    }
    by.set(id, cur);
  }
  return Array.from(by.entries()).map(([id, v]) => ({
    branch_key: `sm:${id}`,
    branch_name: `${names.get(id) ?? `Outlet #${id}`} (SMD)`,
    amount: v.amount,
    mdr: null,
    top_method: v.top,
  }));
}

export interface PayTrendRow {
  sales_date: string;
  payment_type: string;
  amount: number;
  payment_count: number;
}

/** Baris tren pembayaran seri SMD; awalan "SMD - " agar tidak menyatu diam-diam dengan tipe ESB bernama serupa. */
export function smdPayTrendRows(rows: readonly Raw[]): PayTrendRow[] {
  return rows.map((r) => ({
    sales_date: String(r.day),
    payment_type: `SMD - ${smdPayType(r.method_type)}`,
    amount: toNum(r.amount_sum),
    payment_count: toNum(r.pay_n),
  }));
}
