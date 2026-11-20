import { Logger } from '@nestjs/common';
import { AppConfig } from '../config/app-config';
import { CircuitBreaker } from '../odoo-rpc/circuit-breaker';
import { Json2Transport, OdooRpcGates, TransportResponse } from '../odoo-rpc/json2-client';

/**
 * Klien json2 klaim outbox kampanye Odoo (docs/04 §28.7, L-156; pola `efn_promo_push_claim` §18.1). User teknis per PT
 * `efn_campaign_<kode>` (satu company), API key `odoo_api_key_campaign_<pt>`. Respons Odoo divalidasi ulang dengan
 * allowlist (tidak dipercaya mentah). TIDAK ada data member di jalur ini (L-155): Odoo hanya mengirim ringkasan
 * kampanye + kode voucher tanpa partner.
 */
export const ODOO_CAMPAIGN = Symbol('ODOO_CAMPAIGN');

export const ODOO_CAMPAIGN_PATHS = {
  claim: 'efn.campaign/efn_campaign_claim',
  ack: 'efn.campaign/efn_campaign_ack',
} as const;
export type OdooCampaignMethod = keyof typeof ODOO_CAMPAIGN_PATHS;

export const CAMPAIGN_CHANNELS = ['inbox', 'push', 'email', 'wa_unofficial'] as const;
export type CampaignChannel = (typeof CAMPAIGN_CHANNELS)[number];
export const VOUCHER_MODES = ['anonymous_code', 'public_code', 'member_bound'] as const;
export type VoucherMode = (typeof VOUCHER_MODES)[number];
export const MAX_CODES_PER_CLAIM_ROW = 5000;
export const CLAIM_LIMIT = 20;

export type CampaignErrorKind = 'business' | 'auth' | 'not_found' | 'transport' | 'invalid_response' | 'circuit_open' | 'busy';
export class OdooCampaignError extends Error {
  constructor(
    readonly kind: CampaignErrorKind,
    readonly code: string | null = null,
  ) {
    super(`odoo campaign ${kind}${code ? ` ${code}` : ''}`);
  }
}

export interface CampaignPayload {
  campaign_id: number;
  company_code: string;
  kind: 'segment' | 'markdown';
  segment_run_id: string | null;
  segment_code: string | null;
  markdown: { channel_code: string | null; product_ids: number[]; outlet_codes: string[]; valid_to: string } | null;
  channels: CampaignChannel[];
  voucher_mode: VoucherMode | null;
  voucher_program_ref: string | null;
  voucher_value_idr: number | null;
  budget_idr: number | null;
  cap_per_member_week: number | null;
  valid_from: string;
  valid_to: string;
  message: { title: string; body: string };
}

export interface ClaimedCampaign {
  outboxId: number;
  approvalRev: number;
  payload: CampaignPayload;
  /** `anonymous_code`: kode tanpa partner; `public_code`: satu kode. Tidak pernah di-log. */
  codes: string[];
}

export interface ClaimResult {
  items: ClaimedCampaign[];
  /** Baris outbox yang ditolak validasi BFF (tidak di-ack → Odoo mencoba ulang lalu `failed` + aktivitas marketing). */
  rejected: { outboxId: number | null; reason: string }[];
}

export interface OdooCampaignApi {
  claim(company: string, limit: number): Promise<ClaimResult>;
  ack(company: string, outboxIds: number[]): Promise<void>;
}

const CODE_RE = /^[A-Z][A-Z0-9]{1,9}$/;
const CHANNEL_RE = /^[A-Z][A-Z0-9-]{1,15}$/;
const OUTLET_RE = /^[A-Za-z0-9_-]{1,32}$/;
const SEGMENT_RE = /^[a-z][a-z0-9_]{1,39}$/;
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const ISO_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$/;
const VOUCHER_CODE_RE = /^[A-Za-z0-9_-]{4,40}$/;
const REF_RE = /^[A-Za-z0-9_.:-]{1,64}$/;
const BACKOFF_MS = [500, 1000];

