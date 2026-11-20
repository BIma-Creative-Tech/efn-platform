/**
 * Pengurai baris view `efn_insight.v_sm_*` (JSON ClickHouse) menjadi tipe aplikasi. Murni, defensif: nilai tak terduga
 * menjadi null/default, baris yang kehilangan kunci utama dibuang (tidak pernah melempar). Tidak ada PII (§28.13.8:
 * tanpa pelaku/partner; supplier perorangan sudah kosong di view -> "Supplier #id").
 *
 * Waktu ClickHouse (DateTime64(.., 'UTC')) dicetak "YYYY-MM-DD HH:MM:SS[.ffffff]" tanpa zona -> diperlakukan UTC.
 */
import type { KpiConfigRow } from './sm-thresholds';

export type Raw = Record<string, unknown>;

export function toNum(v: unknown): number {
  const n = typeof v === 'number' ? v : typeof v === 'string' && v.trim() !== '' ? Number(v) : NaN;
  return Number.isFinite(n) ? n : 0;
}
export function toNumOrNull(v: unknown): number | null {
  if (v === null || v === undefined || v === '') return null;
  const n = typeof v === 'number' ? v : Number(v);
  return Number.isFinite(n) ? n : null;
}
export const toFlag = (v: unknown): boolean => v === 1 || v === '1' || v === true;

const DT_RE = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):(\d{2})(?:\.\d+)?Z?$/;
const D_RE = /^(\d{4})-(\d{2})-(\d{2})$/;

/** "2026-11-21 07:45:00[.000000]" (UTC) -> Date; selain itu null. "0000-00-00..." (NULL ClickHouse) -> null. */
export function parseChDateTime(v: unknown): Date | null {
  if (typeof v !== 'string') return null;
  const m = DT_RE.exec(v.trim());
  if (!m) return null;
  const [y, mo, d, h, mi, s] = m.slice(1).map(Number);
  if (y < 1971) return null;
  const ms = Date.UTC(y, mo - 1, d, h, mi, s);
  const date = new Date(ms);
  return date.getUTCFullYear() === y && date.getUTCMonth() === mo - 1 && date.getUTCDate() === d ? date : null;
}

/** "YYYY-MM-DD" sah (kalender) atau null. Tanggal nol ClickHouse ("1970-01-01") dianggap tidak ada. */
export function parseChDate(v: unknown): string | null {
  if (typeof v !== 'string') return null;
  const m = D_RE.exec(v.trim());
  if (!m) return null;
  const [y, mo, d] = m.slice(1).map(Number);
  if (y < 1971) return null;
  const date = new Date(Date.UTC(y, mo - 1, d));
  return date.getUTCFullYear() === y && date.getUTCMonth() === mo - 1 && date.getUTCDate() === d ? v.trim() : null;
}

const CONTROL_RE = /[\p{Cc}\p{Cf}\p{Zl}\p{Zp}]/gu;
/** Teks master data untuk tampilan: buang karakter kontrol, rapikan spasi, potong. Dirender React sebagai teks (bukan HTML). */
export function cleanText(v: unknown, max = 120): string {
  if (typeof v !== 'string') return '';
  const s = v.replace(/\s+/g, ' ').replace(CONTROL_RE, '').trim();
  return Array.from(s).slice(0, max).join('');
}

const uint = (v: unknown): number | null => {
  const n = toNumOrNull(v);
  return n !== null && Number.isInteger(n) && n >= 0 ? n : null;
};

// ---------------------------------------------------------------- kesegaran & konfigurasi
export type FreshClass = 'near_live' | 'stock' | 'daily';
export interface MartFreshness {
  mart: string;
  cls: FreshClass;
  dataAsOf: Date | null;
  sourceAsOf: Date | null;
  lastRunAt: Date | null;
  lastStatus: string | null;
}
const FRESH_CLASSES: readonly string[] = ['near_live', 'stock', 'daily'];

export function parseFreshness(rows: readonly Raw[]): MartFreshness[] {
  const out: MartFreshness[] = [];
  for (const r of rows) {
    const mart = typeof r.mart === 'string' ? r.mart : '';
    const cls = typeof r.freshness_class === 'string' ? r.freshness_class : '';
    if (mart === '' || !FRESH_CLASSES.includes(cls)) continue;
    out.push({
      mart,
      cls: cls as FreshClass,
      dataAsOf: parseChDateTime(r.data_as_of),
      sourceAsOf: parseChDateTime(r.source_as_of),
      lastRunAt: parseChDateTime(r.last_run_at),
      lastStatus: typeof r.last_status === 'string' && r.last_status !== '' ? r.last_status : null,
    });
  }
  return out;
}

