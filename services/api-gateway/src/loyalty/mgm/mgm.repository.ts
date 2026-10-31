import { Queryable } from '../../common/db/db';
import { CompanyCode } from '../loyalty.service';
import { MgmPolicyParams } from './mgm-policy';

/**
 * Akses data engine MGM (docs/04 §20, migrasi `17-platform-g31.sql`). SQL parametris saja; semua metode bertanda `q`
 * berjalan di transaksi pemanggil. Test memakai `MemoryMgmStore` (testing/mgm-fakes.ts) yang meniru semantik ini
 * (UNIQUE, ON CONFLICT, filter status).
 */
export const MGM_STORE = Symbol('MGM_STORE');
export type MgmSourceKind = 'pos' | 'online';
export type MgmRewardStatus = 'pending' | 'settled' | 'cancelled' | 'capped';

export interface MgmMemberRow {
  member_hash: string;
  code: string;
  referrer_hash: string | null;
  linked_at: Date | null;
  created_at: Date;
}

export interface MgmSourceRow {
  source_kind: MgmSourceKind;
  source_ref: string;
  company_code: CompanyCode;
  buyer_hash: string;
  basis_idr: number;
  occurred_at: Date;
  processed_at: Date | null;
}

export interface MgmRewardRow {
  id: string;
  source_kind: MgmSourceKind;
  source_ref: string;
  level: number;
  referrer_hash: string;
  company_code: CompanyCode;
  points: number;
  reversed_points: number;
  settled_points: number;
  clawed_points: number;
  status: MgmRewardStatus;
  status_reason: string | null;
  policy_version: number;
  point_value_idr: number;
  cap_points: number;
  occurred_at: Date;
  settle_after: Date;
  settled_at: Date | null;
}

export interface MgmReversalRow {
  source_kind: MgmSourceKind;
  source_ref: string;
  reverse_key: string;
  ratio: number;
}

export interface StoredMgmPolicy {
  version: number;
  params: unknown;
}

