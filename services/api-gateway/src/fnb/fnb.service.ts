import { Inject, Injectable, Logger } from '@nestjs/common';
import { randomUUID } from 'node:crypto';
import { AppConfig, isFnbCompanyCode } from '../config/app-config';
import { ApiError, Errors } from '../common/errors/api-error';
import { MemberService } from '../common/member/member.service';
import { redisKeyHash } from '../common/pii/pii';
import { RateLimiter } from '../common/rate-limit/rate-limiter';
import { AccessTokenService } from '../common/security/access-token.service';
import { isUuid } from '../common/validation';
import { FnbMetrics } from './fnb-metrics';
import { verifyTableToken } from './fnb-token';
import {
  DisplayLine,
  FnbErrors,
  IDEMPOTENCY_KEY_RE,
  SubmissionRow,
  SubmissionStatus,
  SubmitInput,
  TableSessionRow,
  parseSubmitBody,
  storedLines,
  submitSha256,
} from './fnb.model';
import { FNB_REPO, FnbRepo, MenuSyncRow } from './fnb.repository';
import { MenuAttribute, ODOO_QR, OdooQrApi, OdooQrError, QrSubmitResult } from './odoo-qr';

/** Verifikasi ALTCHA (AltchaService; antarmuka sempit agar mudah di-fake). */
export interface AltchaVerifier {
  verify(payload: string | undefined, ip: string | null): Promise<boolean>;
}
export const FNB_ALTCHA = Symbol('FNB_ALTCHA');

/** Konteks HTTP yang relevan (tanpa objek request mentah). Token TIDAK PERNAH di-log. */
export interface FnbRequestContext {
  tableToken: unknown;
  authorization: unknown;
  ip: string | null;
}

export interface SessionView {
  outlet_name: string;
  table_label: string;
  company_code: string;
  brand_code: string | null;
  expires_at: string;
  confirm_mode: 'auto' | 'waiter';
  limits: { max_lines: number; max_line_qty: number; submissions_left: number };
  points: { claimable: boolean; claimed: boolean; claimed_by_me?: boolean };
}
export interface MenuView {
  categories: { category_id: number; name: string; sequence: number; image_sha: null }[];
  products: {
    product_id: number;
    name: string;
    category_id: number | null;
    image_sha: string | null;
    price_excl: number;
    tax_labels: string[];
    price_note: string;
    available: boolean;
    attributes: MenuAttribute[];
  }[];
  menu_synced_at: string | null;
}
export interface SubmitView {
  submission_id: string;
  status: SubmissionStatus;
  lines: DisplayLine[];
  indicative_subtotal_excl: number | null;
}

const BEARER_RE = /^Bearer\s+([A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]+)$/i;
const PRICE_NOTE = 'belum termasuk pajak & layanan';

/**
 * API publik QR meja (docs/04 §14.A.3, ADR-032 Accepted B, L-70/L-71/L-76).
 *
 * Urutan pemeriksaan setiap request: kill switch `FNB_QR_ENABLED` (503) → tanda tangan token `fts1` dari header
 * `X-Table-Session` (SEBELUM DB; gagal apa pun → `401 table_session_invalid` generik) → rate limit per IP (HMAC) →
 * sesi di DB (tidak ada → 401; tidak `open` → 409) → PT ∈ `FNB_QR_COMPANIES` & `efn_qr_enabled` config (503) →
 * rate limit per sesi. `Authorization: Bearer` opsional; ada tetapi tidak valid → `401 unauthorized`.
 *
 * Kiriman (L-70: satu kiriman = satu `pos.order` draft baru): idempotensi `UNIQUE(session, Idempotency-Key)` +
 * `request_sha256`; kunci Odoo `fnb:submit:<submission_id>`. Transport gagal → rekonsiliasi `efn_qr_status` sebelum
 * `failed` (tidak ada pesanan ganda di dapur); Odoo mati/circuit → `503 ordering_unavailable` (tidak diantrekan).
 * Catatan pesanan tidak disimpan/di-log (hanya `has_note`).
 */
@Injectable()
export class FnbService {
  private readonly logger = new Logger('FnbQr');
  now: () => number = () => Date.now();

  constructor(
    private readonly config: AppConfig,
    @Inject(FNB_REPO) private readonly repo: FnbRepo,
    @Inject(ODOO_QR) private readonly odoo: OdooQrApi,
    private readonly limiter: RateLimiter,
    private readonly metrics: FnbMetrics,
    @Inject(FNB_ALTCHA) private readonly altcha: AltchaVerifier,
    private readonly tokens: AccessTokenService,
    private readonly members: MemberService,
  ) {}

