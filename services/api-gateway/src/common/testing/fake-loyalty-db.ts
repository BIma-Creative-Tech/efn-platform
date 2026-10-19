/**
 * Fake PostgreSQL in-memory untuk unit test loyalty POS (bukan runtime). Mengemulasikan HANYA query yang dipakai
 * `LoyaltyService`, `PosLoyaltyService`, dan `LoyaltyReservationSweeper`:
 * - `platform.loyalty_ledger` dengan UNIQUE(idempotency_key) + ON CONFLICT DO NOTHING;
 * - `platform.loyalty_pos_earn` (PK order_ref, UNIQUE idempotency_key);
 * - `platform.loyalty_reservation` dengan UNIQUE token_jti dan indeks unik parsial order_ref (reserved|committed)
 *   → error `{ code: '23505' }` seperti pg;
 * - `pg_advisory_xact_lock(a, b)` = mutex async per kunci sampai akhir transaksi (`noLocks` mematikannya untuk kontrol
 *   negatif test konkurensi); `pg_try_advisory_xact_lock(k)`;
 * - transaksi dengan undo log (rollback saat fn melempar) tanpa mengganggu transaksi paralel lain;
 * - G21: `issuer_company_code` + `created_at` (jam `now`) ledger, lot (expires_at, penerbit), `platform.loyalty_policy`
 *   (append-only), dan tiga query akrual harian `LoyaltyAccrualService`.
 * Setiap query menunggu satu putaran event loop agar transaksi paralel benar-benar ber-interleave.
 * Query yang tidak dikenal → Error (agar SQL baru tidak lolos diam-diam tanpa diuji).
 */
import { Db, Queryable, QueryResultLike } from '../db/db';

export interface FakeLedgerRow {
  id: number;
  member_ref_hash: string;
  member_ref_enc: Buffer | null;
  company_code: string;
  delta_points: number;
  reason: string;
  source: string;
  source_ref: string | null;
  idempotency_key: string;
  expires_at: string | null;
  /** G21: PT penerbit lot (NULL = baris pra-G21). */
  issuer_company_code?: string | null;
  /** Default DB `now()` = `FakeLoyaltyDb.now()` saat INSERT. */
  created_at?: string;
}

/** G21 `platform.loyalty_policy` (append-only). */
export interface FakePolicyRow {
  id: number;
  company_code: string;
  version: number;
  effective_from: string;
  approved_by_uid: number;
  params: unknown;
  point_value_idr: number | null;
  body_sha256: string;
  received_at: string;
}

export interface FakeReservationRow {
  id: string;
  member_hash: string;
  company_code: string;
  order_ref: string;
  token_jti: string;
  points: number;
  value_idr: number;
  amount_total: number;
  status: 'reserved' | 'committed' | 'released' | 'expired';
  expires_at: string;
  hold_until: string;
  created_at: string;
  updated_at: string;
  committed_at: string | null;
  commit_idempotency_key: string | null;
  order_goods_incl?: number | null;
}

type Undo = () => void;
type Res = QueryResultLike<Record<string, unknown>>;

class UniqueViolation extends Error {
  readonly code = '23505';
  constructor(readonly constraint: string) {
    super(`duplicate key value violates unique constraint "${constraint}"`);
  }
}

const tick = (): Promise<void> => new Promise((r) => setImmediate(r));
const ms = (iso: string): number => new Date(iso).getTime();
const ACTIVE = new Set(['reserved', 'committed']);

export interface FakeEarnRow {
  order_ref: string;
  idempotency_key: string;
  member_hash: string;
  company_code: string;
  eligible_amount: number;
  points_earned: number;
  created_at: string;
  /** G21: versi kebijakan PT yang menghitung earn (null = env). */
  policy_version?: number | null;
}

