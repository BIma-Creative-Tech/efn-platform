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
  /**
   * L-75 (G22 §14.D.3 no. 1, D-22-13): kunci OPSIONAL hanya pada kebijakan global `EFN` — parameter job hangus W-17.
   * Absen = fallback env (`LOYALTY_INACTIVITY_EXPIRY_MONTHS`, `LOYALTY_EXPIRY_NOTICE_DAYS`, `LOYALTY_ACTIVITY_EPOCH`).
   * PT lain yang mengirim kunci ini → 400.
   */
  inactivity_expiry_months?: number;
  expiry_notice_days?: number;
  /** `YYYY-MM-DD` (00:00 WIB). */
  activity_epoch?: string;
}

/** L-75: kunci kebijakan hangus (urutan kanonik sidik, ditambahkan SETELAH kunci G21 hanya bila ada). */
export const EXPIRY_POLICY_KEYS = ['inactivity_expiry_months', 'expiry_notice_days', 'activity_epoch'] as const;
/** L-75: rentang pemberitahuan (hari) yang diizinkan platform. */
export const EXPIRY_NOTICE_DAYS_MIN = 7;
export const EXPIRY_NOTICE_DAYS_MAX = 60;
/** L-75: `activity_epoch` tidak boleh lebih dari 1 tahun ke depan. */
const EPOCH_MAX_AHEAD_MS = 366 * 86_400_000;

/** `YYYY-MM-DD` valid (kalender) → 00:00 WIB; selain itu null. */
export function parseEpochDate(v: string): Date | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(v);
  if (!m) return null;
  const [y, mo, d] = [Number(m[1]), Number(m[2]), Number(m[3])];
  const utc = Date.UTC(y, mo - 1, d);
  const back = new Date(utc);
  if (back.getUTCFullYear() !== y || back.getUTCMonth() !== mo - 1 || back.getUTCDate() !== d || y < 2020 || y > 2100) return null;
  return new Date(utc - 7 * 3600 * 1000);
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

/**
 * Validasi bentuk `params` (dipakai juga saat membaca ulang jsonb dari DB — pertahanan berlapis). L-75: kunci
 * `EXPIRY_POLICY_KEYS` hanya diterima bila `company` = `EFN` (tanpa `company` = ditolak, pola fail-closed).
 * Rentang bentuk di sini longgar (1..120 bulan, 1..365 hari); batas platform ada di `policyViolations` (→ 422).
 */
export function parsePolicyParams(v: unknown, company?: CompanyCode): LoyaltyPolicyParams {
  const p = record(v);
  const global = company === GLOBAL_POLICY_COMPANY;
  onlyKeys(p, global ? [...POLICY_PARAM_KEYS, ...EXPIRY_POLICY_KEYS] : POLICY_PARAM_KEYS);
  for (const k of POLICY_PARAM_KEYS) if (!(k in p)) throw Errors.invalidRequest();
  if (typeof p.earn_on_money_only !== 'boolean') throw Errors.invalidRequest();
  const expiry: Pick<LoyaltyPolicyParams, 'inactivity_expiry_months' | 'expiry_notice_days' | 'activity_epoch'> = {};
  if (global) {
    if (p.inactivity_expiry_months !== undefined) expiry.inactivity_expiry_months = int(p.inactivity_expiry_months, 1, 120);
    if (p.expiry_notice_days !== undefined) expiry.expiry_notice_days = int(p.expiry_notice_days, 1, 365);
    if (p.activity_epoch !== undefined) {
      if (typeof p.activity_epoch !== 'string' || parseEpochDate(p.activity_epoch) === null) throw Errors.invalidRequest();
      expiry.activity_epoch = p.activity_epoch;
    }
  }
  return {
    ...expiry,
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
  const params = parsePolicyParams(o.params, pathCompany);
  let pointValueIdr: number | null = null;
  if ('point_value_idr' in o) {
    // L-62: per PT hanya laju & batas; nilai tukar hanya di kebijakan global EFN
    if (pathCompany !== GLOBAL_POLICY_COMPANY) throw Errors.invalidRequest();
    pointValueIdr = o.point_value_idr === null ? null : int(o.point_value_idr, 1, 1_000_000_000);
  }
  return { companyCode: pathCompany, version, effectiveFrom, approvedByUid, params, pointValueIdr };
}

/** Nama field yang melanggar plafon (kosong = dalam batas). Tidak memuat nilai rahasia (kebijakan bukan rahasia). */
export function policyViolations(
  params: LoyaltyPolicyParams,
  pointValueIdr: number | null,
  c: LoyaltyPolicyCeilings,
  nowMs: number = Date.now(),
): string[] {
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
  // L-75: plafon kebijakan hangus (LOYALTY_POLICY_CEILING_INACTIVITY_MONTHS_MIN/_MAX, default 6..36; notice 7..60)
  const minM = c.inactivityMonthsMin ?? 6;
  const maxM = c.inactivityMonthsMax ?? 36;
  if (params.inactivity_expiry_months !== undefined && (params.inactivity_expiry_months < minM || params.inactivity_expiry_months > maxM)) {
    out.push('inactivity_expiry_months');
  }
  if (params.expiry_notice_days !== undefined && (params.expiry_notice_days < EXPIRY_NOTICE_DAYS_MIN || params.expiry_notice_days > EXPIRY_NOTICE_DAYS_MAX)) {
    out.push('expiry_notice_days');
  }
  if (params.activity_epoch !== undefined) {
    const epoch = parseEpochDate(params.activity_epoch);
    if (epoch === null || epoch.getTime() > nowMs + EPOCH_MAX_AHEAD_MS) out.push('activity_epoch');
  }
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
    // L-75: kunci hangus ikut sidik HANYA bila ada → sidik versi lama (G21) tidak berubah
    params: Object.fromEntries([
      ...POLICY_PARAM_KEYS.map((k) => [k, p.params[k]] as const),
      ...EXPIRY_POLICY_KEYS.filter((k) => p.params[k] !== undefined).map((k) => [k, p.params[k]] as const),
    ]),
    point_value_idr: p.pointValueIdr,
  };
  return createHash('sha256').update(JSON.stringify(canonical), 'utf8').digest('hex');
}
