import { Db, Queryable } from '../common/db/db';
import { claimMessage } from '../common/db/processed-message';
import { FnbCompanyCode } from '../config/app-config';
import { ActivityKind, touchActivity } from '../loyalty/loyalty-activity';
import { DisplayLine, EarnStatus, OrderState, StoredLine, SubmissionRow, SubmissionStatus, TableSessionRow } from './fnb.model';
import { MenuAttribute, QrMenuItem } from './odoo-qr';

export const FNB_REPO = Symbol('FNB_REPO');

export type NewSession = Omit<
  TableSessionRow,
  'status' | 'close_reason' | 'closed_at' | 'member_id' | 'member_claimed_at' | 'earn_status' | 'submissions_count' | 'items_count' | 'last_seq' | 'close_event_at'
>;
export type SessionPatch = Partial<
  Pick<TableSessionRow, 'status' | 'close_reason' | 'closed_at' | 'member_id' | 'member_claimed_at' | 'earn_status' | 'submissions_count' | 'items_count' | 'last_seq'>
>;
export type NewSubmission = Pick<SubmissionRow, 'id' | 'session_id' | 'idempotency_key' | 'request_sha256' | 'lines' | 'items_count' | 'display' | 'status'>;
export type SubmissionPatch = Partial<
  Pick<SubmissionRow, 'status' | 'odoo_state' | 'order_uuid' | 'order_state' | 'eligible_amount' | 'earn_status' | 'points_earned' | 'display'>
>;

export interface MenuSyncRow {
  config_id: number;
  company_code: FnbCompanyCode;
  outlet_name: string | null;
  qr_enabled: boolean;
  confirm_mode: 'auto' | 'waiter';
  since: string | null;
  cursor: string | null;
  last_ok_at: Date | null;
}
export interface MenuItemRow {
  product_id: number;
  name: string | null;
  category_id: number | null;
  price_excl: number;
  tax_labels: string[];
  available: boolean;
  attributes: MenuAttribute[];
}
export interface CatalogEntry {
  name: string;
  category_id: number | null;
  image_sha: string | null;
}
export interface FnbGauges {
  open: number;
  overdue: number;
  stuck: number;
  earnPending: number;
}
export interface FnbRetentionResult {
  unlinked: number;
  sessionsDeleted: number;
  menuDeleted: number;
}

/**
 * Akses data QR meja (docs/04 §14.A.8). Implementasi: `PgFnbRepo` (SQL parametris) dan `MemoryFnbRepo` (test,
 * `testing/fnb-fakes.ts`). Tulis lintas-baris di `tx` (satu transaksi PostgreSQL).
 */
export interface FnbRepo {
  tx<T>(fn: (r: FnbRepo) => Promise<T>): Promise<T>;
  /** Klien transaksi PostgreSQL (earn ledger di transaksi yang sama). `null` di fake. */
  sql(): Queryable | null;
  tryJobLock(key: number): Promise<boolean>;
  claim(consumer: string, key: string): Promise<boolean>;
  hasActiveDelete(memberId: string): Promise<boolean>;
  touchActivity(memberHash: string, kind: ActivityKind, at: Date): Promise<void>;

  getSession(id: string, forUpdate?: boolean): Promise<TableSessionRow | null>;
  findSessionByOpenKey(key: string): Promise<TableSessionRow | null>;
  findOpenSession(configId: number, tableId: number, forUpdate?: boolean): Promise<TableSessionRow | null>;
  insertSession(s: NewSession): Promise<void>;
  updateSession(id: string, patch: SessionPatch): Promise<void>;
  /**
   * K3 r15: `close_event_at` sesi ini (bila masih NULL) + sesi pendahulu di meja yang sama yang ditutup BFF karena
   * `reissued` sebelum sesi ini dibuka (Odoo tidak mengirim close untuk sesi yang diganti; order-nya dipindah ke sesi
   * baru → close sesi baru juga menutup "jendela pending" pendahulunya).
   */
  markCloseProcessed(s: Pick<TableSessionRow, 'id' | 'config_id' | 'table_id' | 'opened_at'>, at: Date): Promise<void>;

