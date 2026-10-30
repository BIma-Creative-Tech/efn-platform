import { Inject, Injectable, Logger } from '@nestjs/common';
import { randomBytes } from 'node:crypto';
import { AppConfig } from '../../config/app-config';
import { DB, Db } from '../../common/db/db';
import { ApiError, Errors } from '../../common/errors/api-error';
import { AuthUser } from '../../common/security/access-token.service';
import { MGM_DEPTH_MAX, normalizeReferralCode, referralCodeFrom, wibMonthRange } from './mgm-policy';
import { MGM_STORE, MgmMemberRow, MgmStore } from './mgm.repository';

export const MgmErrors = {
  unavailable: (): ApiError => Errors.serviceUnavailable('mgm_unavailable'),
  codeNotFound: (): ApiError => ApiError.of(404, 'referral_code_not_found', 'Kode referral tidak ditemukan.'),
  alreadyLinked: (): ApiError => Errors.conflict('referral_already_linked', 'Akun ini sudah terhubung dengan kode referral.'),
  self: (): ApiError => Errors.conflict('referral_self', 'Kode referral milik sendiri tidak bisa dipakai.'),
  cycle: (): ApiError => Errors.conflict('referral_cycle', 'Kode referral ini tidak bisa dipakai untuk akun Anda.'),
  windowClosed: (): ApiError => ApiError.of(422, 'referral_window_closed', 'Batas waktu memasukkan kode referral sudah lewat.'),
};

export interface ReferralView {
  code: string;
  referred: boolean;
  linked_at?: string;
  claim_until?: string;
  network: { level1: number; level2: number; level3_plus: number };
  rewards: { pending_points: number; settled_points_month: number };
}

/** Percobaan membuat kode unik (31^8 ≈ 8,5 × 10^11 kombinasi; bentrok praktis tidak terjadi). */
const CODE_ATTEMPTS = 5;

/**
 * Relasi referral (docs/04 §20.2). Kode dibuat saat pertama diminta; klaim = satu referrer permanen per member, dalam
 * jendela `MGM_CLAIM_WINDOW_DAYS` sejak akun dibuat, tanpa diri sendiri/siklus (graf diserialkan advisory lock).
 * Respons hanya hitungan jaringan (tanpa identitas downline/upline).
 */
@Injectable()
export class MgmReferralService {
  private readonly logger = new Logger(MgmReferralService.name);
  now: () => number = () => Date.now();
  random: (n: number) => Buffer = (n) => randomBytes(n);

  constructor(
    private readonly config: AppConfig,
    @Inject(DB) private readonly db: Db,
    @Inject(MGM_STORE) private readonly store: MgmStore,
  ) {}

  requireEnabled(): void {
    if (!this.config.mgmEnabled) throw MgmErrors.unavailable();
  }

  async view(user: AuthUser): Promise<ReferralView> {
    this.requireEnabled();
    const at = new Date(this.now());
    const created = await this.store.memberCreatedAt(this.db, user.memberId);
    if (!created) throw Errors.unauthorized();
    const me = await this.ensureMember(user.memberHash, at);
    const counts = await this.store.networkCounts(this.db, user.memberHash, MGM_DEPTH_MAX);
    const month = wibMonthRange(at);
    const sums = await this.store.rewardSums(this.db, user.memberHash, month.start, month.end);
    const claimUntil = new Date(created.getTime() + this.config.mgmClaimWindowDays * 86_400_000);
    return {
      code: me.code,
      referred: me.linked_at !== null,
      ...(me.linked_at ? { linked_at: me.linked_at.toISOString() } : at < claimUntil ? { claim_until: claimUntil.toISOString() } : {}),
      network: { level1: counts[0] ?? 0, level2: counts[1] ?? 0, level3_plus: counts.slice(2).reduce((a, b) => a + b, 0) },
      rewards: { pending_points: sums.pending, settled_points_month: sums.settledMonth },
    };
  }

  async claim(user: AuthUser, rawCode: unknown): Promise<{ status: 'linked'; linked_at: string }> {
    this.requireEnabled();
    const code = normalizeReferralCode(rawCode);
    if (!code) throw Errors.invalidRequest();
    const at = new Date(this.now());
    const created = await this.store.memberCreatedAt(this.db, user.memberId);
    if (!created) throw Errors.unauthorized();
    if (at.getTime() >= created.getTime() + this.config.mgmClaimWindowDays * 86_400_000) throw MgmErrors.windowClosed();
    await this.ensureMember(user.memberHash, at);
    const linkedAt = await this.db.tx(async (q) => {
      await this.store.lockGraph(q);
      const me = await this.store.memberByHash(q, user.memberHash);
      if (!me) throw new Error('mgm_member hilang setelah dibuat');
      if (me.linked_at !== null) throw MgmErrors.alreadyLinked();
      const owner = await this.store.memberByCode(q, code);
      if (!owner || !(await this.store.activeMemberId(q, owner.member_hash))) throw MgmErrors.codeNotFound();
      if (owner.member_hash === user.memberHash) throw MgmErrors.self();
      // siklus: pemanggil tidak boleh berada di rantai upline pemilik kode
      let cur: MgmMemberRow | null = owner;
      for (let i = 0; cur && cur.referrer_hash && i < 10_000; i += 1) {
        if (cur.referrer_hash === user.memberHash) throw MgmErrors.cycle();
        cur = await this.store.memberByHash(q, cur.referrer_hash);
      }
      if (!(await this.store.link(q, user.memberHash, owner.member_hash, at))) throw MgmErrors.alreadyLinked();
      return at;
    });
    this.logger.log('EFN_MGM_LINK {"status":"linked"}');
    return { status: 'linked', linked_at: linkedAt.toISOString() };
  }

  /** Baris `mgm_member` milik `hash` (dibuat dengan kode acak unik bila belum ada). */
  private async ensureMember(hash: string, at: Date): Promise<MgmMemberRow> {
    for (let i = 0; i < CODE_ATTEMPTS; i += 1) {
      const existing = await this.store.memberByHash(this.db, hash);
      if (existing) return existing;
      await this.store.insertMember(this.db, hash, referralCodeFrom(this.random), at);
    }
    const row = await this.store.memberByHash(this.db, hash);
    if (!row) throw new Error('gagal membuat kode referral unik');
    return row;
  }
}
