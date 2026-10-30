import { Inject, Injectable, Logger } from '@nestjs/common';
import { createHash, randomUUID } from 'node:crypto';
import { AppConfig, FnbCompanyCode, isFnbCompanyCode } from '../config/app-config';
import { ApiError, Errors } from '../common/errors/api-error';
import { RateLimiter } from '../common/rate-limit/rate-limiter';
import { isUuid } from '../common/validation';
import { FnbMetrics } from './fnb-metrics';
import { issueTableToken } from './fnb-token';
import {
  CloseReason,
  EarnStatus,
  FnbErrors,
  OPEN_KEY_RE,
  OrderState,
  SubmissionStatus,
  TableSessionRow,
  canTransitionSubmission,
  validTableLabel,
} from './fnb.model';
import { FNB_REPO, FnbRepo } from './fnb.repository';

export const FNB_EVENTS_CONSUMER = 'fnb.events';
/** QA G22 P3 r15: 16 KB (sebelumnya 8 KB) — Odoo `MAX_BODY_BYTES` harus dinaikkan sama. */
export const FNB_EVENT_BODY_LIMIT = 16 * 1024;
/** QA G22 P3 r15: maksimum entri `close.orders[]` (sebelumnya 50). Lebih → 400 (final di outbox Odoo + alert). */
export const FNB_CLOSE_MAX_ORDERS = 100;

/** A.4 `POST /v1/pos/fnb/table-sessions`. */
export interface OpenSessionInput {
  idempotency_key: string;
  company_code: FnbCompanyCode;
  config_id: number;
  table_id: number;
  table_label: string;
  order_uuid: string | null;
  reissue: boolean;
  print_seq: number;
}
export interface OpenSessionView {
  table_session_id: string;
  qr_url: string;
  expires_at: string;
}

/** L-70: hasil bayar/batal per `pos.order` (kiriman QR atau order pelayan di meja). */
export interface OrderClose {
  order_uuid: string;
  reason: 'paid' | 'cancelled';
  eligible_amount: number | null;
  /**
   * K2/SEC-165: `true` = member sudah dilekatkan di kasir (POS meng-earn); `null` = field TIDAK dikirim Odoo (pelanggaran
   * kontrak r15) → BFF fail-closed: tidak meng-earn + anomali critical (bukan dianggap `false` seperti sebelumnya).
   */
  pos_member_attached: boolean | null;
  company_code: string | null;
  /** G31b §20.10: dasar MGM neto (≤ eligible_amount); null = tidak dikirim → dasar earn. */
  mgm_eligible_amount: number | null;
}
export interface FnbEvent {
  idempotency_key: string;
  seq: number;
  table_session_id: string;
  company_code: string;
  config_id: number;
  event: string;
  submission: { submission_ref: string; status: string; order_uuid: string | null } | null;
  close: { reason: CloseReason; orders: OrderClose[] } | null;
  order: OrderClose | null;
  occurred_at: Date | null;
}
export type FnbEventOutcome = 'applied' | 'duplicate' | 'ignored_stale' | 'ignored_unknown_event';

/**
 * Earn QR (D-22-5) di DALAM transaksi event — implementasi PostgreSQL di `fnb-earn.ts` (PosLoyaltyService.earnQrInTx);
 * test memakai fake. `skipped` = member tidak aktif / poin QR dimatikan.
 */
export interface FnbEarnPort {
  earn(
    r: FnbRepo,
    input: { memberId: string; orderUuid: string; companyCode: FnbCompanyCode; eligibleAmount: number; configId: number },
  ): Promise<{ outcome: 'earned' | 'replay' | 'limited' | 'conflict' | 'skipped'; points: number }>;
}
export const FNB_EARN = Symbol('FNB_EARN');

const MAX_AMOUNT = 10_000_000_000;

function sha256(v: unknown): string {
  return createHash('sha256').update(JSON.stringify(v), 'utf8').digest('hex');
}

