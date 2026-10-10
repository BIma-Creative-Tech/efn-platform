import { ApiError, Errors } from '../../common/errors/api-error';
import { asRecord, requireInt, requireString } from '../../common/validation';
import { CompanyCode, isCompanyCode } from '../../loyalty/loyalty.service';
import { requireMemberRef } from '../../pos/dto/pos.dto';

/** DTO migrasi (docs/04 §9.6 K-7). Validasi manual → 400 `invalid_request` / `invalid_phone`. */

/** UUID versi 4 (acak). v1/v6 (MAC/waktu), v7 (waktu), atau v3/v5 (turunan nama) ditolak. */
const SOURCE_REF_UUID4_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
/** Hex huruf kecil 32..64 (HMAC-SHA256 terpotong ≥ 128 bit, atau penuh 256 bit). */
const SOURCE_REF_HEX_RE = /^[0-9a-f]{32,64}$/;
/** Minimal simbol hex berbeda: keluaran HMAC acak 32 hex hampir pasti memuat ≥ 12; pola berulang/padding ditolak. */
const SOURCE_REF_MIN_DISTINCT = 8;

/**
 * W-25 (Q-P13, keputusan pemilik proyek 10 Okt 2026): `source_ref` WAJIB opak — entri ledger saldo pembuka tidak dapat
 * diubah (append-only, 10 th) dan `source_ref` tetap ada setelah akun dihapus, sehingga id pelanggan sistem lama
 * (nomor HP, nomor pelanggan, email) tidak boleh masuk. Format yang diterima (dinormalkan ke huruf kecil):
 * - UUID v4 acak, mis. `3f2b6c1e-8a4d-4c2e-9b1f-0a2b3c4d5e6f`; atau
 * - hex 32..64, mis. `HMAC-SHA256(kunci_migrasi, id_lama)` (kunci dipegang tim migrasi/Finance di luar platform, dibuang
 *   setelah rekonsiliasi selesai — tanpa kunci, `source_ref` tidak dapat ditautkan ke id lama).
 * Ditolak (400 `invalid_source_ref`): format lain (termasuk `legacy-001`, nomor HP `62812…`/`0812…`, angka pelanggan,
 * email), hex yang seluruhnya angka desimal (mis. nomor yang di-padding atau di-hex-kan dari teks angka), atau hex
 * dengan keragaman simbol rendah (< 8 simbol berbeda).
 */
export function parseOpaqueSourceRef(v: unknown): string {
  if (typeof v !== 'string' || v.length === 0 || v.length > 64) throw invalidSourceRef();
  const s = v.trim().toLowerCase();
  if (s.length !== v.length) throw invalidSourceRef();
  if (SOURCE_REF_UUID4_RE.test(s)) return s;
  if (!SOURCE_REF_HEX_RE.test(s)) throw invalidSourceRef();
  if (/^[0-9]+$/.test(s)) throw invalidSourceRef();
  if (new Set(s).size < SOURCE_REF_MIN_DISTINCT) throw invalidSourceRef();
  return s;
}

function invalidSourceRef(): ApiError {
  return ApiError.of(
    400,
    'invalid_source_ref',
    'source_ref harus opak: UUID v4 acak atau hex 32–64 karakter (HMAC). Nomor HP, nomor pelanggan, atau email tidak diizinkan.',
  );
}

function requireSourceRef(o: Record<string, unknown>): string {
  if (o.source_ref === undefined || o.source_ref === null) throw Errors.invalidRequest();
  return parseOpaqueSourceRef(o.source_ref);
}

export interface MigrationConsentDto {
  purpose: 'location' | 'marketing';
  granted: boolean;
  version: string;
  textSha256: string;
  /** Waktu bukti consent di sistem lama (≤ sekarang); null → waktu migrasi. */
  recordedAt: Date | null;
}

export interface MigrationMemberDto {
  sourceRef: string;
  memberRef: string;
  consents: MigrationConsentDto[];
}

export interface OpeningBalanceDto {
  sourceRef: string;
  memberRef: string;
  points: number;
  companyCode: CompanyCode;
}

function parseConsent(v: unknown, nowMs: number): MigrationConsentDto {
  const o = asRecord(v);
  if (o.purpose !== 'location' && o.purpose !== 'marketing') throw Errors.invalidRequest();
  if (typeof o.granted !== 'boolean') throw Errors.invalidRequest();
  const version = requireString(o, 'version', 32);
  const textSha256 = requireString(o, 'text_sha256', 64).toLowerCase();
  if (!/^[0-9a-f]{64}$/.test(textSha256)) throw Errors.invalidRequest();
  let recordedAt: Date | null = null;
  if (o.recorded_at !== undefined && o.recorded_at !== null) {
    const d = new Date(requireString(o, 'recorded_at', 40));
    if (Number.isNaN(d.getTime()) || d.getTime() > nowMs) throw Errors.invalidRequest();
    recordedAt = d;
  }
  return { purpose: o.purpose, granted: o.granted, version, textSha256, recordedAt };
}

/**
 * `POST /internal/v1/migration/members` `{ source_ref, member_ref, consents?: [{ purpose, granted, version,
 * text_sha256, recorded_at? }] }` — consent hanya bila ada bukti di sistem lama (teks/versi yang disetujui).
 */
export function parseMigrationMember(body: unknown, nowMs: number = Date.now()): MigrationMemberDto {
  const o = asRecord(body);
  const memberRef = requireMemberRef(o.member_ref);
  const raw = o.consents ?? [];
  if (!Array.isArray(raw) || raw.length > 2) throw Errors.invalidRequest();
  const consents = raw.map((c) => parseConsent(c, nowMs));
  if (new Set(consents.map((c) => c.purpose)).size !== consents.length) throw Errors.invalidRequest();
  return { sourceRef: requireSourceRef(o), memberRef, consents };
}

/** `POST /internal/v1/migration/opening-balance` `{ source_ref, member_ref, points, company_code }`. */
export function parseOpeningBalance(body: unknown): OpeningBalanceDto {
  const o = asRecord(body);
  const memberRef = requireMemberRef(o.member_ref);
  if (!isCompanyCode(o.company_code)) throw Errors.invalidRequest();
  return {
    sourceRef: requireSourceRef(o),
    memberRef,
    points: requireInt(o, 'points', 1, 1_000_000),
    companyCode: o.company_code,
  };
}
