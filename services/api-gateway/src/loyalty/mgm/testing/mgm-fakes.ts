/**
 * Fake in-memory `MgmStore` (bukan runtime) — meniru semantik `PgMgmStore` (UNIQUE/ON CONFLICT, filter status, tautan
 * hanya sekali, FOR UPDATE diabaikan karena test berjalan serial). Member aktif = set `activeMembers` (hash → id).
 */
import { Queryable } from '../../../common/db/db';
import { CompanyCode } from '../../loyalty.service';
import { MgmPolicyParams } from '../mgm-policy';
import { MgmMemberRow, MgmReversalRow, MgmRewardRow, MgmSourceKind, MgmSourceRow, MgmStore, StoredMgmPolicy } from '../mgm.repository';

interface PolicyRec {
  company: CompanyCode;
  version: number;
  effectiveFrom: Date;
  receivedAt: Date;
  params: MgmPolicyParams;
  sha: string;
}

export class MemoryMgmStore implements MgmStore {
  readonly policies: PolicyRec[] = [];
  readonly members = new Map<string, MgmMemberRow>();
  readonly sources = new Map<string, MgmSourceRow>();
  readonly rewards = new Map<string, MgmRewardRow>();
  readonly reversals: (MgmReversalRow & { created: number })[] = [];
  readonly applied = new Set<string>();
  /** hash → member.id aktif. */
  readonly activeMembers = new Map<string, string>();
  /** member.id → created_at akun. */
  readonly memberCreated = new Map<string, Date>();
  graphLocks = 0;
  private seq = 0;

  private sk(kind: string, ref: string): string {
    return `${kind}|${ref}`;
  }

  async policySha(_q: Queryable, company: CompanyCode, version: number): Promise<string | null> {
    return this.policies.find((p) => p.company === company && p.version === version)?.sha ?? null;
  }
  async insertPolicy(
    _q: Queryable,
    p: { company: CompanyCode; version: number; effectiveFrom: Date; approvedByUid: number; params: MgmPolicyParams; sha: string; receivedAt: Date },
  ): Promise<boolean> {
    if (this.policies.some((x) => x.company === p.company && x.version === p.version)) return false;
    this.policies.push({ company: p.company, version: p.version, effectiveFrom: p.effectiveFrom, receivedAt: p.receivedAt, params: p.params, sha: p.sha });
    return true;
  }
  async activePolicy(_q: Queryable, company: CompanyCode, at: Date): Promise<StoredMgmPolicy | null> {
    const ok = this.policies
      .filter((p) => p.company === company && Math.max(p.effectiveFrom.getTime(), p.receivedAt.getTime()) <= at.getTime())
      .sort((a, b) => b.version - a.version)[0];
    return ok ? { version: ok.version, params: JSON.parse(JSON.stringify(ok.params)) } : null;
  }

  async memberByHash(_q: Queryable, hash: string): Promise<MgmMemberRow | null> {
    const m = this.members.get(hash);
    return m ? { ...m } : null;
  }
  async memberByCode(_q: Queryable, code: string): Promise<MgmMemberRow | null> {
    const m = [...this.members.values()].find((x) => x.code === code);
    return m ? { ...m } : null;
  }
  async insertMember(_q: Queryable, hash: string, code: string, at: Date): Promise<boolean> {
    if (this.members.has(hash) || [...this.members.values()].some((x) => x.code === code)) return false;
    this.members.set(hash, { member_hash: hash, code, referrer_hash: null, linked_at: null, created_at: at });
    return true;
  }
  async link(_q: Queryable, hash: string, referrerHash: string, at: Date): Promise<boolean> {
    const m = this.members.get(hash);
    if (!m || m.linked_at !== null) return false;
    m.referrer_hash = referrerHash;
    m.linked_at = at;
    return true;
  }
  async networkCounts(_q: Queryable, hash: string, maxDepth: number): Promise<number[]> {
    const out = new Array<number>(maxDepth).fill(0);
    let frontier = [hash];
    for (let d = 0; d < maxDepth; d += 1) {
      const next = [...this.members.values()].filter((m) => m.referrer_hash !== null && frontier.includes(m.referrer_hash)).map((m) => m.member_hash);
      out[d] = next.length;
      frontier = next;
    }
    return out;
  }
  async lockGraph(): Promise<void> {
    this.graphLocks += 1;
  }
  async activeMemberId(_q: Queryable, hash: string): Promise<string | null> {
    return this.activeMembers.get(hash) ?? null;
  }
  async memberCreatedAt(_q: Queryable, memberId: string): Promise<Date | null> {
    return this.memberCreated.get(memberId) ?? null;
  }