export interface MgmStore {
  // ── kebijakan ──
  policySha(q: Queryable, company: CompanyCode, version: number): Promise<string | null>;
  insertPolicy(
    q: Queryable,
    p: { company: CompanyCode; version: number; effectiveFrom: Date; approvedByUid: number; params: MgmPolicyParams; sha: string; receivedAt: Date },
  ): Promise<boolean>;
  activePolicy(q: Queryable, company: CompanyCode, at: Date): Promise<StoredMgmPolicy | null>;
  // ── relasi ──
  memberByHash(q: Queryable, hash: string): Promise<MgmMemberRow | null>;
  memberByCode(q: Queryable, code: string): Promise<MgmMemberRow | null>;
  /** INSERT … ON CONFLICT DO NOTHING; false = member sudah punya baris ATAU kode bentrok (pemanggil membaca ulang). */
  insertMember(q: Queryable, hash: string, code: string, at: Date): Promise<boolean>;
  /** Tautkan bila belum bertaut (`linked_at IS NULL`); false = sudah bertaut. */
  link(q: Queryable, hash: string, referrerHash: string, at: Date): Promise<boolean>;
  networkCounts(q: Queryable, hash: string, maxDepth: number): Promise<number[]>;
  /** Kunci graf referral (advisory xact lock tunggal) — serialisasi klaim agar siklus tidak terbentuk balapan. */
  lockGraph(q: Queryable): Promise<void>;
  /** id member aktif (baris `platform.member` tanpa `deleted_at`) untuk hash; null = tidak aktif / dihapus. */
  activeMemberId(q: Queryable, hash: string): Promise<string | null>;
  memberCreatedAt(q: Queryable, memberId: string): Promise<Date | null>;
  /** §20.11: consent `marketing` terbaru member aktif = granted (sumber sama dengan ConsentService). */
  hasMarketingConsent(q: Queryable, hash: string): Promise<boolean>;
  // ── akrual ──
  insertSource(q: Queryable, s: Omit<MgmSourceRow, 'processed_at'>): Promise<boolean>;
  source(q: Queryable, kind: MgmSourceKind, ref: string, forUpdate: boolean): Promise<MgmSourceRow | null>;
  markSourceProcessed(q: Queryable, kind: MgmSourceKind, ref: string, at: Date): Promise<void>;
  insertReward(q: Queryable, r: Omit<MgmRewardRow, 'reversed_points' | 'settled_points' | 'clawed_points' | 'status' | 'status_reason' | 'settled_at'>): Promise<boolean>;
  // ── retur ──
  insertReversal(q: Queryable, r: MgmReversalRow): Promise<boolean>;
  /** (reward, reversal) yang belum diterapkan, urut dibuat — reward dikunci FOR UPDATE SKIP LOCKED. */
  pendingReversals(q: Queryable, limit: number): Promise<{ reward: MgmRewardRow; reversal: MgmReversalRow }[]>;
  markReversalApplied(q: Queryable, rewardId: string, reverseKey: string, at: Date): Promise<boolean>;
  // ── settle ──
  dueRewards(q: Queryable, at: Date, limit: number): Promise<MgmRewardRow[]>;
  settledInMonth(q: Queryable, referrerHash: string, company: CompanyCode, start: Date, end: Date): Promise<number>;
  updateReward(
    q: Queryable,
    id: string,
    patch: Partial<Pick<MgmRewardRow, 'reversed_points' | 'settled_points' | 'clawed_points' | 'status' | 'status_reason' | 'settled_at'>>,
    at: Date,
  ): Promise<void>;
  rewardSums(q: Queryable, referrerHash: string, monthStart: Date, monthEnd: Date): Promise<{ pending: number; settledMonth: number }>;
  // ── erasure ──
  erase(q: Queryable, hash: string, tombstone: string, at: Date): Promise<void>;
  exportFor(q: Queryable, hash: string): Promise<{ code: string; linked_at: Date | null } | null>;
}

const toDate = (v: Date | string | null): Date | null => (v === null ? null : v instanceof Date ? v : new Date(v));
const MEMBER_COLS = 'member_hash, code, referrer_hash, linked_at, created_at';
const REWARD_COLS = `id::text AS id, source_kind, source_ref, level, referrer_hash, company_code, points, reversed_points,
  settled_points, clawed_points, status, status_reason, policy_version, point_value_idr, cap_points, occurred_at,
  settle_after, settled_at`;
/** Sama dengan REWARD_COLS, berkualifikasi alias `w` (query JOIN dengan mgm_reversal). */
const REWARD_COLS_W = `w.id::text AS id, w.source_kind, w.source_ref, w.level, w.referrer_hash, w.company_code, w.points,
  w.reversed_points, w.settled_points, w.clawed_points, w.status, w.status_reason, w.policy_version, w.point_value_idr,
  w.cap_points, w.occurred_at, w.settle_after, w.settled_at`;
/** Kelas advisory lock graf referral (int4 pasangan, ruang terpisah dari kunci member 'EFNM'). */
export const MGM_GRAPH_LOCK = [0x45464e47, 1] as const; // 'EFNG'

function memberRow(r: Record<string, unknown>): MgmMemberRow {
  return {
    member_hash: String(r.member_hash),
    code: String(r.code),
    referrer_hash: r.referrer_hash === null || r.referrer_hash === undefined ? null : String(r.referrer_hash),
    linked_at: toDate((r.linked_at as Date | string | null) ?? null),
    created_at: toDate(r.created_at as Date | string) as Date,
  };
}

