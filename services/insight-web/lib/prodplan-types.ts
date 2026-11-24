/**
 * Bentuk respons API prodplan yang DIASUMSIKAN (docs/04 §30.6/§30.8). Kontrak hanya memberi nama kolom skema dan ringkasan
 * respons; bentuk JSON final menunggu implementasi `services/prodplan`. Semua parser di bawah defensif: field hilang -> nilai
 * aman, bukan exception. Angka desimal boleh datang sebagai string (numeric(18,4)) atau number.
 */
export type Dec = string | number | null;

export type PlanStatus = 'draft' | 'submitted' | 'approved' | 'posted' | 'cancelled';
export type CalcStatus = 'none' | 'stale' | 'fresh' | 'failed';
export type TransferStatus = 'proposed' | 'approved' | 'rejected' | 'superseded';
export type SuggestionStatus = 'open' | 'accepted' | 'rejected' | 'superseded';
export type OutboxStatus =
  | 'pending'
  | 'sending'
  | 'sent'
  | 'confirmed'
  | 'unknown'
  | 'failed'
  | 'delete_pending'
  | 'deleting'
  | 'deleted'
  | 'delete_failed'
  /** Sesi ESB pemicu berakhir sebelum dokumen dikirim/dihapus (§30.17.9); lanjut lewat `resume-posting`. */
  | 'needs_login';
export type DocKind = 'simple_transfer' | 'goods_transfer_request' | 'simple_manufacturing';
export type DemandSource = 'pr' | 'manual' | 'suggestion';
export type PostPhase = 'transfers' | 'production';

export interface PpMeta {
  esb_env: 'staging' | 'production' | string;
  write_enabled: boolean;
  mfg_mode: string;
  brands: PpMetaBrand[];
}
export interface PpMetaBrand {
  esb_brand: string;
  company_code: string;
  ck: { branch_id: number; production_location_id: number; branch_name: string | null; production_location_name: string | null }[];
  source_locations: unknown[];
}

export type PostingState = 'none' | 'in_progress' | 'needs_attention' | 'complete';
const POSTING_STATES: readonly string[] = ['none', 'in_progress', 'needs_attention', 'complete'];

export interface PackRule {
  esb_brand?: string;
  product_code: string;
  product_name?: string | null;
  base_uom: string;
  pack_qty: Dec;
  opened_shelf_life_hours: number | null;
  notes: string | null;
  active: boolean;
  row_version: number;
}

export interface PlanSummary {
  id: string;
  plan_no: string;
  esb_brand?: string;
  company_code?: string;
  status: PlanStatus;
  calc_status: CalcStatus;
  production_date: string;
  branch_id?: number;
  production_location_id?: number;
  /** Nama cabang/lokasi (opsional; tampilan memakai nama, ID hanya cadangan). */
  branch_name?: string | null;
  production_location_name?: string | null;
  /** Ringkasan posting (opsional): none|in_progress|needs_attention|complete. */
  posting_state?: PostingState;
  created_by_name?: string | null;
  row_version?: number;
}

