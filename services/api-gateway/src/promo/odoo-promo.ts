import { Logger } from '@nestjs/common';
import { createHash } from 'node:crypto';
import { AppConfig } from '../config/app-config';
import { CircuitBreaker } from '../odoo-rpc/circuit-breaker';
import { Json2Transport, OdooRpcGates, TransportResponse } from '../odoo-rpc/json2-client';

/**
 * Klien json2 promo Super-App (docs/04 §18.1, path gateway #22–#24). User teknis per PT (`group_promo_bff`), API key
 * `odoo_api_key_promo_<pt>`; di dev boleh memakai key kanal online PT itu (kunci teknis yang sudah ada).
 * Respons Odoo divalidasi ulang dengan allowlist field (tidak dipercaya mentah). Tidak ada data member di jalur ini.
 */

export const ODOO_PROMO = Symbol('ODOO_PROMO');

export const ODOO_PROMO_PATHS = {
  feed: 'efn.promo.banner/efn_promo_feed',
  image: 'efn.promo.banner/efn_promo_image',
  pushClaim: 'efn.promo.popup/efn_promo_push_claim',
} as const;
export type OdooPromoMethod = keyof typeof ODOO_PROMO_PATHS;

export type PromoErrorKind = 'business' | 'auth' | 'not_found' | 'transport' | 'invalid_response' | 'circuit_open' | 'busy';
export class OdooPromoError extends Error {
  constructor(
    readonly kind: PromoErrorKind,
    readonly code: string | null = null,
  ) {
    super(`odoo promo ${kind}${code ? ` ${code}` : ''}`);
  }
}

export type PromoLink = { type: 'brand'; brand: string } | { type: 'product'; channel: string; product_id: number } | { type: 'url'; url: string } | null;

export interface PromoFeedItem {
  id: number;
  title: string;
  subtitle: string;
  cta_label: string;
  link: PromoLink;
  web: boolean;
  app: boolean;
  date_start: string;
  date_end: string;
  sequence: number;
  image_sha: string | null;
  frequency?: 'once' | 'daily' | 'every_open';
}
export interface PromoFeed {
  banners: PromoFeedItem[];
  popups: PromoFeedItem[];
}
export interface PromoImage {
  sha: string;
  data: Buffer;
}
export interface PromoPush {
  id: number;
  title: string;
  body: string;
  link: PromoLink;
}

export interface OdooPromoApi {
  feed(company: string): Promise<PromoFeed>;
  image(company: string, kind: 'banner' | 'popup', id: number): Promise<PromoImage>;
  pushClaim(company: string, limit: number): Promise<PromoPush[]>;
}

const CODE_RE = /^[A-Z][A-Z0-9]{1,9}$/;
const SHA_RE = /^[0-9a-f]{64}$/;
const ISO_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/;
const URL_RE = /^https:\/\/[A-Za-z0-9.-]+(?::\d{1,5})?(?:\/[A-Za-z0-9._~/%-]*)?(?:\?[A-Za-z0-9._~=&%-]*)?$/;
const FREQ = ['once', 'daily', 'every_open'] as const;
const BACKOFF_MS = [500, 1000];
const MAX_IMAGE_B64 = 14 * 1024 * 1024;

const obj = (v: unknown): Record<string, unknown> | null => (v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : null);
const text = (v: unknown, max: number): string => (typeof v === 'string' ? v.replace(/[\u0000-\u001f\u007f]/g, ' ').trim().slice(0, max) : '');
const posInt = (v: unknown): number | null => (typeof v === 'number' && Number.isSafeInteger(v) && v > 0 ? v : null);

export function parsePromoLink(v: unknown): PromoLink {
  const o = obj(v);
  if (!o) return null;
  if (o.type === 'brand' && typeof o.brand === 'string' && CODE_RE.test(o.brand)) return { type: 'brand', brand: o.brand };
  if (o.type === 'product' && typeof o.channel === 'string' && CODE_RE.test(o.channel) && posInt(o.product_id)) {
    return { type: 'product', channel: o.channel, product_id: o.product_id as number };
  }
  if (o.type === 'url' && typeof o.url === 'string' && o.url.length <= 300 && URL_RE.test(o.url)) return { type: 'url', url: o.url };
  return null;
}