/** A.4 body table-sessions (`additionalProperties:false`). */
export function parseOpenSession(body: unknown): OpenSessionInput {
  if (typeof body !== 'object' || body === null || Array.isArray(body)) throw Errors.invalidRequest();
  const o = body as Record<string, unknown>;
  const keys = ['idempotency_key', 'company_code', 'config_id', 'table_id', 'table_label', 'order_uuid', 'reissue'];
  if (Object.keys(o).some((k) => !keys.includes(k))) throw Errors.invalidRequest();
  if (typeof o.idempotency_key !== 'string') throw Errors.invalidRequest();
  const m = OPEN_KEY_RE.exec(o.idempotency_key);
  if (!m) throw Errors.invalidRequest();
  if (!isFnbCompanyCode(o.company_code)) throw Errors.invalidRequest();
  const posInt = (v: unknown): number => {
    if (typeof v !== 'number' || !Number.isSafeInteger(v) || v < 1 || v > 2_147_483_647) throw Errors.invalidRequest();
    return v;
  };
  if (!validTableLabel(o.table_label)) throw Errors.invalidRequest();
  const configId = posInt(o.config_id);
  const tableId = posInt(o.table_id);
  // Bentuk Odoo E-27: kunci fnb_open:<config>:<table>:<n> harus cocok dengan body; order_uuid opsional
  if (m[1] !== undefined && (Number(m[1]) !== configId || Number(m[2]) !== tableId)) throw Errors.invalidRequest();
  let orderUuid: string | null = null;
  if (o.order_uuid !== undefined && o.order_uuid !== null) {
    if (typeof o.order_uuid !== 'string' || !/^[A-Za-z0-9-]{1,64}$/.test(o.order_uuid)) throw Errors.invalidRequest();
    orderUuid = o.order_uuid;
  }
  if (m[3] !== undefined && m[3] !== orderUuid) throw Errors.invalidRequest(); // bentuk kontrak awal: key memuat order_uuid
  if (o.reissue !== undefined && typeof o.reissue !== 'boolean') throw Errors.invalidRequest();
  return {
    idempotency_key: o.idempotency_key,
    company_code: o.company_code,
    config_id: configId,
    table_id: tableId,
    table_label: o.table_label.trim(),
    order_uuid: orderUuid,
    reissue: o.reissue === true,
    print_seq: Number(m[4]),
  };
}

function parseOrderClose(v: unknown): OrderClose {
  if (typeof v !== 'object' || v === null || Array.isArray(v)) throw Errors.invalidRequest();
  const o = v as Record<string, unknown>;
  // L-80: `company_code` per order (harus = PT sesi, dicek saat diterapkan)
  if (Object.keys(o).some((k) => !['order_uuid', 'reason', 'eligible_amount', 'pos_member_attached', 'state', 'company_code', 'mgm_eligible_amount'].includes(k))) {
    throw Errors.invalidRequest();
  }
  if (o.company_code !== undefined && (typeof o.company_code !== 'string' || !/^[A-Z]{2,8}$/.test(o.company_code))) throw Errors.invalidRequest();
  if (typeof o.order_uuid !== 'string' || !/^[A-Za-z0-9-]{1,64}$/.test(o.order_uuid)) throw Errors.invalidRequest();
  // `reason` (L-70) atau `state` (alias) — paid|cancelled; absen di blok `close.order` kontrak A.4 = alasan sesi
  const reason = o.reason ?? o.state;
  if (reason !== undefined && reason !== 'paid' && reason !== 'cancelled') throw Errors.invalidRequest();
  let eligible: number | null = null;
  if (o.eligible_amount !== undefined && o.eligible_amount !== null) {
    if (typeof o.eligible_amount !== 'number' || !Number.isFinite(o.eligible_amount) || o.eligible_amount < 0 || o.eligible_amount > MAX_AMOUNT) {
      throw Errors.invalidRequest();
    }
    eligible = Math.floor(o.eligible_amount + 1e-9); // Rp1 ke bawah (memihak PT, pola §9.9)
  }
  if (o.pos_member_attached !== undefined && typeof o.pos_member_attached !== 'boolean') throw Errors.invalidRequest();
  let mgm: number | null = null;
  if (o.mgm_eligible_amount !== undefined && o.mgm_eligible_amount !== null) {
    const v = o.mgm_eligible_amount;
    if (typeof v !== 'number' || !Number.isFinite(v) || v < 0 || v > MAX_AMOUNT) throw Errors.invalidRequest();
    mgm = Math.floor(v + 1e-9);
    if (eligible === null || mgm > eligible) throw Errors.invalidRequest();
  }
  return {
    order_uuid: o.order_uuid,
    reason: (reason ?? 'paid') as 'paid' | 'cancelled',
    eligible_amount: eligible,
    pos_member_attached: typeof o.pos_member_attached === 'boolean' ? o.pos_member_attached : null,
    company_code: typeof o.company_code === 'string' ? o.company_code : null,
    mgm_eligible_amount: mgm,
  };
}