  getSubmissionByKey(sessionId: string, key: string): Promise<SubmissionRow | null>;
  getSubmission(sessionId: string, id: string, forUpdate?: boolean): Promise<SubmissionRow | null>;
  listSubmissions(sessionId: string): Promise<SubmissionRow[]>;
  insertSubmission(s: NewSubmission): Promise<void>;
  updateSubmission(id: string, patch: SubmissionPatch): Promise<void>;

  menuSync(configId: number): Promise<MenuSyncRow | null>;
  /** Buat baris sinkron menu config (idempoten); mengembalikan PT yang tersimpan (beda = konflik). */
  ensureMenuSync(configId: number, company: FnbCompanyCode): Promise<FnbCompanyCode>;
  updateMenuSync(configId: number, patch: Partial<Omit<MenuSyncRow, 'config_id' | 'company_code' | 'last_ok_at'>> & { ok?: boolean; error?: string | null }): Promise<void>;
  upsertMenuItem(configId: number, company: FnbCompanyCode, item: QrMenuItem): Promise<void>;
  deleteMenuItems(configId: number, productIds: number[]): Promise<void>;
  menuItems(configId: number): Promise<MenuItemRow[]>;
  catalogEntries(channel: string, productIds: number[]): Promise<Map<number, CatalogEntry>>;
  catalogCategories(channel: string): Promise<{ category_id: number; name: string; sequence: number }[]>;
  brandOf(channel: string): Promise<string | null>;
  configsToSync(companies: readonly FnbCompanyCode[]): Promise<MenuSyncRow[]>;

  expireDue(now: Date, limit: number): Promise<number>;
  stuckSubmitting(before: Date, limit: number): Promise<{ submission: SubmissionRow; session: TableSessionRow }[]>;
  gauges(now: Date): Promise<FnbGauges>;
  retention(now: Date, cfg: { retentionDays: number; memberLinkDays: number }): Promise<FnbRetentionResult>;
}

// ───────────── PostgreSQL ─────────────
type Raw = Record<string, unknown>;
const d = (v: unknown): Date | null => (v === null || v === undefined ? null : v instanceof Date ? v : new Date(String(v)));
const SESSION_COLS = `id::text AS id, company_code, config_id, table_id, table_label, order_uuid, open_idempotency_key, open_request_sha256,
  print_seq, status, close_reason, opened_at, expires_at, closed_at, member_id::text AS member_id, member_claimed_at, earn_status,
  submissions_count, items_count, last_seq, close_event_at`;
const SUB_COLS = `id::text AS id, session_id::text AS session_id, idempotency_key, request_sha256, lines, items_count, display, status, odoo_state,
  order_uuid, order_state, eligible_amount, earn_status, points_earned, created_at, updated_at`;

function toSession(r: Raw): TableSessionRow {
  return {
    id: String(r.id),
    company_code: r.company_code as FnbCompanyCode,
    config_id: Number(r.config_id),
    table_id: Number(r.table_id),
    table_label: String(r.table_label),
    order_uuid: r.order_uuid === null || r.order_uuid === undefined ? null : String(r.order_uuid),
    open_idempotency_key: String(r.open_idempotency_key),
    open_request_sha256: String(r.open_request_sha256),
    print_seq: Number(r.print_seq),
    status: r.status as TableSessionRow['status'],
    close_reason: (r.close_reason ?? null) as TableSessionRow['close_reason'],
    opened_at: d(r.opened_at) as Date,
    expires_at: d(r.expires_at) as Date,
    closed_at: d(r.closed_at),
    member_id: r.member_id === null || r.member_id === undefined ? null : String(r.member_id),
    member_claimed_at: d(r.member_claimed_at),
    earn_status: r.earn_status as EarnStatus,
    submissions_count: Number(r.submissions_count),
    items_count: Number(r.items_count),
    last_seq: Number(r.last_seq),
    close_event_at: d(r.close_event_at),
  };
}

