import { Logger } from '@nestjs/common';
import { randomUUID } from 'node:crypto';
import { AppConfig, FnbCompanyCode } from '../config/app-config';
import { isUuid } from '../common/validation';
import { CircuitBreaker } from '../odoo-rpc/circuit-breaker';
import { Json2Transport, OdooRpcGates, TransportResponse } from '../odoo-rpc/json2-client';
import { FnbMetrics } from './fnb-metrics';

/**
 * RPC Odoo QR meja (docs/04 §14.A.5, path gateway ke-13..15; modul Odoo `efn_pos_bff_order`, L-70). User teknis per PT
 * (`efn_qr_pb`/`efn_qr_bcf`), secret `odoo_api_key_qr_<pt>`. Galat bisnis = `UserError("efn_qr:<kode>")` (HTTP 422, L-15).
 * Konstanta TERPISAH dari `ODOO_ONLINE_PATHS` (12 path online tetap).
 */
export const ODOO_QR_PATHS = {
  menu: 'efn.pos.qr.order/efn_qr_menu',
  submit: 'efn.pos.qr.order/efn_qr_submit',
  status: 'efn.pos.qr.order/efn_qr_status',
} as const;
export type OdooQrMethod = keyof typeof ODOO_QR_PATHS;

export const QR_BUSINESS_CODES = [
  'session_closed',
  'pos_session_closed',
  'item_unavailable',
  'invalid_input',
  'idempotency_conflict',
  'qr_disabled',
  'limit_exceeded',
] as const;
export type QrBusinessCode = (typeof QR_BUSINESS_CODES)[number];

export type QrErrorKind = 'business' | 'auth' | 'not_found' | 'transport' | 'circuit_open' | 'invalid_response' | 'busy';
export class OdooQrError extends Error {
  constructor(
    readonly kind: QrErrorKind,
    readonly code: QrBusinessCode | null = null,
    message?: string,
    /** QA G22 P3 r15: id produk dari `efn_qr:item_unavailable:<id>,<id>` (bila Odoo mengirim); selain itu []. */
    readonly productIds: readonly number[] = [],
  ) {
    super(message ?? (code ? `odoo qr ${kind}: ${code}` : `odoo qr ${kind}`));
    this.name = 'OdooQrError';
  }
}

export type DisplayType = 'radio' | 'select' | 'multi' | 'pills';
export interface MenuAttribute {
  attribute_id: number;
  name: string;
  display_type: DisplayType;
  required: boolean;
  values: { id: number; name: string; price_extra_excl: number }[];
}
export interface QrMenuItem {
  product_id: number;
  name: string | null;
  category_id: number | null;
  price_excl: number;
  tax_labels: string[];
  available: boolean;
  attributes: MenuAttribute[];
}
export interface QrMenuPage {
  config: { config_id: number; company_code: string; outlet_name: string; qr_enabled: boolean; confirm_mode: 'auto' | 'waiter' };
  items: QrMenuItem[];
  deleted: number[];
  next_cursor: string | null;
  server_time: string;
}
export interface QrSubmitInput {
  idempotency_key: string;
  submission_ref: string;
  /** Catatan baris = teks tersaring (D-22-6); hanya dikirim sekali ke Odoo (TLS + mTLS), tidak di-log. */
  lines: { product_id: number; qty: number; attribute_value_ids: number[]; note?: string | null }[];
}
export interface QrSubmitResult {
  submission_ref: string;
  state: 'accepted' | 'pending_confirmation';
  /** L-78: `attribute_value_ids` urut naik + `attributes_text` diteruskan ke klien. */
  lines: { product_id: number; name: string; qty: number; attribute_value_ids: number[]; attributes_text: string | null }[];
  amount_indicative_excl: number | null;
  /** L-70 / K1 r15: `pos.order.uuid` draft BARU untuk kiriman ini (UUID) atau null. */
  order_uuid: string | null;
}
export interface QrStatusRow {
  submission_ref: string;
  /** `absent` = Odoo tidak mengenal kiriman (aman diputuskan `failed`). */
  state: string;
  seq: number | null;
  order_uuid: string | null;
}