const EVENT_KEY_RE = /^fnb:([0-9a-fA-F-]{36}):([a-z_]{1,32}):(\d{1,9})$/;
const CLOSE_REASONS: readonly CloseReason[] = ['paid', 'cancelled', 'closed_by_staff', 'reissued'];

/** A.4 body event (≤ 8 KB, `additionalProperties:false`). Gagal → 400 (final di outbox Odoo). */
export function parseFnbEvent(raw: Buffer | undefined): FnbEvent {
  if (!raw || raw.length === 0 || raw.length > FNB_EVENT_BODY_LIMIT) throw Errors.invalidRequest();
  let o: Record<string, unknown>;
  try {
    const v: unknown = JSON.parse(raw.toString('utf8'));
    if (typeof v !== 'object' || v === null || Array.isArray(v)) throw new Error('bukan objek');
    o = v as Record<string, unknown>;
  } catch {
    throw Errors.invalidRequest();
  }
  const top = ['schema_version', 'idempotency_key', 'seq', 'table_session_id', 'company_code', 'config_id', 'event', 'submission', 'close', 'order', 'occurred_at'];
  if (Object.keys(o).some((k) => !top.includes(k))) throw Errors.invalidRequest();
  if (o.schema_version !== 1) throw Errors.invalidRequest();
  if (typeof o.seq !== 'number' || !Number.isSafeInteger(o.seq) || o.seq < 0) throw Errors.invalidRequest();
  if (!isUuid(o.table_session_id)) throw Errors.invalidRequest();
  const sid = o.table_session_id.toLowerCase();
  if (typeof o.idempotency_key !== 'string') throw Errors.invalidRequest();
  const km = EVENT_KEY_RE.exec(o.idempotency_key);
  if (!km || km[1].toLowerCase() !== sid || Number(km[3]) !== o.seq) throw Errors.invalidRequest();
  if (typeof o.event !== 'string' || !/^[a-z_]{1,32}$/.test(o.event) || km[2] !== o.event) throw Errors.invalidRequest();
  if (typeof o.company_code !== 'string' || !/^[A-Z]{2,8}$/.test(o.company_code)) throw Errors.invalidRequest();
  if (typeof o.config_id !== 'number' || !Number.isSafeInteger(o.config_id) || o.config_id < 1) throw Errors.invalidRequest();
  let submission: FnbEvent['submission'] = null;
  let close: FnbEvent['close'] = null;
  let order: OrderClose | null = null;
  if (o.event === 'submission_status') {
    const s = o.submission;
    if (typeof s !== 'object' || s === null || Array.isArray(s)) throw Errors.invalidRequest();
    const so = s as Record<string, unknown>;
    if (Object.keys(so).some((k) => !['submission_ref', 'status', 'order_uuid'].includes(k))) throw Errors.invalidRequest();
    if (!isUuid(so.submission_ref) || typeof so.status !== 'string' || !/^[a-z_]{1,32}$/.test(so.status)) throw Errors.invalidRequest();
    // K1 r15: `order_uuid` = `pos.order.uuid` (UUID) atau null
    const ou = so.order_uuid;
    if (ou !== undefined && ou !== null && !isUuid(ou)) throw Errors.invalidRequest();
    submission = { submission_ref: so.submission_ref.toLowerCase(), status: so.status, order_uuid: typeof ou === 'string' ? ou : null };
  } else if (o.event === 'session_closed') {
    const c = o.close;
    if (typeof c !== 'object' || c === null || Array.isArray(c)) throw Errors.invalidRequest();
    const co = c as Record<string, unknown>;
    if (Object.keys(co).some((k) => !['reason', 'order', 'orders'].includes(k))) throw Errors.invalidRequest();
    if (!(CLOSE_REASONS as readonly unknown[]).includes(co.reason)) throw Errors.invalidRequest();
    const orders: OrderClose[] = [];
    // kontrak A.4: `close.order` (satu); L-70: `close.orders` (banyak order per sesi meja, ≤ FNB_CLOSE_MAX_ORDERS)
    if (co.order !== undefined && co.order !== null) orders.push(parseOrderClose(co.order));
    if (co.orders !== undefined && co.orders !== null) {
      if (!Array.isArray(co.orders) || co.orders.length > FNB_CLOSE_MAX_ORDERS) throw Errors.invalidRequest();
      for (const x of co.orders) orders.push(parseOrderClose(x));
    }
    // reason sesi paid tanpa reason per order → order paid; sesi lain → reason per order WAJIB eksplisit
    close = { reason: co.reason as CloseReason, orders };
  } else if (o.event === 'order_closed') {
    order = parseOrderClose(o.order);
  }
  const occ = typeof o.occurred_at === 'string' && !Number.isNaN(Date.parse(o.occurred_at)) ? new Date(o.occurred_at) : null;
  return {
    idempotency_key: o.idempotency_key,
    seq: o.seq,
    table_session_id: sid,
    company_code: o.company_code,
    config_id: o.config_id,
    event: o.event,
    submission,
    close,
    order,
    occurred_at: occ,
  };
}

