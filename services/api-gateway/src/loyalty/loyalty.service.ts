import { Inject, Injectable } from '@nestjs/common';
import { AppConfig } from '../config/app-config';
import { DB, Db, Queryable } from '../common/db/db';
import { PII_AAD, PiiCipher } from '../common/pii/crypto';
import { isMemberHash, memberHash } from '../common/pii/pii';

/**
 * Kode PT (docs/04 §12.1, W-32/L-48, ADR-001 revisi 6 entitas): Holding `EFN`, PT Grand Lucky `GL` (dulu `RTL`),
 * PT Paris Baguette `PB` (dulu `FNB`), Bacha Coffee Retail `BCR`, Bacha Coffee F&B `BCF`, `FARM`. Kode lama `RTL`/`FNB`
 * DIHAPUS (tidak ada alias) — CHECK PostgreSQL ikut diganti di `infra/postgres/init/09-platform-g20.sql`.
 */
export const COMPANY_CODES = ['EFN', 'GL', 'PB', 'BCR', 'BCF', 'FARM'] as const;
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

/** Entri ledger umum (kredit atau debit). `delta` ≠ 0. */
export interface LedgerEntryInput {
  idempotencyKey: string;
  memberHash: string;
  memberRefEnc: Buffer;
  delta: number;
  reason: string;
  source: string;
  sourceRef: string | null;
  companyCode: CompanyCode;
  expiresAt: Date | null;
}

export interface EarnResult {
  /** false = idempotency_key sudah ada di ledger (duplikat), tidak ada perubahan saldo. */
  applied: boolean;
}

/**
 * "Bucket" saldo = kelompok entri dengan `expires_at` yang sama (NULL = tidak kedaluwarsa). Sisa bucket =
 * SUM(delta_points) entri bucket itu. Debit (redeem/clawback) DIALOKASIKAN ke bucket (FIFO: yang paling cepat
 * kedaluwarsa dulu) dengan menyalin `expires_at` bucket ke entri debit, sehingga saat bucket kedaluwarsa kredit dan
 * debitnya hilang bersama dan saldo = SUM entri yang belum kedaluwarsa tetap benar.
 */
export interface LedgerBucket {
  expiresAt: Date | null;
  remaining: number;
}

export interface Allocation {
  expiresAt: Date | null;
  points: number;
}

/**
 * Batas teknis absolut satu entri earn. Batas bisnis per pesan `loyalty.earn.requested` jauh lebih ketat:
 * `LOYALTY_MAX_POINTS_PER_EVENT` (default 100, SEC-08); earn POS: `LOYALTY_POS_MAX_EARN_POINTS`.
 */
export const MAX_EARN_POINTS = 1_000_000;
/** Batas teknis |delta| satu entri (kolom integer). */
export const MAX_ENTRY_POINTS = 1_000_000_000;

/** Kelas advisory lock per member (dua-kunci int4; ruang kunci terpisah dari kunci bigint job retensi). */
export const MEMBER_LOCK_CLASS = 0x45464e4d; // 'EFNM'

/** Kunci int4 bertanda dari 8 hex pertama member_hash (tabrakan hanya menyebabkan serialisasi berlebih, bukan salah). */
export function memberLockKey(hash: string): number {
  return parseInt(hash.slice(0, 8), 16) | 0;
}

/**
 * FIFO: alokasikan debit `points` ke bucket bersisa positif (urutan: `expires_at` terdekat dulu, NULL terakhir).
 * Kelebihan (saldo tidak cukup — commit terlambat, clawback) → bucket NULL sebagai utang (saldo negatif,
 * "ditagih di earn berikutnya" — docs/04 §9.4).
 */
export function allocateDebit(buckets: readonly LedgerBucket[], points: number): Allocation[] {
  const sorted = sortBuckets(buckets);
  const out: Allocation[] = [];
  let left = points;
  for (const b of sorted) {
    if (left <= 0) break;
    if (b.remaining <= 0) continue;
    const take = Math.min(left, b.remaining);
    out.push({ expiresAt: b.expiresAt, points: take });
    left -= take;
  }
  if (left > 0) mergeAllocation(out, null, left);
  return out;
}

/**
 * Kredit baru dengan masa berlaku `expiresAt`: bila bucket NULL berutang (sisa negatif), bagian kredit dipakai melunasi
 * utang lebih dulu (entri NULL), sisanya ke bucket bermasa berlaku. Tanpa ini utang akan "hidup lagi" ketika kredit
 * pelunasnya kedaluwarsa.
 */
export function splitCredit(buckets: readonly LedgerBucket[], points: number, expiresAt: Date | null): Allocation[] {
  if (expiresAt === null) return [{ expiresAt: null, points }];
  const debt = -Math.min(0, buckets.find((b) => b.expiresAt === null)?.remaining ?? 0);
  const payoff = Math.min(points, debt);
  const out: Allocation[] = [];
  if (payoff > 0) out.push({ expiresAt: null, points: payoff });
  if (points - payoff > 0) out.push({ expiresAt, points: points - payoff });
  return out;
}