export interface OdooQrApi {
  menu(company: FnbCompanyCode, configId: number, since: string | null, cursor: string | null, limit: number): Promise<QrMenuPage>;
  /** `orderUuid` opsional (Odoo E-27: tidak dikirim bila BFF tidak mengenal order meja). */
  submit(company: FnbCompanyCode, configId: number, tableSessionRef: string, orderUuid: string | null, submission: QrSubmitInput): Promise<QrSubmitResult>;
  status(company: FnbCompanyCode, configId: number, submissionRefs: string[]): Promise<QrStatusRow[]>;
  isCircuitOpen(company: FnbCompanyCode): boolean;
}
export const ODOO_QR = Symbol('ODOO_QR');

// ───────────── parsing ketat (A.10 API10: respons Odoo divalidasi skema) ─────────────
function bad(what: string): never {
  throw new OdooQrError('invalid_response', null, `respons Odoo QR tidak valid: ${what}`);
}
function obj(v: unknown, w: string): Record<string, unknown> {
  if (typeof v !== 'object' || v === null || Array.isArray(v)) bad(w);
  return v as Record<string, unknown>;
}
function arr(v: unknown, w: string, max: number): unknown[] {
  if (!Array.isArray(v) || v.length > max) bad(w);
  return v as unknown[];
}
function str(v: unknown, w: string, max: number): string {
  if (typeof v !== 'string' || v.length > max) bad(w);
  return v as string;
}
function optStr(v: unknown, w: string, max: number): string | null {
  return v === undefined || v === null || v === false ? null : str(v, w, max);
}
function int(v: unknown, w: string, min = 0): number {
  if (typeof v !== 'number' || !Number.isSafeInteger(v) || v < min) bad(w);
  return v as number;
}
/** K1 r15: `order_uuid` Odoo (`pos.order.uuid`) — UUID atau absen/null/false; bentuk lain → respons tidak valid. */
function orderUuid(v: unknown): string | null {
  if (v === undefined || v === null || v === false) return null;
  if (!isUuid(v)) bad('order_uuid');
  return v as string;
}
function money(v: unknown, w: string): number {
  if (typeof v !== 'number' || !Number.isFinite(v) || v < 0 || v > 1e12) bad(w);
  return Math.round((v as number) * 100) / 100;
}

function parseAttributes(v: unknown): MenuAttribute[] {
  if (v === undefined || v === null || v === false) return [];
  return arr(v, 'attributes', 30).map((x) => {
    const a = obj(x, 'attribute');
    const dt = a.display_type;
    if (dt !== 'radio' && dt !== 'select' && dt !== 'multi' && dt !== 'pills') bad('attribute.display_type');
    return {
      attribute_id: int(a.attribute_id, 'attribute_id', 1),
      name: str(a.name, 'attribute.name', 128),
      display_type: dt,
      required: a.required === true,
      values: arr(a.values, 'attribute.values', 50).map((y) => {
        const val = obj(y, 'value');
        return { id: int(val.id, 'value.id', 1), name: str(val.name, 'value.name', 128), price_extra_excl: money(val.price_extra_excl ?? 0, 'price_extra_excl') };
      }),
    };
  });
}

/** L-78: PTAV id dari Odoo → int[] unik urut naik (≤ 10). Absen → []. */
export function parsePtavIds(v: unknown): number[] {
  if (v === undefined || v === null || v === false) return [];
  const ids = arr(v, 'attribute_value_ids', 10).map((x) => int(x, 'attribute_value_id', 1));
  if (new Set(ids).size !== ids.length) bad('attribute_value_ids duplikat');
  return [...ids].sort((a, b) => a - b);
}

function labels(v: unknown): string[] {
  if (v === undefined || v === null || v === false) return [];
  return arr(v, 'tax_labels', 10).map((x) => str(x, 'tax_label', 64));
}

export function parseQrMenu(v: unknown): QrMenuPage {
  const o = obj(v, 'menu');
  const c = obj(o.config, 'menu.config');
  const mode = c.confirm_mode === 'auto' ? 'auto' : c.confirm_mode === 'waiter' || c.confirm_mode === undefined ? 'waiter' : bad('confirm_mode');
  const company = str(c.company_code, 'config.company_code', 8);
  return {
    config: {
      config_id: int(c.config_id, 'config.config_id', 1),
      company_code: company,
      outlet_name: str(c.outlet_name, 'config.outlet_name', 128),
      qr_enabled: c.qr_enabled === true,
      confirm_mode: mode,
    },
    items: arr(o.items, 'items', 1000).map((x) => {
      const i = obj(x, 'item');
      return {
        product_id: int(i.product_id, 'product_id', 1),
        name: optStr(i.name, 'name', 256),
        category_id: i.category_id === undefined || i.category_id === null || i.category_id === false ? null : int(i.category_id, 'category_id', 1),
        price_excl: money(i.price_excl, 'price_excl'),
        tax_labels: labels(i.tax_labels),
        available: i.available === undefined ? true : i.available === true,
        attributes: parseAttributes(i.attributes),
      };
    }),
    deleted: arr(o.deleted ?? [], 'deleted', 10_000).map((x) => int(x, 'deleted', 1)),
    next_cursor: optStr(o.next_cursor, 'next_cursor', 512),
    server_time: str(o.server_time, 'server_time', 64),
  };
}