/**
 * Endpoint Odoo → BFF QR meja (docs/04 §14.A.4; path `/v1/pos/fnb/table-sessions` & `/v1/pos/fnb/events`, L-76).
 * mTLS + service key (+ HMAC `odoo_fnb_event_hmac` untuk event). Tidak pernah mencetak token/qr_url ke log.
 */
@Injectable()
export class FnbPosService {
  private readonly logger = new Logger('FnbPos');
  now: () => number = () => Date.now();

  constructor(
    private readonly config: AppConfig,
    @Inject(FNB_REPO) private readonly repo: FnbRepo,
    private readonly limiter: RateLimiter,
    private readonly metrics: FnbMetrics,
    @Inject(FNB_EARN) private readonly earn: FnbEarnPort,
  ) {}

  private qrUrl(company: FnbCompanyCode, session: Pick<TableSessionRow, 'id' | 'expires_at'>): string {
    const base = this.config.fnbQrBaseUrl(company);
    if (!base) throw FnbErrors.qrDisabled();
    const token = issueTableToken(this.config.fnbTableTokenKey, session.id, Math.floor(session.expires_at.getTime() / 1000));
    return `${base}/meja#t=${token}`;
  }

  async openSession(input: OpenSessionInput): Promise<OpenSessionView> {
    if (!this.config.fnbQrEnabled || !this.config.fnbQrCompanies.includes(input.company_code) || this.config.fnbQrBaseUrl(input.company_code) === null) {
      throw FnbErrors.qrDisabled();
    }
    const rl = this.config.fnbRateLimit('OPEN_CONFIG');
    await this.limiter.enforce(`fnb:open_config:${input.config_id}`, rl.limit, rl.windowSeconds);
    const reqSha = sha256({ ...input });
    let out: TableSessionRow;
    try {
      out = await this.repo.tx(async (r) => {
        const prev = await r.findSessionByOpenKey(input.idempotency_key);
        if (prev) {
          if (prev.open_request_sha256 !== reqSha) throw FnbErrors.idempotencyConflict();
          return prev; // replay → token dihitung ulang
        }
        const stored = await r.ensureMenuSync(input.config_id, input.company_code);
        if (stored !== input.company_code) {
          this.metrics.anomaly('company_mismatch', 'critical', { config_id: input.config_id, company: input.company_code });
          throw FnbErrors.companyMismatch();
        }
        const open = await r.findOpenSession(input.config_id, input.table_id, true);
        if (open) {
          if (!input.reissue) throw FnbErrors.tableSessionExists();
          await r.updateSession(open.id, { status: 'closed', close_reason: 'reissued', closed_at: new Date(this.now()) });
        }
        const openedAt = new Date(Math.floor(this.now() / 1000) * 1000);
        const row = {
          id: randomUUID(),
          company_code: input.company_code,
          config_id: input.config_id,
          table_id: input.table_id,
          table_label: input.table_label,
          order_uuid: input.order_uuid,
          open_idempotency_key: input.idempotency_key,
          open_request_sha256: reqSha,
          print_seq: input.print_seq,
          opened_at: openedAt,
          expires_at: new Date(openedAt.getTime() + this.config.fnbTableSessionTtlSeconds * 1000),
        };
        await r.insertSession(row);
        const fresh = await r.getSession(row.id);
        if (!fresh) throw new Error('sesi hilang setelah insert');
        return fresh;
      });
    } catch (err) {
      // balapan dua pembukaan meja yang sama (indeks unik parsial) → 409
      if (typeof err === 'object' && err !== null && (err as { code?: unknown }).code === '23505') throw FnbErrors.tableSessionExists();
      throw err;
    }
    this.logger.log(`EFN_FNB_SESSION ${JSON.stringify({ table_session_id: out.id, config_id: out.config_id, print_seq: out.print_seq })}`);
    return { table_session_id: out.id, qr_url: this.qrUrl(out.company_code, out), expires_at: out.expires_at.toISOString() };
  }