export interface DemandLine {
  product_detail_id: number;
  product_code?: string;
  product_name?: string;
  bom_id: number;
  uom_name?: string;
  qty: Dec;
  source: DemandSource;
  pr_nums?: string[];
}
export interface UnmappedLine {
  pr_num: string;
  product_code: string;
  product_name?: string | null;
  qty: Dec;
  reason: string;
}
export interface ProductionLine {
  level: number;
  bom_id: number;
  product_detail_id: number;
  product_code: string;
  product_name: string;
  uom_name: string;
  qty: Dec;
  is_demand: boolean;
}
export interface Requirement {
  product_detail_id: number;
  product_code: string;
  product_name: string;
  uom_name: string;
  need_qty: Dec;
  pack_qty: Dec;
  packs: number | null;
  leftover_qty: Dec;
  need_eff_qty: Dec;
  stock_prod_qty: Dec;
  transfer_qty: Dec;
  shortage_qty: Dec;
  flags: string[];
}
export interface Suggestion {
  id: number;
  material_detail_id: number;
  material_code?: string;
  material_name?: string;
  bom_id: number;
  product_detail_id: number;
  product_code?: string;
  product_name?: string;
  extra_units: number;
  uses_qty: Dec;
  leftover_qty: Dec;
  status: SuggestionStatus;
}
export interface TransferLine {
  product_detail_id: number;
  product_code: string;
  product_name?: string;
  uom_name: string;
  qty: Dec;
}
export interface Transfer {
  id: number;
  doc_type: 'simple_transfer' | 'goods_transfer_request';
  origin_branch_id: number;
  origin_location_id: number;
  dest_branch_id: number;
  dest_location_id: number;
  origin_branch_name?: string | null;
  origin_location_name?: string | null;
  dest_branch_name?: string | null;
  dest_location_name?: string | null;
  status: TransferStatus;
  decided_by_name?: string | null;
  decided_at?: string | null;
  reject_reason?: string | null;
  lines: TransferLine[];
}
export interface OutboxItem {
  id: string;
  seq: number;
  phase: PostPhase;
  doc_kind: DocKind;
  status: OutboxStatus;
  esb_doc_number: string | null;
  esb_status_id?: number | null;
  attempts?: number;
  last_error_code: string | null;
  sent_at?: string | null;
  confirmed_at?: string | null;
}

export interface Plan extends PlanSummary {
  esb_env?: string;
  calculated_at?: string | null;
  /** Waktu stok ESB diambil untuk hitungan terakhir (opsional). */
  stock_fetched_at?: string | null;
  notes?: string | null;
  created_by_sub?: number;
  created_at?: string | null;
  submitted_by_sub?: number | null;
  submitted_by_name?: string | null;
  submitted_at?: string | null;
  approved_by_sub?: number | null;
  approved_by_name?: string | null;
  approved_at?: string | null;
  posted_by_name?: string | null;
  posted_at?: string | null;
  cancelled_at?: string | null;
  cancel_reason?: string | null;
  row_version: number;
  demand: DemandLine[];
  unmapped: UnmappedLine[];
  production_lines: ProductionLine[];
  requirements: Requirement[];
  suggestions: Suggestion[];
  transfers: Transfer[];
  outbox: OutboxItem[];
  limitations: string[];
}

export interface PreviewDoc {
  doc_kind: DocKind;
  origin?: unknown;
  dest?: unknown;
  lines?: unknown[];
  /** Dokumen produksi (opsional; bentuk belum pasti): produk hasil + bahan di level dokumen. */
  product_code?: string;
  product_name?: string;
  uom_name?: string;
  qty?: Dec;
  level?: number;
  materials?: unknown[];
}
export interface StockImpact {
  location_id: number;
  location_name?: string | null;
  product_code: string;
  product_name?: string | null;
  qty_out: Dec;
  qty_in: Dec;
}
export interface PostPreview {
  phase: PostPhase;
  docs: PreviewDoc[];
  stock_impact: StockImpact[];
  digest: string;
}

export interface PpJob {
  status: 'queued' | 'running' | 'done' | 'failed';
  kind?: string;
  error_code?: string | null;
  result?: unknown;
}

/** Ringkasan hasil job yang aman dikirim ke browser (`plan_status`, `changed`, jalur siklus BOM). */
export interface JobInfo {
  plan_status: 'approved' | 'draft' | null;
  changed: boolean | null;
  path: string[];
}

export interface BomItem {
  bom_id: number;
  bom_name: string;
  bom_code: string;
  product_detail_id: number;
  product_code: string;
  product_name: string;
  uom_name: string;
}

// ---------------------------------------------------------------- parser defensif

const isObj = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v);
const arr = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);
const str = (v: unknown, d = ''): string => (typeof v === 'string' ? v : d);
const num = (v: unknown, d = 0): number => (typeof v === 'number' && Number.isFinite(v) ? v : d);
const numOrNull = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);
const strOrNull = (v: unknown): string | null => (typeof v === 'string' ? v : null);
function dec(v: unknown): Dec {
  if (typeof v === 'string') return v;
  if (typeof v === 'number' && Number.isFinite(v)) return v;
  return null;
}
const bool = (v: unknown): boolean => v === true;
const strs = (v: unknown): string[] => arr(v).filter((x): x is string => typeof x === 'string');
const objs = (v: unknown): Record<string, unknown>[] => arr(v).filter(isObj);