export function parseQrSubmit(v: unknown): QrSubmitResult {
  const o = obj(v, 'submit');
  const state = o.state;
  if (state !== 'accepted' && state !== 'pending_confirmation') bad('submit.state');
  const ou = orderUuid(o.order_uuid ?? o.order_ref);
  return {
    submission_ref: str(o.submission_ref, 'submission_ref', 64).toLowerCase(),
    state,
    lines: arr(o.lines ?? [], 'submit.lines', 60).map((x) => {
      const l = obj(x, 'submit line');
      return {
        product_id: int(l.product_id, 'line.product_id', 1),
        name: str(l.name, 'line.name', 256),
        qty: int(l.qty, 'line.qty', 1),
        attribute_value_ids: parsePtavIds(l.attribute_value_ids),
        attributes_text: optStr(l.attributes_text, 'line.attributes_text', 512),
      };
    }),
    amount_indicative_excl: o.amount_indicative_excl === undefined || o.amount_indicative_excl === null ? null : money(o.amount_indicative_excl, 'amount_indicative_excl'),
    order_uuid: ou,
  };
}

export function parseQrStatus(v: unknown): QrStatusRow[] {
  return arr(v, 'status', 100).map((x) => {
    const o = obj(x, 'status row');
    const state = str(o.state, 'state', 32);
    if (!/^[a-z_]{1,32}$/.test(state)) bad('state');
    return {
      submission_ref: str(o.submission_ref, 'submission_ref', 64).toLowerCase(),
      state,
      seq: o.seq === undefined || o.seq === null || o.seq === false ? null : int(o.seq, 'seq'),
      order_uuid: orderUuid(o.order_uuid),
    };
  });
}

/**
 * `efn_qr:<kode>` atau (QA G22 P3 r15, usulan kontrak ke Odoo) `efn_qr:item_unavailable:<product_id>[,<product_id>…]`
 * (≤ 30 id desimal, tanpa spasi). Id hanya diterima untuk `item_unavailable`; kode lain dengan sufiks → tidak dikenal.
 */
const QR_BUSINESS_RE = /^efn_qr:([a-z_]{1,40})(?::([1-9][0-9]{0,9}(?:,[1-9][0-9]{0,9}){0,29}))?$/;

/** Ambil `{kode, product_ids}` dari body error json2. */
export function qrBusinessError(body: unknown): { code: QrBusinessCode; productIds: number[] } | null {
  if (typeof body !== 'object' || body === null) return null;
  const o = body as Record<string, unknown>;
  const cands: unknown[] = [o.message];
  if (typeof o.error === 'object' && o.error !== null) {
    const e = o.error as Record<string, unknown>;
    cands.push(e.message);
    if (typeof e.data === 'object' && e.data !== null) cands.push((e.data as Record<string, unknown>).message);
  }
  for (const c of cands) {
    if (typeof c !== 'string') continue;
    const m = QR_BUSINESS_RE.exec(c.trim());
    if (!m || !(QR_BUSINESS_CODES as readonly string[]).includes(m[1])) continue;
    if (m[2] !== undefined && m[1] !== 'item_unavailable') continue;
    const ids = m[2] === undefined ? [] : [...new Set(m[2].split(',').map(Number))].filter((n) => Number.isSafeInteger(n) && n <= 2_147_483_647);
    return { code: m[1] as QrBusinessCode, productIds: ids.sort((a, b) => a - b) };
  }
  return null;
}

/** Ambil `efn_qr:<kode>` dari body error json2. */
export function qrBusinessCode(body: unknown): QrBusinessCode | null {
  return qrBusinessError(body)?.code ?? null;
}

const BACKOFF_MS = [500, 1000, 2000];