function rewardRow(r: Record<string, unknown>): MgmRewardRow {
  return {
    id: String(r.id),
    source_kind: r.source_kind as MgmSourceKind,
    source_ref: String(r.source_ref),
    level: Number(r.level),
    referrer_hash: String(r.referrer_hash),
    company_code: r.company_code as CompanyCode,
    points: Number(r.points),
    reversed_points: Number(r.reversed_points),
    settled_points: Number(r.settled_points),
    clawed_points: Number(r.clawed_points),
    status: r.status as MgmRewardStatus,
    status_reason: (r.status_reason as string | null) ?? null,
    policy_version: Number(r.policy_version),
    point_value_idr: Number(r.point_value_idr),
    cap_points: Number(r.cap_points),
    occurred_at: toDate(r.occurred_at as Date | string) as Date,
    settle_after: toDate(r.settle_after as Date | string) as Date,
    settled_at: toDate((r.settled_at as Date | string | null) ?? null),
  };
}

export class PgMgmStore implements MgmStore {
  async policySha(q: Queryable, company: CompanyCode, version: number): Promise<string | null> {
    const r = await q.query<{ body_sha256: string }>(`SELECT body_sha256 FROM platform.mgm_policy WHERE company_code = $1 AND version = $2`, [company, version]);
    return r.rows[0]?.body_sha256 ?? null;
  }

  async insertPolicy(
    q: Queryable,
    p: { company: CompanyCode; version: number; effectiveFrom: Date; approvedByUid: number; params: MgmPolicyParams; sha: string; receivedAt: Date },
  ): Promise<boolean> {
    const r = await q.query(
      `INSERT INTO platform.mgm_policy (company_code, version, effective_from, approved_by_uid, params, body_sha256, received_at)
       VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7)
       ON CONFLICT (company_code, version) DO NOTHING`,
      [p.company, p.version, p.effectiveFrom.toISOString(), p.approvedByUid, JSON.stringify(p.params), p.sha, p.receivedAt.toISOString()],
    );
    return (r.rowCount ?? 0) > 0;
  }

  async activePolicy(q: Queryable, company: CompanyCode, at: Date): Promise<StoredMgmPolicy | null> {
    const r = await q.query<{ version: number | string; params: unknown }>(
      `SELECT version, params FROM platform.mgm_policy
        WHERE company_code = $1 AND GREATEST(effective_from, received_at) <= $2
        ORDER BY version DESC LIMIT 1`,
      [company, at.toISOString()],
    );
    const row = r.rows[0];
    if (!row) return null;
    return { version: Number(row.version), params: typeof row.params === 'string' ? JSON.parse(row.params) : row.params };
  }

  async memberByHash(q: Queryable, hash: string): Promise<MgmMemberRow | null> {
    const r = await q.query(`SELECT ${MEMBER_COLS} FROM platform.mgm_member WHERE member_hash = $1`, [hash]);
    return r.rows[0] ? memberRow(r.rows[0]) : null;
  }

  async memberByCode(q: Queryable, code: string): Promise<MgmMemberRow | null> {
    const r = await q.query(`SELECT ${MEMBER_COLS} FROM platform.mgm_member WHERE code = $1`, [code]);
    return r.rows[0] ? memberRow(r.rows[0]) : null;
  }

  async insertMember(q: Queryable, hash: string, code: string, at: Date): Promise<boolean> {
    const r = await q.query(
      `INSERT INTO platform.mgm_member (member_hash, code, created_at) VALUES ($1, $2, $3) ON CONFLICT DO NOTHING`,
      [hash, code, at.toISOString()],
    );
    return (r.rowCount ?? 0) > 0;
  }

  async link(q: Queryable, hash: string, referrerHash: string, at: Date): Promise<boolean> {
    const r = await q.query(
      `UPDATE platform.mgm_member SET referrer_hash = $2, linked_at = $3 WHERE member_hash = $1 AND linked_at IS NULL`,
      [hash, referrerHash, at.toISOString()],
    );
    return (r.rowCount ?? 0) > 0;
  }