const PLAN_STATUSES: readonly string[] = ['draft', 'submitted', 'approved', 'posted', 'cancelled'];
const CALC_STATUSES: readonly string[] = ['none', 'stale', 'fresh', 'failed'];

export function parseMeta(raw: unknown): PpMeta | null {
  if (!isObj(raw)) return null;
  return {
    esb_env: str(raw.esb_env, 'unknown'),
    write_enabled: bool(raw.write_enabled),
    mfg_mode: str(raw.mfg_mode),
    brands: objs(raw.brands).map((b) => ({
      esb_brand: str(b.esb_brand),
      company_code: str(b.company_code),
      ck: objs(b.ck).map((c) => ({
        branch_id: num(c.branch_id),
        production_location_id: num(c.production_location_id),
        branch_name: strOrNull(c.branch_name),
        production_location_name: strOrNull(c.production_location_name),
      })),
      source_locations: arr(b.source_locations),
    })),
  };
}

export function parsePackRule(o: Record<string, unknown>): PackRule {
  return {
    esb_brand: str(o.esb_brand) || undefined,
    product_code: str(o.product_code),
    product_name: strOrNull(o.product_name),
    base_uom: str(o.base_uom),
    pack_qty: dec(o.pack_qty),
    opened_shelf_life_hours: numOrNull(o.opened_shelf_life_hours),
    notes: strOrNull(o.notes),
    active: o.active !== false,
    row_version: num(o.row_version, 1),
  };
}

export function parsePackRules(raw: unknown): PackRule[] {
  return isObj(raw) ? objs(raw.items).map(parsePackRule) : [];
}

export function parsePlanSummary(o: Record<string, unknown>): PlanSummary {
  const status = str(o.status);
  const calc = str(o.calc_status);
  return {
    id: str(o.id),
    plan_no: str(o.plan_no),
    esb_brand: str(o.esb_brand) || undefined,
    company_code: str(o.company_code) || undefined,
    status: (PLAN_STATUSES.includes(status) ? status : 'draft') as PlanStatus,
    calc_status: (CALC_STATUSES.includes(calc) ? calc : 'none') as CalcStatus,
    production_date: str(o.production_date),
    branch_id: numOrNull(o.branch_id) ?? undefined,
    production_location_id: numOrNull(o.production_location_id) ?? undefined,
    branch_name: strOrNull(o.branch_name),
    production_location_name: strOrNull(o.production_location_name),
    posting_state: POSTING_STATES.includes(str(o.posting_state)) ? (str(o.posting_state) as PostingState) : undefined,
    created_by_name: strOrNull(o.created_by_name),
    row_version: numOrNull(o.row_version) ?? undefined,
  };
}

export function parsePlanList(raw: unknown): { items: PlanSummary[]; count: number } {
  if (!isObj(raw)) return { items: [], count: 0 };
  const items = objs(raw.items).map(parsePlanSummary);
  return { items, count: num(raw.count, items.length) };
}