/**
 * Klien json2 QR (A.5/A.7): API key PER PT (tidak pernah key PT lain), semaphore PUBLIK bersama klien online (L-20),
 * circuit breaker per PT (5 gagal transport → 30 s). Retry hanya jaringan/timeout/429/5xx, maks 3× dalam batas waktu
 * (aman: menu/status baca, submit membawa `idempotency_key = fnb:submit:<submission_id>`). Log/metrik: metode/PT/jenis
 * saja — tidak pernah body (catatan pelanggan), Authorization, atau pesan Odoo.
 */
export class Json2OdooQrClient implements OdooQrApi {
  private readonly logger = new Logger('OdooQr');
  private readonly breakers = new Map<string, CircuitBreaker>();
  sleep: (ms: number) => Promise<void> = (ms) => new Promise((r) => setTimeout(r, ms).unref());
  now: () => number = () => Date.now();

  constructor(
    private readonly config: AppConfig,
    private readonly transport: Json2Transport,
    private readonly gates: OdooRpcGates,
    private readonly metrics: FnbMetrics,
  ) {}

  isCircuitOpen(company: FnbCompanyCode): boolean {
    return this.breaker(company).isOpen();
  }

  private breaker(company: string): CircuitBreaker {
    let b = this.breakers.get(company);
    if (!b) {
      b = new CircuitBreaker(5, 30_000, () => this.now(), (open) => {
        if (open) this.logger.warn(`Circuit Odoo QR PT ${company} TERBUKA (30 s)`);
      });
      this.breakers.set(company, b);
    }
    return b;
  }

  private classify(res: TransportResponse): { value: unknown } | OdooQrError {
    let parsed: unknown;
    let jsonOk = true;
    try {
      parsed = res.body.length > 0 ? JSON.parse(res.body) : null;
    } catch {
      jsonOk = false;
    }
    if (res.status === 200) return jsonOk ? { value: parsed } : new OdooQrError('invalid_response');
    const biz = qrBusinessError(parsed);
    if (biz) return new OdooQrError('business', biz.code, undefined, biz.productIds);
    if (res.status === 401 || res.status === 403) return new OdooQrError('auth');
    if (res.status === 404) return new OdooQrError('not_found');
    if (res.status === 429 || res.status >= 500 || res.status === 0) return new OdooQrError('transport');
    return new OdooQrError('invalid_response');
  }

  private async call(method: OdooQrMethod, company: FnbCompanyCode, args: Record<string, unknown>, deadlineMs: number): Promise<unknown> {
    const breaker = this.breaker(company);
    const started = this.now();
    let apiKey: string;
    try {
      apiKey = this.config.odooApiKeyQr(company);
    } catch {
      this.metrics.odoo(method, 'auth');
      throw new OdooQrError('auth');
    }
    if (!breaker.allow()) {
      this.metrics.odoo(method, 'circuit_open');
      throw new OdooQrError('circuit_open');
    }
    const headers: Record<string, string> = { 'Content-Type': 'application/json', Accept: 'application/json', Authorization: `bearer ${apiKey}` };
    const db = this.config.odooDb;
    if (db) headers['X-Odoo-Database'] = db;
    const body = JSON.stringify({ ...args, context: { lang: 'id_ID' } });
    for (let attempt = 0; ; attempt += 1) {
      let release: () => void;
      try {
        release = await this.gates.public.acquire();
      } catch {
        this.metrics.odoo(method, 'busy');
        throw new OdooQrError('busy');
      }
      let err: OdooQrError;
      try {
        const out = this.classify(await this.transport(ODOO_QR_PATHS[method], body, headers, this.config.odooRpcTimeoutMs));
        if (!(out instanceof OdooQrError)) {
          breaker.success();
          this.metrics.odoo(method, 'ok');
          return out.value;
        }
        err = out;
      } catch (e) {
        err = new OdooQrError('transport', null, e instanceof Error && e.name === 'TimeoutError' ? 'timeout' : 'network');
      } finally {
        release();
      }
      if (err.kind !== 'transport') {
        breaker.neutral();
        this.metrics.odoo(method, err.kind === 'business' ? 'business' : err.kind);
        this.logger.warn(`json2 QR ${method} PT ${company}: ${err.kind}${err.code ? ` ${err.code}` : ''}`);
        throw err;
      }
      breaker.failure();
      const delay = attempt < BACKOFF_MS.length ? BACKOFF_MS[attempt] : -1;
      if (delay < 0 || this.now() - started + delay >= deadlineMs || !breaker.allow()) {
        this.metrics.odoo(method, 'transport');
        throw err;
      }
      await this.sleep(delay);
    }
  }