  async networkCounts(q: Queryable, hash: string, maxDepth: number): Promise<number[]> {
    const r = await q.query<{ depth: number | string; n: number | string }>(
      `WITH RECURSIVE down(member_hash, depth) AS (
         SELECT member_hash, 1 FROM platform.mgm_member WHERE referrer_hash = $1
         UNION ALL
         SELECT m.member_hash, d.depth + 1 FROM platform.mgm_member m JOIN down d ON m.referrer_hash = d.member_hash
          WHERE d.depth < $2
       )
       SELECT depth, COUNT(*)::bigint AS n FROM down GROUP BY depth ORDER BY depth`,
      [hash, maxDepth],
    );
    const out = new Array<number>(maxDepth).fill(0);
    for (const row of r.rows) out[Number(row.depth) - 1] = Number(row.n);
    return out;
  }

  async lockGraph(q: Queryable): Promise<void> {
    await q.query('SELECT pg_advisory_xact_lock($1::int4, $2::int4)', [MGM_GRAPH_LOCK[0], MGM_GRAPH_LOCK[1]]);
  }

  async activeMemberId(q: Queryable, hash: string): Promise<string | null> {
    const r = await q.query<{ id: string }>(`SELECT id::text AS id FROM platform.member WHERE member_hash = $1 AND deleted_at IS NULL`, [hash]);
    return r.rows[0]?.id ?? null;
  }

  async hasMarketingConsent(q: Queryable, hash: string): Promise<boolean> {
    const r = await q.query<{ ok: boolean }>(
      `SELECT EXISTS (
         SELECT 1 FROM platform.member m
          WHERE m.member_hash = $1 AND m.deleted_at IS NULL
            AND (SELECT c.granted FROM platform.consent c
                  WHERE c.member_id = m.id AND c.purpose = 'marketing'
                  ORDER BY c.created_at DESC, c.id DESC LIMIT 1) IS TRUE) AS ok`,
      [hash],
    );
    return r.rows[0]?.ok === true;
  }

  async memberCreatedAt(q: Queryable, memberId: string): Promise<Date | null> {
    const r = await q.query<{ created_at: Date | string }>(`SELECT created_at FROM platform.member WHERE id = $1 AND deleted_at IS NULL`, [memberId]);
    return r.rows[0] ? toDate(r.rows[0].created_at) : null;
  }

  async insertSource(q: Queryable, s: Omit<MgmSourceRow, 'processed_at'>): Promise<boolean> {
    const r = await q.query(
      `INSERT INTO platform.mgm_source (source_kind, source_ref, company_code, buyer_hash, basis_idr, occurred_at)
       VALUES ($1, $2, $3, $4, $5, $6) ON CONFLICT (source_kind, source_ref) DO NOTHING`,
      [s.source_kind, s.source_ref, s.company_code, s.buyer_hash, s.basis_idr, s.occurred_at.toISOString()],
    );
    return (r.rowCount ?? 0) > 0;
  }

  async source(q: Queryable, kind: MgmSourceKind, ref: string, forUpdate: boolean): Promise<MgmSourceRow | null> {
    const r = await q.query<Record<string, unknown>>(
      `SELECT source_kind, source_ref, company_code, buyer_hash, basis_idr, occurred_at, processed_at
         FROM platform.mgm_source WHERE source_kind = $1 AND source_ref = $2${forUpdate ? ' FOR UPDATE' : ''}`,
      [kind, ref],
    );
    const x = r.rows[0];
    if (!x) return null;
    return {
      source_kind: x.source_kind as MgmSourceKind,
      source_ref: String(x.source_ref),
      company_code: x.company_code as CompanyCode,
      buyer_hash: String(x.buyer_hash),
      basis_idr: Number(x.basis_idr),
      occurred_at: toDate(x.occurred_at as Date | string) as Date,
      processed_at: toDate((x.processed_at as Date | string | null) ?? null),
    };
  }

  async markSourceProcessed(q: Queryable, kind: MgmSourceKind, ref: string, at: Date): Promise<void> {
    await q.query(`UPDATE platform.mgm_source SET processed_at = $3 WHERE source_kind = $1 AND source_ref = $2 AND processed_at IS NULL`, [kind, ref, at.toISOString()]);
  }

