/**
 * Fake in-memory QR meja untuk unit test (bukan runtime). `MemoryFnbRepo` meniru `PgFnbRepo`: transaksi = snapshot +
 * restore saat error (rollback), indeks unik (open key, satu sesi open per meja, (sesi, idempotency_key)).
 */
import { FnbCompanyCode } from '../../config/app-config';
import { Queryable } from '../../common/db/db';
import { ActivityKind } from '../../loyalty/loyalty-activity';
import { SubmissionRow, TableSessionRow } from '../fnb.model';
import {
  CatalogEntry,
  FnbGauges,
  FnbRepo,
  FnbRetentionResult,
  MenuItemRow,
  MenuSyncRow,
  NewSession,
  NewSubmission,
  SessionPatch,
  SubmissionPatch,
} from '../fnb.repository';
import { FnbEarnPort } from '../fnb-pos.service';
import { QrMenuItem } from '../odoo-qr';

interface State {
  sessions: Map<string, TableSessionRow>;
  subs: Map<string, SubmissionRow>;
  claims: Set<string>;
  sync: Map<number, MenuSyncRow>;
  menu: Map<string, MenuItemRow & { config_id: number; deleted: boolean }>;
  activity: Map<string, { at: Date; kind: string }>;
  activeDeletes: Set<string>;
  catalog: Map<string, CatalogEntry>;
  categories: Map<string, { category_id: number; name: string; sequence: number }[]>;
  brands: Map<string, string>;
}

class UniqueViolation extends Error {
  readonly code = '23505';
}

export class MemoryFnbRepo implements FnbRepo {
  state: State = {
    sessions: new Map(),
    subs: new Map(),
    claims: new Set(),
    sync: new Map(),
    menu: new Map(),
    activity: new Map(),
    activeDeletes: new Set(),
    catalog: new Map(),
    categories: new Map(),
    brands: new Map(),
  };
  private depth = 0;
  now: () => number = () => Date.now();

  async tx<T>(fn: (r: FnbRepo) => Promise<T>): Promise<T> {
    if (this.depth > 0) return fn(this);
    const snap = structuredClone(this.state);
    this.depth += 1;
    try {
      return await fn(this);
    } catch (err) {
      this.state = snap;
      throw err;
    } finally {
      this.depth -= 1;
    }
  }
  sql(): Queryable | null {
    return null;
  }
  async tryJobLock(): Promise<boolean> {
    return true;
  }
  async claim(consumer: string, key: string): Promise<boolean> {
    const k = `${consumer}|${key}`;
    if (this.state.claims.has(k)) return false;
    this.state.claims.add(k);
    return true;
  }
  async hasActiveDelete(memberId: string): Promise<boolean> {
    return this.state.activeDeletes.has(memberId);
  }
  async touchActivity(memberHash: string, kind: ActivityKind, at: Date): Promise<void> {
    const prev = this.state.activity.get(memberHash);
    if (!prev || at >= prev.at) this.state.activity.set(memberHash, { at, kind });
  }

  async getSession(id: string): Promise<TableSessionRow | null> {
    return structuredClone(this.state.sessions.get(id) ?? null);
  }
  async findSessionByOpenKey(key: string): Promise<TableSessionRow | null> {
    return structuredClone([...this.state.sessions.values()].find((s) => s.open_idempotency_key === key) ?? null);
  }
  async findOpenSession(configId: number, tableId: number): Promise<TableSessionRow | null> {
    return structuredClone([...this.state.sessions.values()].find((s) => s.config_id === configId && s.table_id === tableId && s.status === 'open') ?? null);
  }
  async insertSession(s: NewSession): Promise<void> {
    const all = [...this.state.sessions.values()];
    if (all.some((x) => x.open_idempotency_key === s.open_idempotency_key)) throw new UniqueViolation('open key');
    if (all.some((x) => x.status === 'open' && x.config_id === s.config_id && x.table_id === s.table_id)) throw new UniqueViolation('open table');
    this.state.sessions.set(s.id, {
      ...s,
      status: 'open',
      close_reason: null,
      closed_at: null,
      member_id: null,
      member_claimed_at: null,
      earn_status: 'none',
      submissions_count: 0,
      items_count: 0,
      last_seq: 0,
      close_event_at: null,
    });
  }
  async updateSession(id: string, patch: SessionPatch): Promise<void> {
    const s = this.state.sessions.get(id);
    if (s) Object.assign(s, Object.fromEntries(Object.entries(patch).filter(([, v]) => v !== undefined)));
  }
  async markCloseProcessed(ref: Pick<TableSessionRow, 'id' | 'config_id' | 'table_id' | 'opened_at'>, at: Date): Promise<void> {
    for (const s of this.state.sessions.values()) {
      if (s.close_event_at !== null) continue;
      const predecessor =
        s.config_id === ref.config_id && s.table_id === ref.table_id && s.status === 'closed' && s.close_reason === 'reissued' && s.opened_at <= ref.opened_at;
      if (s.id === ref.id || predecessor) s.close_event_at = at;
    }
  }