function toSubmission(r: Raw): SubmissionRow {
  return {
    id: String(r.id),
    session_id: String(r.session_id),
    idempotency_key: String(r.idempotency_key),
    request_sha256: String(r.request_sha256),
    lines: (r.lines ?? []) as StoredLine[],
    items_count: Number(r.items_count),
    display: (r.display ?? null) as { lines: DisplayLine[]; indicative_subtotal_excl: number | null } | null,
    status: r.status as SubmissionStatus,
    odoo_state: (r.odoo_state ?? null) as string | null,
    order_uuid: (r.order_uuid ?? null) as string | null,
    order_state: (r.order_state ?? null) as OrderState | null,
    eligible_amount: r.eligible_amount === null || r.eligible_amount === undefined ? null : Number(r.eligible_amount),
    earn_status: r.earn_status as EarnStatus,
    points_earned: Number(r.points_earned ?? 0),
    created_at: d(r.created_at) as Date,
    updated_at: d(r.updated_at) as Date,
  };
}

function toSync(r: Raw): MenuSyncRow {
  return {
    config_id: Number(r.config_id),
    company_code: r.company_code as FnbCompanyCode,
    outlet_name: (r.outlet_name ?? null) as string | null,
    qr_enabled: r.qr_enabled === true,
    confirm_mode: r.confirm_mode === 'auto' ? 'auto' : 'waiter',
    since: (r.since ?? null) as string | null,
    cursor: (r.cursor ?? null) as string | null,
    last_ok_at: d(r.last_ok_at),
  };
}

const SESSION_PATCH = new Set(['status', 'close_reason', 'closed_at', 'member_id', 'member_claimed_at', 'earn_status', 'submissions_count', 'items_count', 'last_seq']);
const SUB_PATCH = new Set(['status', 'odoo_state', 'order_uuid', 'order_state', 'eligible_amount', 'earn_status', 'points_earned', 'display']);

function setClause(patch: Record<string, unknown>, allowed: ReadonlySet<string>, offset: number): { sql: string; params: unknown[] } {
  const parts: string[] = [];
  const params: unknown[] = [];
  for (const [k, v] of Object.entries(patch)) {
    if (v === undefined) continue;
    if (!allowed.has(k)) throw new Error(`kolom patch tidak diizinkan: ${k}`);
    params.push(v instanceof Date ? v.toISOString() : k === 'display' && v !== null ? JSON.stringify(v) : v);
    parts.push(k === 'display' ? `${k} = $${offset + params.length}::jsonb` : `${k} = $${offset + params.length}`);
  }
  parts.push('updated_at = now()');
  return { sql: parts.join(', '), params };
}

export class PgFnbRepo implements FnbRepo {
  constructor(
    private readonly q: Queryable,
    private readonly db: Db | null = null,
  ) {}

  async tx<T>(fn: (r: FnbRepo) => Promise<T>): Promise<T> {
    if (!this.db) return fn(this);
    return this.db.tx((q) => fn(new PgFnbRepo(q, null)));
  }
  sql(): Queryable | null {
    return this.db ? null : this.q; // hanya di dalam transaksi
  }
  async tryJobLock(key: number): Promise<boolean> {
    const r = await this.q.query<{ ok: boolean }>('SELECT pg_try_advisory_xact_lock($1) AS ok', [key]);
    return r.rows[0]?.ok === true;
  }
  claim(consumer: string, key: string): Promise<boolean> {
    return claimMessage(this.q, consumer, key);
  }
  async hasActiveDelete(memberId: string): Promise<boolean> {
    const r = await this.q.query(
      `SELECT 1 FROM platform.dsar_request WHERE member_id = $1 AND type IN ('delete', 'retention') AND status IN ('scheduled', 'processing') LIMIT 1`,
      [memberId],
    );
    return (r.rowCount ?? 0) > 0;
  }
  touchActivity(memberHash: string, kind: ActivityKind, at: Date): Promise<void> {
    return touchActivity(this.q, memberHash, kind, at);
  }