  // ───────────── sesi dari token ─────────────
  private tokenKeys(): Buffer[] {
    const keys = [this.config.fnbTableTokenKey];
    const prev = this.config.fnbTableTokenKeyPrev;
    if (prev) keys.push(prev);
    return keys;
  }

  private async limit(name: Parameters<AppConfig['fnbRateLimit']>[0], key: string): Promise<void> {
    const rl = this.config.fnbRateLimit(name);
    await this.limiter.enforce(`fnb:${name.toLowerCase()}:${key}`, rl.limit, rl.windowSeconds);
  }

  private ipKey(ip: string | null): string {
    return redisKeyHash(this.config.redisKeyPepper, ip ?? 'unknown');
  }

  /**
   * Verifikasi token → sesi. `requireOpen=false` dipakai GET orders (sesi ditutup tetap boleh melihat status? TIDAK —
   * kontrak A.3: 409 untuk semua endpoint sesi tidak open). Melempar error kontrak.
   */
  async resolve(ctx: FnbRequestContext, ipLimit: 'IP_READ' | 'SUBMIT_IP' = 'IP_READ'): Promise<{ session: TableSessionRow; sync: MenuSyncRow }> {
    if (!this.config.fnbQrEnabled) throw FnbErrors.orderingUnavailable();
    let keys: Buffer[];
    try {
      keys = this.tokenKeys();
    } catch {
      this.logger.error('Kunci token sesi meja (fnb_table_token_key) tidak tersedia — QR ditolak');
      throw FnbErrors.orderingUnavailable();
    }
    const verified = verifyTableToken(ctx.tableToken, keys, Math.floor(this.now() / 1000));
    // rate limit IP SETELAH cek tanda tangan murah tetapi SEBELUM DB (token palsu tetap dihitung agar tidak gratis)
    await this.limit(ipLimit, this.ipKey(ctx.ip));
    if (!verified) throw FnbErrors.sessionInvalid();
    const session = await this.repo.getSession(verified.sessionId);
    if (!session) throw FnbErrors.sessionInvalid();
    if (session.status !== 'open') throw FnbErrors.sessionClosed();
    if (session.expires_at.getTime() <= this.now()) throw FnbErrors.sessionInvalid();
    if (!isFnbCompanyCode(session.company_code) || !this.config.fnbQrCompanies.includes(session.company_code)) throw FnbErrors.orderingUnavailable();
    const sync = await this.repo.menuSync(session.config_id);
    if (!sync || !sync.qr_enabled || sync.company_code !== session.company_code) throw FnbErrors.orderingUnavailable();
    return { session, sync };
  }

  /** Bearer opsional: tidak ada → null; ada tetapi tidak valid / member tidak aktif → 401 unauthorized (A.3). */
  async optionalMember(authorization: unknown): Promise<{ memberId: string; memberHash: string } | null> {
    if (authorization === undefined || authorization === null || authorization === '') return null;
    const m = typeof authorization === 'string' ? BEARER_RE.exec(authorization.trim()) : null;
    if (!m) throw Errors.unauthorized();
    const claims = this.tokens.verify(m[1]);
    if (!claims) throw Errors.unauthorized();
    const member = await this.members.findActive(claims.memberId);
    if (!member) throw Errors.unauthorized();
    return { memberId: member.id, memberHash: member.memberHash };
  }

  // ───────────── GET /v1/fnb/session ─────────────
  async session(ctx: FnbRequestContext): Promise<SessionView> {
    const { session, sync } = await this.resolve(ctx);
    await this.limit('SESSION_READ', session.id);
    const me = await this.optionalMember(ctx.authorization);
    const claimed = session.member_id !== null;
    const points: SessionView['points'] = { claimable: this.config.fnbQrPointsEnabled && !claimed, claimed };
    if (me) points.claimed_by_me = session.member_id === me.memberId;
    return {
      outlet_name: sync.outlet_name ?? '',
      table_label: session.table_label,
      company_code: session.company_code,
      brand_code: await this.repo.brandOf(session.company_code),
      expires_at: session.expires_at.toISOString(),
      confirm_mode: sync.confirm_mode,
      limits: {
        max_lines: this.config.fnbMaxLines,
        max_line_qty: this.config.fnbMaxLineQty,
        submissions_left: Math.max(0, this.config.fnbMaxSubmissionsPerSession - session.submissions_count),
      },
      points,
    };
  }