  async getSubmissionByKey(sessionId: string, key: string): Promise<SubmissionRow | null> {
    return structuredClone([...this.state.subs.values()].find((x) => x.session_id === sessionId && x.idempotency_key === key) ?? null);
  }
  async getSubmission(sessionId: string, id: string): Promise<SubmissionRow | null> {
    const s = this.state.subs.get(id);
    return s && s.session_id === sessionId ? structuredClone(s) : null;
  }
  async listSubmissions(sessionId: string): Promise<SubmissionRow[]> {
    return [...this.state.subs.values()].filter((x) => x.session_id === sessionId).map((x) => structuredClone(x));
  }
  async insertSubmission(s: NewSubmission): Promise<void> {
    if ([...this.state.subs.values()].some((x) => x.session_id === s.session_id && x.idempotency_key === s.idempotency_key)) throw new UniqueViolation('sub key');
    const at = new Date(this.now());
    this.state.subs.set(s.id, {
      ...s,
      odoo_state: null,
      order_uuid: null,
      order_state: null,
      eligible_amount: null,
      earn_status: 'none',
      points_earned: 0,
      created_at: at,
      updated_at: at,
    });
  }
  async updateSubmission(id: string, patch: SubmissionPatch): Promise<void> {
    const s = this.state.subs.get(id);
    if (s) Object.assign(s, Object.fromEntries(Object.entries(patch).filter(([, v]) => v !== undefined)), { updated_at: new Date(this.now()) });
  }

