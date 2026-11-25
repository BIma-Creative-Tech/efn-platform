/**
 * Daftar aksi mutasi Production Order (docs/04 §30.8). Murni (tanpa I/O): setiap aksi mendefinisikan peran yang diizinkan,
 * validasi input browser, dan pemetaan ke (method, path, body) API prodplan. Route handler `POST /api/prodplan/<aksi>`
 * HANYA melayani aksi di sini (tidak ada penerusan path bebas). Identitas aktor tidak pernah berasal dari input browser:
 * ia dibuat dari sesi (lib/prodplan-token.ts). Validasi di sini = lapis pertama; prodplan tetap sumber validasi.
 */
import { isIsoDate } from './prodplan-format';
import type { PpCaps } from './prodplan-access';

/** P = pp_planner, W = pp_warehouse, R = salah satu (§30.8). */
export type ActionRole = 'P' | 'W' | 'R';

export type BuildResult =
  | { ok: true; method: 'POST' | 'PUT'; path: string; body: Record<string, unknown> }
  | { ok: false; field: string };

type Input = Record<string, unknown>;

export interface ActionSpec {
  role: ActionRole;
  build: (input: Input) => BuildResult;
}

export const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const BRAND_RE = /^[a-z]{2,8}$/;
const PRODUCT_CODE_RE = /^[A-Za-z0-9._-]{1,40}$/;
const DIGEST_RE = /^[0-9a-f]{64}$/;
export const MAX_DEMAND_LINES = 500;
const MAX_INT = 2_147_483_647;

const bad = (field: string): BuildResult => ({ ok: false, field });
const isObj = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v);

export function roleAllowed(role: ActionRole, caps: PpCaps): boolean {
  if (role === 'P') return caps.planner;
  if (role === 'W') return caps.warehouse;
  return caps.planner || caps.warehouse;
}

// ---------------------------------------------------------------- validator skalar (null = tidak sah)

export function parseUuid(v: unknown): string | null {
  return typeof v === 'string' && UUID_RE.test(v) ? v : null;
}

/** Bilangan bulat positif <= 2^31-1 (id, row_version). Terima number atau string digit. */
export function parsePosInt(v: unknown): number | null {
  const n = typeof v === 'string' && /^[0-9]{1,10}$/.test(v) ? Number(v) : v;
  return typeof n === 'number' && Number.isSafeInteger(n) && n >= 1 && n <= MAX_INT ? n : null;
}

/** Kuantitas > 0, maks 4 desimal, < 1e12. Terima number atau string desimal titik ("12.5"); hasil number. */
export function parseQty(v: unknown): number | null {
  let n: number;
  if (typeof v === 'number') {
    if (!Number.isFinite(v)) return null;
    if (Math.abs(v * 10_000 - Math.round(v * 10_000)) > 1e-6) return null;
    n = v;
  } else if (typeof v === 'string' && /^[0-9]{1,12}(\.[0-9]{1,4})?$/.test(v.trim())) {
    n = Number(v.trim());
  } else {
    return null;
  }
  return n > 0 && n < 1e12 ? n : null;
}

/** Teks bebas: dipangkas, 1..max karakter (tanpa karakter kontrol). */
export function parseText(v: unknown, max: number, required: boolean): string | null | undefined {
  if (v === undefined || v === null || v === '') return required ? null : undefined;
  if (typeof v !== 'string') return null;
  const t = v.replace(/\p{Cc}/gu, ' ').trim();
  if (t === '') return required ? null : undefined;
  return Array.from(t).length <= max ? t : null;
}

export const BOM_Q_MIN = 2;
export const BOM_Q_MAX = 64;
export const BOM_LIMIT = 20;

/** Query pencarian BOM: brand huruf kecil, `q` dipangkas 2..64 karakter tanpa karakter kontrol. Selain itu null. */
export function parseBomQuery(brand: unknown, q: unknown): { brand: string; q: string } | null {
  if (typeof brand !== 'string' || !BRAND_RE.test(brand)) return null;
  if (typeof q !== 'string') return null;
  const t = q.replace(/\p{Cc}/gu, ' ').replace(/\s+/g, ' ').trim();
  const n = Array.from(t).length;
  return n >= BOM_Q_MIN && n <= BOM_Q_MAX ? { brand, q: t } : null;
}