  // ───────────── GET /v1/fnb/menu ─────────────
  /** A.6: overlay per config ∩ katalog kanal PT; produk overlay tanpa salinan katalog tetap tampil dengan nama overlay. */
  async menu(ctx: FnbRequestContext): Promise<MenuView> {
    const { session, sync } = await this.resolve(ctx);
    await this.limit('SESSION_READ', session.id);
    const items = await this.repo.menuItems(session.config_id);
    const catalog = await this.repo.catalogEntries(session.company_code, items.map((i) => i.product_id));
    const products = items
      .map((i) => {
        const c = catalog.get(i.product_id);
        const name = c?.name || i.name || '';
        return {
          product_id: i.product_id,
          name,
          category_id: c?.category_id ?? i.category_id,
          image_sha: c?.image_sha ?? null,
          price_excl: i.price_excl,
          tax_labels: i.tax_labels,
          price_note: PRICE_NOTE,
          available: i.available,
          attributes: i.attributes,
        };
      })
      .filter((p) => p.name.length > 0);
    const used = new Set(products.map((p) => p.category_id).filter((x): x is number => x !== null));
    const categories = (await this.repo.catalogCategories(session.company_code))
      .filter((c) => used.has(c.category_id))
      .sort((a, b) => a.sequence - b.sequence || a.category_id - b.category_id)
      .map((c) => ({ ...c, image_sha: null }));
    return { categories, products, menu_synced_at: sync.last_ok_at ? sync.last_ok_at.toISOString() : null };
  }

  // ───────────── POST /v1/fnb/orders ─────────────
  async submit(ctx: FnbRequestContext, idempotencyKey: unknown, body: unknown): Promise<SubmitView> {
    if (idempotencyKey === undefined || idempotencyKey === null || idempotencyKey === '') throw FnbErrors.idempotencyKeyRequired();
    if (typeof idempotencyKey !== 'string' || !IDEMPOTENCY_KEY_RE.test(idempotencyKey)) throw Errors.invalidRequest('Idempotency-Key tidak valid.');
    const input = parseSubmitBody(body, { maxLines: this.config.fnbMaxLines, maxLineQty: this.config.fnbMaxLineQty });
    const { session } = await this.resolve(ctx, 'SUBMIT_IP');
    try {
      await this.limit('SUBMIT_SESSION', session.id);
    } catch (err) {
      this.metrics.anomaly('submit_rate_limited', 'warning', { table_session_id: session.id });
      throw err;
    }
    const sha = submitSha256(input);
    // replay cepat (tanpa kunci baris)
    const prev = await this.repo.getSubmissionByKey(session.id, idempotencyKey);
    if (prev) return this.replay(session, prev, sha, input);
    await this.captcha(input, session, ctx.ip);
    const created = await this.create(session, idempotencyKey, sha, input);
    if ('replay' in created) return this.replay(session, created.replay, sha, input);
    return this.sendToOdoo(session, created.submission, input);
  }

  private async captcha(input: SubmitInput, session: TableSessionRow, ip: string | null): Promise<void> {
    const mode = this.config.fnbCaptchaMode;
    if (mode === 'off') return;
    // ASUMSI: "kiriman pertama per sesi per perangkat" didekati per sesi (BFF tidak mengenal perangkat tamu)
    if (mode === 'first_submit' && session.submissions_count > 0) return;
    if (!(await this.altcha.verify(input.altcha ?? undefined, ip))) throw Errors.captchaRequired();
  }