/** Baris `v_sm_kpi_config` -> KpiConfigRow (baris tanpa company_id/key dibuang). Nilai divalidasi di lib/sm-thresholds.ts. */
export function parseKpiRows(rows: readonly Raw[]): KpiConfigRow[] {
  const out: KpiConfigRow[] = [];
  for (const r of rows) {
    const company = uint(r.company_id);
    if (company === null || company === 0 || typeof r.key !== 'string' || r.key === '') continue;
    out.push({
      company_id: company,
      key: r.key,
      value_num: toNumOrNull(r.value_num),
      value_str: typeof r.value_str === 'string' ? r.value_str : '',
      is_default: toFlag(r.is_default) ? 1 : 0,
      effective_since: typeof r.effective_since === 'string' && r.effective_since !== '' ? r.effective_since : null,
    });
  }
  return out;
}

// ---------------------------------------------------------------- usulan order (ST-1, DC-2)
export type ProposalState = 'draft' | 'proposed' | 'approved' | 'rejected' | 'expired' | 'unknown';
const PROPOSAL_STATES: readonly string[] = ['draft', 'proposed', 'approved', 'rejected', 'expired'];

export interface ProposalRow {
  proposalId: number;
  warehouseId: number;
  deliveryDate: string;
  cutoffAt: Date | null;
  state: ProposalState;
  lineCount: number;
  linesChanged: number;
  linesExcluded: number;
  qtyProposed: number | null;
  qtyApproved: number | null;
}

export function parseProposals(rows: readonly Raw[]): ProposalRow[] {
  const out: ProposalRow[] = [];
  for (const r of rows) {
    const proposalId = uint(r.proposal_id);
    const warehouseId = uint(r.warehouse_id);
    const deliveryDate = parseChDate(r.delivery_date);
    if (proposalId === null || proposalId === 0 || warehouseId === null || deliveryDate === null) continue;
    const st = typeof r.state === 'string' && PROPOSAL_STATES.includes(r.state) ? (r.state as ProposalState) : 'unknown';
    out.push({
      proposalId,
      warehouseId,
      deliveryDate,
      cutoffAt: parseChDateTime(r.cutoff_at),
      state: st,
      lineCount: toNum(r.line_count),
      linesChanged: toNum(r.lines_changed),
      linesExcluded: toNum(r.lines_excluded),
      qtyProposed: toNumOrNull(r.qty_proposed_total),
      qtyApproved: toNumOrNull(r.qty_approved_total),
    });
  }
  return out;
}

// ---------------------------------------------------------------- near-expiry (ST-2, CM-4)
export type ExpiryDisplay = 'kedaluwarsa' | 'markdown_sekarang' | 'markdown_aktif' | 'pantau';
const EXPIRY_DISPLAYS: readonly string[] = ['kedaluwarsa', 'markdown_sekarang', 'markdown_aktif', 'pantau'];

export interface ExpiryLot {
  alertId: number;
  warehouseId: number;
  lotId: number | null;
  lotName: string;
  productTmplId: number | null;
  productName: string;
  categId: number;
  daysLeft: number;
  qty: number;
  valueCost: number;
  markdownId: number | null;
  markdownPct: number | null;
  markdownPrice: number | null;
  markdownUntil: string | null;
  display: ExpiryDisplay;
}

export function parseExpiryLots(rows: readonly Raw[]): ExpiryLot[] {
  const out: ExpiryLot[] = [];
  for (const r of rows) {
    const alertId = uint(r.alert_id);
    const warehouseId = uint(r.warehouse_id);
    const daysLeft = toNumOrNull(r.days_left);
    if (alertId === null || alertId === 0 || warehouseId === null || daysLeft === null) continue;
    const disp = typeof r.display_status === 'string' && EXPIRY_DISPLAYS.includes(r.display_status) ? (r.display_status as ExpiryDisplay) : 'pantau';
    const mdId = uint(r.markdown_id);
    out.push({
      alertId,
      warehouseId,
      lotId: uint(r.lot_id) || null,
      lotName: cleanText(r.lot_name, 40),
      productTmplId: uint(r.product_tmpl_id) || null,
      productName: cleanText(r.product_name) || 'Produk tanpa nama',
      categId: toNum(r.categ_id),
      daysLeft: Math.trunc(daysLeft),
      qty: toNum(r.qty),
      valueCost: toNum(r.value_cost),
      markdownId: mdId === null || mdId === 0 ? null : mdId,
      markdownPct: toNumOrNull(r.markdown_effective_pct),
      markdownPrice: toNumOrNull(r.markdown_price),
      markdownUntil: parseChDate(r.markdown_date_to),
      display: disp,
    });
  }
  return out;
}

