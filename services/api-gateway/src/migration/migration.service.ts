import { Inject, Injectable, Logger } from '@nestjs/common';
import { randomUUID } from 'node:crypto';
import { AppConfig } from '../config/app-config';
import { DB, Db, Queryable } from '../common/db/db';
import { ApiError, Errors } from '../common/errors/api-error';
import { MetricsService } from '../common/metrics/metrics.service';
import { erasureBlock } from '../common/pii/erasure-suppression';
import { PII_AAD, PiiCipher } from '../common/pii/crypto';
import { maskMsisdn } from '../common/pii/pii';
import { reportLoyaltyAnomaly } from '../loyalty/loyalty-anomaly';
import { LoyaltyService, splitCredit } from '../loyalty/loyalty.service';
import { expiryFor } from '../pos/pos-loyalty.service';
import { MigrationMemberDto, OpeningBalanceDto, parseOpaqueSourceRef } from './dto/migration.dto';

export interface MigrationMemberResponse {
  member: 'created' | 'existing';
  consents_recorded: number;
}

export interface OpeningBalanceResponse {
  /** false = source_ref sudah pernah diimpor dengan data yang sama (idempoten). */
  applied: boolean;
  points_balance: number;
}

export const MIGRATION_SOURCE = 'migration_opening';

const MigrationErrors = {
  memberErased: (): ApiError => Errors.conflict('member_erased', 'Data pelanggan ini sudah dihapus atas permintaan pelanggan.'),
};

/**
 * Migrasi data member & saldo pembuka dari sistem lama (docs/04 §9.6 K-7). Internal, dijaga `MigrationKeyGuard`.
 *
 * - Member: upsert `platform.member` (nomor HP terenkripsi AAD member, hash member) — idempoten per nomor. Consent hanya
 *   bila ada bukti (versi + SHA-256 teks yang disetujui), `source = 'migration'`, satu baris per (member, purpose)
 *   — impor ulang tidak menambah baris (consent append-only, docs/04 §8.4).
 * - Saldo pembuka: entri ledger `source = 'migration_opening'`, `idempotency_key = migration_opening:<source_ref>`,
 *   `company_code` = PT penanggung liabilitas (ADR-020). Idempoten per `source_ref`: impor ulang identik → `applied:false`;
 *   source_ref sama dengan data berbeda → `409 idempotency_conflict`. Masa berlaku mengikuti
 *   `LOYALTY_POINT_EXPIRY_DAYS` sejak tanggal migrasi (PLACEHOLDER — menunggu Finance/Q-13); bila member berutang,
 *   kredit melunasi utang dulu (sama dengan earn).
 * - Log hanya nomor ter-mask. W-25 (Q-P13): `source_ref` WAJIB opak (UUID v4 / hex HMAC 32..64, `parseOpaqueSourceRef`)
 *   — divalidasi di DTO DAN lagi di service (pertahanan berlapis untuk pemanggil internal); selain itu
 *   `400 invalid_source_ref`. Entri ledger tidak bisa diubah, sehingga id pelanggan lama tidak boleh tersimpan.
 * - SEC-65 (Gelombang 13c): member DAN saldo pembuka ditolak bila subjek sedang/pernah dihapus (DSAR) —
 *   `409 member_deleting` (erasure `scheduled`/`processing`) atau `409 member_erased` (tag di
 *   `platform.erasure_suppression`, masa `ERASURE_SUPPRESSION_DAYS`). Tanpa ini impor ulang file lama menghidupkan
 *   kembali subjek beserta consent dan saldonya. Penolakan dicatat `EFN_LOYALTY_ANOMALY kind=migration_erased_member`
 *   (tanpa nomor).
 * - SEC-61/SEC-66: setiap saldo pembuka yang dibukukan menambah `efn_loyalty_migration_points_total{company}` dan
 *   mencetak `EFN_LOYALTY_MIGRATION {json}` (company, points, source_ref — tanpa nomor) untuk rekonsiliasi Finance;
 *   saldo > `LOYALTY_MIGRATION_ANOMALY_POINTS` → `EFN_LOYALTY_ANOMALY kind=migration_large_balance` (tetap diterima).
 */
@Injectable()
export class MigrationService {
  private readonly logger = new Logger(MigrationService.name);
  now: () => number = () => Date.now();

  constructor(
    private readonly config: AppConfig,
    @Inject(DB) private readonly db: Db,
    private readonly cipher: PiiCipher,
    private readonly loyalty: LoyaltyService,
    private readonly metrics: MetricsService,
  ) {}

  /** SEC-65: tolak subjek yang sedang/pernah dihapus (dipanggil di dalam transaksi impor). */
  private async assertNotErased(q: Queryable, hash: string, sourceRef: string, op: 'member' | 'opening_balance'): Promise<void> {
    const block = await erasureBlock(q, this.config.erasureSuppressionKey, hash, new Date(this.now()));
    if (block === null) return;
    reportLoyaltyAnomaly(this.logger, this.metrics, 'migration_erased_member', { op, state: block, source_ref: sourceRef });
    throw block === 'erasure_active'
      ? Errors.conflict('member_deleting', 'Akun sedang dalam proses penghapusan.')
      : MigrationErrors.memberErased();
  }