  private async create(
    s: TableSessionRow,
    key: string,
    sha: string,
    input: SubmitInput,
  ): Promise<{ submission: SubmissionRow } | { replay: SubmissionRow }> {
    const names = new Map((await this.repo.menuItems(s.config_id)).map((i) => [i.product_id, i.name ?? '']));
    return this.repo.tx(async (r) => {
      const session = await r.getSession(s.id, true);
      if (!session || session.status !== 'open' || session.expires_at.getTime() <= this.now()) throw FnbErrors.sessionClosed();
      const again = await r.getSubmissionByKey(session.id, key);
      if (again) return { replay: again };
      const items = input.lines.reduce((t, l) => t + l.qty, 0);
      if (session.submissions_count >= this.config.fnbMaxSubmissionsPerSession || session.items_count + items > this.config.fnbMaxItemsPerSession) {
        this.metrics.anomaly('session_limit_hit', 'warning', { table_session_id: session.id });
        throw FnbErrors.sessionLimitReached();
      }
      const id = randomUUID();
      const lines = storedLines(input);
      await r.insertSubmission({
        id,
        session_id: session.id,
        idempotency_key: key,
        request_sha256: sha,
        lines,
        items_count: items,
        display: {
          lines: input.lines.map((l) => ({
            product_id: l.product_id,
            name: names.get(l.product_id) ?? '',
            qty: l.qty,
            attribute_value_ids: l.attribute_value_ids,
            attributes_text: null,
          })),
          indicative_subtotal_excl: null,
        },
        status: 'submitting',
      });
      await r.updateSession(session.id, { submissions_count: session.submissions_count + 1, items_count: session.items_count + items });
      this.metrics.submission('submitting');
      const row = await r.getSubmission(session.id, id);
      if (!row) throw new Error('kiriman hilang setelah insert');
      return { submission: row };
    });
  }

  /** Replay: body beda → 409; `submitting` → kirim ulang ke Odoo (idempoten); `failed` → 503; lainnya → status terkini. */
  private async replay(session: TableSessionRow, prev: SubmissionRow, sha: string, input: SubmitInput): Promise<SubmitView> {
    if (prev.request_sha256 !== sha) throw FnbErrors.idempotencyConflict();
    if (prev.status === 'submitting') return this.sendToOdoo(session, prev, input);
    if (prev.status === 'failed') throw FnbErrors.orderingUnavailable();
    return this.view(prev);
  }

  private view(s: SubmissionRow): SubmitView {
    return {
      submission_id: s.id,
      status: s.status,
      lines: s.display?.lines ?? s.lines.map((l) => ({ product_id: l.product_id, name: '', qty: l.qty, attribute_value_ids: l.attribute_value_ids, attributes_text: null })),
      indicative_subtotal_excl: s.display?.indicative_subtotal_excl ?? null,
    };
  }

  private async sendToOdoo(session: TableSessionRow, sub: SubmissionRow, input: SubmitInput): Promise<SubmitView> {
    if (this.odoo.isCircuitOpen(session.company_code)) {
      await this.finish(sub, 'failed', null);
      throw FnbErrors.orderingUnavailable();
    }
    let res: QrSubmitResult;
    try {
      res = await this.odoo.submit(session.company_code, session.config_id, session.id, session.order_uuid, {
        idempotency_key: `fnb:submit:${sub.id}`,
        submission_ref: sub.id,
        lines: input.lines.map((l) => ({ product_id: l.product_id, qty: l.qty, attribute_value_ids: l.attribute_value_ids, note: l.note })),
      });
    } catch (err) {
      const recovered = await this.onSubmitError(session, sub, err, input);
      if (recovered instanceof ApiError) throw recovered;
      return this.view(recovered); // Odoo ternyata sudah menerima (rekonsiliasi) → 202 status terkini
    }
    if (res.submission_ref !== sub.id) {
      this.metrics.anomaly('odoo_config_error', 'critical', { table_session_id: session.id, submission_id: sub.id, code: 'submission_ref_mismatch' });
      await this.finish(sub, 'failed', null);
      throw FnbErrors.orderingUnavailable();
    }
    const updated = await this.applyResult(sub, res);
    return this.view(updated);
  }

  private async applyResult(sub: SubmissionRow, res: QrSubmitResult): Promise<SubmissionRow> {
    return this.repo.tx(async (r) => {
      const cur = await r.getSubmission(sub.session_id, sub.id, true);
      if (!cur) throw new Error('kiriman hilang');
      if (cur.status === 'submitting') {
        await r.updateSubmission(cur.id, {
          status: res.state,
          odoo_state: res.state,
          order_uuid: res.order_uuid,
          order_state: res.order_uuid ? 'draft' : null,
          display: { lines: res.lines.length > 0 ? res.lines : (cur.display?.lines ?? []), indicative_subtotal_excl: res.amount_indicative_excl },
        });
        this.metrics.submission(res.state);
      }
      return (await r.getSubmission(sub.session_id, sub.id)) ?? cur;
    });
  }