// ---------------------------------------------------------------- stok kritis (ST-3)
export interface CriticalRow {
  warehouseId: number;
  productId: number;
  productTmplId: number | null;
  code: string;
  name: string;
  onHand: number;
  minQty: number;
  avgDaily: number | null;
  cover: number | null;
  isOut: boolean;
  isCritical: boolean;
  openProposalId: number | null;
  snapshotTs: Date | null;
}

export function parseCritical(rows: readonly Raw[]): CriticalRow[] {
  const out: CriticalRow[] = [];
  for (const r of rows) {
    const productId = uint(r.product_id);
    const warehouseId = uint(r.warehouse_id);
    if (productId === null || productId === 0 || warehouseId === null) continue;
    const op = uint(r.open_proposal_id);
    out.push({
      warehouseId,
      productId,
      productTmplId: uint(r.product_tmpl_id) || null,
      code: cleanText(r.default_code, 32),
      name: cleanText(r.product_name) || 'Produk tanpa nama',
      onHand: toNum(r.on_hand_qty),
      minQty: toNum(r.min_qty),
      avgDaily: toNumOrNull(r.avg_daily_sales),
      cover: toNumOrNull(r.days_of_cover),
      isOut: toFlag(r.is_out),
      isCritical: toFlag(r.is_critical),
      openProposalId: op === null || op === 0 ? null : op,
      snapshotTs: parseChDateTime(r.snapshot_ts),
    });
  }
  return out;
}

// ---------------------------------------------------------------- PO DC (DC-5)
export interface PoRow {
  orderId: number;
  supplier: string;
  state: string;
  plannedAt: Date | null;
  value: number;
  lines: number;
  late: boolean;
  fromOrderpoint: boolean;
}

export function parsePurchaseOrders(rows: readonly Raw[]): PoRow[] {
  const out: PoRow[] = [];
  for (const r of rows) {
    const orderId = uint(r.order_id);
    if (orderId === null || orderId === 0) continue;
    const name = cleanText(r.supplier_label, 80);
    const sid = uint(r.supplier_key);
    out.push({
      orderId,
      supplier: name !== '' ? name : sid ? `Supplier #${sid}` : 'Supplier tidak diketahui',
      state: typeof r.state_key === 'string' ? r.state_key : '',
      plannedAt: parseChDateTime(r.planned_at),
      value: toNum(r.value_sum),
      lines: toNum(r.lines_n),
      late: toFlag(r.late_flag),
      fromOrderpoint: toFlag(r.auto_flag),
    });
  }
  return out;
}

// ---------------------------------------------------------------- harga vs pasar (CM-5, CM-6)
export interface PriceRow {
  productTmplId: number | null;
  code: string;
  name: string;
  categId: number;
  ourPrice: number | null;
  marketMedian: number | null;
  sampleN: number;
  competitorsN: number;
  lastSample: string | null;
  diffPct: number | null;
}

export function parsePriceRows(rows: readonly Raw[]): PriceRow[] {
  return rows.map((r) => ({
    productTmplId: uint(r.product_tmpl_id) || null,
    code: cleanText(r.default_code, 32),
    name: cleanText(r.product_name) || 'Produk tanpa nama',
    categId: toNum(r.categ_id),
    ourPrice: toNumOrNull(r.our_price),
    marketMedian: toNumOrNull(r.market_median_30d),
    sampleN: toNum(r.sample_n_30d),
    competitorsN: toNum(r.competitors_n_30d),
    lastSample: parseChDate(r.last_sample_date),
    diffPct: toNumOrNull(r.diff_pct),
  }));
}

// ---------------------------------------------------------------- kampanye (MK-4)
export interface CampaignRow {
  campaignId: number;
  name: string;
  kind: string;
  state: string;
  channels: string[];
  dateFrom: string | null;
  dateTo: string | null;
  delivered: number;
  vouchersIssued: number;
  vouchersUsed: number;
  discountIdr: number;
  budgetIdr: number | null;
}

export function parseCampaigns(rows: readonly Raw[]): CampaignRow[] {
  const out: CampaignRow[] = [];
  for (const r of rows) {
    const id = uint(r.campaign_id);
    if (id === null || id === 0) continue;
    out.push({
      campaignId: id,
      name: cleanText(r.name, 64) || `Kampanye #${id}`,
      kind: typeof r.kind === 'string' ? r.kind : '',
      state: typeof r.state === 'string' ? r.state : '',
      channels: Array.isArray(r.channels) ? r.channels.filter((c): c is string => typeof c === 'string').slice(0, 5) : [],
      dateFrom: parseChDate(r.date_from),
      dateTo: parseChDate(r.date_to),
      delivered: toNum(r.delivered),
      vouchersIssued: toNum(r.vouchers_issued),
      vouchersUsed: toNum(r.vouchers_used),
      discountIdr: toNum(r.discount_idr),
      budgetIdr: toNumOrNull(r.budget_idr),
    });
  }
  return out;
}