  async menu(company: FnbCompanyCode, configId: number, since: string | null, cursor: string | null, limit: number): Promise<QrMenuPage> {
    const v = await this.call('menu', company, { config_id: configId, since, cursor, limit: Math.min(200, limit) }, this.config.odooRpcCatalogTimeoutMs);
    return parseQrMenu(v);
  }
  async submit(company: FnbCompanyCode, configId: number, tableSessionRef: string, orderUuid: string | null, submission: QrSubmitInput): Promise<QrSubmitResult> {
    // catatan `null` tidak dikirim (Odoo: `note?` opsional)
    const lines = submission.lines.map((l) => (l.note === null ? { product_id: l.product_id, qty: l.qty, attribute_value_ids: l.attribute_value_ids } : l));
    const v = await this.call(
      'submit',
      company,
      { config_id: configId, table_session_ref: tableSessionRef, ...(orderUuid ? { order_uuid: orderUuid } : {}), submission: { ...submission, lines } },
      this.config.odooRpcTimeoutMs * 2,
    );
    return parseQrSubmit(v);
  }
  async status(company: FnbCompanyCode, configId: number, refs: string[]): Promise<QrStatusRow[]> {
    const v = await this.call('status', company, { config_id: configId, submission_refs: refs.slice(0, 100) }, this.config.odooRpcTimeoutMs);
    return parseQrStatus(v);
  }
}

/**
 * STUB QR DEV/TEST SAJA (`ODOO_ONLINE_MOCK=true`; ditolak di staging/production oleh `AppConfig.odooOnlineMock`).
 * Data demo PLACEHOLDER: config 7 = PB (mode `waiter`, L-71), config 8 = BCF (mode `auto`); produk 21 Croissant,
 * 22 Latte (atribut Gula radio wajib + Ekstra shot multi +Rp6.000), 23 "Uji habis" (`available=false`). Meniru Odoo:
 * idempotensi per `idempotency_key` (payload beda → idempotency_conflict), satu kiriman = satu order baru (L-70),
 * `item_unavailable`, `limit_exceeded` (> 30 baris / qty > 20), PTAV harus milik produk.
 */
export class MockOdooQr implements OdooQrApi {
  readonly configs = new Map<number, { company: FnbCompanyCode; outlet: string; qr: boolean; mode: 'auto' | 'waiter' }>([
    [7, { company: 'PB', outlet: 'Paris Baguette Demo', qr: true, mode: 'waiter' }],
    [8, { company: 'BCF', outlet: 'Bacha Coffee Kafe Demo', qr: true, mode: 'auto' }],
  ]);
  readonly products: QrMenuItem[] = [
    { product_id: 21, name: 'Croissant (demo)', category_id: 5, price_excl: 25_000, tax_labels: ['PB1 10%', 'Service 5%'], available: true, attributes: [] },
    {
      product_id: 22,
      name: 'Latte (demo)',
      category_id: 6,
      price_excl: 38_000,
      tax_labels: ['PB1 10%', 'Service 5%'],
      available: true,
      attributes: [
        { attribute_id: 1, name: 'Gula', display_type: 'radio', required: true, values: [{ id: 101, name: 'Normal', price_extra_excl: 0 }, { id: 102, name: 'Tanpa gula', price_extra_excl: 0 }] },
        { attribute_id: 2, name: 'Ekstra', display_type: 'multi', required: false, values: [{ id: 201, name: 'Shot espresso', price_extra_excl: 6_000 }] },
      ],
    },
    { product_id: 23, name: 'Produk Uji Habis (demo)', category_id: 5, price_excl: 15_000, tax_labels: ['PB1 10%'], available: false, attributes: [] },
  ];
  readonly submissions = new Map<string, { key: string; sha: string; result: QrSubmitResult; config: number; state: string; seq: number }>();
  readonly calls: { method: string; company: string }[] = [];
  readonly failNext = new Map<string, OdooQrError>();
  /** Test: sesi POS tertutup per config. */
  readonly posSessionClosed = new Set<number>();
  now: () => number = () => Date.now();

  isCircuitOpen(): boolean {
    return false;
  }

