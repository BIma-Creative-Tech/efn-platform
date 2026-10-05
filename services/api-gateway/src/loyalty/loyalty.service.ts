import { Inject, Injectable } from '@nestjs/common';
import { AppConfig } from '../config/app-config';
import { DB, Db, Queryable } from '../common/db/db';
import { PII_AAD, PiiCipher } from '../common/pii/crypto';
import { isMemberHash, memberHash } from '../common/pii/pii';

/** Kode PT (kontrak §1). */
export const COMPANY_CODES = ['EFN', 'RTL', 'FNB', 'FARM'] as const;
export type CompanyCode = (typeof COMPANY_CODES)[number];

export function isCompanyCode(v: unknown): v is CompanyCode {
  return typeof v === 'string' && (COMPANY_CODES as readonly string[]).includes(v);
}

/**
 * PT penanggung liabilitas poin bila sumber tidak menyebutkannya. Untuk price_hunter = holding `EFN`
 * sampai Finance memutuskan (ADR-020, Proposed).
 */
export const DEFAULT_LIABILITY_COMPANY: CompanyCode = 'EFN';

export interface EarnInput {
  idempotencyKey: string;
  /** member_hash (kontrak §1). */
  memberHash: string;
  /**
   * member_ref terenkripsi (AES-256-GCM, AAD `efn:loyalty_ledger.member_ref_enc:v1`) — WAJIB (docs/04 §7.2).
   * Gunakan `encryptMemberRef()` bila member_ref diketahui.
   */
  memberRefEnc: Buffer;
  points: number;
  reason: string;
  source: string;
  sourceRef: string | null;
  /** Default `EFN`. */
  companyCode?: CompanyCode;
  /** NULL = tidak kedaluwarsa (kebijakan kedaluwarsa poin belum diputuskan). */
  expiresAt?: Date | null;
}

export interface EarnResult {
  /** false = idempotency_key sudah ada di ledger (duplikat), tidak ada perubahan saldo. */
  applied: boolean;
}

/**
 * Batas teknis absolut satu entri earn. Batas bisnis per pesan `loyalty.earn.requested` jauh lebih ketat:
 * `LOYALTY_MAX_POINTS_PER_EVENT` (default 100, SEC-08).
 */
export const MAX_EARN_POINTS = 1_000_000;

/**
 * Ledger poin tunggal (D-05) di `platform.loyalty_ledger` (kontrak §5).
 * - Saldo = SUM(delta_points) atas entri yang belum kedaluwarsa (`expires_at IS NULL OR expires_at > now()`).
 * - `company_code` = PT yang menanggung liabilitas poin (ADR-020) untuk settlement antar-PT.
 * - Identitas member: `member_ref_hash` = member_hash (kontrak §1) + `member_ref_enc` (wajib, docs/04 §7.2) agar
 *   nomor bisa dipulihkan untuk DSAR/notifikasi tanpa menyimpan plaintext.
 * - Idempotensi earn: UNIQUE(idempotency_key) + `ON CONFLICT DO NOTHING`.
 *
 * Catatan: redeem/debit (delta negatif) belum ada; ketika ditambahkan, aturan FIFO terhadap entri yang
 * kedaluwarsa harus dirancang ulang (SUM sederhana tidak cukup bila debit memakai poin yang lalu kedaluwarsa).
 */
@Injectable()
export class LoyaltyService {
  now: () => number = () => Date.now();

  constructor(
    private readonly config: AppConfig,
    @Inject(DB) private readonly db: Db,
    private readonly cipher: PiiCipher,
  ) {}

  hashMember(memberRef: string): string {
    return memberHash(this.config.memberHashPepper, memberRef);
  }

  encryptMemberRef(memberRef: string): Buffer {
    return this.cipher.encrypt(memberRef, PII_AAD.LOYALTY_LEDGER_MEMBER_REF);
  }

  /** Jalankan di dalam transaksi pemanggil (param `q`) bila digabung dengan klaim processed_message. */
  async earn(input: EarnInput, q: Queryable = this.db): Promise<EarnResult> {
    if (!Number.isSafeInteger(input.points) || input.points <= 0 || input.points > MAX_EARN_POINTS) {
      throw new RangeError('points harus bilangan bulat positif dalam batas');
    }
    const companyCode = input.companyCode ?? DEFAULT_LIABILITY_COMPANY;
    if (!isCompanyCode(companyCode)) throw new RangeError('company_code tidak dikenal');
    if (!isMemberHash(input.memberHash)) throw new RangeError('member_hash tidak valid');
    if (!Buffer.isBuffer(input.memberRefEnc) || input.memberRefEnc.length === 0) {
      throw new RangeError('member_ref_enc wajib diisi');
    }
    const res = await q.query<{ id: string }>(
      `INSERT INTO platform.loyalty_ledger
         (member_ref_hash, member_ref_enc, company_code, delta_points, reason, source, source_ref, idempotency_key, expires_at)
       VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
       ON CONFLICT (idempotency_key) DO NOTHING
       RETURNING id`,
      [
        input.memberHash,
        input.memberRefEnc,
        companyCode,
        input.points,
        input.reason,
        input.source,
        input.sourceRef,
        input.idempotencyKey,
        input.expiresAt ? input.expiresAt.toISOString() : null,
      ],
    );
    return { applied: (res.rowCount ?? 0) > 0 };
  }

  async balance(memberRef: string): Promise<number> {
    return this.balanceByHash(this.hashMember(memberRef));
  }

  /** Saldo pada waktu `now` (parameter eksplisit agar konsisten dengan clock aplikasi & mudah diuji). */
  async balanceByHash(hash: string): Promise<number> {
    const res = await this.db.query<{ balance: string | number | null }>(
      `SELECT COALESCE(SUM(delta_points), 0)::bigint AS balance
         FROM platform.loyalty_ledger
        WHERE member_ref_hash = $1
          AND (expires_at IS NULL OR expires_at > $2)`,
      [hash, new Date(this.now()).toISOString()],
    );
    return Number(res.rows[0]?.balance ?? 0);
  }
}