  /** `failed` final + kuota sesi dikembalikan (kiriman yang tidak pernah masuk dapur tidak memakan batas). */
  private async finish(sub: SubmissionRow, status: 'failed', odooState: string | null): Promise<void> {
    await this.repo.tx(async (r) => {
      const cur = await r.getSubmission(sub.session_id, sub.id, true);
      if (!cur || cur.status !== 'submitting') return;
      await r.updateSubmission(cur.id, { status, odoo_state: odooState });
      const s = await r.getSession(sub.session_id, true);
      if (s) {
        await r.updateSession(s.id, {
          submissions_count: Math.max(0, s.submissions_count - 1),
          items_count: Math.max(0, s.items_count - cur.items_count),
        });
      }
      this.metrics.submission(status);
    });
  }

  /**
   * QA G22 P3 r15: `product_ids` untuk `409 item_unavailable` — hanya id yang ADA di kiriman ini (tidak membocorkan menu
   * lain). Sumber: (1) id dari Odoo `efn_qr:item_unavailable:<id>,…` bila dikirim; (2) cadangan: overlay menu BFF
   * (produk tidak ada / `available=false`). Overlay bisa basi (Odoo lebih baru) → bisa `[]`; FE tetap meminta "perbarui".
   */
  private async unavailableIds(session: TableSessionRow, input: SubmitInput, fromOdoo: readonly number[]): Promise<number[]> {
    const submitted = new Set(input.lines.map((l) => l.product_id));
    const direct = [...new Set(fromOdoo)].filter((id) => submitted.has(id));
    if (direct.length > 0) return direct.sort((a, b) => a - b);
    try {
      const menu = new Map((await this.repo.menuItems(session.config_id)).map((i) => [i.product_id, i.available]));
      return [...submitted].filter((id) => menu.get(id) !== true).sort((a, b) => a - b);
    } catch {
      return [];
    }
  }

  /** A.5 peta galat Odoo → HTTP; transport → rekonsiliasi `efn_qr_status` dulu. */
  private async onSubmitError(session: TableSessionRow, sub: SubmissionRow, err: unknown, input: SubmitInput): Promise<ApiError | SubmissionRow> {
    if (err instanceof OdooQrError && err.kind === 'business') {
      const code = err.code;
      await this.finish(sub, 'failed', code);
      if (code === 'session_closed' || code === 'pos_session_closed') return FnbErrors.sessionClosed();
      if (code === 'item_unavailable') return FnbErrors.itemUnavailable(await this.unavailableIds(session, input, err.productIds));
      if (code === 'limit_exceeded') return FnbErrors.sessionLimitReached();
      if (code !== 'qr_disabled') this.metrics.anomaly('odoo_config_error', 'critical', { table_session_id: session.id, submission_id: sub.id, code: code ?? 'unknown' });
      return FnbErrors.orderingUnavailable();
    }
    if (err instanceof OdooQrError && (err.kind === 'auth' || err.kind === 'not_found' || err.kind === 'invalid_response')) {
      // invalid_response: Odoo MUNGKIN sudah membuat order → rekonsiliasi dulu (bukan langsung failed)
      if (err.kind !== 'invalid_response') {
        await this.finish(sub, 'failed', err.kind);
        this.metrics.anomaly('odoo_config_error', 'critical', { table_session_id: session.id, submission_id: sub.id, code: err.kind });
        return FnbErrors.orderingUnavailable();
      }
    }
    // transport / timeout / circuit / busy / respons rusak → apakah Odoo sudah menerima?
    const outcome = await this.reconcile(session, sub);
    if (outcome === 'applied') {
      const fresh = await this.repo.getSubmission(session.id, sub.id);
      if (fresh) return fresh;
    }
    return FnbErrors.orderingUnavailable();
  }

  /**
   * Rekonsiliasi kiriman `submitting` lewat `efn_qr_status` (A.3, A.5 #15): ditemukan → status Odoo diterapkan
   * (`submit_reconciled`); `absent` → `failed`; status tidak bisa ditanya → tetap `submitting` (job mencoba lagi).
   */
  async reconcile(session: TableSessionRow, sub: SubmissionRow): Promise<'applied' | 'failed' | 'unknown'> {
    let rows;
    try {
      rows = await this.odoo.status(session.company_code, session.config_id, [sub.id]);
    } catch {
      return 'unknown';
    }
    const row = rows.find((x) => x.submission_ref === sub.id);
    if (!row || row.state === 'absent') {
      await this.finish(sub, 'failed', 'absent');
      this.metrics.anomaly('submit_failed', 'warning', { table_session_id: session.id, submission_id: sub.id, code: 'absent' });
      return 'failed';
    }
    const status = mapOdooSubmissionState(row.state);
    if (!status) return 'unknown';
    await this.repo.tx(async (r) => {
      const cur = await r.getSubmission(sub.session_id, sub.id, true);
      if (!cur || cur.status !== 'submitting') return;
      await r.updateSubmission(cur.id, {
        status,
        odoo_state: row.state.slice(0, 32),
        ...(row.order_uuid ? { order_uuid: row.order_uuid, order_state: 'draft' as const } : {}),
      });
      this.metrics.submission(status);
    });
    this.metrics.anomaly('submit_reconciled', 'warning', { table_session_id: session.id, submission_id: sub.id });
    return 'applied';
  }