  private track(method: string, company: string, configId: number): { company: FnbCompanyCode; outlet: string; qr: boolean; mode: 'auto' | 'waiter' } {
    this.calls.push({ method, company });
    const f = this.failNext.get(method);
    if (f) {
      this.failNext.delete(method);
      throw f;
    }
    const c = this.configs.get(configId);
    if (!c || c.company !== company) throw new OdooQrError('auth'); // user teknis PT lain = AccessError
    return c;
  }

  async menu(company: FnbCompanyCode, configId: number, since: string | null): Promise<QrMenuPage> {
    const c = this.track('menu', company, configId);
    return {
      config: { config_id: configId, company_code: c.company, outlet_name: c.outlet, qr_enabled: c.qr, confirm_mode: c.mode },
      items: since ? [] : this.products.map((p) => ({ ...p, attributes: p.attributes.map((a) => ({ ...a, values: [...a.values] })) })),
      deleted: [],
      next_cursor: null,
      server_time: new Date(this.now()).toISOString(),
    };
  }

  async submit(company: FnbCompanyCode, configId: number, _sessionRef: string, _orderUuid: string | null, s: QrSubmitInput): Promise<QrSubmitResult> {
    const c = this.track('submit', company, configId);
    const sha = JSON.stringify(s);
    const prev = this.submissions.get(s.idempotency_key);
    if (prev) {
      if (prev.sha !== sha) throw new OdooQrError('business', 'idempotency_conflict');
      return prev.result;
    }
    if (!c.qr) throw new OdooQrError('business', 'qr_disabled');
    if (this.posSessionClosed.has(configId)) throw new OdooQrError('business', 'pos_session_closed');
    if (s.lines.length > 30 || s.lines.some((l) => l.qty > 20)) throw new OdooQrError('business', 'limit_exceeded');
    const lines = s.lines.map((l) => {
      const p = this.products.find((x) => x.product_id === l.product_id);
      // Odoo saat ini: `efn_qr:item_unavailable` tanpa id (BFF memakai overlay menu sebagai cadangan)
      if (!p || !p.available) throw new OdooQrError('business', 'item_unavailable');
      const allowed = new Set(p.attributes.flatMap((a) => a.values.map((v) => v.id)));
      if (l.attribute_value_ids.some((id) => !allowed.has(id))) throw new OdooQrError('business', 'invalid_input');
      const chosen = p.attributes.flatMap((a) => a.values.filter((v) => l.attribute_value_ids.includes(v.id)).map((v) => ({ a: a.name, v })));
      const extra = chosen.reduce((t, x) => t + x.v.price_extra_excl, 0);
      return {
        product_id: p.product_id,
        name: p.name ?? '',
        qty: l.qty,
        attribute_value_ids: [...l.attribute_value_ids].sort((a, b) => a - b),
        attributes_text: chosen.length > 0 ? chosen.map((x) => `${x.a}: ${x.v.name}`).join(', ') : null,
        amount: (p.price_excl + extra) * l.qty,
      };
    });
    const result: QrSubmitResult = {
      submission_ref: s.submission_ref,
      state: c.mode === 'auto' ? 'accepted' : 'pending_confirmation',
      lines: lines.map(({ product_id, name, qty, attribute_value_ids, attributes_text }) => ({ product_id, name, qty, attribute_value_ids, attributes_text })),
      amount_indicative_excl: lines.reduce((t, l) => t + l.amount, 0),
      order_uuid: randomUUID(), // L-70 / K1 r15: `pos.order.uuid` (uuid4) order draft baru per kiriman
    };
    this.submissions.set(s.idempotency_key, { key: s.idempotency_key, sha, result, config: configId, state: result.state, seq: 1 });
    return result;
  }

  async status(company: FnbCompanyCode, configId: number, refs: string[]): Promise<QrStatusRow[]> {
    this.track('status', company, configId);
    return refs.map((ref) => {
      const s = [...this.submissions.values()].find((x) => x.result.submission_ref === ref && x.config === configId);
      // K1 r15: bentuk Odoo `efn_qr_status` — `{submission_ref, state, seq, order_uuid}`; absent = seq 0, order_uuid null
      return s ? { submission_ref: ref, state: s.state, seq: s.seq, order_uuid: s.result.order_uuid } : { submission_ref: ref, state: 'absent', seq: 0, order_uuid: null };
    });
  }
}
