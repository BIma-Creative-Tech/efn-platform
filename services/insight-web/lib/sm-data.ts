/**
 * Pemuat data halaman supermarket (server-only). Setiap kartu = satu atau beberapa query katalog (lib/queries-sm.ts);
 * satu query gagal = satu kartu galat (Result), bukan halaman putih. Cakupan SQL_efn_* dihitung dari SESI terverifikasi
 * (lib/scope.ts), tidak pernah dari input browser. Tanpa PII di log (log hanya nama query, jumlah baris, durasi).
 */
import 'server-only';
import { chQuerySm } from './clickhouse';
import { addDays, wibToday, wibYesterday } from './dates';
import { getConfig } from './runtime';
import type { Session } from './session';
import type { SmParamBag } from './ch-params-sm';
import { settle, type Result } from './loaders';
import type { SmQueryName } from './queries-sm';
import {
  allWh,
  effectiveWh,
  effectiveWhz,
  firstOfMonthMinus,
  type SmFilters,
} from './sm-filters';
import { buildSmConfig, buildThresholds, cfgNum, cfgStr, DEFAULT_SM_CONFIG, type KpiConfigRow, type SmConfig, type SmThresholds } from './sm-thresholds';
import { computeGmroi, type GmroiMonthRow, type GmroiResult } from './sm-kpi';
import {
  parseCampaigns,
  parseCritical,
  parseExpiryLots,
  parseFreshness,
  parseKpiRows,
  parsePriceRows,
  parseProposals,
  parsePurchaseOrders,
  toNum,
  type CampaignRow,
  type CriticalRow,
  type ExpiryLot,
  type MartFreshness,
  type PoRow,
  type PriceRow,
  type ProposalRow,
  type Raw,
} from './sm-parse';
import type { WarehouseInfo } from './sm-personas';
import { buildShelfRows, shelfProductIds, type ShelfRow } from './sm-shelf';
import {
  buildOosHeat,
  buildOutletRows,
  buildWasteVm,
  expiryDelta,
  summarizeExpiryTotals,
  wasteBase,
  type ExpirySummary,
  type HeatVm,
  type OutletRow,
  type WasteVm,
} from './sm-vm';

// ---------------------------------------------------------------- konteks & dasar
export interface SmBase {
  config: SmConfig;
  t: SmThresholds;
  freshness: Map<string, MartFreshness>;
  freshnessOk: boolean;
  warehouses: WarehouseInfo[];
  whNames: Map<number, string>;
  categories: { id: number; name: string }[];
  catNames: Map<number, string>;
}

export interface SmCtx {
  session: Session;
  filters: SmFilters;
  now: Date;
  today: string;
  yesterday: string;
  base: SmBase;
  /** Origin Odoo untuk tautan "Buka di Odoo" (null = tautan tidak dirender). */
  odooOrigin: string | null;
}

const KPI_TTL_MS = 5 * 60_000;
const kpiCache = new Map<string, { at: number; rows: KpiConfigRow[] }>();

async function loadKpiRows(session: Session, bag: SmParamBag): Promise<KpiConfigRow[]> {
  const key = session.cids.join(',');
  const hit = kpiCache.get(key);
  const nowMs = Date.now();
  if (hit && nowMs - hit.at < KPI_TTL_MS) return hit.rows;
  try {
    const rows = parseKpiRows(await chQuerySm<Raw>('sm_kpi_config', session, bag));
    kpiCache.set(key, { at: nowMs, rows });
    return rows;
  } catch {
    // Konfigurasi gagal dibaca: pakai salinan lama bila ada, selain itu default kontrak (fail aman, bukan fail kosong).
    return hit ? hit.rows : [];
  }
}

export async function loadSmBase(session: Session, now: Date): Promise<SmBase> {
  const bag: SmParamBag = { cids: session.cids, whs: allWh(session) };
  const [rows, fresh, whs, cats] = await Promise.all([
    loadKpiRows(session, bag),
    settle(chQuerySm<Raw>('sm_freshness', session, bag)),
    settle(chQuerySm<Raw>('sm_warehouses', session, bag)),
    settle(chQuerySm<Raw>('sm_categories', session, bag)),
  ]);
  const config = rows.length > 0 ? buildSmConfig(rows) : DEFAULT_SM_CONFIG;
  const freshness = new Map<string, MartFreshness>();
  if (fresh.ok) for (const f of parseFreshness(fresh.data)) freshness.set(f.mart, f);
  const warehouses: WarehouseInfo[] = whs.ok
    ? whs.data
        .map((r) => ({
          id: toNum(r.warehouse_id),
          name: typeof r.warehouse_name === 'string' && r.warehouse_name !== '' ? r.warehouse_name : `Gudang #${toNum(r.warehouse_id)}`,
          type: (r.outlet_type === 'dc' ? 'dc' : r.outlet_type === 'outlet' ? 'outlet' : 'none') as WarehouseInfo['type'],
        }))
        .filter((w) => w.id > 0)
    : [];
  const categories = cats.ok
    ? cats.data
        .map((r) => ({ id: toNum(r.categ_id), name: typeof r.category_name === 'string' ? r.category_name : '' }))
        .filter((c) => c.id > 0 && c.name !== '')
    : [];
  return {
    config,
    t: buildThresholds(config),
    freshness,
    freshnessOk: fresh.ok,
    warehouses,
    whNames: new Map(warehouses.map((w) => [w.id, w.name])),
    categories,
    catNames: new Map(categories.map((c) => [c.id, c.name])),
  };
}