  // ───────────── GET /v1/fnb/orders(/:id) ─────────────
  async list(ctx: FnbRequestContext): Promise<{
    items: { submission_id: string; created_at: string; status: SubmissionStatus; lines: { name: string; qty: number; attribute_value_ids: number[]; attributes_text: string | null }[] }[];
  }> {
    const { session } = await this.resolve(ctx);
    await this.limit('ORDERS_SESSION', session.id);
    const subs = await this.repo.listSubmissions(session.id);
    return {
      items: subs.map((s) => ({
        submission_id: s.id,
        created_at: s.created_at.toISOString(),
        status: s.status,
        lines: (s.display?.lines ?? []).map((l) => ({
          name: l.name,
          qty: l.qty,
          attribute_value_ids: l.attribute_value_ids ?? [],
          attributes_text: l.attributes_text ?? null,
        })),
      })),
    };
  }

  async one(ctx: FnbRequestContext, id: string): Promise<{ submission_id: string; status: SubmissionStatus; updated_at: string }> {
    const { session } = await this.resolve(ctx);
    await this.limit('ORDERS_SESSION', session.id);
    if (!isUuid(id)) throw Errors.notFound();
    const s = await this.repo.getSubmission(session.id, id.toLowerCase());
    if (!s) throw Errors.notFound(); // API1: id milik sesi lain = tidak ada
    return { submission_id: s.id, status: s.status, updated_at: s.updated_at.toISOString() };
  }

  // ───────────── klaim poin (A.3) ─────────────
  async claim(ctx: FnbRequestContext): Promise<{ claimed: true }> {
    const me = await this.optionalMember(ctx.authorization);
    if (!me) throw Errors.unauthorized();
    await this.limit('CLAIM_MEMBER', me.memberId);
    const { session } = await this.resolve(ctx);
    if (!this.config.fnbQrPointsEnabled) throw FnbErrors.pointsUnavailable();
    return this.repo.tx(async (r) => {
      const s = await r.getSession(session.id, true);
      if (!s || s.status !== 'open') throw FnbErrors.sessionClosed();
      if (s.member_id === me.memberId) return { claimed: true as const };
      if (s.member_id !== null) throw FnbErrors.pointsAlreadyClaimed();
      if (await r.hasActiveDelete(me.memberId)) throw FnbErrors.accountDeleting();
      const at = new Date(this.now());
      await r.updateSession(s.id, { member_id: me.memberId, member_claimed_at: at });
      await r.touchActivity(me.memberHash, 'qr_claim', at); // §14.D.2
      return { claimed: true as const };
    });
  }

  async unclaim(ctx: FnbRequestContext): Promise<void> {
    const me = await this.optionalMember(ctx.authorization);
    if (!me) throw Errors.unauthorized();
    await this.limit('CLAIM_MEMBER', me.memberId);
    const { session } = await this.resolve(ctx);
    await this.repo.tx(async (r) => {
      const s = await r.getSession(session.id, true);
      if (!s || s.member_id !== me.memberId) throw Errors.notFound();
      if (s.status !== 'open') throw FnbErrors.sessionClosed();
      await r.updateSession(s.id, { member_id: null, member_claimed_at: null });
    });
  }
}

/** State Odoo kiriman → status BFF (A.3; status turunan tiket KDS dihitung Odoo). */
export function mapOdooSubmissionState(state: string): SubmissionStatus | null {
  switch (state) {
    case 'accepted':
    case 'pending_confirmation':
    case 'preparing':
    case 'ready':
    case 'served':
    case 'rejected':
    case 'cancelled':
      return state;
    case 'in_kitchen':
      return 'preparing';
    default:
      return null;
  }
}