export class FakeLoyaltyDb implements Db {
  readonly ledger: FakeLedgerRow[] = [];
  readonly earns = new Map<string, FakeEarnRow>();
  /** G18 K-18-6: `platform.loyalty_pos_reverse` (PK refund_order_ref, UNIQUE idempotency_key). */
  readonly reverses = new Map<string, Record<string, unknown>>();
  readonly reservations = new Map<string, FakeReservationRow>();
  /** G21: kebijakan poin (UNIQUE company_code+version; UPDATE/DELETE tidak didukung = append-only). */
  readonly policies: FakePolicyRow[] = [];
  readonly queries: string[] = [];
  /** Jam DB (`now()` / DEFAULT created_at). Test mengikatnya ke FakeClock. */
  now: () => number = () => Date.now();
  /** SEC-65 (`erasureBlock`): member_hash dengan DSAR delete aktif; tag daftar tolak → expires_at ISO. */
  readonly erasureActive = new Set<string>();
  readonly suppression = new Map<string, string>();
  noLocks = false;
  /** G22 §14.D.2: `platform.loyalty_member_activity` (member_hash → jam aktivitas terakhir). */
  readonly activity = new Map<string, { last_activity_at: string; last_kind: string }>();
  /** G22 §14.A.4: order QR yang dikenal BFF (`order_uuid` → `draft`|`paid`|`cancelled`|`closed_unlisted`). */
  readonly qrOrders = new Map<string, string>();
  /** Jumlah transaksi yang di-rollback (untuk asersi). */
  rollbacks = 0;
  private seq = 0;
  private readonly locks = new Map<string, Promise<void>>();

  async query<R = Record<string, unknown>>(text: string, params: readonly unknown[] = []): Promise<QueryResultLike<R>> {
    return (await this.run(text, params, null, [])) as unknown as QueryResultLike<R>;
  }

  async tx<T>(fn: (q: Queryable) => Promise<T>): Promise<T> {
    const undo: Undo[] = [];
    const held: (() => void)[] = [];
    const q: Queryable = {
      query: async <R>(text: string, params: readonly unknown[] = []) =>
        (await this.run(text, params, held, undo)) as unknown as QueryResultLike<R>,
    };
    try {
      return await fn(q);
    } catch (err) {
      for (const u of undo.reverse()) u();
      this.rollbacks += 1;
      throw err;
    } finally {
      for (const release of held) release();
    }
  }

  /** Saldo pada `at` (bantuan asersi). */
  balance(hash: string, at: Date): number {
    return this.ledger
      .filter((r) => r.member_ref_hash === hash && (r.expires_at === null || ms(r.expires_at) > at.getTime()))
      .reduce((s, r) => s + r.delta_points, 0);
  }

  private async lock(key: string, held: (() => void)[] | null): Promise<void> {
    if (this.noLocks) return;
    if (held === null) throw new Error('advisory xact lock di luar transaksi');
    for (;;) {
      const cur = this.locks.get(key);
      if (!cur) break;
      await cur;
    }
    let release!: () => void;
    const p = new Promise<void>((r) => (release = r));
    this.locks.set(key, p);
    held.push(() => {
      this.locks.delete(key);
      release();
    });
  }