  async insertReward(
    q: Queryable,
    r: Omit<MgmRewardRow, 'reversed_points' | 'settled_points' | 'clawed_points' | 'status' | 'status_reason' | 'settled_at'>,
  ): Promise<boolean> {
    const res = await q.query(
      `INSERT INTO platform.mgm_reward
         (id, source_kind, source_ref, level, referrer_hash, company_code, points, policy_version, point_value_idr, cap_points,
          occurred_at, settle_after)
       VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
       ON CONFLICT (source_kind, source_ref, level) DO NOTHING`,
      [
        r.id,
        r.source_kind,
        r.source_ref,
        r.level,
        r.referrer_hash,
        r.company_code,
        r.points,
        r.policy_version,
        r.point_value_idr,
        r.cap_points,
        r.occurred_at.toISOString(),
        r.settle_after.toISOString(),
      ],
    );
    return (res.rowCount ?? 0) > 0;
  }

  async insertReversal(q: Queryable, r: MgmReversalRow): Promise<boolean> {
    const res = await q.query(
      `INSERT INTO platform.mgm_reversal (source_kind, source_ref, reverse_key, ratio) VALUES ($1, $2, $3, $4)
       ON CONFLICT (source_kind, source_ref, reverse_key) DO NOTHING`,
      [r.source_kind, r.source_ref, r.reverse_key, r.ratio],
    );
    return (res.rowCount ?? 0) > 0;
  }

  async pendingReversals(q: Queryable, limit: number): Promise<{ reward: MgmRewardRow; reversal: MgmReversalRow }[]> {
    const r = await q.query<Record<string, unknown>>(
      `SELECT ${REWARD_COLS_W}, v.reverse_key, v.ratio
         FROM platform.mgm_reversal v
         JOIN platform.mgm_reward w ON w.source_kind = v.source_kind AND w.source_ref = v.source_ref
        WHERE NOT EXISTS (SELECT 1 FROM platform.mgm_reversal_applied a WHERE a.reward_id = w.id AND a.reverse_key = v.reverse_key)
          AND w.status IN ('pending', 'settled')
        ORDER BY v.created_at, w.level
        LIMIT $1
        FOR UPDATE OF w SKIP LOCKED`,
      [limit],
    );
    return r.rows.map((x) => ({
      reward: rewardRow(x),
      reversal: { source_kind: x.source_kind as MgmSourceKind, source_ref: String(x.source_ref), reverse_key: String(x.reverse_key), ratio: Number(x.ratio) },
    }));
  }

  async markReversalApplied(q: Queryable, rewardId: string, reverseKey: string, at: Date): Promise<boolean> {
    const r = await q.query(
      `INSERT INTO platform.mgm_reversal_applied (reward_id, reverse_key, applied_at) VALUES ($1, $2, $3) ON CONFLICT DO NOTHING`,
      [rewardId, reverseKey, at.toISOString()],
    );
    return (r.rowCount ?? 0) > 0;
  }

  async dueRewards(q: Queryable, at: Date, limit: number): Promise<MgmRewardRow[]> {
    const r = await q.query<Record<string, unknown>>(
      `SELECT ${REWARD_COLS} FROM platform.mgm_reward w
        WHERE w.status = 'pending' AND w.settle_after <= $1
          AND NOT EXISTS (
            SELECT 1 FROM platform.mgm_reversal v
             WHERE v.source_kind = w.source_kind AND v.source_ref = w.source_ref
               AND NOT EXISTS (SELECT 1 FROM platform.mgm_reversal_applied a WHERE a.reward_id = w.id AND a.reverse_key = v.reverse_key))
        ORDER BY w.settle_after, w.id
        LIMIT $2
        FOR UPDATE SKIP LOCKED`,
      [at.toISOString(), limit],
    );
    return r.rows.map(rewardRow);
  }