  /**
   * A.4 pemrosesan event (pola §10.5): satu transaksi; 404/409 dilempar sebelum commit → klaim ikut rollback.
   *
   * SEC-173 r15: rate limit `FNB_RL_EVENTS_*` per `config_id` (default 1200/60 s). Dipanggil dari handler controller,
   * jadi SELALU setelah `ServiceKeyGuard` + `FnbEventHmacGuard` (pengirim tanpa kedua secret tidak bisa menghabiskan
   * kuota event sah). 429 = retry di outbox Odoo; klaim idempotensi belum disentuh → tidak ada event yang hilang.
   */
  async handleEvent(ev: FnbEvent): Promise<{ ok: true; status: FnbEventOutcome }> {
    const rl = this.config.fnbRateLimit('EVENTS');
    try {
      await this.limiter.enforce(`fnb:events:${ev.config_id}`, rl.limit, rl.windowSeconds);
    } catch (err) {
      this.metrics.anomaly('events_rate_limited', 'warning', { config_id: ev.config_id, event: ev.event });
      throw err;
    }
    const status = await this.repo.tx(async (r): Promise<FnbEventOutcome> => {
      if (!(await r.claim(FNB_EVENTS_CONSUMER, ev.idempotency_key))) return 'duplicate';
      const s = await r.getSession(ev.table_session_id, true);
      if (!s) throw Errors.notFound();
      if (s.company_code !== ev.company_code || s.config_id !== ev.config_id) {
        this.metrics.anomaly('company_mismatch', 'critical', { table_session_id: s.id, config_id: ev.config_id });
        throw FnbErrors.companyMismatch();
      }
      if (!['submission_status', 'session_closed', 'order_closed'].includes(ev.event)) return 'ignored_unknown_event';
      if (ev.seq <= s.last_seq) return 'ignored_stale';
      await r.updateSession(s.id, { last_seq: ev.seq });
      if (ev.event === 'submission_status' && ev.submission) return this.applySubmission(r, s, ev.submission);
      if (ev.event === 'order_closed' && ev.order) {
        await this.applyOrder(r, s, ev.order);
        return 'applied';
      }
      if (ev.event === 'session_closed' && ev.close) {
        for (const o of ev.close.orders) await this.applyOrder(r, s, o);
        if (s.status === 'open') {
          const at = ev.occurred_at && ev.occurred_at.getTime() <= this.now() ? ev.occurred_at : new Date(this.now());
          await r.updateSession(s.id, { status: 'closed', close_reason: ev.close.reason, closed_at: at });
        }
        // K3 r15: penanda "close sudah diproses" (transaksi yang sama dengan earn) → reverse QR tidak lagi earn_pending
        await r.markCloseProcessed(s, new Date(this.now()));
        return 'applied';
      }
      return 'ignored_unknown_event';
    });
    this.metrics.odoo(`event_${ev.event}`.slice(0, 40), status);
    return { ok: true, status };
  }

  private async applySubmission(r: FnbRepo, s: TableSessionRow, sub: NonNullable<FnbEvent['submission']>): Promise<FnbEventOutcome> {
    const row = await r.getSubmission(s.id, sub.submission_ref, true);
    if (!row) {
      this.metrics.anomaly('event_unknown_submission', 'warning', { table_session_id: s.id, submission_id: sub.submission_ref });
      return 'ignored_stale';
    }
    const KNOWN: readonly SubmissionStatus[] = ['accepted', 'pending_confirmation', 'preparing', 'ready', 'served', 'rejected', 'cancelled'];
    if (!(KNOWN as readonly string[]).includes(sub.status)) return 'ignored_unknown_event';
    const to = sub.status as SubmissionStatus;
    // K1 r15: order_uuid dari event mengisi kolom yang masih kosong (juga bila status basi); uuid berbeda → anomali, tetap
    // yang pertama (hasil submit/rekonsiliasi = sumber pertama)
    const fillUuid = sub.order_uuid !== null && row.order_uuid === null;
    if (sub.order_uuid !== null && row.order_uuid !== null && sub.order_uuid !== row.order_uuid) {
      this.metrics.anomaly('order_uuid_mismatch', 'warning', { table_session_id: s.id, submission_id: row.id });
    }
    // `submitting` yang terlewat (respons submit hilang) boleh langsung ke status apa pun
    if (!canTransitionSubmission(row.status, to)) {
      if (fillUuid) await r.updateSubmission(row.id, { order_uuid: sub.order_uuid, order_state: row.order_state ?? 'draft' });
      return 'ignored_stale';
    }
    await r.updateSubmission(row.id, {
      status: to,
      odoo_state: to,
      ...(fillUuid ? { order_uuid: sub.order_uuid, order_state: row.order_state ?? ('draft' as const) } : {}),
    });
    this.metrics.submission(to);
    return 'applied';
  }

