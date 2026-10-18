import { Logger } from '@nestjs/common';
import { Agent, request } from 'node:https';
import { AppConfig } from '../config/app-config';
import { Semaphore } from '../common/concurrency/semaphore';
import { CircuitBreaker } from './circuit-breaker';
import { OnlineMetrics } from './online-metrics';
import {
  parseCancel,
  parseCatalogPage,
  parseCounterRefundAuthorize,
  parseImage,
  parseMarkPaid,
  parsePlace,
  parseQuote,
  parseRefund,
  parseRefundSettled,
  parseStatus,
  parseStock,
} from './odoo-online.parse';
import {
  CancelReason,
  CancelResult,
  CatalogPage,
  CounterRefundAuthorizeInput,
  CounterRefundAuthorizeResult,
  Fulfilment,
  ImageResult,
  MarkPaidInput,
  MarkPaidResult,
  ODOO_BUSINESS_CODES,
  ODOO_ONLINE_PATHS,
  OdooBusinessCode,
  OdooOnlineApi,
  OdooOnlineMethod,
  OdooRpcError,
  PlaceInput,
  PlaceResult,
  QuoteLineIn,
  QuoteResult,
  RefundInput,
  RefundResult,
  RefundSettledInput,
  StatusRow,
  StockRow,
} from './odoo-online.types';

export interface TransportResponse {
  status: number;
  body: string;
}
/** POST JSON ke `<base>/json/2/<path>`. Implementasi nyata: https + mTLS (`createJson2Transport`); test: fake. */
export type Json2Transport = (path: string, body: string, headers: Record<string, string>, timeoutMs: number) => Promise<TransportResponse>;

const MAX_RESPONSE_BYTES = 16 * 1024 * 1024;
const BUSINESS_RE = /^efn_online:([a-z_]{1,40})$/;
const BACKOFF_MS = [500, 1000, 2000];
/** SEC-109: metode yang dipicu pengunjung (pool publik); sisanya pool saga. */
const PUBLIC_METHODS: ReadonlySet<OdooOnlineMethod> = new Set<OdooOnlineMethod>(['catalog', 'image', 'stock', 'quote']);

/**
 * Transport https + mTLS ke `odoo-rpc-gateway` (docs/04 §10.4.1): verify-full terhadap CA internal (servername = host),
 * sertifikat klien CN `api-gateway`, TLS ≥ 1.2, keep-alive. Tidak ada fallback plaintext. Body respons dibatasi 16 MB.
 */
export function createJson2Transport(config: AppConfig): Json2Transport {
  const base = config.odooRpcUrl;
  const tls = config.odooRpcTls;
  const agent = new Agent({
    keepAlive: true,
    maxSockets: config.odooRpcPublicMaxConcurrency + config.odooRpcSagaMaxConcurrency, // SEC-96/109
    ca: tls.ca,
    cert: tls.cert,
    key: tls.key,
    rejectUnauthorized: true,
    minVersion: tls.minVersion,
    ...(tls.servername ? { servername: tls.servername } : {}),
  });
  const prefix = base.pathname.replace(/\/+$/, '');
  return (path, body, headers, timeoutMs) =>
    new Promise<TransportResponse>((resolve, reject) => {
      const req = request(
        {
          protocol: 'https:',
          hostname: base.hostname,
          port: base.port || 443,
          method: 'POST',
          path: `${prefix}/json/2/${path}`,
          agent,
          headers: { ...headers, 'Content-Length': Buffer.byteLength(body, 'utf8') },
        },
        (res) => {
          const chunks: Buffer[] = [];
          let size = 0;
          res.on('data', (c: Buffer) => {
            size += c.length;
            if (size > MAX_RESPONSE_BYTES) {
              req.destroy(new Error('respons Odoo terlalu besar'));
              return;
            }
            chunks.push(c);
          });
          res.on('end', () => {
            clearTimeout(timer);
            resolve({ status: res.statusCode ?? 0, body: Buffer.concat(chunks).toString('utf8') });
          });
          res.on('error', (e) => {
            clearTimeout(timer);
            reject(e);
          });
        },
      );
      const timer = setTimeout(() => req.destroy(Object.assign(new Error('timeout Odoo RPC'), { name: 'TimeoutError' })), timeoutMs);
      timer.unref();
      req.on('error', (e) => {
        clearTimeout(timer);
        reject(e);
      });
      req.write(body, 'utf8');
      req.end();
    });
}

