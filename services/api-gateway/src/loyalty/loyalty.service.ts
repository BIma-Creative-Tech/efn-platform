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
  /** PT tempat transaksi terjadi (earn: PT penerbit; redeem: PT penebus; reverse: PT order asal). */
  companyCode: CompanyCode;
  expiresAt: Date | null;
  /**
   * G21 §13.1 no. 5 (ADR-020 revisi): PT PENERBIT lot yang dikredit/didebit entri ini. Default = `companyCode` (kredit
   * earn baru). Debit redeem/clawback/forfeit = penerbit lot yang dikonsumsi FIFO (bisa ≠ PT penebus → lintas PT).
   */
  issuerCompanyCode?: CompanyCode;
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
  /**
   * G21 §13.1 no. 5: lot = (expires_at, PT penerbit). Opsional agar alokasi murni tetap bisa dipakai tanpa penerbit
   * (semua `undefined` = satu penerbit).
   */
  issuer?: CompanyCode;
  /** id entri pertama lot — pemecah seri FIFO bila dua penerbit punya `expires_at` sama (lot terbit lebih dulu dulu). */
  firstId?: number;
}

export interface Allocation {
  expiresAt: Date | null;
  points: number;
  /** PT penerbit lot yang dialokasikan (dicatat di `issuer_company_code`). */
  issuer?: CompanyCode;
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
 * FIFO: alokasikan debit `points` ke lot bersisa positif (urutan: `expires_at` terdekat dulu, NULL terakhir; seri →
 * lot yang terbit lebih dulu, `firstId`). Setiap bagian membawa PT penerbit lot (G21 §13.1 no. 5).
 * Kelebihan (saldo tidak cukup — commit terlambat, clawback) → lot NULL sebagai utang (saldo negatif,
 * "ditagih di earn berikutnya" — docs/04 §9.4) atas nama `fallbackIssuer`.
 */
export function allocateDebit(buckets: readonly LedgerBucket[], points: number, fallbackIssuer?: CompanyCode): Allocation[] {
  const sorted = sortBuckets(buckets);
  const out: Allocation[] = [];
  let left = points;
  for (const b of sorted) {
    if (left <= 0) break;
    if (b.remaining <= 0) continue;
    const take = Math.min(left, b.remaining);
    mergeAllocation(out, b.expiresAt, take, b.issuer);
    left -= take;
  }
  if (left > 0) mergeAllocation(out, null, left, fallbackIssuer);
  return out;
}

/**
 * Kredit baru dengan masa berlaku `expiresAt` dari PT `issuer`: bila lot NULL berutang (sisa negatif), bagian kredit
 * dipakai melunasi utang lebih dulu (entri NULL atas nama penerbit lot berutang itu — utang penerbit lain tidak tersisa
 * abadi), sisanya ke lot baru. Tanpa ini utang akan "hidup lagi" ketika kredit pelunasnya kedaluwarsa.
 */
export function splitCredit(
  buckets: readonly LedgerBucket[],
  points: number,
  expiresAt: Date | null,
  issuer?: CompanyCode,
): Allocation[] {
  const out: Allocation[] = [];
  let left = points;
  for (const d of sortBuckets(buckets)) {
    if (left <= 0) break;
    if (d.expiresAt !== null || d.remaining >= 0) continue;
    const take = Math.min(left, -d.remaining);
    mergeAllocation(out, null, take, d.issuer);
    left -= take;
  }
  if (left > 0) mergeAllocation(out, expiresAt, left, issuer);
  return out;
}

function sortBuckets(buckets: readonly LedgerBucket[]): LedgerBucket[] {
  return [...buckets].sort((a, b) => {
    if (a.expiresAt === null && b.expiresAt !== null) return 1;
    if (b.expiresAt === null && a.expiresAt !== null) return -1;
    const byExpiry = a.expiresAt !== null && b.expiresAt !== null ? a.expiresAt.getTime() - b.expiresAt.getTime() : 0;
    if (byExpiry !== 0) return byExpiry;
    const byId = (a.firstId ?? 0) - (b.firstId ?? 0);
    if (byId !== 0) return byId;
    return (a.issuer ?? '').localeCompare(b.issuer ?? '');
  });
}

function mergeAllocation(out: Allocation[], expiresAt: Date | null, points: number, issuer?: CompanyCode): void {
  const same = out.find((a) => (a.expiresAt?.getTime() ?? null) === (expiresAt?.getTime() ?? null) && a.issuer === issuer);
  if (same) same.points += points;
  else out.push(issuer === undefined ? { expiresAt, points } : { expiresAt, points, issuer });
}

/**
 * Ledger poin tunggal (D-05) di `platform.loyalty_ledger` (kontrak §5, §9).
 * - Saldo = SUM(delta_points) atas entri yang belum kedaluwarsa (`expires_at IS NULL OR expires_at > now`); debit
 *   dialokasikan FIFO ke bucket `expires_at` (lihat `LedgerBucket`) sehingga SUM tetap benar setelah kedaluwarsa.
 * - `company_code` = PT tempat transaksi; `issuer_company_code` (G21 §13.1 no. 5, ADR-020 revisi) = PT PENERBIT lot
 *   (penanggung liabilitas). Lot = (expires_at, penerbit); redeem/clawback/forfeit mengonsumsi lot FIFO dan mencatat
 *   penerbitnya; kedaluwarsa tidak menulis entri (lot hilang dari SUM) sehingga breakage per penerbit = SUM entri lot itu.
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

  /** G22: dekripsi `loyalty_ledger.member_ref_enc` (hanya di memori; pemanggil WAJIB memverifikasi hash). */
  decryptLedgerMemberRef(enc: Buffer): string {
    return this.cipher.decrypt(enc, PII_AAD.LOYALTY_LEDGER_MEMBER_REF);
  }

