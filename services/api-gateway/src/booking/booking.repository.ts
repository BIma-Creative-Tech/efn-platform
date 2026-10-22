import { Db, Queryable } from '../common/db/db';
import { claimMessage } from '../common/db/processed-message';
import { BookingRow, BookingStatus } from './booking.model';

export const BOOKING_REPO = Symbol('BOOKING_REPO');
export const BOOKING_EVENT_CONSUMER = 'bff.booking-events';

export type NewBooking = Pick<
  BookingRow,
  'id' | 'member_id' | 'idempotency_key' | 'request_sha256' | 'company_code' | 'channel_code' | 'outlet_code' | 'outlet_name' | 'start_at' | 'end_at' | 'party_size'
>;
export type BookingPatch = Partial<Pick<BookingRow, 'status' | 'odoo_booking_ref' | 'table_label' | 'last_seq' | 'start_at' | 'end_at'>>;

/** Akses `platform.table_booking` (migrasi 12). Semua query parameterized; tidak ada kolom PII tamu. */
export interface BookingRepo {
  tx<T>(fn: (r: BookingRepo) => Promise<T>): Promise<T>;
  insert(b: NewBooking): Promise<BookingRow | null>;
  byKey(memberId: string, key: string): Promise<BookingRow | null>;
  byId(id: string, forUpdate?: boolean): Promise<BookingRow | null>;
  ofMember(memberId: string, limit: number): Promise<BookingRow[]>;
  countActive(memberId: string, now: Date): Promise<number>;
  update(id: string, patch: BookingPatch): Promise<void>;
  claim(key: string): Promise<boolean>;
  memberHashOf(memberId: string): Promise<string | null>;
  enqueue(queue: string, payload: Record<string, unknown>, messageId: string): Promise<void>;
  /** Reservasi `pending|requested|confirmed` (start ≤ until) untuk rekonsiliasi status ke Odoo. */
  staleActive(until: Date, limit: number): Promise<BookingRow[]>;
  unlinkMembers(endedBefore: Date): Promise<number>;
  purge(createdBefore: Date): Promise<number>;
}

const COLS = `id, member_id, idempotency_key, request_sha256, company_code, channel_code, outlet_code, outlet_name, start_at, end_at,
  party_size, status, odoo_booking_ref, table_label, last_seq, created_at, updated_at`;

export class PgBookingRepo implements BookingRepo {
  constructor(
    private readonly db: Db,
    private readonly q: Queryable,
  ) {}

  tx<T>(fn: (r: BookingRepo) => Promise<T>): Promise<T> {
    return this.db.tx((q) => fn(new PgBookingRepo(this.db, q)));
  }

  async insert(b: NewBooking): Promise<BookingRow | null> {
    const res = await this.q.query<BookingRow>(
      `INSERT INTO platform.table_booking (id, member_id, idempotency_key, request_sha256, company_code, channel_code, outlet_code,
         outlet_name, start_at, end_at, party_size, status)
       VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, 'pending')
       ON CONFLICT (member_id, idempotency_key) DO NOTHING
       RETURNING ${COLS}`,
      [b.id, b.member_id, b.idempotency_key, b.request_sha256, b.company_code, b.channel_code, b.outlet_code, b.outlet_name, b.start_at, b.end_at, b.party_size],
    );
    return res.rows[0] ?? null;
  }

  async byKey(memberId: string, key: string): Promise<BookingRow | null> {
    const res = await this.q.query<BookingRow>(`SELECT ${COLS} FROM platform.table_booking WHERE member_id = $1 AND idempotency_key = $2`, [memberId, key]);
    return res.rows[0] ?? null;
  }

  async byId(id: string, forUpdate = false): Promise<BookingRow | null> {
    const res = await this.q.query<BookingRow>(`SELECT ${COLS} FROM platform.table_booking WHERE id = $1${forUpdate ? ' FOR UPDATE' : ''}`, [id]);
    return res.rows[0] ?? null;
  }

  async ofMember(memberId: string, limit: number): Promise<BookingRow[]> {
    const res = await this.q.query<BookingRow>(
      `SELECT ${COLS} FROM platform.table_booking WHERE member_id = $1 AND status <> 'failed' ORDER BY start_at DESC LIMIT $2`,
      [memberId, limit],
    );
    return res.rows;
  }

  async countActive(memberId: string, now: Date): Promise<number> {
    const res = await this.q.query<{ n: string }>(
      `SELECT count(*)::text AS n FROM platform.table_booking
        WHERE member_id = $1 AND status IN ('pending', 'requested', 'confirmed') AND end_at > $2`,
      [memberId, now],
    );
    return Number(res.rows[0]?.n ?? 0);
  }

  async update(id: string, patch: BookingPatch): Promise<void> {
    const keys = Object.keys(patch) as (keyof BookingPatch)[];
    if (keys.length === 0) return;
    const sets = keys.map((k, i) => `${k} = $${i + 2}`);
    await this.q.query(`UPDATE platform.table_booking SET ${sets.join(', ')}, updated_at = now() WHERE id = $1`, [id, ...keys.map((k) => patch[k])]);
  }

  claim(key: string): Promise<boolean> {
    return claimMessage(this.q, BOOKING_EVENT_CONSUMER, key);
  }

  async memberHashOf(memberId: string): Promise<string | null> {
    const res = await this.q.query<{ member_hash: string }>(`SELECT member_hash FROM platform.member WHERE id = $1 AND deleted_at IS NULL`, [memberId]);
    return res.rows[0]?.member_hash ?? null;
  }

  async enqueue(queue: string, payload: Record<string, unknown>, messageId: string): Promise<void> {
    await this.q.query(`INSERT INTO platform.outbox (queue, payload, message_id) VALUES ($1, $2::jsonb, $3)`, [queue, JSON.stringify(payload), messageId]);
  }

  async staleActive(until: Date, limit: number): Promise<BookingRow[]> {
    const res = await this.q.query<BookingRow>(
      `SELECT ${COLS} FROM platform.table_booking
        WHERE status IN ('pending', 'requested', 'confirmed') AND start_at <= $1 ORDER BY start_at LIMIT $2`,
      [until, limit],
    );
    return res.rows;
  }

  async unlinkMembers(endedBefore: Date): Promise<number> {
    const res = await this.q.query(
      `UPDATE platform.table_booking SET member_id = NULL, updated_at = now()
        WHERE member_id IS NOT NULL AND end_at < $1 AND status NOT IN ('pending', 'requested', 'confirmed', 'seated')`,
      [endedBefore],
    );
    return res.rowCount ?? 0;
  }

  async purge(createdBefore: Date): Promise<number> {
    const res = await this.q.query(`DELETE FROM platform.table_booking WHERE created_at < $1`, [createdBefore]);
    return res.rowCount ?? 0;
  }
}

export type { BookingStatus };
