import { Inject, Injectable } from '@nestjs/common';
import { randomUUID } from 'node:crypto';
import { AppConfig } from '../../config/app-config';
import { DB, Db, Queryable } from '../db/db';
import { PII_AAD, PiiCipher } from '../pii/crypto';
import { memberHash } from '../pii/pii';

export interface ActiveMember {
  id: string;
  memberHash: string;
}

export interface UpsertMemberOptions {
  ageConfirmed?: boolean;
  /** Kanal pernyataan usia (default `app`). */
  ageSource?: 'app' | 'pos';
  /** K-18-8: pos.config kasir yang mencentang pernyataan (jejak, bukan PII). */
  posConfigId?: number | null;
}

export interface UpsertMemberResult {
  id: string;
  /** true = akun sedang dihapus (DSAR `processing`), login ditolak. */
  deleting: boolean;
}

/**
 * Identitas member opak (docs/04 §8.2). Tabel `platform.member (id uuid PK, member_hash UNIQUE, member_ref_enc,
 * created_at, deleted_at)`:
 * - JWT `sub` = `member.id` (UUID acak), BUKAN nomor HP → token yang bocor/ter-log tidak membuka nomor.
 * - `member_ref_enc` = nomor HP AES-256-GCM (AAD `efn:member.member_ref_enc:v1`), hanya didekripsi saat perlu
 *   (masking `/v1/me`, `user_enc` laporan Market Scout, export DSAR).
 * - Ledger & laporan tetap dikunci `member_hash`.
 * - `deleted_at` diisi saat DSAR delete masuk `processing`; baris dihapus setelah erasure platform selesai.
 *   Token yang masih beredar ditolak karena guard selalu memeriksa member aktif.
 */
@Injectable()
export class MemberService {
  constructor(
    private readonly config: AppConfig,
    @Inject(DB) private readonly db: Db,
    private readonly cipher: PiiCipher,
  ) {}

  hash(memberRef: string): string {
    return memberHash(this.config.memberHashPepper, memberRef);
  }

  /** Dipanggil setelah OTP login terverifikasi (login = daftar). Tidak menimpa `member_ref_enc` yang ada. */
  /**
   * W-19 (Q-P3) / K-18-8: `ageConfirmed` → `age_confirmed_at` + `age_confirmed_source` (`app` login Super-App |
   * `pos` kasir, + `age_confirmed_pos_config_id`) diisi (member baru) atau dilengkapi sekali (member lama hasil
   * migrasi yang belum pernah menyatakan); tidak pernah ditimpa. Tanpa tanggal lahir.
   */
  async upsertOnLogin(memberRef: string, opts: UpsertMemberOptions = {}): Promise<UpsertMemberResult> {
    const hash = this.hash(memberRef);
    const age = opts.ageConfirmed === true;
    const source = age ? (opts.ageSource ?? 'app') : null;
    const cfg = age && source === 'pos' ? (opts.posConfigId ?? null) : null;
    const ins = await this.db.query<{ id: string }>(
      `INSERT INTO platform.member (id, member_hash, member_ref_enc, age_confirmed_at, age_confirmed_source, age_confirmed_pos_config_id)
       VALUES ($1, $2, $3, CASE WHEN $4::boolean THEN now() END, $5, $6)
       ON CONFLICT (member_hash) DO NOTHING
       RETURNING id::text AS id`,
      [randomUUID(), hash, this.cipher.encrypt(memberRef, PII_AAD.MEMBER_REF), age, source, cfg],
    );
    if (ins.rows[0]) return { id: ins.rows[0].id, deleting: false };
    const sel = await this.db.query<{ id: string; deleted_at: Date | string | null }>(
      `SELECT id::text AS id, deleted_at FROM platform.member WHERE member_hash = $1`,
      [hash],
    );
    const row = sel.rows[0];
    if (!row) throw new Error('member hilang setelah konflik upsert');
    if (age && row.deleted_at === null) {
      await this.db.query(
        `UPDATE platform.member SET age_confirmed_at = now(), age_confirmed_source = $2, age_confirmed_pos_config_id = $3
          WHERE id = $1 AND age_confirmed_at IS NULL`,
        [row.id, source, cfg],
      );
    }
    return { id: row.id, deleting: row.deleted_at !== null };
  }

  async findActive(id: string, q: Queryable = this.db): Promise<ActiveMember | null> {
    const res = await q.query<{ id: string; member_hash: string }>(
      `SELECT id::text AS id, member_hash FROM platform.member WHERE id = $1 AND deleted_at IS NULL`,
      [id],
    );
    const row = res.rows[0];
    return row ? { id: row.id, memberHash: row.member_hash } : null;
  }

  /** Nomor HP member (plaintext, hanya di memori — jangan di-log tanpa mask). `null` bila member tidak aktif. */
  async memberRef(id: string): Promise<string | null> {
    const res = await this.db.query<{ member_hash: string; member_ref_enc: Buffer }>(
      `SELECT member_hash, member_ref_enc FROM platform.member WHERE id = $1 AND deleted_at IS NULL`,
      [id],
    );
    const row = res.rows[0];
    if (!row) return null;
    const ref = this.cipher.decrypt(row.member_ref_enc, PII_AAD.MEMBER_REF);
    // Integritas: nomor hasil dekripsi harus konsisten dengan member_hash baris
    if (this.hash(ref) !== row.member_hash) throw new Error('member_ref_enc tidak konsisten dengan member_hash');
    return ref;
  }
}