  async getSession(id: string, forUpdate = false): Promise<TableSessionRow | null> {
    const r = await this.q.query<Raw>(`SELECT ${SESSION_COLS} FROM platform.fnb_table_session WHERE id = $1${forUpdate ? ' FOR UPDATE' : ''}`, [id]);
    return r.rows[0] ? toSession(r.rows[0]) : null;
  }
  async findSessionByOpenKey(key: string): Promise<TableSessionRow | null> {
    const r = await this.q.query<Raw>(`SELECT ${SESSION_COLS} FROM platform.fnb_table_session WHERE open_idempotency_key = $1`, [key]);
    return r.rows[0] ? toSession(r.rows[0]) : null;
  }
  async findOpenSession(configId: number, tableId: number, forUpdate = false): Promise<TableSessionRow | null> {
    const r = await this.q.query<Raw>(
      `SELECT ${SESSION_COLS} FROM platform.fnb_table_session WHERE config_id = $1 AND table_id = $2 AND status = 'open'${forUpdate ? ' FOR UPDATE' : ''}`,
      [configId, tableId],
    );
    return r.rows[0] ? toSession(r.rows[0]) : null;
  }
  async insertSession(s: NewSession): Promise<void> {
    await this.q.query(
      `INSERT INTO platform.fnb_table_session
         (id, company_code, config_id, table_id, table_label, order_uuid, open_idempotency_key, open_request_sha256, print_seq,
          status, opened_at, expires_at)
       VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, 'open', $10, $11)`,
      [s.id, s.company_code, s.config_id, s.table_id, s.table_label, s.order_uuid, s.open_idempotency_key, s.open_request_sha256, s.print_seq, s.opened_at.toISOString(), s.expires_at.toISOString()],
    );
  }
  async updateSession(id: string, patch: SessionPatch): Promise<void> {
    const s = setClause(patch, SESSION_PATCH, 1);
    await this.q.query(`UPDATE platform.fnb_table_session SET ${s.sql} WHERE id = $1`, [id, ...s.params]);
  }
  async markCloseProcessed(s: Pick<TableSessionRow, 'id' | 'config_id' | 'table_id' | 'opened_at'>, at: Date): Promise<void> {
    await this.q.query(
      `UPDATE platform.fnb_table_session SET close_event_at = $5, updated_at = now()
        WHERE close_event_at IS NULL
          AND (id = $1
               OR (config_id = $2 AND table_id = $3 AND status = 'closed' AND close_reason = 'reissued' AND opened_at <= $4))`,
      [s.id, s.config_id, s.table_id, s.opened_at.toISOString(), at.toISOString()],
    );
  }

  async getSubmissionByKey(sessionId: string, key: string): Promise<SubmissionRow | null> {
    const r = await this.q.query<Raw>(`SELECT ${SUB_COLS} FROM platform.fnb_submission WHERE session_id = $1 AND idempotency_key = $2`, [sessionId, key]);
    return r.rows[0] ? toSubmission(r.rows[0]) : null;
  }
  async getSubmission(sessionId: string, id: string, forUpdate = false): Promise<SubmissionRow | null> {
    const r = await this.q.query<Raw>(
      `SELECT ${SUB_COLS} FROM platform.fnb_submission WHERE session_id = $1 AND id = $2${forUpdate ? ' FOR UPDATE' : ''}`,
      [sessionId, id],
    );
    return r.rows[0] ? toSubmission(r.rows[0]) : null;
  }
  async listSubmissions(sessionId: string): Promise<SubmissionRow[]> {
    const r = await this.q.query<Raw>(`SELECT ${SUB_COLS} FROM platform.fnb_submission WHERE session_id = $1 ORDER BY created_at, id LIMIT 500`, [sessionId]);
    return r.rows.map(toSubmission);
  }
  async insertSubmission(s: NewSubmission): Promise<void> {
    await this.q.query(
      `INSERT INTO platform.fnb_submission (id, session_id, idempotency_key, request_sha256, lines, items_count, display, status)
       VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7::jsonb, $8)`,
      [s.id, s.session_id, s.idempotency_key, s.request_sha256, JSON.stringify(s.lines), s.items_count, s.display === null ? null : JSON.stringify(s.display), s.status],
    );
  }
  async updateSubmission(id: string, patch: SubmissionPatch): Promise<void> {
    const s = setClause(patch, SUB_PATCH, 1);
    await this.q.query(`UPDATE platform.fnb_submission SET ${s.sql} WHERE id = $1`, [id, ...s.params]);
  }