export function makeCtx(session: Session, filters: SmFilters, base: SmBase, now: Date): SmCtx {
  return {
    session,
    filters,
    now,
    today: wibToday(now),
    yesterday: wibYesterday(now),
    base,
    odooOrigin: getConfig().odooOrigin,
  };
}

// ---------------------------------------------------------------- util kueri
function bagFor(ctx: SmCtx, extra: SmParamBag = {}): SmParamBag {
  const f = ctx.filters;
  const s = ctx.session;
  return {
    cids: s.cids,
    from: f.from,
    to: f.to,
    prev_from: f.prev.from,
    prev_to: f.prev.to,
    yoy_from: f.yoy.from,
    yoy_to: f.yoy.to,
    today: ctx.today,
    until: addDays(ctx.today, 7),
    gm_from: firstOfMonthMinus(f.to, 11),
    wh: effectiveWh(f, s),
    whs: allWh(s),
    whz: effectiveWhz(f, s),
    cat: f.cat,
    ids: [],
    channel: '',
    price_warn: ctx.base.t.priceWarn,
    min_n: ctx.base.t.priceMinSamples,
    ...extra,
  };
}

function q(name: SmQueryName, ctx: SmCtx, extra?: SmParamBag): Promise<Result<Raw[]>> {
  return settle(chQuerySm<Raw>(name, ctx.session, bagFor(ctx, extra)));
}

function map<T, U>(r: Result<T>, fn: (d: T) => U): Result<U> {
  return r.ok ? { ok: true, data: fn(r.data) } : r;
}

/** Gabungkan dua hasil: gagal bila salah satu gagal (kartu yang butuh keduanya). */
function both<A, B, U>(a: Result<A>, b: Result<B>, fn: (x: A, y: B) => U): Result<U> {
  if (!a.ok) return a;
  if (!b.ok) return b;
  return { ok: true, data: fn(a.data, b.data) };
}

const first = (rows: Raw[]): Raw | undefined => rows[0];

// ---------------------------------------------------------------- Umur simpan (G51 B4)
/** Lot terdekat kedaluwarsa per produk x outlet (v_sm_lot_stock) + master produk (shelf_life_days). Dua query berurutan. */
async function loadShelf(ctx: SmCtx): Promise<Result<ShelfRow[]>> {
  const lots = await q('sm_shelf_lots', ctx);
  if (!lots.ok) return lots;
  const ids = shelfProductIds(lots.data);
  // Master produk gagal -> baris tetap tampil dengan nama "Produk #id" dan umur simpan "tidak dikelola" (bukan galat kartu).
  const prods = ids.length > 0 ? await q('sm_shelf_products', ctx, { ids }) : ({ ok: true, data: [] } as Result<Raw[]>);
  return { ok: true, data: buildShelfRows(lots.data, prods.ok ? prods.data : []) };
}

// ---------------------------------------------------------------- Kepala toko
export interface StoreData {
  shelf: Result<ShelfRow[]>;
  proposals: Result<ProposalRow[]>;
  expiry: Result<{ lots: ExpiryLot[]; delta: ReturnType<typeof expiryDelta> }>;
  critical: Result<CriticalRow[]>;
  waste: Result<WasteVm>;
}