const planPath =(id: string, tail: string): string => `/v1/plans/${id}${tail}`;

function withVersion(input: Input, tail: string, extra: (b: Record<string, unknown>) => string | null = () => null): BuildResult {
  const id = parseUuid(input.id);
  if (!id) return bad('id');
  const rv = parsePosInt(input.row_version);
  if (!rv) return bad('row_version');
  const body: Record<string, unknown> = { row_version: rv };
  const badField = extra(body);
  if (badField) return bad(badField);
  return { ok: true, method: 'POST', path: planPath(id, tail), body };
}

function reasonField(input: Input, required: boolean) {
  return (b: Record<string, unknown>): string | null => {
    const r = parseText(input.reason, 500, required);
    if (r === null) return 'reason';
    if (r !== undefined) b.reason = r;
    return null;
  };
}

// ---------------------------------------------------------------- aksi

function buildDemand(input: Input): BuildResult {
  const id = parseUuid(input.id);
  if (!id) return bad('id');
  const rv = parsePosInt(input.row_version);
  if (!rv) return bad('row_version');
  if (!Array.isArray(input.lines) || input.lines.length > MAX_DEMAND_LINES) return bad('lines');
  const seen = new Set<string>();
  const lines: Record<string, unknown>[] = [];
  for (const raw of input.lines as unknown[]) {
    if (!isObj(raw)) return bad('lines');
    const pd = parsePosInt(raw.product_detail_id);
    const bom = parsePosInt(raw.bom_id);
    const qty = parseQty(raw.qty);
    // 'suggestion' dikelola server lewat terima usulan; tidak boleh dikirim dari editor.
    const source = raw.source === 'pr' ? 'pr' : raw.source === 'manual' ? 'manual' : null;
    if (!pd || !bom || !qty || !source) return bad('lines');
    const key = `${pd}:${source}`;
    if (seen.has(key)) return bad('lines');
    seen.add(key);
    const line: Record<string, unknown> = { product_detail_id: pd, bom_id: bom, qty, source };
    if (source === 'pr' && Array.isArray(raw.pr_nums)) {
      const nums = (raw.pr_nums as unknown[]).filter((x): x is string => typeof x === 'string' && /^[A-Za-z0-9 ._/-]{1,40}$/.test(x)).slice(0, 50); // prodplan menolak > 50 nomor PR per baris (service.put_demand)
      if (nums.length > 0) line.pr_nums = nums;
    }
    lines.push(line);
  }
  return { ok: true, method: 'PUT', path: planPath(id, '/demand'), body: { row_version: rv, lines } };
}

function buildImport(input: Input): BuildResult {
  const id = parseUuid(input.id);
  if (!id) return bad('id');
  const from = input.required_date_from;
  const to = input.required_date_to;
  if (typeof from !== 'string' || !isIsoDate(from)) return bad('required_date_from');
  if (typeof to !== 'string' || !isIsoDate(to)) return bad('required_date_to');
  if (from > to) return bad('required_date_to');
  return { ok: true, method: 'POST', path: planPath(id, '/import-pr'), body: { required_date_from: from, required_date_to: to } };
}

function buildSuggestion(input: Input, verb: 'accept' | 'reject'): BuildResult {
  const sid = parsePosInt(input.sid);
  if (!sid) return bad('sid');
  const r = withVersion(input, '');
  if (!r.ok) return r;
  return { ...r, path: `${r.path}/suggestions/${sid}/${verb}` };
}

function buildTransfer(input: Input, verb: 'approve' | 'reject'): BuildResult {
  const tid = parsePosInt(input.tid);
  if (!tid) return bad('tid');
  const r = withVersion(input, '', reasonField(input, verb === 'reject'));
  if (!r.ok) return r;
  return { ...r, path: `${r.path}/transfers/${tid}/${verb}` };
}

function buildPost(input: Input): BuildResult {
  const phase = input.phase;
  if (phase !== 'transfers' && phase !== 'production') return bad('phase');
  const digest = input.digest;
  if (typeof digest !== 'string' || !DIGEST_RE.test(digest)) return bad('digest');
  return withVersion(input, '/post', (b) => {
    b.phase = phase;
    b.digest = digest;
    b.accept_shortage = input.accept_shortage === true;
    return null;
  });
}