interface CallOptions {
  timeoutMs: number;
  /** Batas total (termasuk retry) — jalur interaktif pelanggan. */
  deadlineMs?: number;
}

/**
 * Klien `/json/2` untuk 10 metode E-28 (docs/04 §10.4). Aturan:
 * - API key PER KANAL (`odoo_api_key_online_<channel>`), dipilih dari `channel_code`; tidak pernah key kanal lain.
 * - Body = argumen bernama + `context: { lang: "id_ID" }` saja.
 * - Retry hanya jaringan/timeout/429/5xx, maks 3× (0,5/1/2 s + jitter) — aman: metode baca tanpa efek samping, metode
 *   tulis membawa `idempotency_key`. Tidak retry UserError/401/403/404.
 * - Circuit breaker per kanal (5 gagal transport beruntun → 30 s). Metrik & log hanya metode/kanal/jenis/kode —
 *   tidak pernah body, Authorization, kode ambil, atau pesan/traceback Odoo.
 */
export class Json2OdooOnlineClient implements OdooOnlineApi {
  private readonly logger = new Logger('OdooJson2');
  private readonly breakers = new Map<string, CircuitBreaker>();
  /** SEC-96/SEC-109: semaphore terpisah jalur publik vs saga (antrean pendek); slot hanya ditahan selama request. */
  private readonly gates: Record<'public' | 'saga', Semaphore>;
  sleep: (ms: number) => Promise<void> = (ms) => new Promise((r) => setTimeout(r, ms).unref());
  now: () => number = () => Date.now();
  random: () => number = Math.random;

  constructor(
    private readonly config: AppConfig,
    private readonly transport: Json2Transport,
    private readonly metrics: OnlineMetrics,
  ) {
    const pub = config.odooRpcPublicMaxConcurrency;
    const saga = config.odooRpcSagaMaxConcurrency;
    this.gates = { public: new Semaphore(pub, pub * 8, 2_000), saga: new Semaphore(saga, saga * 8, 2_000) };
  }

  isCircuitOpen(channel: string): boolean {
    return this.breaker(channel).isOpen();
  }

  private breaker(channel: string): CircuitBreaker {
    let b = this.breakers.get(channel);
    if (!b) {
      b = new CircuitBreaker(5, 30_000, () => this.now(), (open) => {
        this.metrics.circuit(channel, open);
        if (open) this.logger.warn(`Circuit Odoo kanal ${channel} TERBUKA (30 s)`);
      });
      this.breakers.set(channel, b);
    }
    return b;
  }

  /** Slot pool untuk satu percobaan; antrean penuh/timeout → `busy`. */
  private async slot(method: OdooOnlineMethod): Promise<() => void> {
    try {
      return await this.gates[PUBLIC_METHODS.has(method) ? 'public' : 'saga'].acquire();
    } catch {
      this.metrics.odoo(method, 'busy', 0);
      throw new OdooRpcError('busy');
    }
  }

  private async call(method: OdooOnlineMethod, channel: string, args: Record<string, unknown>, opts: CallOptions): Promise<unknown> {
    const breaker = this.breaker(channel);
    const started = this.now();
    // slot diambil SEBELUM izin circuit (busy tidak mengganggu percobaan half-open)
    let release: (() => void) | null = await this.slot(method);
    if (!breaker.allow()) {
      release();
      this.metrics.odoo(method, 'circuit_open', 0);
      throw new OdooRpcError('circuit_open');
    }
    try {
      return await this.callAttempts(method, channel, args, opts, breaker, started, () => {
        const r = release;
        release = null;
        r?.();
      });
    } finally {
      release?.();
    }
  }

  private async callAttempts(
    method: OdooOnlineMethod,
    channel: string,
    args: Record<string, unknown>,
    opts: CallOptions,
    breaker: CircuitBreaker,
    started: number,
    releaseFirst: () => void,
  ): Promise<unknown> {
    let apiKey: string;
    try {
      apiKey = this.config.odooApiKeyOnline(channel);
    } catch (err) {
      breaker.neutral();
      this.logger.error(`API key kanal ${channel} tidak tersedia: ${err instanceof Error ? err.message : 'config'}`);
      this.metrics.authFailure(channel);
      this.metrics.odoo(method, 'auth', 0);
      throw new OdooRpcError('auth');
    }
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
      Accept: 'application/json',
      Authorization: `bearer ${apiKey}`,
    };
    const db = this.config.odooDb;
    if (db) headers['X-Odoo-Database'] = db;
    const body = JSON.stringify({ ...args, context: { lang: 'id_ID' } });