function sortBuckets(buckets: readonly LedgerBucket[]): LedgerBucket[] {
  return [...buckets].sort((a, b) => {
    if (a.expiresAt === null) return b.expiresAt === null ? 0 : 1;
    if (b.expiresAt === null) return -1;
    return a.expiresAt.getTime() - b.expiresAt.getTime();
  });
}

function mergeAllocation(out: Allocation[], expiresAt: Date | null, points: number): void {
  const same = out.find((a) => (a.expiresAt?.getTime() ?? null) === (expiresAt?.getTime() ?? null));
  if (same) same.points += points;
  else out.push({ expiresAt, points });
}

/**
 * Ledger poin tunggal (D-05) di `platform.loyalty_ledger` (kontrak §5, §9).
 * - Saldo = SUM(delta_points) atas entri yang belum kedaluwarsa (`expires_at IS NULL OR expires_at > now`); debit
 *   dialokasikan FIFO ke bucket `expires_at` (lihat `LedgerBucket`) sehingga SUM tetap benar setelah kedaluwarsa.
 * - `company_code` = PT yang menanggung liabilitas poin (ADR-020) untuk settlement antar-PT.
 * - Identitas member: `member_ref_hash` = member_hash (kontrak §1) + `member_ref_enc` (wajib, docs/04 §7.2) agar
 *   nomor bisa dipulihkan untuk DSAR/notifikasi tanpa menyimpan plaintext.
 * - Idempotensi: UNIQUE(idempotency_key) + `ON CONFLICT DO NOTHING`.
 * - Mutasi yang bergantung pada saldo (reserve/commit/earn POS/reverse) diserialkan per member dengan
 *   `pg_advisory_xact_lock(MEMBER_LOCK_CLASS, memberLockKey(hash))` di dalam transaksi (`lockMember`).
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
    const applied = await this.insertEntry(
      {
        idempotencyKey: input.idempotencyKey,
        memberHash: input.memberHash,
        memberRefEnc: input.memberRefEnc,
        delta: input.points,
        reason: input.reason,
        source: input.source,
        sourceRef: input.sourceRef,
        companyCode: input.companyCode ?? DEFAULT_LIABILITY_COMPANY,
        expiresAt: input.expiresAt ?? null,
      },
      q,
    );
    return { applied };
  }

  /** Tulis satu entri (kredit/debit). true = baris baru; false = idempotency_key sudah ada. */
  async insertEntry(input: LedgerEntryInput, q: Queryable = this.db): Promise<boolean> {
    if (!Number.isSafeInteger(input.delta) || input.delta === 0 || Math.abs(input.delta) > MAX_ENTRY_POINTS) {
      throw new RangeError('delta harus bilangan bulat ≠ 0 dalam batas');
    }
    if (!isCompanyCode(input.companyCode)) throw new RangeError('company_code tidak dikenal');
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
        input.companyCode,
        input.delta,
        input.reason,
        input.source,
        input.sourceRef,
        input.idempotencyKey,
        input.expiresAt ? input.expiresAt.toISOString() : null,
      ],
    );
    return (res.rowCount ?? 0) > 0;
  }

  /** Serialisasi per member sampai akhir transaksi `q` (WAJIB dipanggil di dalam `db.tx`). */
  async lockMember(q: Queryable, hash: string): Promise<void> {
    if (!isMemberHash(hash)) throw new RangeError('member_hash tidak valid');
    await q.query('SELECT pg_advisory_xact_lock($1::int4, $2::int4)', [MEMBER_LOCK_CLASS, memberLockKey(hash)]);
  }

  /** Bucket yang belum kedaluwarsa pada `at` (urut expires_at naik, NULL terakhir). */
  async buckets(q: Queryable, hash: string, at: Date = new Date(this.now())): Promise<LedgerBucket[]> {
    const res = await q.query<{ expires_at: Date | string | null; remaining: string | number }>(
      `SELECT expires_at, SUM(delta_points)::bigint AS remaining
         FROM platform.loyalty_ledger
        WHERE member_ref_hash = $1
          AND (expires_at IS NULL OR expires_at > $2)
        GROUP BY expires_at
        ORDER BY expires_at ASC NULLS LAST`,
      [hash, at.toISOString()],
    );
    return res.rows.map((r) => ({
      expiresAt: r.expires_at === null ? null : new Date(r.expires_at),
      remaining: Number(r.remaining),
    }));
  }

  async balance(memberRef: string): Promise<number> {
    return this.balanceByHash(this.hashMember(memberRef));
  }

  /** Saldo pada waktu `now` (parameter eksplisit agar konsisten dengan clock aplikasi & mudah diuji). */
  async balanceByHash(hash: string, q: Queryable = this.db): Promise<number> {
    const res = await q.query<{ balance: string | number | null }>(
      `SELECT COALESCE(SUM(delta_points), 0)::bigint AS balance
         FROM platform.loyalty_ledger
        WHERE member_ref_hash = $1
          AND (expires_at IS NULL OR expires_at > $2)`,
      [hash, new Date(this.now()).toISOString()],
    );
    return Number(res.rows[0]?.balance ?? 0);
  }
}