  private async run(text: string, p: readonly unknown[], held: (() => void)[] | null, undo: Undo[]): Promise<Res> {
    await tick();
    this.queries.push(text);
    const ok = (rows: Record<string, unknown>[], rowCount = rows.length): Res => ({ rows, rowCount });

    if (text.includes('pg_advisory_xact_lock(')) {
      await this.lock(`${String(p[0])}:${String(p[1])}`, held);
      return ok([{}]);
    }
    if (text.includes('pg_try_advisory_xact_lock(')) {
      const key = `try:${String(p[0])}`;
      if (this.locks.has(key)) return ok([{ ok: false }]);
      await this.lock(key, held);
      return ok([{ ok: true }]);
    }

    // ───── erasure (SEC-65) ─────
    if (text.includes('FROM platform.dsar_request') && text.includes('platform.erasure_suppression')) {
      if (this.erasureActive.has(String(p[0]))) return ok([{ kind: 'erasure_active' }]);
      const exp = this.suppression.get(String(p[1]));
      return ok(exp !== undefined && ms(exp) > ms(String(p[2])) ? [{ kind: 'erased' }] : []);
    }

    if (text.startsWith('SET TRANSACTION')) return ok([]); // snapshot/read-only: fake sudah serial per query

    // ───── G22 §14.D.2 aktivitas member (GREATEST, idempoten) ─────
    if (text.includes('INSERT INTO platform.loyalty_member_activity')) {
      const hash = String(p[0]);
      const at = String(p[1]);
      const prev = this.activity.get(hash);
      const next = !prev || ms(at) >= ms(prev.last_activity_at) ? { last_activity_at: at, last_kind: String(p[2]) } : prev;
      this.activity.set(hash, next);
      undo.push(() => (prev ? this.activity.set(hash, prev) : this.activity.delete(hash)));
      return ok([], 1);
    }
    // ───── G22 §14.D.5 akrual: hangus tidak aktif (entri eksplisit) per penerbit per tanggal ─────
    if (text.includes('AS inactivity_points')) {
      const [from, to, company] = [ms(String(p[0])), ms(String(p[1])), String(p[2])];
      const sum = this.ledger
        .filter((r) => r.reason === 'inactivity_expire' && (r.issuer_company_code ?? r.company_code) === company)
        .filter((r) => ms(this.createdAt(r)) >= from && ms(this.createdAt(r)) < to)
        .reduce((s, r) => s - r.delta_points, 0);
      return ok([{ inactivity_points: String(sum) }]);
    }
    // ───── G22 §14.A.4 reverse earn_channel=qr: status order QR (fnb_submission / sesi) ─────
    if (text.includes('AS qr_known') && text.includes('platform.fnb_')) {
      // r15 K3: `draft` = sesi belum close; `closed_unlisted` = close sudah diproses tetapi order tidak di orders[]
      const s = this.qrOrders.get(String(p[0]));
      return ok(
        s === undefined
          ? []
          : [{ qr_known: true, settled: s === 'paid' || s === 'cancelled', close_processed: s !== 'draft' }],
      );
    }

    // ───── G21 kebijakan poin ─────
    if (text.includes('INSERT INTO platform.loyalty_policy')) {
      const company = String(p[0]);
      const version = Number(p[1]);
      if (this.policies.some((r) => r.company_code === company && r.version === version)) return ok([], 0);
      const row: FakePolicyRow = {
        id: ++this.seq,
        company_code: company,
        version,
        effective_from: String(p[2]),
        approved_by_uid: Number(p[3]),
        params: JSON.parse(String(p[4])),
        point_value_idr: p[5] === null || p[5] === undefined ? null : Number(p[5]),
        body_sha256: String(p[6]),
        received_at: String(p[7]),
      };
      this.policies.push(row);
      undo.push(() => this.policies.splice(this.policies.indexOf(row), 1));
      return ok([{ id: String(row.id) }]);
    }
    if (text.includes('SELECT body_sha256 FROM platform.loyalty_policy WHERE company_code = $1 AND version = $2')) {
      return ok(this.policies.filter((r) => r.company_code === p[0] && r.version === Number(p[1])).map((r) => ({ body_sha256: r.body_sha256 })));
    }
    if (text.includes('FROM platform.loyalty_policy') && text.includes('GREATEST(effective_from, received_at) <= $2')) {
      const at = ms(String(p[1]));
      const rows = this.policies
        .filter((r) => r.company_code === p[0] && Math.max(ms(r.effective_from), ms(r.received_at)) <= at)
        .filter((r) => !text.includes('point_value_idr IS NOT NULL') || r.point_value_idr !== null)
        .sort((a, b) => b.version - a.version)
        .slice(0, 1)
        .map((r) => ({ ...r, effective_from: new Date(r.effective_from), received_at: new Date(r.received_at) }));
      return ok(rows);
    }
    if (/UPDATE platform\.loyalty_policy|DELETE FROM platform\.loyalty_policy/.test(text)) {
      throw new Error('platform.loyalty_policy append-only'); // = trigger DB
    }

    // ───── G21 akrual harian (§13.1 no. 6) ─────
    if (text.includes('AS flow_points')) {
      const [from, to, company] = [ms(String(p[0])), ms(String(p[1])), String(p[2])];
      const groups = new Map<string, { issuer: string; company_code: string; reason: string; credit: boolean; flow_points: number }>();
      for (const r of this.ledger) {
        const created = ms(this.createdAt(r));
        if (created < from || created >= to) continue;
        if (r.expires_at !== null && ms(r.expires_at) <= created) continue; // lahir kedaluwarsa
        const issuer = r.issuer_company_code ?? r.company_code;
        if (r.company_code !== company && issuer !== company) continue;
        const credit = r.delta_points > 0;
        const k = `${issuer}|${r.company_code}|${r.reason}|${credit}`;
        const g = groups.get(k) ?? { issuer, company_code: r.company_code, reason: r.reason, credit, flow_points: 0 };
        g.flow_points += r.delta_points;
        groups.set(k, g);
      }
      return ok([...groups.values()].map((g) => ({ ...g, flow_points: String(g.flow_points) })));
    }
    if (text.includes('AS expired_points')) {
      const [from, to, company] = [ms(String(p[0])), ms(String(p[1])), String(p[2])];
      const sum = this.ledger
        .filter((r) => (r.issuer_company_code ?? r.company_code) === company && r.expires_at !== null)
        .filter((r) => ms(r.expires_at as string) >= from && ms(r.expires_at as string) < to && ms(this.createdAt(r)) < ms(r.expires_at as string))
        .reduce((s, r) => s + r.delta_points, 0);
      return ok([{ expired_points: String(sum) }]);
    }
    if (text.includes('AS closing_points')) {
      const [company, at] = [String(p[0]), ms(String(p[1]))];
      const sum = this.ledger
        .filter((r) => (r.issuer_company_code ?? r.company_code) === company && ms(this.createdAt(r)) < at)
        .filter((r) => r.expires_at === null || ms(r.expires_at) >= at)
        .reduce((s, r) => s + r.delta_points, 0);
      return ok([{ closing_points: String(sum) }]);
    }
    if (text.includes('AS rv') && text.includes('FROM platform.loyalty_reservation')) {
      const rows = [...this.reservations.values()].filter((r) => r.order_ref === p[0] && r.status === 'committed' && r.points > 0);
      return ok([{ rv: String(rows.reduce((s, r) => s + r.value_idr, 0)), rp: String(rows.reduce((s, r) => s + r.points, 0)) }]);
    }

    // ───── ledger ─────
    if (text.includes('INSERT INTO platform.loyalty_ledger')) {
      const key = String(p[7]);
      if (this.ledger.some((r) => r.idempotency_key === key)) return ok([], 0);
      const row: FakeLedgerRow = {
        id: ++this.seq,
        member_ref_hash: String(p[0]),
        member_ref_enc: p[1] as Buffer,
        company_code: String(p[2]),
        delta_points: Number(p[3]),
        reason: String(p[4]),
        source: String(p[5]),
        source_ref: (p[6] as string | null) ?? null,
        idempotency_key: key,
        expires_at: (p[8] as string | null) ?? null,
        issuer_company_code: (p[9] as string | null | undefined) ?? null,
        created_at: new Date(this.now()).toISOString(),
      };
      this.ledger.push(row);
      undo.push(() => this.ledger.splice(this.ledger.indexOf(row), 1));
      return ok([{ id: String(row.id) }]);
    }
    if (text.includes('FROM platform.loyalty_ledger') && text.includes('GROUP BY expires_at')) {
      const live = this.live(String(p[0]), String(p[1]));
      const groups = new Map<string, { expires_at: string | null; issuer: string; remaining: number; first_id: number }>();
      for (const r of live) {
        const issuer = r.issuer_company_code ?? r.company_code;
        const k = `${r.expires_at ?? 'null'}|${issuer}`;
        const g = groups.get(k) ?? { expires_at: r.expires_at, issuer, remaining: 0, first_id: r.id };
        g.remaining += r.delta_points;
        g.first_id = Math.min(g.first_id, r.id);
        groups.set(k, g);
      }
      const rows = [...groups.values()]
        .sort((a, b) => {
          const byExp = a.expires_at === b.expires_at ? 0 : a.expires_at === null ? 1 : b.expires_at === null ? -1 : ms(a.expires_at) - ms(b.expires_at);
          return byExp !== 0 ? byExp : a.first_id - b.first_id;
        })
        .map((g) => ({
          expires_at: g.expires_at === null ? null : new Date(g.expires_at),
          issuer: g.issuer,
          remaining: String(g.remaining),
          first_id: String(g.first_id),
        }));
      return ok(rows);
    }
    if (text.includes('AS balance') && text.includes('FROM platform.loyalty_ledger')) {
      const sum = this.live(String(p[0]), String(p[1])).reduce((s, r) => s + r.delta_points, 0);
      return ok([{ balance: String(sum) }]);
    }
    if (text.includes('starts_with(idempotency_key, $1)')) {
      const prefix = String(p[0]);
      const keys = p[2] as string[];
      return ok(
        this.ledger
          .filter(
            (r) =>
              (r.source === 'pos_reverse' && r.source_ref === p[1] && r.idempotency_key.startsWith(prefix)) ||
              keys.includes(r.idempotency_key),
          )
          .map((r) => this.ledgerOut(r)),
      );
    }
    if (text.includes('FROM platform.loyalty_ledger WHERE idempotency_key = ANY($1::text[])')) {
      const keys = p[0] as string[];
      return ok(this.ledger.filter((r) => keys.includes(r.idempotency_key)).map((r) => this.ledgerOut(r)));
    }
    if (text.includes("WHERE source IN ('pos_earn', 'pos_redeem', 'pos_reverse') AND source_ref = $1")) {
      return ok(
        this.ledger
          .filter((r) => ['pos_earn', 'pos_redeem', 'pos_reverse'].includes(r.source) && r.source_ref === p[0])
          .map((r) => this.ledgerOut(r)),
      );
    }

    // ───── loyalty_pos_earn ─────
    if (text.includes('FROM platform.loyalty_pos_earn WHERE order_ref = $1 OR idempotency_key = $2')) {
      return ok([...this.earns.values()].filter((r) => r.order_ref === p[0] || r.idempotency_key === p[1]).map((r) => ({ ...r })));
    }
    if (text.includes('AS earned') && text.includes('FROM platform.loyalty_pos_earn WHERE member_hash = $1 AND created_at >= $2')) {
      const sum = [...this.earns.values()]
        .filter((r) => r.member_hash === p[0] && ms(r.created_at) >= ms(String(p[1])))
        .reduce((s, r) => s + r.points_earned, 0);
      return ok([{ earned: String(sum) }]);
    }
    if (text.includes('FROM platform.loyalty_pos_earn WHERE order_ref = $1')) {
      const r = this.earns.get(String(p[0]));
      return ok(r ? [{ ...r }] : []);
    }
    if (text.includes('INSERT INTO platform.loyalty_pos_earn')) {
      const row: FakeEarnRow = {
        order_ref: String(p[0]),
        idempotency_key: String(p[1]),
        member_hash: String(p[2]),
        company_code: String(p[3]),
        eligible_amount: Number(p[4]),
        points_earned: Number(p[5]),
        created_at: String(p[6]),
        policy_version: p[7] === null || p[7] === undefined ? null : Number(p[7]),
      };
      if (this.earns.has(row.order_ref) || [...this.earns.values()].some((r) => r.idempotency_key === row.idempotency_key)) {
        throw new UniqueViolation('loyalty_pos_earn_pkey');
      }
      this.earns.set(row.order_ref, row);
      undo.push(() => this.earns.delete(row.order_ref));
      return ok([], 1);
    }
    if (text.includes('DELETE FROM platform.loyalty_pos_earn')) {
      const old = [...this.earns.values()].filter((r) => ms(r.created_at) < ms(String(p[0]))).slice(0, Number(p[1]));
      for (const r of old) {
        this.earns.delete(r.order_ref);
        undo.push(() => this.earns.set(r.order_ref, r));
      }
      return ok([], old.length);
    }

    // ───── reservation ─────
    if (text.includes("SET status = 'expired', updated_at = $2") && text.includes('WHERE order_ref = $1')) {
      let n = 0;
      for (const r of this.reservations.values()) {
        if (r.order_ref === p[0] && r.status === 'reserved' && ms(r.hold_until) <= ms(String(p[1]))) {
          this.setStatus(r, { status: 'expired', updated_at: String(p[1]) }, undo);
          n += 1;
        }
      }
      return ok([], n);
    }
    if (text.includes('WHERE token_jti = $1')) {
      return ok(
        [...this.reservations.values()].filter((r) => r.token_jti === p[0]).map((r) => this.resOut(r)),
      );
    }
    if (text.includes('SELECT 1 AS n FROM platform.loyalty_reservation WHERE order_ref = $1')) {
      return ok([...this.reservations.values()].filter((r) => r.order_ref === p[0] && ACTIVE.has(r.status)).map(() => ({ n: 1 })));
    }
    if (text.includes('AS held')) {
      const held = [...this.reservations.values()]
        .filter((r) => r.member_hash === p[0] && r.status === 'reserved' && ms(r.hold_until) > ms(String(p[1])))
        .reduce((s, r) => s + r.points, 0);
      return ok([{ held: String(held) }]);
    }
    if (text.includes('INSERT INTO platform.loyalty_pos_reverse')) {
      const row = {
        refund_order_ref: String(p[0]),
        original_order_ref: String(p[1]),
        company_code: String(p[2]),
        points_restored_value: Number(p[3]),
        points_restored: Number(p[4]),
        idempotency_key: String(p[5]),
        created_at: String(p[6]),
        refund_ratio: Number(p[7] ?? 0),
        refund_goods_incl: p[8] === null || p[8] === undefined ? null : Number(p[8]),
        restore_approved: p[9] === true,
      };
      if (this.reverses.has(row.refund_order_ref) || [...this.reverses.values()].some((r) => r.idempotency_key === row.idempotency_key)) {
        return ok([], 0);
      }
      this.reverses.set(row.refund_order_ref, row);
      undo.push(() => this.reverses.delete(row.refund_order_ref));
      return ok([], 1);
    }
    if (text.includes('FROM platform.loyalty_pos_reverse WHERE original_order_ref = $1')) {
      const rows = [...this.reverses.values()].filter((r) => r.original_order_ref === p[0] && r.refund_order_ref !== p[1]);
      return ok([
        {
          ratio: rows.reduce((s, r) => s + Number(r.refund_ratio ?? 0), 0),
          restored: String(rows.reduce((s, r) => s + Number(r.points_restored_value), 0)),
          goods: String(rows.reduce((s, r) => s + Number(r.refund_goods_incl ?? 0), 0)),
          goods_missing: String(rows.filter((r) => r.refund_goods_incl === null || r.refund_goods_incl === undefined).length),
        },
      ]);
    }
    if (text.includes('SUM(value_idr), 0)::bigint AS v') && text.includes('FROM platform.loyalty_reservation')) {
      const rows = [...this.reservations.values()].filter((r) => r.order_ref === p[0] && r.status === 'committed');
      return ok([
        {
          v: String(rows.reduce((s, r) => s + r.value_idr, 0)),
          t: String(rows.reduce((s, r) => s + Number(r.order_goods_incl ?? 0), 0)),
          t_missing: String(rows.filter((r) => r.order_goods_incl === null || r.order_goods_incl === undefined).length),
          amt: String(rows.reduce((s, r) => s + r.amount_total, 0)),
        },
      ]);
    }
    if (text.includes('SELECT 1 AS pending FROM platform.loyalty_reservation')) {
      return ok(
        [...this.reservations.values()]
          .filter((r) => r.order_ref === p[0] && r.status === 'reserved' && ms(r.hold_until) > ms(String(p[1])))
          .map(() => ({ n: 1 })),
      );
    }
    if (text.includes('FROM platform.loyalty_pos_reverse')) {
      return ok([...this.reverses.values()].filter((r) => r.refund_order_ref === p[0] || r.idempotency_key === p[1]).map((r) => ({ ...r })));
    }
    if (text.includes('SELECT value_idr, amount_total FROM platform.loyalty_reservation WHERE order_ref = $1 AND status = \'committed\'')) {
      return ok(
        [...this.reservations.values()]
          .filter((r) => r.order_ref === p[0] && r.status === 'committed')
          .map((r) => ({ value_idr: String(r.value_idr), amount_total: String(r.amount_total) })),
      );
    }
    if (text.includes('INSERT INTO platform.loyalty_reservation')) {
      const row: FakeReservationRow = {
        id: String(p[0]),
        member_hash: String(p[1]),
        company_code: String(p[2]),
        order_ref: String(p[3]),
        token_jti: String(p[4]),
        points: Number(p[5]),
        value_idr: Number(p[6]),
        amount_total: Number(p[7]),
        status: 'reserved',
        expires_at: String(p[8]),
        hold_until: String(p[9]),
        created_at: String(p[10]),
        updated_at: String(p[10]),
        committed_at: null,
        commit_idempotency_key: null,
      };
      this.checkUnique(row);
      this.reservations.set(row.id, row);
      undo.push(() => this.reservations.delete(row.id));
      return ok([], 1);
    }
    if (text.includes('FROM platform.loyalty_reservation WHERE id = $1 FOR UPDATE')) {
      const r = this.reservations.get(String(p[0]));
      return ok(r ? [this.resOut(r)] : []);
    }
    if (text.includes("SET status = 'committed'")) {
      const r = this.reservations.get(String(p[0]));
      if (!r) return ok([], 0);
      const next = { ...r, status: 'committed' as const };
      this.checkUnique(next, r.id);
      this.setStatus(
        r,
        {
          status: 'committed',
          committed_at: String(p[1]),
          commit_idempotency_key: String(p[2]),
          updated_at: String(p[1]),
          order_goods_incl: p[3] === null || p[3] === undefined ? null : Number(p[3]),
        },
        undo,
      );
      return ok([], 1);
    }
    if (text.includes("SET status = 'released'")) {
      const r = this.reservations.get(String(p[0]));
      if (!r || !['reserved', 'expired'].includes(r.status)) return ok([], 0);
      this.setStatus(r, { status: 'released', updated_at: String(p[1]) }, undo);
      return ok([{ id: r.id }]);
    }
    if (text.includes('SELECT status FROM platform.loyalty_reservation WHERE id = $1')) {
      const r = this.reservations.get(String(p[0]));
      return ok(r ? [{ status: r.status }] : []);
    }
    if (text.includes("UPDATE platform.loyalty_reservation SET status = 'expired', updated_at = $1")) {
      const limit = Number(p[1]);
      const due = [...this.reservations.values()]
        .filter((r) => r.status === 'reserved' && ms(r.hold_until) <= ms(String(p[0])))
        .slice(0, limit);
      for (const r of due) this.setStatus(r, { status: 'expired', updated_at: String(p[0]) }, undo);
      return ok([], due.length);
    }
    if (text.includes('DELETE FROM platform.loyalty_reservation') && text.includes('updated_at < $1')) {
      const limit = Number(p[1]);
      const old = [...this.reservations.values()]
        .filter(
          (r) =>
            ((r.status === 'released' || r.status === 'expired') && ms(r.updated_at) < ms(String(p[0]))) ||
            (r.status === 'committed' && p[2] !== undefined && ms(r.updated_at) < ms(String(p[2]))),
        )
        .slice(0, limit);
      for (const r of old) {
        this.reservations.delete(r.id);
        undo.push(() => this.reservations.set(r.id, r));
      }
      return ok([], old.length);
    }
    throw new Error(`FakeLoyaltyDb: query tidak dikenal: ${text.slice(0, 120)}`);
  }

