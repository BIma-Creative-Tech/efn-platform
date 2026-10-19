import { createHash } from 'node:crypto';
import type { LoyaltyPolicyCeilings } from '../config/app-config';
import { ApiError, Errors } from '../common/errors/api-error';
import { CompanyCode, isCompanyCode } from './loyalty.service';

/**
 * Kebijakan poin per PT (docs/04 §13.1 no. 1–4, W-29/E-44, L-61/L-62; ADR-020 revisi, ADR-001 §poin). Modul murni
 * (tanpa I/O): parsing body publikasi Odoo dengan skema KETAT, pemeriksaan plafon env, sidik kanonik untuk deteksi
 * `409 policy_conflict`.
 *
 * Body `PUT /v1/pos/loyalty/policies/{company_code}/{version}`:
 *   `{ schema_version: 1, company_code, version, effective_from, approved_by_uid, params: {...}, point_value_idr? }`
 * — `additionalProperties: false` di semua tingkat; semua angka bilangan bulat ≥ 0 (tanpa desimal);
 * `point_value_idr` HANYA untuk `company_code = "EFN"` (kebijakan global, L-62) dan opsional di sana.
 */
export const LOYALTY_POLICY_SCHEMA_VERSION = 1;
/** Kebijakan global (nilai tukar poin seragam, L-49/L-62). */
export const GLOBAL_POLICY_COMPANY: CompanyCode = 'EFN';
export const MAX_POLICY_BODY_BYTES = 8 * 1024;
const INT32_MAX = 2_147_483_647;

export interface LoyaltyPolicyParams {
  earn_idr_per_point: number;
  redeem_min_points: number;
  redeem_max_pct: number;
  max_points_per_order: number;
  daily_max_points_member: number;
  pos_config_hourly_max: number;
  company_hourly_max: number;
  earn_on_money_only: boolean;
}

/** Urutan tetap = urutan kanonik sidik. */
export const POLICY_PARAM_KEYS = [
  'earn_idr_per_point',
  'redeem_min_points',
  'redeem_max_pct',
  'max_points_per_order',
  'daily_max_points_member',
  'pos_config_hourly_max',
  'company_hourly_max',
  'earn_on_money_only',
] as const;

const TOP_KEYS = ['schema_version', 'company_code', 'version', 'effective_from', 'approved_by_uid', 'params', 'point_value_idr'];

export interface LoyaltyPolicyInput {
  companyCode: CompanyCode;
  version: number;
  effectiveFrom: Date;
  approvedByUid: number;
  params: LoyaltyPolicyParams;
  pointValueIdr: number | null;
}

export const PolicyErrors = {
  conflict: (): ApiError => Errors.conflict('policy_conflict', 'Versi kebijakan poin ini sudah ada dengan isi berbeda.'),
  outOfBounds: (): ApiError =>
    ApiError.of(422, 'policy_out_of_bounds', 'Nilai kebijakan poin di luar batas yang diizinkan platform.'),
  /** §13.1 no. 6: tanggal akrual belum ditutup (hari ini/masa depan WIB, atau masih dalam jeda tutup). */
  dateNotClosed: (): ApiError => ApiError.of(422, 'date_not_closed', 'Tanggal akrual belum ditutup.'),
};

function int(v: unknown, min: number, max: number): number {
  if (typeof v !== 'number' || !Number.isSafeInteger(v) || v < min || v > max) throw Errors.invalidRequest();
  return v;
}

function onlyKeys(o: Record<string, unknown>, allowed: readonly string[]): void {
  if (Object.keys(o).some((k) => !allowed.includes(k))) throw Errors.invalidRequest();
}

function record(v: unknown): Record<string, unknown> {
  if (typeof v !== 'object' || v === null || Array.isArray(v)) throw Errors.invalidRequest();
  return v as Record<string, unknown>;
}

/**
 * `effective_from`: ISO 8601 dengan zona (`Z` / `±hh:mm`) atau format Datetime Odoo `YYYY-MM-DD HH:MM:SS` (UTC, tanpa
 * zona — ASUMSI: serialisasi default `fields.Datetime` Odoo). Rentang 2020..2100.
 */
export function parseEffectiveFrom(v: unknown): Date {
  if (typeof v !== 'string' || v.length > 40) throw Errors.invalidRequest();
  let iso: string;
  if (/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(v)) iso = `${v.replace(' ', 'T')}Z`;
  else if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z|[+-]\d{2}:\d{2})$/.test(v)) iso = v;
  else throw Errors.invalidRequest();
  const ms = Date.parse(iso);
  if (Number.isNaN(ms) || ms < Date.UTC(2020, 0, 1) || ms > Date.UTC(2100, 0, 1)) throw Errors.invalidRequest();
  return new Date(ms);
}