  async settledInMonth(q: Queryable, referrerHash: string, company: CompanyCode, start: Date, end: Date): Promise<number> {
    const r = await q.query<{ settled: string | number | null }>(
      `SELECT COALESCE(SUM(settled_points), 0)::bigint AS settled FROM platform.mgm_reward
        WHERE referrer_hash = $1 AND company_code = $2 AND occurred_at >= $3 AND occurred_at < $4 AND status = 'settled'`,
      [referrerHash, company, start.toISOString(), end.toISOString()],
    );
    return Number(r.rows[0]?.settled ?? 0);
  }

  async updateReward(
    q: Queryable,
    id: string,
    patch: Partial<Pick<MgmRewardRow, 'reversed_points' | 'settled_points' | 'clawed_points' | 'status' | 'status_reason' | 'settled_at'>>,
    at: Date,
  ): Promise<void> {
    const cols = Object.keys(patch) as (keyof typeof patch)[];
    if (cols.length === 0) return;
    // nama kolom dari allowlist tipe (bukan input pengguna); nilai tetap parametris
    const allowed = new Set(['reversed_points', 'settled_points', 'clawed_points', 'status', 'status_reason', 'settled_at']);
    if (cols.some((c) => !allowed.has(c))) throw new Error('kolom mgm_reward tidak diizinkan');
    const sets = cols.map((c, i) => `${c} = $${i + 3}`);
    const values = cols.map((c) => {
      const v = patch[c];
      return v instanceof Date ? v.toISOString() : v;
    });
    await q.query(`UPDATE platform.mgm_reward SET updated_at = $2, ${sets.join(', ')} WHERE id = $1`, [id, at.toISOString(), ...values]);
  }

  async rewardSums(q: Queryable, referrerHash: string, monthStart: Date, monthEnd: Date): Promise<{ pending: number; settledMonth: number }> {
    const r = await q.query<{ pending: string | number | null; settled_month: string | number | null }>(
      `SELECT COALESCE(SUM(points - reversed_points) FILTER (WHERE status = 'pending'), 0)::bigint AS pending,
              COALESCE(SUM(settled_points - clawed_points) FILTER (WHERE status = 'settled' AND settled_at >= $2 AND settled_at < $3), 0)::bigint
                AS settled_month
         FROM platform.mgm_reward WHERE referrer_hash = $1`,
      [referrerHash, monthStart.toISOString(), monthEnd.toISOString()],
    );
    return { pending: Number(r.rows[0]?.pending ?? 0), settledMonth: Number(r.rows[0]?.settled_month ?? 0) };
  }

  async erase(q: Queryable, hash: string, tombstone: string, at: Date): Promise<void> {
    // §20.7: reward pending subjek sebagai referrer dibatalkan; hash diganti tombstone di reward & sumber; kode/relasi dihapus
    await q.query(
      `UPDATE platform.mgm_reward SET status = 'cancelled', status_reason = 'referrer_erased', updated_at = $2
        WHERE referrer_hash = $1 AND status = 'pending'`,
      [hash, at.toISOString()],
    );
    await q.query(`UPDATE platform.mgm_reward SET referrer_hash = $2 WHERE referrer_hash = $1`, [hash, tombstone]);
    await q.query(`UPDATE platform.mgm_source SET buyer_hash = $2 WHERE buyer_hash = $1`, [hash, tombstone]);
    // downline kehilangan upline: tautan tetap tercatat (linked_at) tanpa referrer → tidak bisa diganti, rantai berhenti
    await q.query(`UPDATE platform.mgm_member SET referrer_hash = NULL WHERE referrer_hash = $1`, [hash]);
    await q.query(`DELETE FROM platform.mgm_member WHERE member_hash = $1`, [hash]);
  }

  async exportFor(q: Queryable, hash: string): Promise<{ code: string; linked_at: Date | null } | null> {
    const m = await this.memberByHash(q, hash);
    return m ? { code: m.code, linked_at: m.linked_at } : null;
  }
}