export function parsePlan(raw: unknown): Plan | null {
  if (!isObj(raw)) return null;
  const base = parsePlanSummary(raw);
  if (base.id === '') return null;
  return {
    ...base,
    esb_env: str(raw.esb_env) || undefined,
    calculated_at: strOrNull(raw.calculated_at),
    stock_fetched_at: strOrNull(raw.stock_fetched_at),
    notes: strOrNull(raw.notes),
    created_by_sub: numOrNull(raw.created_by_sub) ?? undefined,
    created_at: strOrNull(raw.created_at),
    submitted_by_sub: numOrNull(raw.submitted_by_sub),
    submitted_by_name: strOrNull(raw.submitted_by_name),
    submitted_at: strOrNull(raw.submitted_at),
    approved_by_sub: numOrNull(raw.approved_by_sub),
    approved_by_name: strOrNull(raw.approved_by_name),
    approved_at: strOrNull(raw.approved_at),
    posted_by_name: strOrNull(raw.posted_by_name),
    posted_at: strOrNull(raw.posted_at),
    cancelled_at: strOrNull(raw.cancelled_at),
    cancel_reason: strOrNull(raw.cancel_reason),
    row_version: num(raw.row_version, 1),
    demand: objs(raw.demand).map((d) => ({
      product_detail_id: num(d.product_detail_id),
      product_code: str(d.product_code) || undefined,
      product_name: str(d.product_name) || undefined,
      bom_id: num(d.bom_id),
      uom_name: str(d.uom_name) || undefined,
      qty: dec(d.qty),
      source: (['pr', 'manual', 'suggestion'].includes(str(d.source)) ? str(d.source) : 'manual') as DemandSource,
      pr_nums: strs(d.pr_nums),
    })),
    unmapped: objs(raw.unmapped).map((u) => ({
      pr_num: str(u.pr_num),
      product_code: str(u.product_code),
      product_name: strOrNull(u.product_name),
      qty: dec(u.qty),
      reason: str(u.reason),
    })),
    production_lines: objs(raw.production_lines).map((l) => ({
      level: num(l.level, 1),
      bom_id: num(l.bom_id),
      product_detail_id: num(l.product_detail_id),
      product_code: str(l.product_code),
      product_name: str(l.product_name),
      uom_name: str(l.uom_name),
      qty: dec(l.qty),
      is_demand: bool(l.is_demand),
    })),
    requirements: objs(raw.requirements).map((r) => ({
      product_detail_id: num(r.product_detail_id),
      product_code: str(r.product_code),
      product_name: str(r.product_name),
      uom_name: str(r.uom_name),
      need_qty: dec(r.need_qty),
      pack_qty: dec(r.pack_qty),
      packs: numOrNull(r.packs),
      leftover_qty: dec(r.leftover_qty),
      need_eff_qty: dec(r.need_eff_qty),
      stock_prod_qty: dec(r.stock_prod_qty),
      transfer_qty: dec(r.transfer_qty),
      shortage_qty: dec(r.shortage_qty),
      flags: strs(r.flags),
    })),
    suggestions: objs(raw.suggestions).map((s) => ({
      id: num(s.id),
      material_detail_id: num(s.material_detail_id),
      material_code: str(s.material_code) || undefined,
      material_name: str(s.material_name) || undefined,
      bom_id: num(s.bom_id),
      product_detail_id: num(s.product_detail_id),
      product_code: str(s.product_code) || undefined,
      product_name: str(s.product_name) || undefined,
      extra_units: num(s.extra_units),
      uses_qty: dec(s.uses_qty),
      leftover_qty: dec(s.leftover_qty),
      status: (['open', 'accepted', 'rejected', 'superseded'].includes(str(s.status)) ? str(s.status) : 'open') as SuggestionStatus,
    })),
    transfers: objs(raw.transfers).map((t) => ({
      id: num(t.id),
      doc_type: (str(t.doc_type) === 'goods_transfer_request' ? 'goods_transfer_request' : 'simple_transfer') as Transfer['doc_type'],
      origin_branch_id: num(t.origin_branch_id),
      origin_location_id: num(t.origin_location_id),
      dest_branch_id: num(t.dest_branch_id),
      dest_location_id: num(t.dest_location_id),
      origin_branch_name: strOrNull(t.origin_branch_name),
      origin_location_name: strOrNull(t.origin_location_name),
      dest_branch_name: strOrNull(t.dest_branch_name),
      dest_location_name: strOrNull(t.dest_location_name),
      status: (['proposed', 'approved', 'rejected', 'superseded'].includes(str(t.status)) ? str(t.status) : 'proposed') as TransferStatus,
      decided_by_name: strOrNull(t.decided_by_name),
      decided_at: strOrNull(t.decided_at),
      reject_reason: strOrNull(t.reject_reason),
      lines: objs(t.lines).map((l) => ({
        product_detail_id: num(l.product_detail_id),
        product_code: str(l.product_code),
        product_name: str(l.product_name) || undefined,
        uom_name: str(l.uom_name),
        qty: dec(l.qty),
      })),
    })),
    outbox: objs(raw.outbox).map(parseOutbox),
    limitations: strs(raw.limitations),
  };
}