const obj = (v: unknown): Record<string, unknown> | null => (v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : null);
const posInt = (v: unknown): number | null => (typeof v === 'number' && Number.isSafeInteger(v) && v > 0 ? v : null);
const nnInt = (v: unknown): number | null => (typeof v === 'number' && Number.isSafeInteger(v) && v >= 0 ? v : null);
// eslint-disable-next-line no-control-regex
const text = (v: unknown, max: number): string => (typeof v === 'string' ? v.replace(/[\u0000-\u001f\u007f]/g, ' ').trim().slice(0, max) : '');
const optPosInt = (v: unknown): number | null | 'bad' => (v === undefined || v === null ? null : (posInt(v) ?? 'bad'));

class Invalid extends Error {}
function req<T>(v: T | null | undefined | false, reason: string): T {
  if (v === null || v === undefined || v === false) throw new Invalid(reason);
  return v;
}

/**
 * Validasi satu payload kampanye (allowlist). `company` = PT pemilik API key; payload PT lain ditolak.
 * SATU format (keputusan lead, SEC-G50-02): bentuk di bawah = kontrak; Odoo `efn_campaign` mengirim persis bentuk ini dan
 * fixture bersama `services/odoo/addons/efn_campaign/tests/fixtures/campaign_payload_v1.json` diuji kedua sisi. Tidak ada
 * normalisasi bentuk lama (title/body datar, `voucher{}`, tanggal `YYYY-MM-DD`, program ref integer → ditolak).
 */
export function parseCampaignPayload(rawInput: unknown, company: string): { payload: CampaignPayload; codes: string[] } {
  const o = req(obj(rawInput), 'payload');
  const campaignId = req(posInt(o.campaign_id), 'campaign_id');
  const companyCode = req(typeof o.company_code === 'string' && CODE_RE.test(o.company_code) ? o.company_code : null, 'company_code');
  if (companyCode !== company) throw new Invalid('company_mismatch');
  const kind = req(o.kind === 'segment' || o.kind === 'markdown' ? o.kind : null, 'kind');
  const validFrom = req(typeof o.valid_from === 'string' && ISO_RE.test(o.valid_from) ? o.valid_from : null, 'valid_from');
  const validTo = req(typeof o.valid_to === 'string' && ISO_RE.test(o.valid_to) ? o.valid_to : null, 'valid_to');
  if (Date.parse(validTo) <= Date.parse(validFrom)) throw new Invalid('valid_range');
  let runId: string | null = null;
  let segmentCode: string | null = null;
  if (o.segment_run_id !== undefined && o.segment_run_id !== null) {
    runId = req(typeof o.segment_run_id === 'string' && UUID_RE.test(o.segment_run_id) ? o.segment_run_id.toLowerCase() : null, 'segment_run_id');
    segmentCode = req(typeof o.segment_code === 'string' && SEGMENT_RE.test(o.segment_code) ? o.segment_code : null, 'segment_code');
  }
  if (kind === 'segment' && runId === null) throw new Invalid('segment_required');
  let markdown: CampaignPayload['markdown'] = null;
  if (kind === 'markdown') {
    const m = req(obj(o.markdown), 'markdown');
    if (!Array.isArray(m.product_ids) || m.product_ids.length < 1 || m.product_ids.length > 200) throw new Invalid('product_ids');
    const productIds = (m.product_ids as unknown[]).map((x) => req(posInt(x), 'product_ids'));
    if (!Array.isArray(m.outlet_codes) || m.outlet_codes.length > 50) throw new Invalid('outlet_codes');
    const outletCodes = (m.outlet_codes as unknown[]).map((x) => req(typeof x === 'string' && OUTLET_RE.test(x) ? x : null, 'outlet_codes'));
    const channelCode = m.channel_code === undefined || m.channel_code === null ? null : req(typeof m.channel_code === 'string' && CHANNEL_RE.test(m.channel_code) ? m.channel_code : null, 'markdown.channel_code');
    const mValidTo = req(typeof m.valid_to === 'string' && ISO_RE.test(m.valid_to) ? m.valid_to : null, 'markdown.valid_to');
    markdown = { channel_code: channelCode, product_ids: [...new Set(productIds)], outlet_codes: [...new Set(outletCodes)], valid_to: mValidTo };
  }
  if (!Array.isArray(o.channels) || o.channels.length < 1 || o.channels.length > 4) throw new Invalid('channels');
  const channels = [
    ...new Set((o.channels as unknown[]).map((c) => req((CAMPAIGN_CHANNELS as readonly unknown[]).includes(c) ? (c as CampaignChannel) : null, 'channels'))),
  ];
  const voucherMode: VoucherMode | null =
    o.voucher_mode === undefined || o.voucher_mode === null ? null : req((VOUCHER_MODES as readonly unknown[]).includes(o.voucher_mode) ? (o.voucher_mode as VoucherMode) : null, 'voucher_mode');
  const programRef = o.voucher_program_ref === undefined || o.voucher_program_ref === null ? null : req(typeof o.voucher_program_ref === 'string' && REF_RE.test(o.voucher_program_ref) ? o.voucher_program_ref : null, 'voucher_program_ref');
  let codes: string[] = [];
  if (voucherMode === 'anonymous_code') {
    if (!Array.isArray(o.voucher_codes) || o.voucher_codes.length < 1 || o.voucher_codes.length > MAX_CODES_PER_CLAIM_ROW) throw new Invalid('voucher_codes');
    codes = [...new Set((o.voucher_codes as unknown[]).map((c) => req(typeof c === 'string' && VOUCHER_CODE_RE.test(c) ? c : null, 'voucher_codes')))];
  } else if (voucherMode === 'public_code') {
    codes = [req(typeof o.public_code === 'string' && VOUCHER_CODE_RE.test(o.public_code) ? o.public_code : null, 'public_code')];
  } else if (o.voucher_codes !== undefined || o.public_code !== undefined) {
    throw new Invalid('codes_without_mode'); // member_bound / tanpa voucher: kode tidak boleh dikirim
  }
  const value = optPosInt(o.voucher_value_idr);
  const budget = optPosInt(o.budget_idr);
  const cap = optPosInt(o.cap_per_member_week);
  if (value === 'bad' || budget === 'bad' || cap === 'bad') throw new Invalid('numbers');
  const msg = req(obj(o.message), 'message');
  const title = req(text(msg.title, 48) || null, 'message.title');
  const body = req(text(msg.body, 160) || null, 'message.body');
  return {
    payload: {
      campaign_id: campaignId,
      company_code: companyCode,
      kind,
      segment_run_id: runId,
      segment_code: segmentCode,
      markdown,
      channels,
      voucher_mode: voucherMode,
      voucher_program_ref: programRef,
      voucher_value_idr: value,
      budget_idr: budget,
      cap_per_member_week: cap,
      valid_from: validFrom,
      valid_to: validTo,
      message: { title, body },
    },
    codes,
  };
}