  private createdAt(r: FakeLedgerRow): string {
    return r.created_at ?? new Date(0).toISOString();
  }

  private live(hash: string, at: string): FakeLedgerRow[] {
    return this.ledger.filter((r) => r.member_ref_hash === hash && (r.expires_at === null || ms(r.expires_at) > ms(at)));
  }

  private ledgerOut(r: FakeLedgerRow): Record<string, unknown> {
    return { ...r, expires_at: r.expires_at === null ? null : new Date(r.expires_at) };
  }

  private resOut(r: FakeReservationRow): Record<string, unknown> {
    return { ...r, value_idr: String(r.value_idr), expires_at: new Date(r.expires_at), hold_until: new Date(r.hold_until) };
  }

  private setStatus(r: FakeReservationRow, patch: Partial<FakeReservationRow>, undo: Undo[]): void {
    const before = { ...r };
    Object.assign(r, patch);
    undo.push(() => Object.assign(r, before));
  }

  private checkUnique(row: FakeReservationRow, selfId: string | null = null): void {
    for (const other of this.reservations.values()) {
      if (other.id !== selfId && other.id !== row.id && other.token_jti === row.token_jti) {
        throw new UniqueViolation('uq_loyalty_reservation_token_jti');
      }
    }
    if (!ACTIVE.has(row.status)) return;
    for (const other of this.reservations.values()) {
      if (other.id === selfId || other.id === row.id || !ACTIVE.has(other.status)) continue;
      if (other.order_ref === row.order_ref) throw new UniqueViolation('uq_loyalty_reservation_order_active');
    }
  }
}