  async menuSync(configId: number): Promise<MenuSyncRow | null> {
    const r = await this.q.query<Raw>(`SELECT * FROM platform.fnb_menu_sync WHERE config_id = $1`, [configId]);
    return r.rows[0] ? toSync(r.rows[0]) : null;
  }
  async ensureMenuSync(configId: number, company: FnbCompanyCode): Promise<FnbCompanyCode> {
    await this.q.query(
      `INSERT INTO platform.fnb_menu_sync (config_id, company_code) VALUES ($1, $2) ON CONFLICT (config_id) DO NOTHING`,
      [configId, company],
    );
    const r = await this.q.query<{ company_code: string }>(`SELECT company_code FROM platform.fnb_menu_sync WHERE config_id = $1`, [configId]);
    return (r.rows[0]?.company_code ?? company) as FnbCompanyCode;
  }
  async updateMenuSync(
    configId: number,
    patch: Partial<Omit<MenuSyncRow, 'config_id' | 'company_code' | 'last_ok_at'>> & { ok?: boolean; error?: string | null },
  ): Promise<void> {
    await this.q.query(
      `UPDATE platform.fnb_menu_sync
          SET outlet_name = COALESCE($2, outlet_name),
              qr_enabled = COALESCE($3, qr_enabled),
              confirm_mode = COALESCE($4, confirm_mode),
              since = CASE WHEN $5::boolean THEN $6 ELSE since END,
              cursor = CASE WHEN $5::boolean THEN $7 ELSE cursor END,
              last_ok_at = CASE WHEN $8::boolean THEN now() ELSE last_ok_at END,
              last_attempt_at = now(),
              last_error_code = $9
        WHERE config_id = $1`,
      [
        configId,
        patch.outlet_name ?? null,
        patch.qr_enabled ?? null,
        patch.confirm_mode ?? null,
        patch.since !== undefined || patch.cursor !== undefined,
        patch.since ?? null,
        patch.cursor ?? null,
        patch.ok === true,
        patch.error ?? null,
      ],
    );
  }
  async upsertMenuItem(configId: number, company: FnbCompanyCode, item: QrMenuItem): Promise<void> {
    await this.q.query(
      `INSERT INTO platform.fnb_menu_item (config_id, product_id, company_code, name, category_id, price_excl, tax_labels, available, attributes, updated_at, deleted_at)
       VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8, $9::jsonb, now(), NULL)
       ON CONFLICT (config_id, product_id) DO UPDATE
         SET company_code = EXCLUDED.company_code, name = EXCLUDED.name, category_id = EXCLUDED.category_id,
             price_excl = EXCLUDED.price_excl, tax_labels = EXCLUDED.tax_labels, available = EXCLUDED.available,
             attributes = EXCLUDED.attributes, updated_at = now(), deleted_at = NULL`,
      [configId, item.product_id, company, item.name, item.category_id, item.price_excl, JSON.stringify(item.tax_labels), item.available, JSON.stringify(item.attributes)],
    );
  }
  async deleteMenuItems(configId: number, productIds: number[]): Promise<void> {
    if (productIds.length === 0) return;
    await this.q.query(
      `UPDATE platform.fnb_menu_item SET deleted_at = now(), updated_at = now() WHERE config_id = $1 AND product_id = ANY($2::int[]) AND deleted_at IS NULL`,
      [configId, productIds],
    );
  }
  async menuItems(configId: number): Promise<MenuItemRow[]> {
    const r = await this.q.query<Raw>(
      `SELECT product_id, name, category_id, price_excl, tax_labels, available, attributes
         FROM platform.fnb_menu_item WHERE config_id = $1 AND deleted_at IS NULL ORDER BY product_id LIMIT 2000`,
      [configId],
    );
    return r.rows.map((x) => ({
      product_id: Number(x.product_id),
      name: (x.name ?? null) as string | null,
      category_id: x.category_id === null || x.category_id === undefined ? null : Number(x.category_id),
      price_excl: Number(x.price_excl),
      tax_labels: (x.tax_labels ?? []) as string[],
      available: x.available === true,
      attributes: (x.attributes ?? []) as MenuAttribute[],
    }));
  }
  async catalogEntries(channel: string, productIds: number[]): Promise<Map<number, CatalogEntry>> {
    if (productIds.length === 0) return new Map();
    const r = await this.q.query<{ product_id: number; name: string; category_id: number | null; image_sha: string | null; image_ready_sha: string | null }>(
      `SELECT product_id, data->>'name' AS name, category_id, image_sha, image_ready_sha FROM platform.online_catalog_item
        WHERE channel_code = $1 AND product_id = ANY($2::int[]) AND deleted_at IS NULL`,
      [channel, productIds],
    );
    return new Map(
      r.rows.map((x) => [
        Number(x.product_id),
        {
          name: String(x.name ?? ''),
          category_id: x.category_id === null ? null : Number(x.category_id),
          image_sha: x.image_sha !== null && x.image_sha === x.image_ready_sha ? x.image_sha : null,
        },
      ]),
    );
  }
  async catalogCategories(channel: string): Promise<{ category_id: number; name: string; sequence: number }[]> {
    const r = await this.q.query<{ category_id: number; data: { name?: string; sequence?: number } }>(
      `SELECT category_id, data FROM platform.online_catalog_category WHERE channel_code = $1 AND deleted_at IS NULL LIMIT 500`,
      [channel],
    );
    return r.rows.map((x) => ({ category_id: Number(x.category_id), name: String(x.data?.name ?? ''), sequence: Number(x.data?.sequence ?? 0) }));
  }
  async brandOf(channel: string): Promise<string | null> {
    const r = await this.q.query<{ brand: string | null }>(
      `SELECT channel_data->>'brand_code' AS brand FROM platform.online_catalog_sync WHERE channel_code = $1`,
      [channel],
    );
    const b = r.rows[0]?.brand ?? null;
    return b !== null && /^[A-Z][A-Z0-9]{1,9}$/.test(b) ? b : null;
  }
  async configsToSync(companies: readonly FnbCompanyCode[]): Promise<MenuSyncRow[]> {
    // A.6: config dengan sesi terbuka ATAU efn_qr_enabled (terakhir diketahui)
    const r = await this.q.query<Raw>(
      `SELECT s.* FROM platform.fnb_menu_sync s
        WHERE s.company_code = ANY($1::text[])
          AND (s.qr_enabled OR s.last_ok_at IS NULL
               OR EXISTS (SELECT 1 FROM platform.fnb_table_session t WHERE t.config_id = s.config_id AND t.status = 'open'))
        ORDER BY s.config_id LIMIT 500`,
      [[...companies]],
    );
    return r.rows.map(toSync);
  }

