import { Errors } from '../../common/errors/api-error';
import { asRecord, requireInt, requireString } from '../../common/validation';
import { CompanyCode, isCompanyCode } from '../../loyalty/loyalty.service';
import { requireMemberRef } from '../../pos/dto/pos.dto';

/** DTO migrasi (docs/04 §9.6 K-7). Validasi manual → 400 `invalid_request` / `invalid_phone`. */

/** `source_ref` = id baris di sistem lama (opak, ASCII tercetak tanpa spasi, ≤ 64). */
function requireSourceRef(o: Record<string, unknown>): string {
  const v = requireString(o, 'source_ref', 64);
  if (!/^[\x21-\x7e]{1,64}$/.test(v)) throw Errors.invalidRequest();
  return v;
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