export async function loadStore(ctx: SmCtx): Promise<StoreData> {
  const [prop, exp, trend, crit, sums, reasons, net, shelf] = await Promise.all([
    q('sm_st_proposals', ctx),
    q('sm_st_expiry', ctx),
    q('sm_st_expiry_trend', ctx, { prev_from: ctx.yesterday, to: ctx.today }),
    q('sm_st_critical', ctx),
    q('sm_waste_sums', ctx),
    q('sm_waste_reasons', ctx),
    q('sm_sales_net', ctx),
    loadShelf(ctx),
  ]);
  const basis = cfgStr(ctx.base.config, 'waste.basis');
  return {
    shelf,
    proposals: map(prop, parseProposals),
    expiry: both(exp, trend, (e, t) => ({ lots: parseExpiryLots(e), delta: expiryDelta(t, ctx.today, ctx.yesterday) })),
    critical: map(crit, parseCritical),
    // Kepala toko tidak boleh membaca margin (HPP terjual): bila penyebut = HPP, persentase tidak ditampilkan (nilai Rp tetap).
    waste: both(both(sums, reasons, (s, r) => ({ s, r })), net, ({ s, r }, n) =>
      buildWasteVm(first(s), wasteBase(basis, false, first(n), undefined), r),
    ),
  };
}

// ---------------------------------------------------------------- Direksi
export interface WeeklyPoint {
  week: string;
  wape: number | null;
  bias: number | null;
}

export interface ExecData {
  sales: Result<Raw | undefined>;
  trend: Result<Raw[]>;
  margin: Result<Raw | undefined>;
  gmroi: Result<GmroiResult>;
  waste: Result<WasteVm>;
  weekly: Result<Raw[]>;
  outlets: Result<OutletRow[]>;
  wapeOutlets: Result<Raw[]>;
  wasteCats: Result<{ id: number; name: string; wastePct: number | null; waste: number }[]>;
}

export async function loadExec(ctx: SmCtx): Promise<ExecData> {
  const cfg = ctx.base.config;
  // QA G51: week_start = Senin; 8 minggu selesai = Senin (minggu ini - 8 .. - 1) = sampai today-62 (-55 membuang minggu tertua).
  const wapeRange = { from: addDays(ctx.today, -62), to: ctx.today };
  const [sales, trend, margin, gm, sums, weekly, wapeOut, oSales, oMargin, oWaste, wCat, sCat] = await Promise.all([
    q('sm_ex_sales', ctx),
    q('sm_ex_trend', ctx),
    q('sm_ex_margin', ctx),
    q('sm_ex_gmroi', ctx),
    q('sm_waste_sums', ctx),
    q('sm_ex_wape_weekly', ctx, wapeRange),
    q('sm_ex_wape_outlets', ctx, wapeRange),
    q('sm_ex_by_outlet_sales', ctx),
    q('sm_ex_by_outlet_margin', ctx),
    q('sm_ex_by_outlet_waste', ctx),
    q('sm_ex_waste_by_cat', ctx),
    q('sm_ex_sales_by_cat', ctx),
  ]);
  const basis = cfgStr(cfg, 'waste.basis');
  const gmroiRows = (rows: Raw[]): GmroiMonthRow[] =>
    rows.map((r) => ({
      month: String(r.month),
      outletType: typeof r.outlet_type === 'string' ? r.outlet_type : '',
      gm: toNum(r.gm_sum),
      stockDaily: toNum(r.stock_daily_sum),
      stockMonthEnd: toNum(r.stock_me_sum),
      days: toNum(r.days_n),
    }));
  return {
    sales: map(sales, first),
    trend,
    margin: map(margin, first),
    gmroi: map(gm, (rows) =>
      computeGmroi(
        gmroiRows(rows),
        {
          period: cfgStr(cfg, 'gmroi.period') as 'month' | 'quarter' | 'year',
          stockAvg: cfgStr(cfg, 'gmroi.stock_avg') as 'daily' | 'month_end',
          includeDc: cfgNum(cfg, 'gmroi.include_dc') === 1,
          annualize: cfgNum(cfg, 'gmroi.annualize') === 1,
        },
        ctx.filters.to,
      ),
    ),
    waste: both(both(sums, sales, (s, a) => ({ s, a })), margin, ({ s, a }, m) =>
      buildWasteVm(first(s), wasteBase(basis, true, first(a), first(m)), []),
    ),
    weekly,
    outlets: both(both(oSales, oMargin, (a, b) => ({ a, b })), oWaste, ({ a, b }, c) => buildOutletRows(a, b, c, ctx.base.whNames, ctx.base.t)),
    wapeOutlets: wapeOut,
    wasteCats: both(wCat, sCat, (w, s) => {
      const net = new Map(s.map((r) => [toNum(r.categ_id), toNum(r.net_cur)]));
      return w
        .map((r) => {
          const id = toNum(r.categ_id);
          const base = net.get(id) ?? 0;
          const waste = toNum(r.waste_cur);
          return { id, name: ctx.base.catNames.get(id) ?? `Kategori #${id}`, waste, wastePct: base > 0 ? (waste / base) * 100 : null };
        })
        .filter((r) => r.waste > 0)
        .sort((x, y) => (y.wastePct ?? 0) - (x.wastePct ?? 0))
        .slice(0, 9);
    }),
  };
}