/** Respons `efn_campaign_claim`: `{ items: [{ id, approval_rev, payload }] }` (≤ 20). */
export function parseClaim(v: unknown, company: string): ClaimResult {
  const o = obj(v);
  if (!o || !Array.isArray(o.items)) throw new OdooCampaignError('invalid_response');
  const out: ClaimResult = { items: [], rejected: [] };
  for (const raw of o.items.slice(0, CLAIM_LIMIT)) {
    const it = obj(raw);
    const outboxId = it ? posInt(it.id) : null;
    try {
      const id = req(outboxId, 'id');
      const rev = req(nnInt(it!.approval_rev), 'approval_rev');
      const { payload, codes } = parseCampaignPayload(it!.payload, company);
      out.items.push({ outboxId: id, approvalRev: rev, payload, codes });
    } catch (err) {
      out.rejected.push({ outboxId, reason: err instanceof Invalid ? err.message : 'invalid' });
    }
  }
  return out;
}

function businessCode(parsed: unknown): string | null {
  const msg = (obj(obj(parsed)?.error) ?? obj(parsed))?.message;
  const m = typeof msg === 'string' ? /efn_campaign:([a-z_]{1,32})/.exec(msg) : null;
  return m ? m[1]! : null;
}

export class Json2OdooCampaignClient implements OdooCampaignApi {
  private readonly logger = new Logger('OdooCampaign');
  private readonly breakers = new Map<string, CircuitBreaker>();
  sleep: (ms: number) => Promise<void> = (ms) => new Promise((r) => setTimeout(r, ms).unref());
  now: () => number = () => Date.now();

  constructor(
    private readonly config: AppConfig,
    private readonly transport: Json2Transport,
    private readonly gates: OdooRpcGates,
  ) {}