  /**
   * G22: `member.member_ref_enc` (AAD member) → dekripsi + verifikasi hash → enkripsi ulang dengan AAD ledger, untuk entri
   * ledger dari kanal ber-akun (online, QR). `null` bila member tidak aktif. Nomor tidak pernah keluar dari fungsi ini
   * kecuali sebagai ciphertext ledger.
   */
  async ledgerEncForMember(q: Queryable, memberId: string): Promise<{ hash: string; enc: Buffer; memberRef: string } | null> {
    const res = await q.query<{ member_hash: string; member_ref_enc: Buffer }>(
      `SELECT member_hash, member_ref_enc FROM platform.member WHERE id = $1 AND deleted_at IS NULL`,
      [memberId],
    );
    const row = res.rows[0];
    if (!row) return null;
    const ref = this.cipher.decrypt(row.member_ref_enc, PII_AAD.MEMBER_REF);
    if (this.hashMember(ref) !== row.member_hash) throw new Error('member_ref_enc tidak konsisten dengan member_hash');
    return { hash: row.member_hash, enc: this.encryptMemberRef(ref), memberRef: ref };
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
    const issuer = input.issuerCompanyCode ?? input.companyCode;
    if (!isCompanyCode(issuer)) throw new RangeError('issuer_company_code tidak dikenal');
    if (!isMemberHash(input.memberHash)) throw new RangeError('member_hash tidak valid');
    if (!Buffer.isBuffer(input.memberRefEnc) || input.memberRefEnc.length === 0) {
      throw new RangeError('member_ref_enc wajib diisi');
    }
    const res = await q.query<{ id: string }>(
      `INSERT INTO platform.loyalty_ledger
         (member_ref_hash, member_ref_enc, company_code, delta_points, reason, source, source_ref, idempotency_key, expires_at,
          issuer_company_code)
       VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
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
        issuer,
      ],
    );
    return (res.rowCount ?? 0) > 0;
  }

  /** Serialisasi per member sampai akhir transaksi `q` (WAJIB dipanggil di dalam `db.tx`). */
  async lockMember(q: Queryable, hash: string): Promise<void> {
    if (!isMemberHash(hash)) throw new RangeError('member_hash tidak valid');
    await q.query('SELECT pg_advisory_xact_lock($1::int4, $2::int4)', [MEMBER_LOCK_CLASS, memberLockKey(hash)]);
  }

  /**
   * Lot yang belum kedaluwarsa pada `at` = (expires_at, PT penerbit) — G21 §13.1 no. 5. Baris lama tanpa
   * `issuer_company_code` (pra-G21) → penerbit = `company_code`. Urut expires_at naik (NULL terakhir), lalu lot tertua.
   */
  async buckets(q: Queryable, hash: string, at: Date = new Date(this.now())): Promise<LedgerBucket[]> {
    const res = await q.query<{ expires_at: Date | string | null; issuer: string; remaining: string | number; first_id: string | number }>(
      `SELECT expires_at, COALESCE(issuer_company_code, company_code) AS issuer,
              SUM(delta_points)::bigint AS remaining, MIN(id) AS first_id
         FROM platform.loyalty_ledger
        WHERE member_ref_hash = $1
          AND (expires_at IS NULL OR expires_at > $2)
        GROUP BY expires_at, COALESCE(issuer_company_code, company_code)
        ORDER BY expires_at ASC NULLS LAST, first_id ASC`,
      [hash, at.toISOString()],
    );
    return res.rows.map((r) => {
      if (!isCompanyCode(r.issuer)) throw new RangeError('issuer ledger tidak dikenal');
      return {
        expiresAt: r.expires_at === null ? null : new Date(r.expires_at),
        remaining: Number(r.remaining),
        issuer: r.issuer,
        firstId: Number(r.first_id),
      };
    });
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