// ---------------------------------------------------------------- Category manager
export interface CategoryData {
  shelf: Result<ShelfRow[]>;
  oos: Result<Raw | undefined>;
  heat: Result<HeatVm>;
  products: Result<{ id: number; tmplId: number | null; name: string; oosDays: number; universeDays: number; outlets: number }[]>;
  expiry: Result<ExpiryLot[]>;
  expiryTotals: Result<ExpirySummary>;
  price: Result<PriceRow[]>;
  coverage: Result<Raw | undefined>;
  signal: Result<{ rows: Raw[]; names: Map<number, { name: string; tmplId: number | null }> }>;
}

export async function loadCategory(ctx: SmCtx): Promise<CategoryData> {
  const [oos, matrix, prods, exp, expT, price, cov, sig, shelf] = await Promise.all([
    q('sm_cm_oos', ctx),
    q('sm_cm_oos_matrix', ctx),
    q('sm_cm_oos_products', ctx),
    q('sm_cm_expiry', ctx),
    q('sm_cm_expiry_totals', ctx),
    q('sm_cm_price', ctx),
    q('sm_cm_price_coverage', ctx),
    q('sm_cm_signal', ctx),
    loadShelf(ctx),
  ]);
  // Nama produk untuk CM-3/CM-6 (view OOS/sinyal tidak membawa nama): satu query tambahan dengan id terbatas.
  const ids = Array.from(
    new Set([...(prods.ok ? prods.data : []), ...(sig.ok ? sig.data : [])].map((r) => toNum(r.product_id)).filter((n) => n > 0)),
  ).slice(0, 200);
  const nameRows = ids.length > 0 ? await q('sm_product_names', ctx, { ids }) : ({ ok: true, data: [] } as Result<Raw[]>);
  const names = new Map<number, { name: string; tmplId: number | null }>();
  if (nameRows.ok) {
    for (const r of nameRows.data) {
      names.set(toNum(r.product_id), { name: typeof r.name_text === 'string' && r.name_text !== '' ? r.name_text : 'Produk tanpa nama', tmplId: toNum(r.tmpl_id) || null });
    }
  }
  return {
    shelf,
    oos: map(oos, first),
    heat: map(matrix, (rows) => buildOosHeat(rows, ctx.base.catNames, ctx.base.whNames)),
    products: map(prods, (rows) =>
      rows.map((r) => {
        const id = toNum(r.product_id);
        const n = names.get(id);
        return { id, tmplId: n?.tmplId ?? null, name: n?.name ?? `Produk #${id}`, oosDays: toNum(r.oos_days), universeDays: toNum(r.universe_days), outlets: toNum(r.outlets_n) };
      }),
    ),
    expiry: map(exp, parseExpiryLots),
    expiryTotals: map(expT, summarizeExpiryTotals),
    price: map(price, parsePriceRows),
    coverage: map(cov, first),
    signal: map(sig, (rows) => ({ rows, names })),
  };
}

// ---------------------------------------------------------------- DC
export interface DcData {
  fill: Result<Raw | undefined>;
  fillDaily: Result<Raw[]>;
  fillOutlets: Result<Raw[]>;
  orders: Result<ProposalRow[]>;
  po: Result<PoRow[]>;
  poTotals: Result<Raw | undefined>;
  stockAge: Result<Raw | undefined>;
}

export async function loadDc(ctx: SmCtx): Promise<DcData> {
  const [fill, daily, outlets, orders, po, poT, age] = await Promise.all([
    q('sm_dc_fill_sums', ctx),
    q('sm_dc_fill_daily', ctx),
    q('sm_dc_fill_outlets', ctx),
    q('sm_dc_orders', ctx),
    q('sm_dc_po', ctx),
    q('sm_dc_po_totals', ctx),
    q('sm_dc_stock_age', ctx),
  ]);
  return {
    fill: map(fill, first),
    fillDaily: daily,
    fillOutlets: outlets,
    orders: map(orders, parseProposals),
    po: map(po, parsePurchaseOrders),
    poTotals: map(poT, first),
    stockAge: map(age, first),
  };
}

// ---------------------------------------------------------------- Marketing
export interface MarketingData {
  funnel: Result<Raw | undefined>;
  campaigns: Result<CampaignRow[]>;
}

export async function loadMarketing(ctx: SmCtx, channel: string): Promise<MarketingData> {
  const [funnel, camp] = await Promise.all([q('sm_mk_funnel', ctx, { channel }), q('sm_mk_campaigns', ctx)]);
  return { funnel: map(funnel, first), campaigns: map(camp, parseCampaigns) };
}