  async claim(company: string, limit: number): Promise<ClaimResult> {
    return parseClaim(await this.call('claim', company, { limit: Math.min(CLAIM_LIMIT, Math.max(1, limit)) }), company);
  }

  async ack(company: string, outboxIds: number[]): Promise<void> {
    if (outboxIds.length === 0) return;
    await this.call('ack', company, { ids: outboxIds });
  }

  private breaker(company: string): CircuitBreaker {
    let b = this.breakers.get(company);
    if (!b) {
      b = new CircuitBreaker(5, 30_000, () => this.now(), (open) => {
        if (open) this.logger.warn(`Circuit Odoo kampanye PT ${company} TERBUKA (30 s)`);
      });
      this.breakers.set(company, b);
    }
    return b;
  }

  private classify(res: TransportResponse): { value: unknown } | OdooCampaignError {
    let parsed: unknown = null;
    let jsonOk = true;
    try {
      parsed = res.body.length > 0 ? JSON.parse(res.body) : null;
    } catch {
      jsonOk = false;
    }
    if (res.status === 200) return jsonOk ? { value: parsed } : new OdooCampaignError('invalid_response');
    const biz = businessCode(parsed);
    if (biz) return new OdooCampaignError('business', biz);
    if (res.status === 401 || res.status === 403) return new OdooCampaignError('auth');
    if (res.status === 404) return new OdooCampaignError('not_found');
    if (res.status === 429 || res.status >= 500 || res.status === 0) return new OdooCampaignError('transport');
    return new OdooCampaignError('invalid_response');
  }

  private async call(method: OdooCampaignMethod, company: string, args: Record<string, unknown>): Promise<unknown> {
    const breaker = this.breaker(company);
    let apiKey: string;
    try {
      apiKey = this.config.odooApiKeyCampaign(company);
    } catch {
      throw new OdooCampaignError('auth');
    }
    if (!breaker.allow()) throw new OdooCampaignError('circuit_open');
    const headers: Record<string, string> = { 'Content-Type': 'application/json', Accept: 'application/json', Authorization: `Bearer ${apiKey}` };
    const db = this.config.odooDb;
    if (db) headers['X-Odoo-Database'] = db;
    const body = JSON.stringify({ ...args, context: { lang: 'id_ID' } });
    // claim mengubah status outbox (queued→claimed) → TIDAK diulang (klaim yang hilang kembali `queued` setelah 10 menit).
    // ack idempoten (done → done) → boleh diulang.
    const retries = method === 'claim' ? 0 : BACKOFF_MS.length;
    for (let attempt = 0; ; attempt += 1) {
      let release: () => void;
      try {
        release = await this.gates.saga.acquire();
      } catch {
        throw new OdooCampaignError('busy');
      }
      let err: OdooCampaignError;
      try {
        const out = this.classify(await this.transport(ODOO_CAMPAIGN_PATHS[method], body, headers, this.config.odooRpcTimeoutMs));
        if (!(out instanceof OdooCampaignError)) {
          breaker.success();
          return out.value;
        }
        err = out;
      } catch {
        err = new OdooCampaignError('transport');
      } finally {
        release();
      }
      if (err.kind !== 'transport') {
        breaker.neutral();
        this.logger.warn(`json2 kampanye ${method} PT ${company}: ${err.kind}${err.code ? ` ${err.code}` : ''}`);
        throw err;
      }
      breaker.failure();
      if (attempt >= retries || !breaker.allow()) throw err;
      await this.sleep(BACKOFF_MS[attempt]!);
    }
  }
}

/** Stub DEV/TEST (`ODOO_ONLINE_MOCK=true`): antrean kosong kecuali diisi test. */
export class MockOdooCampaign implements OdooCampaignApi {
  readonly queued = new Map<string, unknown[]>();
  readonly acked: { company: string; ids: number[] }[] = [];
  async claim(company: string, limit: number): Promise<ClaimResult> {
    const q = this.queued.get(company) ?? [];
    return parseClaim({ items: q.splice(0, limit) }, company);
  }
  async ack(company: string, outboxIds: number[]): Promise<void> {
    this.acked.push({ company, ids: [...outboxIds] });
  }
}