  async expireDue(now: Date, limit: number): Promise<number> {
    const r = await this.q.query(
      `UPDATE platform.fnb_table_session SET status = 'expired', close_reason = 'expired', closed_at = $1, updated_at = now()
        WHERE id IN (SELECT id FROM platform.fnb_table_session WHERE status = 'open' AND expires_at <= $1 ORDER BY expires_at LIMIT $2)`,
      [now.toISOString(), limit],
    );
    return r.rowCount ?? 0;
  }
  async stuckSubmitting(before: Date, limit: number): Promise<{ submission: SubmissionRow; session: TableSessionRow }[]> {
    const r = await this.q.query<Raw>(
      `SELECT ${SUB_COLS} FROM platform.fnb_submission WHERE status = 'submitting' AND updated_at < $1 ORDER BY updated_at LIMIT $2`,
      [before.toISOString(), limit],
    );
    const out: { submission: SubmissionRow; session: TableSessionRow }[] = [];
    for (const row of r.rows) {
      const sub = toSubmission(row);
      const sess = await this.getSession(sub.session_id);
      if (sess) out.push({ submission: sub, session: sess });
    }
    return out;
  }
  async gauges(now: Date): Promise<FnbGauges> {
    const r = await this.q.query<{ open: string; overdue: string; stuck: string; earn: string }>(
      `SELECT
         (SELECT count(*) FROM platform.fnb_table_session WHERE status = 'open') AS open,
         (SELECT count(*) FROM platform.fnb_table_session
           WHERE status = 'open' AND (opened_at < $1::timestamptz - interval '4 hours' OR expires_at < $1::timestamptz)) AS overdue,
         (SELECT count(*) FROM platform.fnb_submission WHERE status = 'submitting' AND updated_at < $1::timestamptz - interval '5 minutes') AS stuck,
         -- L-76: sesi diklaim member yang kedaluwarsa tanpa event bayar > 15 menit dengan kiriman yang masuk dapur
         -- (Odoo tetap mengirim session_closed paid setelah expired; earn tertunda sampai event itu tiba)
         (SELECT count(*) FROM platform.fnb_table_session t
           WHERE t.member_id IS NOT NULL AND t.status = 'expired' AND t.earn_status = 'none'
             AND COALESCE(t.closed_at, t.expires_at) < $1::timestamptz - interval '15 minutes'
             AND EXISTS (SELECT 1 FROM platform.fnb_submission s WHERE s.session_id = t.id
                          AND s.status NOT IN ('submitting', 'rejected', 'cancelled', 'failed'))) AS earn`,
      [now.toISOString()],
    );
    const x = r.rows[0];
    return { open: Number(x?.open ?? 0), overdue: Number(x?.overdue ?? 0), stuck: Number(x?.stuck ?? 0), earnPending: Number(x?.earn ?? 0) };
  }
  async retention(now: Date, cfg: { retentionDays: number; memberLinkDays: number }): Promise<FnbRetentionResult> {
    const ts = now.toISOString();
    // A.11: tautan member dilepas N hari setelah sesi ditutup
    const unlink = await this.q.query(
      `UPDATE platform.fnb_table_session SET member_id = NULL, member_unlinked_at = now(), updated_at = now()
        WHERE member_id IS NOT NULL AND status <> 'open' AND COALESCE(closed_at, expires_at) < $1::timestamptz - make_interval(days => $2::int)`,
      [ts, cfg.memberLinkDays],
    );
    // E.5: fnb_* 180 hari (kiriman ikut CASCADE), batch
    const del = await this.q.query(
      `DELETE FROM platform.fnb_table_session
        WHERE id IN (SELECT id FROM platform.fnb_table_session
                      WHERE status <> 'open' AND COALESCE(closed_at, expires_at) < $1::timestamptz - make_interval(days => $2::int) LIMIT 5000)`,
      [ts, cfg.retentionDays],
    );
    const menu = await this.q.query(
      `DELETE FROM platform.fnb_menu_item WHERE deleted_at IS NOT NULL AND deleted_at < $1::timestamptz - interval '30 days'`,
      [ts],
    );
    return { unlinked: unlink.rowCount ?? 0, sessionsDeleted: del.rowCount ?? 0, menuDeleted: menu.rowCount ?? 0 };
  }
}