function buildPackRule(input: Input): BuildResult {
  const brand = input.brand;
  if (typeof brand !== 'string' || !BRAND_RE.test(brand)) return bad('brand');
  const code = input.product_code;
  if (typeof code !== 'string' || !PRODUCT_CODE_RE.test(code)) return bad('product_code');
  const uom = parseText(input.base_uom, 20, true);
  if (!uom) return bad('base_uom');
  const qty = parseQty(input.pack_qty);
  if (!qty) return bad('pack_qty');
  const body: Record<string, unknown> = { base_uom: uom, pack_qty: qty, active: input.active !== false };
  const hoursRaw = input.opened_shelf_life_hours;
  if (hoursRaw !== undefined && hoursRaw !== null && hoursRaw !== '') {
    const h = parsePosInt(hoursRaw);
    if (!h || h > 720) return bad('opened_shelf_life_hours');
    body.opened_shelf_life_hours = h;
  }
  const notes = parseText(input.notes, 500, false);
  if (notes === null) return bad('notes');
  if (notes !== undefined) body.notes = notes;
  if (input.row_version !== undefined && input.row_version !== null && input.row_version !== '') {
    const rv = parsePosInt(input.row_version);
    if (!rv) return bad('row_version');
    body.row_version = rv;
  }
  return { ok: true, method: 'PUT', path: `/v1/pack-rules/${encodeURIComponent(brand)}/${encodeURIComponent(code)}`, body };
}

function buildCreate(input: Input): BuildResult {
  const brand = input.brand;
  if (typeof brand !== 'string' || !BRAND_RE.test(brand)) return bad('brand');
  const branch = parsePosInt(input.branch_id);
  if (!branch) return bad('branch_id');
  const loc = parsePosInt(input.production_location_id);
  if (!loc) return bad('production_location_id');
  const date = input.production_date;
  if (typeof date !== 'string' || !isIsoDate(date)) return bad('production_date');
  const notes = parseText(input.notes, 1000, false);
  if (notes === null) return bad('notes');
  const body: Record<string, unknown> = { brand, branch_id: branch, production_location_id: loc, production_date: date };
  if (notes !== undefined) body.notes = notes;
  return { ok: true, method: 'POST', path: '/v1/plans', body };
}

export const ACTIONS: Readonly<Record<string, ActionSpec>> = {
  'plan-create': { role: 'P', build: buildCreate },
  'plan-demand': { role: 'P', build: buildDemand },
  'plan-import-pr': { role: 'P', build: buildImport },
  // R: draf = planner; rencana `approved` boleh planner ATAU gudang (segarkan stok). Server menegakkan per status.
  'plan-calculate': { role: 'R', build: (i) => withVersion(i, '/calculate') },
  'suggestion-accept': { role: 'P', build: (i) => buildSuggestion(i, 'accept') },
  'suggestion-reject': { role: 'P', build: (i) => buildSuggestion(i, 'reject') },
  'plan-submit': { role: 'P', build: (i) => withVersion(i, '/submit') },
  'transfer-approve': { role: 'W', build: (i) => buildTransfer(i, 'approve') },
  'transfer-reject': { role: 'W', build: (i) => buildTransfer(i, 'reject') },
  'plan-approve': { role: 'W', build: (i) => withVersion(i, '/approve') },
  'plan-return': { role: 'R', build: (i) => withVersion(i, '/return-to-draft', reasonField(i, true)) },
  'plan-post': { role: 'R', build: buildPost },
  'plan-cancel': { role: 'P', build: (i) => withVersion(i, '/cancel', reasonField(i, true)) },
  // §30.17.9: lanjutkan posting yang berhenti (`needs_login`) dengan token pemanggil; hak dokumen/cabang dicek server.
  'plan-resume-posting': { role: 'R', build: (i) => withVersion(i, '/resume-posting') },
  'outbox-resend': {
    role: 'P',
    build: (i) => {
      const oid = parseUuid(i.oid);
      return oid ? { ok: true, method: 'POST', path: `/v1/outbox/${oid}/resend`, body: {} } : bad('oid');
    },
  },
  'pack-rule-put': { role: 'P', build: buildPackRule },
};

export function getAction(name: string): ActionSpec | null {
  return Object.prototype.hasOwnProperty.call(ACTIONS, name) ? ACTIONS[name] : null;
}