  /**
   * L-70: bayar/batal per order. Order = kiriman QR (`fnb_submission.order_uuid`) atau order pelayan (`session.order_uuid`).
   * `paid` + sesi diklaim member + `pos_member_attached=false` + `FNB_QR_POINTS_ENABLED` → earn `pos_earn:<order_uuid>` di
   * transaksi ini (D-22-5). K2/SEC-165 r15: `pos_member_attached=true` → status `pos_member`, POS yang meng-earn (BFF
   * tidak); field absen → tidak earn + anomali critical (fail-closed). `eligible_amount` sudah NETO refund (Odoo).
   */
  private async applyOrder(r: FnbRepo, s: TableSessionRow, o: OrderClose): Promise<void> {
    if (o.company_code !== null && o.company_code !== s.company_code) {
      this.metrics.anomaly('company_mismatch', 'critical', { table_session_id: s.id, company: o.company_code });
      throw FnbErrors.companyMismatch();
    }
    // L-80: Odoo menautkan order meja yang dibayar selama sesi hidup; BFF tidak wajib mengenal order_uuid sebelumnya
    const subs = (await r.listSubmissions(s.id)).filter((x) => x.order_uuid === o.order_uuid);
    const state: OrderState = o.reason;
    let earnStatus: EarnStatus = 'none';
    let points = 0;
    if (state === 'paid') {
      if (o.pos_member_attached === true) {
        earnStatus = 'pos_member';
      } else if (o.pos_member_attached === null) {
        this.metrics.anomaly('odoo_config_error', 'critical', { table_session_id: s.id, code: 'pos_member_attached_missing' });
        earnStatus = 'skipped';
      } else if (s.member_id === null || o.eligible_amount === null || !this.config.fnbQrPointsEnabled) {
        earnStatus = 'skipped';
      } else {
        // idempoten di ledger (`pos_earn:<order_uuid>` → replay); event ulang/tertunda aman
        const res = await this.earn.earn(r, {
          memberId: s.member_id,
          orderUuid: o.order_uuid,
          companyCode: s.company_code,
          eligibleAmount: o.eligible_amount,
          configId: s.config_id,
          ...(o.mgm_eligible_amount !== null ? { mgmEligibleAmount: o.mgm_eligible_amount } : {}),
        });
        points = res.points;
        earnStatus = res.outcome === 'replay' || res.outcome === 'earned' ? 'earned' : res.outcome === 'limited' ? 'limited' : res.outcome === 'conflict' ? 'conflict' : 'skipped';
        if (res.outcome === 'conflict') this.metrics.anomaly('qr_pos_member_conflict', 'critical', { table_session_id: s.id });
        if (res.outcome === 'limited') this.metrics.anomaly('earn_limited', 'warning', { table_session_id: s.id });
      }
    }
    for (const x of subs) {
      await r.updateSubmission(x.id, {
        order_state: state,
        eligible_amount: o.eligible_amount,
        ...(state === 'paid' && x.earn_status === 'none' ? { earn_status: earnStatus, points_earned: points } : {}),
      });
      points = 0; // poin dicatat sekali
    }
    // Ringkasan sesi: `earned` menang atas hasil lain (beberapa order per sesi)
    if (state === 'paid' && earnStatus !== 'none' && s.earn_status !== 'earned') {
      await r.updateSession(s.id, { earn_status: earnStatus });
      s.earn_status = earnStatus;
    }
  }
}

/** Error kontrak untuk 401 event (dipakai guard). */
export function eventError(): ApiError {
  return Errors.invalidSignature();
}