  async insertSource(_q: Queryable, s: Omit<MgmSourceRow, 'processed_at'>): Promise<boolean> {
    const k = this.sk(s.source_kind, s.source_ref);
    if (this.sources.has(k)) return false;
    this.sources.set(k, { ...s, processed_at: null });
    return true;
  }
  async source(_q: Queryable, kind: MgmSourceKind, ref: string): Promise<MgmSourceRow | null> {
    const s = this.sources.get(this.sk(kind, ref));
    return s ? { ...s } : null;
  }
  async markSourceProcessed(_q: Queryable, kind: MgmSourceKind, ref: string, at: Date): Promise<void> {
    const s = this.sources.get(this.sk(kind, ref));
    if (s && s.processed_at === null) s.processed_at = at;
  }
  async insertReward(
    _q: Queryable,
    r: Omit<MgmRewardRow, 'reversed_points' | 'settled_points' | 'clawed_points' | 'status' | 'status_reason' | 'settled_at'>,
  ): Promise<boolean> {
    if ([...this.rewards.values()].some((x) => x.source_kind === r.source_kind && x.source_ref === r.source_ref && x.level === r.level)) return false;
    this.rewards.set(r.id, { ...r, reversed_points: 0, settled_points: 0, clawed_points: 0, status: 'pending', status_reason: null, settled_at: null });
    return true;
  }
  async insertReversal(_q: Queryable, r: MgmReversalRow): Promise<boolean> {
    if (this.reversals.some((x) => x.source_kind === r.source_kind && x.source_ref === r.source_ref && x.reverse_key === r.reverse_key)) return false;
    this.seq += 1;
    this.reversals.push({ ...r, created: this.seq });
    return true;
  }
  async pendingReversals(_q: Queryable, limit: number): Promise<{ reward: MgmRewardRow; reversal: MgmReversalRow }[]> {
    const out: { reward: MgmRewardRow; reversal: MgmReversalRow }[] = [];
    for (const v of [...this.reversals].sort((a, b) => a.created - b.created)) {
      const rows = [...this.rewards.values()]
        .filter((w) => w.source_kind === v.source_kind && w.source_ref === v.source_ref && (w.status === 'pending' || w.status === 'settled'))
        .filter((w) => !this.applied.has(`${w.id}|${v.reverse_key}`))
        .sort((a, b) => a.level - b.level);
      for (const w of rows) out.push({ reward: { ...w }, reversal: { source_kind: v.source_kind, source_ref: v.source_ref, reverse_key: v.reverse_key, ratio: v.ratio } });
    }
    return out.slice(0, limit);
  }
  async markReversalApplied(_q: Queryable, rewardId: string, reverseKey: string): Promise<boolean> {
    const k = `${rewardId}|${reverseKey}`;
    if (this.applied.has(k)) return false;
    this.applied.add(k);
    return true;
  }
  async dueRewards(_q: Queryable, at: Date, limit: number): Promise<MgmRewardRow[]> {
    return [...this.rewards.values()]
      .filter((w) => w.status === 'pending' && w.settle_after.getTime() <= at.getTime())
      .filter((w) => !this.reversals.some((v) => v.source_kind === w.source_kind && v.source_ref === w.source_ref && !this.applied.has(`${w.id}|${v.reverse_key}`)))
      .sort((a, b) => a.settle_after.getTime() - b.settle_after.getTime() || a.id.localeCompare(b.id))
      .slice(0, limit)
      .map((w) => ({ ...w }));
  }
  async settledInMonth(_q: Queryable, referrerHash: string, company: CompanyCode, start: Date, end: Date): Promise<number> {
    return [...this.rewards.values()]
      .filter((w) => w.referrer_hash === referrerHash && w.company_code === company && w.status === 'settled')
      .filter((w) => w.occurred_at.getTime() >= start.getTime() && w.occurred_at.getTime() < end.getTime())
      .reduce((s, w) => s + w.settled_points, 0);
  }
  async updateReward(
    _q: Queryable,
    id: string,
    patch: Partial<Pick<MgmRewardRow, 'reversed_points' | 'settled_points' | 'clawed_points' | 'status' | 'status_reason' | 'settled_at'>>,
  ): Promise<void> {
    const w = this.rewards.get(id);
    if (!w) throw new Error('reward tidak ada');
    Object.assign(w, patch);
    if (w.reversed_points > w.points || w.settled_points > w.points || w.clawed_points > w.settled_points) throw new Error('ck_mgm_reward_amounts');
  }
  async rewardSums(_q: Queryable, referrerHash: string, monthStart: Date, monthEnd: Date): Promise<{ pending: number; settledMonth: number }> {
    let pending = 0;
    let settledMonth = 0;
    for (const w of this.rewards.values()) {
      if (w.referrer_hash !== referrerHash) continue;
      if (w.status === 'pending') pending += w.points - w.reversed_points;
      if (w.status === 'settled' && w.settled_at && w.settled_at >= monthStart && w.settled_at < monthEnd) settledMonth += w.settled_points - w.clawed_points;
    }
    return { pending, settledMonth };
  }
  async erase(_q: Queryable, hash: string, tombstone: string): Promise<void> {
    for (const w of this.rewards.values()) {
      if (w.referrer_hash !== hash) continue;
      if (w.status === 'pending') Object.assign(w, { status: 'cancelled', status_reason: 'referrer_erased' });
      w.referrer_hash = tombstone;
    }
    for (const s of this.sources.values()) if (s.buyer_hash === hash) s.buyer_hash = tombstone;
    for (const m of this.members.values()) if (m.referrer_hash === hash) m.referrer_hash = null;
    this.members.delete(hash);
  }
  async exportFor(_q: Queryable, hash: string): Promise<{ code: string; linked_at: Date | null } | null> {
    const m = this.members.get(hash);
    return m ? { code: m.code, linked_at: m.linked_at } : null;
  }
}