  async importMember(dto: MigrationMemberDto): Promise<MigrationMemberResponse> {
    if (parseOpaqueSourceRef(dto.sourceRef) !== dto.sourceRef) throw Errors.invalidRequest(); // W-25: hanya bentuk ternormalisasi
    const hash = this.loyalty.hashMember(dto.memberRef);
    return this.db.tx(async (q) => {
      await this.assertNotErased(q, hash, dto.sourceRef, 'member');
      const ins = await q.query<{ id: string }>(
        `INSERT INTO platform.member (id, member_hash, member_ref_enc)
         VALUES ($1, $2, $3)
         ON CONFLICT (member_hash) DO NOTHING
         RETURNING id::text AS id`,
        [randomUUID(), hash, this.cipher.encrypt(dto.memberRef, PII_AAD.MEMBER_REF)],
      );
      let memberId = ins.rows[0]?.id;
      const created = memberId !== undefined;
      if (!memberId) {
        const sel = await q.query<{ id: string; deleted_at: Date | string | null }>(
          `SELECT id::text AS id, deleted_at FROM platform.member WHERE member_hash = $1`,
          [hash],
        );
        const row = sel.rows[0];
        if (!row) throw new Error('member hilang setelah konflik upsert');
        if (row.deleted_at !== null) throw Errors.conflict('member_deleting', 'Akun sedang dalam proses penghapusan.');
        memberId = row.id;
      }
      let recorded = 0;
      for (const c of dto.consents) {
        const res = await q.query(
          `INSERT INTO platform.consent (member_id, purpose, granted, version, text_sha256, source, created_at)
           SELECT $1, $2, $3, $4, $5, 'migration', COALESCE($6::timestamptz, now())
            WHERE NOT EXISTS (SELECT 1 FROM platform.consent WHERE member_id = $1 AND purpose = $2 AND source = 'migration')`,
          [memberId, c.purpose, c.granted, c.version, c.textSha256, c.recordedAt ? c.recordedAt.toISOString() : null],
        );
        recorded += res.rowCount ?? 0;
      }
      if (created) this.logger.log(`migrasi member ${maskMsisdn(dto.memberRef)} (${dto.sourceRef})`);
      return { member: created ? ('created' as const) : ('existing' as const), consents_recorded: recorded };
    });
  }

  async openingBalance(dto: OpeningBalanceDto): Promise<OpeningBalanceResponse> {
    if (parseOpaqueSourceRef(dto.sourceRef) !== dto.sourceRef) throw Errors.invalidRequest(); // W-25: hanya bentuk ternormalisasi
    const hash = this.loyalty.hashMember(dto.memberRef);
    const key = `${MIGRATION_SOURCE}:${dto.sourceRef}`;
    // G21: pelunasan utang bisa terpecah per penerbit lot berutang (splitCredit) → ≤ 1 + jumlah PT bagian
    const keys = [key, ...Array.from({ length: 6 }, (_, i) => `${key}#${i + 2}`)];
    return this.db.tx(async (q) => {
      await this.loyalty.lockMember(q, hash);
      await this.assertNotErased(q, hash, dto.sourceRef, 'opening_balance');
      const existing = await q.query<{ member_ref_hash: string; company_code: string; delta_points: number }>(
        `SELECT member_ref_hash, company_code, delta_points FROM platform.loyalty_ledger WHERE idempotency_key = ANY($1::text[])`,
        [keys],
      );
      if (existing.rows.length > 0) {
        const total = existing.rows.reduce((s, r) => s + Number(r.delta_points), 0);
        const same =
          total === dto.points && existing.rows.every((r) => r.member_ref_hash === hash && r.company_code === dto.companyCode);
        if (!same) throw Errors.conflict('idempotency_conflict', 'source_ref sudah dipakai untuk data lain.');
        return { applied: false, points_balance: await this.loyalty.balanceByHash(hash, q) };
      }
      const at = new Date(this.now());
      const parts = splitCredit(
        await this.loyalty.buckets(q, hash, at),
        dto.points,
        expiryFor(at.getTime(), this.config.loyaltyPointExpiryDays),
        dto.companyCode,
      );
      if (parts.length > keys.length) throw new Error('migration: bagian kredit melebihi kunci idempotensi');
      const enc = this.loyalty.encryptMemberRef(dto.memberRef);
      for (const [i, part] of parts.entries()) {
        await this.loyalty.insertEntry(
          {
            idempotencyKey: keys[i],
            memberHash: hash,
            memberRefEnc: enc,
            delta: part.points,
            reason: 'opening_balance',
            source: MIGRATION_SOURCE,
            sourceRef: dto.sourceRef,
            companyCode: dto.companyCode,
            expiresAt: part.expiresAt,
            issuerCompanyCode: part.issuer ?? dto.companyCode,
          },
          q,
        );
      }
      this.metrics.recordLoyaltyMigrationPoints(dto.companyCode, dto.points);
      this.logger.log(
        `EFN_LOYALTY_MIGRATION ${JSON.stringify({ company: dto.companyCode, points: dto.points, source_ref: dto.sourceRef.replace(/[^A-Za-z0-9_.:-]/g, '').slice(0, 64) })}`,
      );
      if (dto.points > this.config.loyaltyMigrationAnomalyPoints) {
        reportLoyaltyAnomaly(this.logger, this.metrics, 'migration_large_balance', {
          company: dto.companyCode,
          points: dto.points,
          limit: this.config.loyaltyMigrationAnomalyPoints,
          source_ref: dto.sourceRef,
        });
      }
      return { applied: true, points_balance: await this.loyalty.balanceByHash(hash, q) };
    });
  }
}
