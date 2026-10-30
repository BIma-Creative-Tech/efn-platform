import { createHash } from 'node:crypto';
import type { MgmPolicyCeilings } from '../../config/app-config';
import { ApiError, Errors } from '../../common/errors/api-error';
import { parseEffectiveFrom } from '../loyalty-policy';
import { CompanyCode, isCompanyCode } from '../loyalty.service';

/**
 * Kebijakan MGM per PT (docs/04 §20.1, K25-10, ADR-009). Modul murni (tanpa I/O): parsing body publikasi Odoo dengan
 * skema KETAT, plafon BFF, sidik kanonik (409 `policy_conflict`), dan rumus reward per level (§20.3).
 *
 * Body `PUT /v1/pos/loyalty/mgm-policies/{company_code}/{version}`:
 *   `{ schema_version: 1, company_code, version, effective_from, approved_by_uid, params: {...} }`
 * — `additionalProperties: false` di semua tingkat; semua angka bilangan bulat.
 */
export const MGM_POLICY_SCHEMA_VERSION = 1;
export const MAX_MGM_POLICY_BODY_BYTES = 4 * 1024;
const INT32_MAX = 2_147_483_647;

/** Batas struktural platform (sama dengan Odoo `efn_mgm_policy`; kedalaman tak terbatas tidak pernah diterima). */
export const MGM_DEPTH_MIN = 2;
export const MGM_DEPTH_MAX = 5;
export const MGM_HOLD_DAYS_MAX = 60;
export const MGM_RELATION_MONTHS_MAX = 36;

export interface MgmPolicyParams {
  level1_pct_bp: number;
  level2_pct_bp: number;
  flat_points_level3: number;
  max_depth: number;
  cap_points_per_referrer_month: number;
  min_order_idr: number;
  settle_hold_days: number;
  relation_months: number;
}

/** Urutan tetap = urutan kanonik sidik. */
export const MGM_PARAM_KEYS = [
  'level1_pct_bp',
  'level2_pct_bp',
  'flat_points_level3',
  'max_depth',
  'cap_points_per_referrer_month',
  'min_order_idr',
  'settle_hold_days',
  'relation_months',
] as const;

const TOP_KEYS = ['schema_version', 'company_code', 'version', 'effective_from', 'approved_by_uid', 'params'];

export interface MgmPolicyInput {
  companyCode: CompanyCode;
  version: number;
  effectiveFrom: Date;
  approvedByUid: number;
  params: MgmPolicyParams;
}

export const MgmPolicyErrors = {
  conflict: (): ApiError => Errors.conflict('policy_conflict', 'Versi kebijakan MGM ini sudah ada dengan isi berbeda.'),
  outOfBounds: (): ApiError => ApiError.of(422, 'policy_out_of_bounds', 'Nilai kebijakan MGM di luar batas yang diizinkan platform.'),
};

function int(v: unknown, min: number, max: number): number {
  if (typeof v !== 'number' || !Number.isSafeInteger(v) || v < min || v > max) throw Errors.invalidRequest();
  return v;
}

function record(v: unknown): Record<string, unknown> {
  if (typeof v !== 'object' || v === null || Array.isArray(v)) throw Errors.invalidRequest();
  return v as Record<string, unknown>;
}

function onlyKeys(o: Record<string, unknown>, allowed: readonly string[]): void {
  if (Object.keys(o).some((k) => !allowed.includes(k))) throw Errors.invalidRequest();
}

/** Bentuk `params` (dipakai juga saat membaca ulang jsonb dari DB). Rentang longgar; batas platform di `mgmPolicyViolations`. */
export function parseMgmParams(v: unknown): MgmPolicyParams {
  const p = record(v);
  onlyKeys(p, MGM_PARAM_KEYS);
  for (const k of MGM_PARAM_KEYS) if (!(k in p)) throw Errors.invalidRequest();
  return {
    level1_pct_bp: int(p.level1_pct_bp, 0, 10_000),
    level2_pct_bp: int(p.level2_pct_bp, 0, 10_000),
    flat_points_level3: int(p.flat_points_level3, 0, 1_000_000),
    max_depth: int(p.max_depth, 1, 100),
    cap_points_per_referrer_month: int(p.cap_points_per_referrer_month, 0, 1_000_000_000),
    min_order_idr: int(p.min_order_idr, 0, 1_000_000_000_000),
    settle_hold_days: int(p.settle_hold_days, 0, 3650),
    relation_months: int(p.relation_months, 0, 1200),
  };
}

/** Parse body mentah (sudah diverifikasi tanda tangannya) + parameter path. Penyimpangan bentuk → 400 generik. */
export function parseMgmPolicy(raw: Buffer | undefined, pathCompany: string, pathVersion: string): MgmPolicyInput {
  if (!raw || raw.length === 0 || raw.length > MAX_MGM_POLICY_BODY_BYTES) throw Errors.invalidRequest();
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
  if (o.schema_version !== MGM_POLICY_SCHEMA_VERSION) throw Errors.invalidRequest();
  if (o.company_code !== pathCompany) throw Errors.invalidRequest();
  const version = int(o.version, 1, INT32_MAX);
  if (version !== Number(pathVersion)) throw Errors.invalidRequest();
  return {
    companyCode: pathCompany,
    version,
    effectiveFrom: parseEffectiveFrom(o.effective_from),
    approvedByUid: int(o.approved_by_uid, 1, INT32_MAX),
    params: parseMgmParams(o.params),
  };
}