function parseItem(v: unknown, popup: boolean): PromoFeedItem | null {
  const o = obj(v);
  if (!o) return null;
  const id = posInt(o.id);
  if (id === null || typeof o.date_start !== 'string' || !ISO_RE.test(o.date_start) || typeof o.date_end !== 'string' || !ISO_RE.test(o.date_end)) return null;
  if (Date.parse(o.date_end) <= Date.parse(o.date_start)) return null;
  const title = text(o.title, 80);
  if (!title) return null;
  const item: PromoFeedItem = {
    id,
    title,
    subtitle: text(o.subtitle, 160),
    cta_label: text(o.cta_label, 24),
    link: parsePromoLink(o.link),
    web: o.web !== false,
    app: o.app !== false,
    date_start: o.date_start,
    date_end: o.date_end,
    sequence: typeof o.sequence === 'number' && Number.isSafeInteger(o.sequence) ? Math.max(-1e6, Math.min(1e6, o.sequence)) : 10,
    image_sha: typeof o.image_sha === 'string' && SHA_RE.test(o.image_sha) ? o.image_sha : null,
  };
  if (popup) item.frequency = (FREQ as readonly unknown[]).includes(o.frequency) ? (o.frequency as PromoFeedItem['frequency']) : 'once';
  return item;
}

export function parsePromoFeed(v: unknown): PromoFeed {
  const o = obj(v);
  if (!o || !Array.isArray(o.banners) || !Array.isArray(o.popups)) throw new OdooPromoError('invalid_response');
  return {
    banners: o.banners.slice(0, 20).map((x) => parseItem(x, false)).filter((x): x is PromoFeedItem => x !== null),
    popups: o.popups.slice(0, 10).map((x) => parseItem(x, true)).filter((x): x is PromoFeedItem => x !== null),
  };
}

export function parsePromoImage(v: unknown): PromoImage {
  const o = obj(v);
  if (!o || typeof o.sha !== 'string' || !SHA_RE.test(o.sha) || typeof o.data_b64 !== 'string' || o.data_b64.length > MAX_IMAGE_B64) {
    throw new OdooPromoError('invalid_response');
  }
  const data = Buffer.from(o.data_b64, 'base64');
  // sha diklaim Odoo harus cocok dengan byte yang diterima (alamat gambar berbasis hash).
  if (data.length === 0 || createHash('sha256').update(data).digest('hex') !== o.sha) throw new OdooPromoError('invalid_response');
  return { sha: o.sha, data };
}

export function parsePromoPush(v: unknown): PromoPush[] {
  const o = obj(v);
  if (!o || !Array.isArray(o.items)) throw new OdooPromoError('invalid_response');
  const out: PromoPush[] = [];
  for (const x of o.items.slice(0, 5)) {
    const i = obj(x);
    const id = i ? posInt(i.id) : null;
    const title = i ? text(i.push_title, 48) : '';
    const body = i ? text(i.push_body, 120) : '';
    if (id !== null && title && body) out.push({ id, title, body, link: parsePromoLink(i!.link) });
  }
  return out;
}

function businessCode(parsed: unknown): string | null {
  const msg = (obj(obj(parsed)?.error) ?? obj(parsed))?.message;
  const m = typeof msg === 'string' ? /efn_promo:([a-z_]{1,32})/.exec(msg) : null;
  return m ? m[1]! : null;
}

export class Json2OdooPromoClient implements OdooPromoApi {
  private readonly logger = new Logger('OdooPromo');
  private readonly breakers = new Map<string, CircuitBreaker>();
  sleep: (ms: number) => Promise<void> = (ms) => new Promise((r) => setTimeout(r, ms).unref());
  now: () => number = () => Date.now();

  constructor(
    private readonly config: AppConfig,
    private readonly transport: Json2Transport,
    private readonly gates: OdooRpcGates,
  ) {}

  async feed(company: string): Promise<PromoFeed> {
    return parsePromoFeed(await this.call('feed', company, {}));
  }

  async image(company: string, kind: 'banner' | 'popup', id: number): Promise<PromoImage> {
    return parsePromoImage(await this.call('image', company, { kind, record_id: id }));
  }