/** Validasi bentuk `params` (dipakai juga saat membaca ulang jsonb dari DB — pertahanan berlapis). */
export function parsePolicyParams(v: unknown): LoyaltyPolicyParams {
  const p = record(v);
  onlyKeys(p, POLICY_PARAM_KEYS);
  for (const k of POLICY_PARAM_KEYS) if (!(k in p)) throw Errors.invalidRequest();
  if (typeof p.earn_on_money_only !== 'boolean') throw Errors.invalidRequest();
  return {
    earn_idr_per_point: int(p.earn_idr_per_point, 1, 1_000_000_000),
    redeem_min_points: int(p.redeem_min_points, 1, 1_000_000),
    redeem_max_pct: int(p.redeem_max_pct, 0, 100),
    max_points_per_order: int(p.max_points_per_order, 1, 1_000_000),
    daily_max_points_member: int(p.daily_max_points_member, 0, 1_000_000),
    pos_config_hourly_max: int(p.pos_config_hourly_max, 0, 100_000_000),
    company_hourly_max: int(p.company_hourly_max, 0, 1_000_000_000),
    earn_on_money_only: p.earn_on_money_only,
  };
}

/**
 * Parse body mentah (yang sudah diverifikasi tanda tangannya) + parameter path. Semua penyimpangan bentuk → `400
 * invalid_request` (pesan generik; tidak menyebut field).
 */
export function parseLoyaltyPolicy(raw: Buffer | undefined, pathCompany: string, pathVersion: string): LoyaltyPolicyInput {
  if (!raw || raw.length === 0 || raw.length > MAX_POLICY_BODY_BYTES) throw Errors.invalidRequest();
  if (!isCompanyCode(pathCompany) || !/^[1-9]\d{0,9}$/.test(pathVersion) || Number(pathVersion) > INT32_MAX) {
    throw Errors.invalidRequest();
  }
  let body: unknown;
  try {
    body = JSON.parse(raw.toString('utf8'));
  } catch {
    throw Errors.invalidRequest();
  }
  const o = record(body);
  onlyKeys(o, TOP_KEYS);
  if (o.schema_version !== LOYALTY_POLICY_SCHEMA_VERSION) throw Errors.invalidRequest();
  if (o.company_code !== pathCompany) throw Errors.invalidRequest();
  const version = int(o.version, 1, INT32_MAX);
  if (version !== Number(pathVersion)) throw Errors.invalidRequest();
  const approvedByUid = int(o.approved_by_uid, 1, INT32_MAX);
  const effectiveFrom = parseEffectiveFrom(o.effective_from);
  const params = parsePolicyParams(o.params);
  let pointValueIdr: number | null = null;
  if ('point_value_idr' in o) {
    // L-62: per PT hanya laju & batas; nilai tukar hanya di kebijakan global EFN
    if (pathCompany !== GLOBAL_POLICY_COMPANY) throw Errors.invalidRequest();
    pointValueIdr = o.point_value_idr === null ? null : int(o.point_value_idr, 1, 1_000_000_000);
  }
  return { companyCode: pathCompany, version, effectiveFrom, approvedByUid, params, pointValueIdr };
}

/** Nama field yang melanggar plafon (kosong = dalam batas). Tidak memuat nilai rahasia (kebijakan bukan rahasia). */
export function policyViolations(params: LoyaltyPolicyParams, pointValueIdr: number | null, c: LoyaltyPolicyCeilings): string[] {
  const out: string[] = [];
  /** plafon 0 = tanpa plafon; selain itu nilai 1..plafon (0 = "tanpa batas" ditolak). */
  const capped = (v: number, cap: number): boolean => cap === 0 || (v >= 1 && v <= cap);
  if (params.earn_idr_per_point < c.earnIdrPerPointMin) out.push('earn_idr_per_point');
  if (params.redeem_min_points < c.redeemMinPointsMin) out.push('redeem_min_points');
  if (params.redeem_max_pct > c.redeemMaxPctMax) out.push('redeem_max_pct');
  if (params.max_points_per_order > c.maxPointsPerOrderMax) out.push('max_points_per_order');
  if (!capped(params.daily_max_points_member, c.dailyMaxPointsMemberMax)) out.push('daily_max_points_member');
  if (!capped(params.pos_config_hourly_max, c.posConfigHourlyMax)) out.push('pos_config_hourly_max');
  if (!capped(params.company_hourly_max, c.companyHourlyMax)) out.push('company_hourly_max');
  if (c.earnOnMoneyOnlyRequired && !params.earn_on_money_only) out.push('earn_on_money_only');
  if (pointValueIdr !== null && pointValueIdr > c.pointValueIdrMax) out.push('point_value_idr');
  return out;
}

/**
 * Sidik kanonik isi kebijakan (bukan body mentah: urutan key/spasi tidak relevan). Versi sama + sidik beda →
 * `409 policy_conflict`; sidik sama → replay idempoten (200).
 */
export function policyFingerprint(p: LoyaltyPolicyInput): string {
  const canonical = {
    schema_version: LOYALTY_POLICY_SCHEMA_VERSION,
    company_code: p.companyCode,
    version: p.version,
    effective_from: p.effectiveFrom.toISOString(),
    approved_by_uid: p.approvedByUid,
    params: Object.fromEntries(POLICY_PARAM_KEYS.map((k) => [k, p.params[k]])),
    point_value_idr: p.pointValueIdr,
  };
  return createHash('sha256').update(JSON.stringify(canonical), 'utf8').digest('hex');
}