/** Nama field yang melanggar batas platform/plafon (kosong = dalam batas). */
export function mgmPolicyViolations(p: MgmPolicyParams, c: MgmPolicyCeilings): string[] {
  const out: string[] = [];
  if (p.max_depth < MGM_DEPTH_MIN || p.max_depth > MGM_DEPTH_MAX) out.push('max_depth');
  if (p.level1_pct_bp > c.pctBpMax) out.push('level1_pct_bp');
  if (p.level2_pct_bp > c.pctBpMax || p.level2_pct_bp > p.level1_pct_bp) out.push('level2_pct_bp');
  if (p.flat_points_level3 > c.flatPointsMax) out.push('flat_points_level3');
  if (p.cap_points_per_referrer_month < 1 || p.cap_points_per_referrer_month > c.capPointsMax) out.push('cap_points_per_referrer_month');
  if (p.settle_hold_days < 1 || p.settle_hold_days > MGM_HOLD_DAYS_MAX) out.push('settle_hold_days');
  if (p.relation_months < 1 || p.relation_months > MGM_RELATION_MONTHS_MAX) out.push('relation_months');
  return out;
}

/** Sidik kanonik isi kebijakan (versi sama + sidik beda → 409; sidik sama → replay idempoten). */
export function mgmPolicyFingerprint(p: MgmPolicyInput): string {
  const canonical = {
    schema_version: MGM_POLICY_SCHEMA_VERSION,
    company_code: p.companyCode,
    version: p.version,
    effective_from: p.effectiveFrom.toISOString(),
    approved_by_uid: p.approvedByUid,
    params: Object.fromEntries(MGM_PARAM_KEYS.map((k) => [k, p.params[k]] as const)),
  };
  return createHash('sha256').update(JSON.stringify(canonical), 'utf8').digest('hex');
}

/**
 * §20.3 — poin reward satu level (rumus acuan Odoo `efn.mgm.policy._efn_reward_points`): level 1/2 =
 * floor(basis × bp ÷ 10.000 ÷ nilai 1 poin); level 3..max_depth = flat; di luar kedalaman / basis < minimal = 0.
 * Bilangan bulat (BigInt) agar basis besar tidak kehilangan presisi.
 */
export function mgmRewardPoints(p: MgmPolicyParams, level: number, basisIdr: number, pointValueIdr: number): number {
  if (!Number.isSafeInteger(level) || level < 1 || level > p.max_depth) return 0;
  if (!Number.isSafeInteger(basisIdr) || basisIdr < p.min_order_idr || basisIdr <= 0) return 0;
  if (!Number.isSafeInteger(pointValueIdr) || pointValueIdr < 1) return 0;
  if (level <= 2) {
    const bp = level === 1 ? p.level1_pct_bp : p.level2_pct_bp;
    return Number((BigInt(basisIdr) * BigInt(bp)) / (10_000n * BigInt(pointValueIdr)));
  }
  return p.flat_points_level3;
}

/** `occurred_at + n` bulan kalender (akhir bulan dijepit, mis. 31 Jan + 1 = 28/29 Feb). */
export function addMonths(at: Date, months: number): Date {
  const d = new Date(at.getTime());
  const day = d.getUTCDate();
  d.setUTCDate(1);
  d.setUTCMonth(d.getUTCMonth() + months);
  const last = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 0)).getUTCDate();
  d.setUTCDate(Math.min(day, last));
  return d;
}

const WIB_OFFSET_MS = 7 * 3600 * 1000;

/** Rentang [awal, akhir) bulan kalender WIB yang memuat `at` (cap bulanan §20.5). */
export function wibMonthRange(at: Date): { start: Date; end: Date } {
  const wib = new Date(at.getTime() + WIB_OFFSET_MS);
  const start = Date.UTC(wib.getUTCFullYear(), wib.getUTCMonth(), 1) - WIB_OFFSET_MS;
  const end = Date.UTC(wib.getUTCFullYear(), wib.getUTCMonth() + 1, 1) - WIB_OFFSET_MS;
  return { start: new Date(start), end: new Date(end) };
}

/** Alfabet kode referral (tanpa 0/O/1/I/L agar mudah dibaca & diketik). */
export const REFERRAL_ALPHABET = 'ABCDEFGHJKMNPQRSTUVWXYZ23456789';
export const REFERRAL_CODE_RE = /^[ABCDEFGHJKMNPQRSTUVWXYZ23456789]{8}$/;

/** Normalisasi input kode dari klien (huruf kecil/spasi/tanda hubung diterima) → kode atau null. */
export function normalizeReferralCode(v: unknown): string | null {
  if (typeof v !== 'string' || v.length > 32) return null;
  const c = v.replace(/[\s-]/g, '').toUpperCase();
  return REFERRAL_CODE_RE.test(c) ? c : null;
}

/** Kode acak 8 karakter dari byte acak (rejection sampling agar distribusi rata). */
export function referralCodeFrom(randomBytes: (n: number) => Buffer): string {
  const n = REFERRAL_ALPHABET.length; // 31
  const limit = 256 - (256 % n);
  let out = '';
  while (out.length < 8) {
    for (const b of randomBytes(16)) {
      if (b >= limit) continue;
      out += REFERRAL_ALPHABET[b % n];
      if (out.length === 8) break;
    }
  }
  return out;
}