    let held: (() => void) | null = releaseFirst;
    for (let attempt = 0; ; attempt += 1) {
      let err: OdooRpcError;
      if (!held) held = await this.slot(method); // SEC-109: slot diambil ulang per percobaan (dilepas selama backoff)
      try {
        const res = await this.transport(ODOO_ONLINE_PATHS[method], body, headers, opts.timeoutMs);
        const out = this.classify(res);
        if (!(out instanceof OdooRpcError)) {
          breaker.success();
          this.metrics.odoo(method, 'ok', (this.now() - started) / 1000);
          return out.value;
        }
        err = out;
      } catch (e) {
        err = new OdooRpcError('transport', null, e instanceof Error && e.name === 'TimeoutError' ? 'timeout' : 'network');
      } finally {
        held();
        held = null;
      }
      if (err.kind !== 'transport') {
        breaker.neutral();
        if (err.kind === 'auth') this.metrics.authFailure(channel);
        this.metrics.odoo(method, err.kind === 'business' ? 'business' : err.kind, (this.now() - started) / 1000);
        this.logger.warn(`json2 ${method} kanal ${channel}: ${err.kind}${err.code ? ` ${err.code}` : ''}`);
        throw err;
      }
      breaker.failure();
      const delay = attempt < BACKOFF_MS.length ? BACKOFF_MS[attempt] + Math.floor(this.random() * 250) : -1;
      const elapsed = this.now() - started;
      const withinDeadline = opts.deadlineMs === undefined || elapsed + delay + 1 < opts.deadlineMs;
      if (delay < 0 || !withinDeadline || !breaker.allow()) {
        this.metrics.odoo(method, 'transport', elapsed / 1000);
        this.logger.warn(`json2 ${method} kanal ${channel}: transport gagal setelah ${attempt + 1} percobaan (${err.message})`);
        throw err;
      }
      await this.sleep(delay);
    }
  }

  private classify(res: TransportResponse): { value: unknown } | OdooRpcError {
    let parsed: unknown = undefined;
    let jsonOk = true;
    try {
      parsed = res.body.length > 0 ? JSON.parse(res.body) : null;
    } catch {
      jsonOk = false;
    }
    if (res.status === 200) {
      return jsonOk ? { value: parsed } : new OdooRpcError('invalid_response', null, 'body bukan JSON');
    }
    const code = businessCode(parsed);
    if (code) return new OdooRpcError('business', code);
    if (res.status === 401 || res.status === 403) return new OdooRpcError('auth', null, `HTTP ${res.status}`);
    if (res.status === 404) return new OdooRpcError('not_found', null, 'HTTP 404');
    if (res.status === 429 || res.status >= 500 || res.status === 0) return new OdooRpcError('transport', null, `HTTP ${res.status}`);
    return new OdooRpcError('invalid_response', null, `HTTP ${res.status}`);
  }

  // ───────────── 10 metode ─────────────
  async catalog(channel: string, since: string | null, cursor: string | null, limit: number): Promise<CatalogPage> {
    const v = await this.call('catalog', channel, { channel_code: channel, since, cursor, limit: Math.min(200, limit) }, {
      timeoutMs: this.config.odooRpcCatalogTimeoutMs,
    });
    return parseCatalogPage(v);
  }
  async stock(channel: string, outlet: string, productIds: number[]): Promise<StockRow[]> {
    const v = await this.call('stock', channel, { channel_code: channel, outlet_code: outlet, product_ids: productIds.slice(0, 200) }, {
      timeoutMs: this.config.odooRpcTimeoutMs,
      deadlineMs: this.config.odooRpcTimeoutMs,
    });
    return parseStock(v);
  }
  async image(channel: string, tmplId: number, size: 512 | 1024): Promise<ImageResult> {
    const v = await this.call('image', channel, { channel_code: channel, tmpl_id: tmplId, size }, { timeoutMs: this.config.odooRpcImageTimeoutMs });
    return parseImage(v);
  }
  async quote(channel: string, outlet: string, fulfilment: Fulfilment, lines: QuoteLineIn[], promoCodes: string[]): Promise<QuoteResult> {
    // §11.2 no. 2: `promo_codes` selalu dikirim (eksplisit, `[]` bila tanpa kode) — bentuk argumen tetap untuk Odoo
    const args = { channel_code: channel, outlet_code: outlet, fulfilment, lines, promo_codes: [...promoCodes] };
    const v = await this.call('quote', channel, args, {
      timeoutMs: this.config.odooRpcTimeoutMs,
      deadlineMs: this.config.odooRpcTimeoutMs,
    });
    return parseQuote(v);
  }
  async place(channel: string, order: PlaceInput): Promise<PlaceResult> {
    const v = await this.call('place', channel, { channel_code: channel, order }, {
      timeoutMs: this.config.odooRpcTimeoutMs,
      deadlineMs: this.config.odooRpcTimeoutMs * 2,
    });
    return parsePlace(v);
  }
  async markPaid(channel: string, platformOrderRef: string, payment: MarkPaidInput): Promise<MarkPaidResult> {
    const v = await this.call('markPaid', channel, { channel_code: channel, platform_order_ref: platformOrderRef, payment }, {
      timeoutMs: this.config.odooRpcTimeoutMs,
    });
    return parseMarkPaid(v);
  }
  async cancel(channel: string, platformOrderRef: string, reason: CancelReason, idempotencyKey: string): Promise<CancelResult> {
    const v = await this.call(
      'cancel',
      channel,
      { channel_code: channel, platform_order_ref: platformOrderRef, reason, idempotency_key: idempotencyKey },
      { timeoutMs: this.config.odooRpcTimeoutMs },
    );
    return parseCancel(v);
  }
  async refund(channel: string, platformOrderRef: string, refund: RefundInput): Promise<RefundResult> {
    const v = await this.call('refund', channel, { channel_code: channel, platform_order_ref: platformOrderRef, refund }, {
      timeoutMs: this.config.odooRpcTimeoutMs,
    });
    return parseRefund(v);
  }
  async refundSettled(channel: string, refundRef: string, settlement: RefundSettledInput): Promise<{ state: string }> {
    const v = await this.call('refundSettled', channel, { channel_code: channel, refund_ref: refundRef, settlement }, {
      timeoutMs: this.config.odooRpcTimeoutMs,
    });
    return parseRefundSettled(v);
  }
  /** L-60 (SEC-145): pool saga; idempoten di Odoo per `authorization.idempotency_key`. */
  async counterRefundAuthorize(channel: string, platformOrderRef: string, authorization: CounterRefundAuthorizeInput): Promise<CounterRefundAuthorizeResult> {
    const v = await this.call(
      'counterRefundAuthorize',
      channel,
      { channel_code: channel, platform_order_ref: platformOrderRef, authorization },
      { timeoutMs: this.config.odooRpcTimeoutMs },
    );
    return parseCounterRefundAuthorize(v);
  }
  async status(channel: string, platformOrderRefs: string[]): Promise<StatusRow[]> {
    const v = await this.call('status', channel, { channel_code: channel, platform_order_refs: platformOrderRefs.slice(0, 100) }, {
      timeoutMs: this.config.odooRpcTimeoutMs,
    });
    return parseStatus(v);
  }
}

/** Ambil kode `efn_online:<kode>` dari body error json2 (`{name, message}`; bentuk bersarang juga diterima). */
export function businessCode(body: unknown): OdooBusinessCode | null {
  if (typeof body !== 'object' || body === null) return null;
  const o = body as Record<string, unknown>;
  const candidates: unknown[] = [o.message];
  const e = o.error;
  if (typeof e === 'object' && e !== null) {
    const eo = e as Record<string, unknown>;
    candidates.push(eo.message);
    if (typeof eo.data === 'object' && eo.data !== null) candidates.push((eo.data as Record<string, unknown>).message);
  }
  for (const c of candidates) {
    if (typeof c !== 'string') continue;
    const m = BUSINESS_RE.exec(c.trim());
    if (m && (ODOO_BUSINESS_CODES as readonly string[]).includes(m[1])) return m[1] as OdooBusinessCode;
  }
  return null;
}
