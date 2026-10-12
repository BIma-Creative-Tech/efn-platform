/**
 * Fake PostgreSQL in-memory untuk unit test loyalty POS (bukan runtime). Mengemulasikan HANYA query yang dipakai
 * `LoyaltyService`, `PosLoyaltyService`, dan `LoyaltyReservationSweeper`:
 * - `platform.loyalty_ledger` dengan UNIQUE(idempotency_key) + ON CONFLICT DO NOTHING;
 * - `platform.loyalty_pos_earn` (PK order_ref, UNIQUE idempotency_key);
 * - `platform.loyalty_reservation` dengan UNIQUE token_jti dan indeks unik parsial order_ref (reserved|committed)
 *   → error `{ code: '23505' }` seperti pg;
 * - `pg_advisory_xact_lock(a, b)` = mutex async per kunci sampai akhir transaksi (`noLocks` mematikannya untuk kontrol
 *   negatif test konkurensi); `pg_try_advisory_xact_lock(k)`;
 * - transaksi dengan undo log (rollback saat fn melempar) tanpa mengganggu transaksi paralel lain.
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
}

export class FakeLoyaltyDb implements Db {
  readonly ledger: FakeLedgerRow[] = [];
  readonly earns = new Map<string, FakeEarnRow>();
  /** G18 K-18-6: `platform.loyalty_pos_reverse` (PK refund_order_ref, UNIQUE idempotency_key). */
  readonly reverses = new Map<string, Record<string, unknown>>();
  readonly reservations = new Map<string, FakeReservationRow>();
  readonly queries: string[] = [];
  /** SEC-65 (`erasureBlock`): member_hash dengan DSAR delete aktif; tag daftar tolak → expires_at ISO. */
  readonly erasureActive = new Set<string>();
  readonly suppression = new Map<string, string>();
  noLocks = false;
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
      };
      this.ledger.push(row);
      undo.push(() => this.ledger.splice(this.ledger.indexOf(row), 1));
      return ok([{ id: String(row.id) }]);
    }
    if (text.includes('FROM platform.loyalty_ledger') && text.includes('GROUP BY expires_at')) {
      const live = this.live(String(p[0]), String(p[1]));
      const groups = new Map<string | null, number>();
      for (const r of live) groups.set(r.expires_at, (groups.get(r.expires_at) ?? 0) + r.delta_points);
      const rows = [...groups.entries()]
        .sort(([a], [b]) => (a === null ? 1 : b === null ? -1 : ms(a) - ms(b)))
        .map(([expires_at, remaining]) => ({ expires_at: expires_at === null ? null : new Date(expires_at), remaining: String(remaining) }));
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
      };
      if (this.reverses.has(row.refund_order_ref) || [...this.reverses.values()].some((r) => r.idempotency_key === row.idempotency_key)) {
        return ok([], 0);
      }
      this.reverses.set(row.refund_order_ref, row);
      undo.push(() => this.reverses.delete(row.refund_order_ref));
      return ok([], 1);
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
      this.setStatus(r, { status: 'committed', committed_at: String(p[1]), commit_idempotency_key: String(p[2]), updated_at: String(p[1]) }, undo);
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
        .filter((r) => r.status !== 'reserved' && ms(r.updated_at) < ms(String(p[0])))
        .slice(0, limit);
      for (const r of old) {
        this.reservations.delete(r.id);
        undo.push(() => this.reservations.set(r.id, r));
      }
      return ok([], old.length);
    }
    throw new Error(`FakeLoyaltyDb: query tidak dikenal: ${text.slice(0, 120)}`);
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