export function parseOutbox(o: Record<string, unknown>): OutboxItem {
  return {
    id: str(o.id),
    seq: num(o.seq),
    phase: str(o.phase) === 'production' ? 'production' : 'transfers',
    doc_kind: (['simple_transfer', 'goods_transfer_request', 'simple_manufacturing'].includes(str(o.doc_kind)) ? str(o.doc_kind) : 'simple_transfer') as DocKind,
    status: str(o.status, 'pending') as OutboxStatus,
    esb_doc_number: strOrNull(o.esb_doc_number),
    esb_status_id: numOrNull(o.esb_status_id),
    attempts: numOrNull(o.attempts) ?? undefined,
    last_error_code: strOrNull(o.last_error_code),
    sent_at: strOrNull(o.sent_at),
    confirmed_at: strOrNull(o.confirmed_at),
  };
}

export function parsePreview(raw: unknown): PostPreview | null {
  if (!isObj(raw)) return null;
  const digest = str(raw.digest);
  if (!/^[0-9a-f]{64}$/.test(digest)) return null;
  return {
    phase: str(raw.phase) === 'production' ? 'production' : 'transfers',
    docs: objs(raw.docs).map((d) => ({
      doc_kind: str(d.doc_kind) as DocKind,
      origin: d.origin,
      dest: d.dest,
      lines: arr(d.lines),
      // Field produksi hanya ditambahkan bila ada (bentuk dokumen produksi belum pasti).
      ...(str(d.product_code) ? { product_code: str(d.product_code) } : {}),
      ...(str(d.product_name) ? { product_name: str(d.product_name) } : {}),
      ...(str(d.uom_name) ? { uom_name: str(d.uom_name) } : {}),
      ...(dec(d.qty ?? d.manufacturing_qty) !== null ? { qty: dec(d.qty ?? d.manufacturing_qty) } : {}),
      ...(numOrNull(d.level) !== null ? { level: num(d.level) } : {}),
      ...(Array.isArray(d.materials) ? { materials: arr(d.materials) } : {}),
    })),
    stock_impact: objs(raw.stock_impact).map((s) => ({
      location_id: num(s.location_id),
      ...(str(s.location_name) ? { location_name: str(s.location_name) } : {}),
      ...(str(s.product_name) ? { product_name: str(s.product_name) } : {}),
      product_code: str(s.product_code),
      qty_out: dec(s.qty_out),
      qty_in: dec(s.qty_in),
    })),
    digest,
  };
}

/** `GET /v1/boms` -> `{items:[...]}`; baris tanpa id sah dibuang, maks 50. */
export function parseBoms(raw: unknown): BomItem[] {
  if (!isObj(raw)) return [];
  return objs(raw.items)
    .map((b) => ({
      bom_id: num(b.bom_id),
      bom_name: str(b.bom_name),
      bom_code: str(b.bom_code),
      product_detail_id: num(b.product_detail_id),
      product_code: str(b.product_code),
      product_name: str(b.product_name),
      uom_name: str(b.uom_name),
    }))
    .filter((b) => b.bom_id > 0 && b.product_detail_id > 0)
    .slice(0, 50);
}

const SAFE_PATH_ITEM = /^[A-Za-z0-9._ -]{1,40}$/;

/** `job.result` -> ringkasan aman: plan_status (approved|draft), changed (bool), path siklus BOM (maks 12 kode aman). */
export function parseJobInfo(result: unknown): JobInfo {
  const r = isObj(result) ? result : {};
  const ps = str(r.plan_status);
  return {
    plan_status: ps === 'approved' || ps === 'draft' ? ps : null,
    changed: typeof r.changed === 'boolean' ? r.changed : null,
    path: strs(r.path).filter((x) => SAFE_PATH_ITEM.test(x)).slice(0, 12),
  };
}

export function parseJob(raw: unknown): PpJob | null {
  if (!isObj(raw)) return null;
  const status = str(raw.status);
  if (!['queued', 'running', 'done', 'failed'].includes(status)) return null;
  return { status: status as PpJob['status'], kind: str(raw.kind) || undefined, error_code: strOrNull(raw.error_code), result: raw.result };
}