  async pushClaim(company: string, limit: number): Promise<PromoPush[]> {
    return parsePromoPush(await this.call('pushClaim', company, { limit }));
  }

  private breaker(company: string): CircuitBreaker {
    let b = this.breakers.get(company);
    if (!b) {
      b = new CircuitBreaker(5, 30_000, () => this.now(), (open) => {
        if (open) this.logger.warn(`Circuit Odoo promo PT ${company} TERBUKA (30 s)`);
      });
      this.breakers.set(company, b);
    }
    return b;
  }

  private classify(res: TransportResponse): { value: unknown } | OdooPromoError {
    let parsed: unknown = null;
    let jsonOk = true;
    try {
      parsed = res.body.length > 0 ? JSON.parse(res.body) : null;
    } catch {
      jsonOk = false;
    }
    if (res.status === 200) return jsonOk ? { value: parsed } : new OdooPromoError('invalid_response');
    const biz = businessCode(parsed);
    if (biz) return new OdooPromoError(biz === 'not_found' ? 'not_found' : 'business', biz);
    if (res.status === 401 || res.status === 403) return new OdooPromoError('auth');
    if (res.status === 404) return new OdooPromoError('not_found');
    if (res.status === 429 || res.status >= 500 || res.status === 0) return new OdooPromoError('transport');
    return new OdooPromoError('invalid_response');
  }

  private async call(method: OdooPromoMethod, company: string, args: Record<string, unknown>): Promise<unknown> {
    const breaker = this.breaker(company);
    let apiKey: string;
    try {
      apiKey = this.config.odooApiKeyPromo(company);
    } catch {
      throw new OdooPromoError('auth');
    }
    if (!breaker.allow()) throw new OdooPromoError('circuit_open');
    const headers: Record<string, string> = { 'Content-Type': 'application/json', Accept: 'application/json', Authorization: `Bearer ${apiKey}` };
    const db = this.config.odooDb;
    if (db) headers['X-Odoo-Database'] = db;
    const body = JSON.stringify({ ...args, context: { lang: 'id_ID' } });
    // feed/image hanya baca; pushClaim mengubah status di Odoo (queued→sent) → TIDAK diulang (hindari klaim ganda hilang).
    const retries = method === 'pushClaim' ? 0 : BACKOFF_MS.length;
    for (let attempt = 0; ; attempt += 1) {
      let release: () => void;
      try {
        release = await this.gates.public.acquire();
      } catch {
        throw new OdooPromoError('busy');
      }
      let err: OdooPromoError;
      try {
        const out = this.classify(await this.transport(ODOO_PROMO_PATHS[method], body, headers, this.config.odooRpcTimeoutMs));
        if (!(out instanceof OdooPromoError)) {
          breaker.success();
          return out.value;
        }
        err = out;
      } catch {
        err = new OdooPromoError('transport');
      } finally {
        release();
      }
      if (err.kind !== 'transport') {
        breaker.neutral();
        this.logger.warn(`json2 promo ${method} PT ${company}: ${err.kind}${err.code ? ` ${err.code}` : ''}`);
        throw err;
      }
      breaker.failure();
      if (attempt >= retries || !breaker.allow()) throw err;
      await this.sleep(BACKOFF_MS[attempt]!);
    }
  }
}

/** Stub DEV/TEST (`ODOO_ONLINE_MOCK=true`): feed kosong kecuali diisi test. */
export class MockOdooPromo implements OdooPromoApi {
  feeds = new Map<string, PromoFeed>();
  images = new Map<string, Buffer>();
  pushes = new Map<string, PromoPush[]>();
  async feed(company: string): Promise<PromoFeed> {
    return this.feeds.get(company) ?? { banners: [], popups: [] };
  }
  async image(_company: string, kind: 'banner' | 'popup', id: number): Promise<PromoImage> {
    const data = this.images.get(`${kind}:${id}`);
    if (!data) throw new OdooPromoError('not_found', 'not_found');
    return { sha: createHash('sha256').update(data).digest('hex'), data };
  }
  async pushClaim(company: string, limit: number): Promise<PromoPush[]> {
    const q = this.pushes.get(company) ?? [];
    const out = q.splice(0, limit);
    return out;
  }
}