  async menuSync(configId: number): Promise<MenuSyncRow | null> {
    return structuredClone(this.state.sync.get(configId) ?? null);
  }
  async ensureMenuSync(configId: number, company: FnbCompanyCode): Promise<FnbCompanyCode> {
    const cur = this.state.sync.get(configId);
    if (cur) return cur.company_code;
    this.state.sync.set(configId, { config_id: configId, company_code: company, outlet_name: null, qr_enabled: false, confirm_mode: 'waiter', since: null, cursor: null, last_ok_at: null });
    return company;
  }
  async updateMenuSync(configId: number, patch: Partial<MenuSyncRow> & { ok?: boolean; error?: string | null }): Promise<void> {
    const s = this.state.sync.get(configId);
    if (!s) return;
    if (patch.outlet_name !== undefined && patch.outlet_name !== null) s.outlet_name = patch.outlet_name;
    if (patch.qr_enabled !== undefined) s.qr_enabled = patch.qr_enabled;
    if (patch.confirm_mode !== undefined) s.confirm_mode = patch.confirm_mode;
    if (patch.since !== undefined || patch.cursor !== undefined) {
      s.since = patch.since ?? null;
      s.cursor = patch.cursor ?? null;
    }
    if (patch.ok) s.last_ok_at = new Date(this.now());
  }
  async upsertMenuItem(configId: number, _company: FnbCompanyCode, item: QrMenuItem): Promise<void> {
    this.state.menu.set(`${configId}:${item.product_id}`, { ...structuredClone(item), config_id: configId, deleted: false });
  }
  async deleteMenuItems(configId: number, ids: number[]): Promise<void> {
    for (const id of ids) {
      const m = this.state.menu.get(`${configId}:${id}`);
      if (m) m.deleted = true;
    }
  }
  async menuItems(configId: number): Promise<MenuItemRow[]> {
    return [...this.state.menu.values()]
      .filter((m) => m.config_id === configId && !m.deleted)
      .map(({ product_id, name, category_id, price_excl, tax_labels, available, attributes }) => structuredClone({ product_id, name, category_id, price_excl, tax_labels, available, attributes }));
  }
  async catalogEntries(channel: string, ids: number[]): Promise<Map<number, CatalogEntry>> {
    const out = new Map<number, CatalogEntry>();
    for (const id of ids) {
      const c = this.state.catalog.get(`${channel}:${id}`);
      if (c) out.set(id, c);
    }
    return out;
  }
  async catalogCategories(channel: string): Promise<{ category_id: number; name: string; sequence: number }[]> {
    return this.state.categories.get(channel) ?? [];
  }
  async brandOf(channel: string): Promise<string | null> {
    return this.state.brands.get(channel) ?? null;
  }
  async configsToSync(companies: readonly FnbCompanyCode[]): Promise<MenuSyncRow[]> {
    return [...this.state.sync.values()].filter((s) => companies.includes(s.company_code)).map((s) => structuredClone(s));
  }
  async expireDue(now: Date): Promise<number> {
    let n = 0;
    for (const s of this.state.sessions.values()) {
      if (s.status === 'open' && s.expires_at <= now) {
        Object.assign(s, { status: 'expired', close_reason: 'expired', closed_at: now });
        n += 1;
      }
    }
    return n;
  }
  async stuckSubmitting(before: Date): Promise<{ submission: SubmissionRow; session: TableSessionRow }[]> {
    return [...this.state.subs.values()]
      .filter((x) => x.status === 'submitting' && x.updated_at < before)
      .map((x) => ({ submission: structuredClone(x), session: structuredClone(this.state.sessions.get(x.session_id) as TableSessionRow) }));
  }
  async gauges(now: Date): Promise<FnbGauges> {
    const sessions = [...this.state.sessions.values()];
    return {
      open: sessions.filter((s) => s.status === 'open').length,
      overdue: sessions.filter((s) => s.status === 'open' && (s.opened_at.getTime() < now.getTime() - 4 * 3_600_000 || s.expires_at < now)).length,
      stuck: [...this.state.subs.values()].filter((x) => x.status === 'submitting' && x.updated_at.getTime() < now.getTime() - 300_000).length,
      earnPending: 0,
    };
  }
  async retention(now: Date, cfg: { retentionDays: number; memberLinkDays: number }): Promise<FnbRetentionResult> {
    let unlinked = 0;
    let sessionsDeleted = 0;
    for (const s of [...this.state.sessions.values()]) {
      const end = (s.closed_at ?? s.expires_at).getTime();
      if (s.status !== 'open' && s.member_id && end < now.getTime() - cfg.memberLinkDays * 86_400_000) {
        s.member_id = null;
        unlinked += 1;
      }
      if (s.status !== 'open' && end < now.getTime() - cfg.retentionDays * 86_400_000) {
        this.state.sessions.delete(s.id);
        for (const x of [...this.state.subs.values()]) if (x.session_id === s.id) this.state.subs.delete(x.id);
        sessionsDeleted += 1;
      }
    }
    return { unlinked, sessionsDeleted, menuDeleted: 0 };
  }

  /** Bantuan test: config QR aktif dengan menu demo yang sudah disinkron. */
  seedConfig(configId: number, company: FnbCompanyCode, items: QrMenuItem[], mode: 'auto' | 'waiter' = 'waiter'): void {
    this.state.sync.set(configId, { config_id: configId, company_code: company, outlet_name: 'Outlet Demo', qr_enabled: true, confirm_mode: mode, since: null, cursor: null, last_ok_at: new Date(this.now()) });
    for (const i of items) this.state.menu.set(`${configId}:${i.product_id}`, { ...structuredClone(i), config_id: configId, deleted: false });
  }
}

/** Fake earn: mencatat panggilan; hasil dapat diatur. */
export class FakeFnbEarn implements FnbEarnPort {
  readonly calls: { memberId: string; orderUuid: string; eligibleAmount: number }[] = [];
  next: 'earned' | 'replay' | 'limited' | 'conflict' | 'skipped' = 'earned';
  async earn(_r: FnbRepo, input: { memberId: string; orderUuid: string; eligibleAmount: number }): Promise<{ outcome: FakeFnbEarn['next']; points: number }> {
    this.calls.push({ memberId: input.memberId, orderUuid: input.orderUuid, eligibleAmount: input.eligibleAmount });
    return { outcome: this.next, points: this.next === 'earned' ? Math.floor(input.eligibleAmount / 10_000) : 0 };
  }
}
