import { Inject, Injectable, Logger } from '@nestjs/common';
import { createHash, randomUUID } from 'node:crypto';
import { AppConfig } from '../config/app-config';
import { ApiError, Errors } from '../common/errors/api-error';
import { QUEUES } from '../common/amqp/queues';
import { PiiCipher } from '../common/pii/crypto';
import { CatalogService } from '../catalog/catalog.service';
import { CartService } from '../cart/cart.service';
import { QuoteService, StoredQuote } from '../cart/quote.service';
import { ODOO_ONLINE, OdooOnlineApi, PlaceResult } from '../odoo-rpc/odoo-online.types';
import { OnlineErrors, mapOdooError } from '../odoo-rpc/online-errors';
import { OnlineMetrics } from '../odoo-rpc/online-metrics';
import { PaymentStatus } from '../payments/payment-gateway';
import { ONLINE_PAYMENTS, OnlineIntent, OnlinePaymentsPort } from '../payments/online-payments';
import { OrderRow, OrderStatus, PartRow, unpaidClosedMessage } from './online.model';
import { ONLINE_REPO, OnlineRepo } from './online.repository';
import { decryptPickupCode, encryptPickupCode, formatPickupCode, generatePickupCode } from './pickup-code';

/** L-8 (§10.17): key opak dari route handler Next.js (`chk1_…`, 48 karakter); BFF hanya memeriksa bentuk. */
export const IDEMPOTENCY_KEY_RE = /^[A-Za-z0-9_-]{16,128}$/;
/** Selisih minimum `expires_at` SO Odoo terhadap batas bayar BFF (§10.3.5 no. 5). */
const ODOO_EXPIRY_MARGIN_MS = 5 * 60_000;

export interface CheckoutInput {
  quoteId: string;
  acceptTotal: number;
}

export interface OrderView {
  order_id: string;
  status: OrderStatus;
  created_at: string;
  expires_at: string;
  paid_at: string | null;
  amount_payable: number;
  currency: 'IDR';
  payment: { method: 'qris'; status: PaymentStatus | 'none'; qr_string?: string; expires_at: string | null };
  parts: {
    channel_code: string;
    company_code: string;
    outlet_code: string;
    outlet_name: string;
    status: string;
    sale_order_name: string | null;
    lines: PartRow['lines'];
    tax_groups: PartRow['tax_groups'];
    amount_total: number;
    amount_payable: number;
    refunded_amount: number;
    ready_at: string | null;
    handed_over_at: string | null;
    cancel_reason: string | null;
  }[];
}

const iso = (d: Date | null): string | null => (d ? d.toISOString() : null);

export function requestSha256(input: CheckoutInput): string {
  return createHash('sha256').update(JSON.stringify({ quote_id: input.quoteId, accept_total: input.acceptTotal }), 'utf8').digest('hex');
}

/** Cursor daftar order (opak): base64url `<created_at iso>|<order_id>`. */
export function encodeOrderCursor(o: OrderRow): string {
  return Buffer.from(`${o.created_at.toISOString()}|${o.id}`, 'utf8').toString('base64url');
}
export function decodeOrderCursor(c: string): { createdAt: Date; id: string } | null {
  if (!/^[A-Za-z0-9_-]{1,120}$/.test(c)) return null;
  const m = /^(\d{4}-\d{2}-\d{2}T[\d:.]+Z)\|([0-9a-f-]{36})$/.exec(Buffer.from(c, 'base64url').toString('utf8'));
  return m ? { createdAt: new Date(m[1]), id: m[2] } : null;
}

/**
 * Order pelanggan (docs/04 §10.3.5–10.3.6, §10.6.3). Checkout:
 * idempotensi per member (`UNIQUE(member_id, idempotency_key)` + `request_sha256`) → validasi quote/cart/total/batas
 * order terbuka (di bawah advisory lock member) → order `created` + bagian `placing` + kode ambil terenkripsi →
 * `efn_online_place` per bagian (Odoo menghitung ulang, `quote_hash`) → cek Σ & `expires_at` → satu payment intent
 * (porsi per bagian) → `awaiting_payment`. Gagal di langkah mana pun → kompensasi `efn_online_cancel` (idempoten;
 * gagal → outbox `order.unpaid_closed`), order `failed`. Harga TIDAK PERNAH dari klien (`accept_total` hanya pembanding).
 * Polling status dijawab dari DB (tidak memanggil Odoo).
 */
@Injectable()
export class OrdersService {
  private readonly logger = new Logger('OnlineOrders');
  now: () => number = () => Date.now();

  constructor(
    private readonly config: AppConfig,
    @Inject(ONLINE_REPO) private readonly repo: OnlineRepo,
    @Inject(ODOO_ONLINE) private readonly odoo: OdooOnlineApi,
    @Inject(ONLINE_PAYMENTS) private readonly payments: OnlinePaymentsPort,
    private readonly quotes: QuoteService,
    private readonly cart: CartService,
    private readonly catalog: CatalogService,
    private readonly cipher: PiiCipher,
    private readonly metrics: OnlineMetrics,
  ) {}

  // ───────────── checkout ─────────────
  async checkout(memberId: string, key: string | undefined, input: CheckoutInput): Promise<OrderView> {
    if (!key) throw OnlineErrors.idempotencyKeyRequired();
    if (!IDEMPOTENCY_KEY_RE.test(key)) throw Errors.invalidRequest('Idempotency-Key tidak valid.');
    const sha = requestSha256(input);
    const replay = await this.repo.findOrderByKey(memberId, key);
    if (replay) return this.replay(replay, sha);

    const quote = await this.quotes.load(input.quoteId, memberId);
    if (!quote || Date.parse(quote.valid_until) <= this.now()) throw OnlineErrors.quoteExpired();
    const cart = await this.cart.get(memberId);
    if (cart.version !== quote.cart_version) throw OnlineErrors.cartChanged();
    if (input.acceptTotal !== quote.amount_payable_total) throw OnlineErrors.priceChanged();
    if (quote.groups.length === 0 || quote.groups.some((g) => g.issues.length > 0)) throw OnlineErrors.cartHasIssues();
    for (const g of quote.groups) if (this.odoo.isCircuitOpen(g.channel_code)) throw OnlineErrors.unavailable();

    const orderId = randomUUID();
    const expiresAt = new Date(this.now() + this.config.onlinePaymentTimeoutSeconds * 1000);
    const codes = new Map<string, string>();
    const created = await this.repo.tx(async (r) => {
      await r.lockMember(memberId);
      const again = await r.findOrderByKey(memberId, key);
      if (again) return again;
      if (await r.hasActiveDelete(memberId)) throw OnlineErrors.accountDeleting();
      if ((await r.countOpenOrders(memberId)) >= this.config.onlineMaxOpenOrdersPerMember) throw OnlineErrors.tooManyOpenOrders();
      await r.insertOrder({
        id: orderId,
        member_id: memberId,
        idempotency_key: key,
        request_sha256: sha,
        quote_id: input.quoteId,
        status: 'created',
        amount_payable: quote.amount_payable_total,
        currency: 'IDR',
        expires_at: expiresAt,
      });
      for (const g of quote.groups) {
        const partId = randomUUID();
        const code = generatePickupCode();
        codes.set(partId, code);
        await r.insertPart({
          id: partId,
          order_id: orderId,
          channel_code: g.channel_code,
          company_code: g.company_code,
          outlet_code: g.outlet_code,
          fulfilment: g.fulfilment,
          status: 'placing',
          lines: g.lines.map((l) => ({
            product_id: l.product_id,
            name: l.name,
            qty: l.qty,
            price_unit_incl: l.price_unit_incl,
            subtotal_incl: l.subtotal_incl,
            tax_labels: l.tax_labels,
          })),
          tax_groups: g.tax_groups,
          amount_total: g.amount_total,
          amount_payable: g.amount_payable,
          quote_hash: g.quote_hash,
          pickup_code_enc: encryptPickupCode(this.cipher, code),
        });
      }
      return null;
    });
    if (created) return this.replay(created, sha);
    this.metrics.order('created');
    return this.place(memberId, orderId, input.quoteId, quote, codes, input.acceptTotal, expiresAt);
  }

  private async replay(order: OrderRow, sha: string): Promise<OrderView> {
    if (order.request_sha256 !== sha) throw OnlineErrors.idempotencyConflict();
    return this.view(order);
  }

  private async place(
    memberId: string,
    orderId: string,
    quoteId: string,
    quote: StoredQuote,
    codes: Map<string, string>,
    acceptTotal: number,
    expiresAt: Date,
  ): Promise<OrderView> {
    const parts = await this.repo.getParts(orderId);
    const results = new Map<string, PlaceResult>();
    let failure: ApiError | null = null;
    for (const part of parts) {
      const g = quote.groups.find((x) => x.channel_code === part.channel_code);
      const code = codes.get(part.id);
      if (!g || !code) {
        failure = OnlineErrors.unavailable();
        break;
      }
      try {
        const res = await this.odoo.place(part.channel_code, {
          idempotency_key: `online:place:${part.id}`,
          platform_order_ref: part.id,
          checkout_ref: orderId,
          outlet_code: part.outlet_code,
          fulfilment: part.fulfilment,
          lines: g.lines.map((l) => ({ product_id: l.product_id, qty: l.qty })),
          quote_hash: g.quote_hash,
          pickup_code: code,
        });
        results.set(part.id, res);
        await this.repo.updatePart(part.id, {
          status: 'placed',
          sale_order_name: res.sale_order_name,
          odoo_state: res.order_state.slice(0, 32),
          amount_total: res.amount_total,
          amount_payable: res.amount_payable,
        });
        this.metrics.part(part.channel_code, 'placed');
      } catch (err) {
        failure = mapOdooError(err, this.metrics, { method: 'place', channel: part.channel_code, partId: part.id });
        break;
      }
    }
    codes.clear();
    if (!failure) {
      const sum = [...results.values()].reduce((s, r) => s + r.amount_payable, 0);
      if (sum !== acceptTotal) {
        this.metrics.mismatch('checkout');
        this.metrics.anomaly('checkout_total_mismatch', 'warning', { order_id: orderId, expected: acceptTotal, actual: sum });
        failure = OnlineErrors.priceChanged();
      } else {
        for (const [partId, r] of results) {
          if (!r.expires_at || r.expires_at.getTime() < expiresAt.getTime() + ODOO_EXPIRY_MARGIN_MS) {
            this.metrics.anomaly('online_config_error', 'critical', { order_id: orderId, part_id: partId, code: 'odoo_expiry_too_short' });
            failure = OnlineErrors.unavailable();
            break;
          }
        }
      }
    }
    let intent: OnlineIntent | null = null;
    if (!failure) {
      const fresh = await this.repo.getParts(orderId);
      try {
        intent = await this.payments.createOnlineIntent({
          orderId,
          amount: acceptTotal,
          portions: fresh.map((p) => ({ partId: p.id, channelCode: p.channel_code, companyCode: p.company_code, amount: p.amount_payable })),
          expiresAt,
          idempotencyKey: `online:intent:${orderId}`,
        });
      } catch (err) {
        failure = err instanceof ApiError ? err : Errors.paymentProviderError();
      }
    }
    if (failure || !intent) {
      await this.fail(orderId);
      throw failure ?? OnlineErrors.unavailable();
    }
    const paymentId = intent.paymentId;
    await this.repo.tx(async (r) => {
      await r.updateOrder(orderId, { payment_intent_id: paymentId });
      if (await r.transitionOrder(orderId, ['created'], 'awaiting_payment')) this.metrics.order('awaiting_payment');
    });
    await this.cart.clearIfVersion(memberId, quote.cart_version);
    await this.quotes.remove(quoteId).catch(() => undefined);
    const order = await this.repo.getOrder(orderId);
    if (!order) throw OnlineErrors.unavailable();
    this.logger.log(`Order online ${orderId} menunggu pembayaran (${parts.length} bagian)`);
    return this.view(order);
  }

  /** Kompensasi checkout gagal: batalkan semua bagian di Odoo (idempoten; `absent` = selesai). Gagal → outbox. */
  private async fail(orderId: string): Promise<void> {
    let pending = false;
    for (const part of await this.repo.getParts(orderId)) {
      try {
        await this.odoo.cancel(part.channel_code, part.id, 'place_failed', `online:cancel:${part.id}`);
        await this.repo.tx(async (r) => {
          const p = await r.getPart(part.id, true);
          if (!p) return;
          await r.updatePart(p.id, {
            status: p.status === 'placing' ? 'place_failed' : 'cancelled',
            cancel_reason: 'place_failed',
            cancelled_at: new Date(this.now()),
            pickup_code_enc: null,
          });
        });
      } catch {
        pending = true;
      }
    }
    await this.repo.tx(async (r) => {
      if (await r.transitionOrder(orderId, ['created'], 'failed', { close_reason: 'place_failed', closed_at: new Date(this.now()) })) {
        this.metrics.order('failed');
      }
      if (pending) {
        const m = unpaidClosedMessage(orderId, 'place_failed');
        await r.enqueue(QUEUES.ORDER_UNPAID_CLOSED, { ...m }, m.idempotency_key);
      }
    });
  }

  // ───────────── baca ─────────────
  async list(memberId: string, cursor: string | null, limit: number): Promise<{ items: unknown[]; next_cursor: string | null }> {
    const before = cursor ? decodeOrderCursor(cursor) : null;
    if (cursor && !before) throw Errors.invalidRequest();
    const orders = await this.repo.listOrdersOfMember(memberId, before, limit + 1);
    const page = orders.slice(0, limit);
    const parts = await this.repo.getPartsOfOrders(page.map((o) => o.id));
    return {
      items: page.map((o) => ({
        order_id: o.id,
        status: o.status,
        created_at: o.created_at.toISOString(),
        amount_payable: o.amount_payable,
        parts: parts
          .filter((p) => p.order_id === o.id)
          .map((p) => ({ channel_code: p.channel_code, company_code: p.company_code, status: p.status })),
      })),
      next_cursor: orders.length > limit && page.length > 0 ? encodeOrderCursor(page[page.length - 1]) : null,
    };
  }

  /** Anti-enumeration: order milik member lain / tidak ada → 404 sama. */
  async get(memberId: string, orderId: string): Promise<OrderView> {
    const order = await this.repo.getOrderOfMember(orderId, memberId);
    if (!order) throw Errors.notFound();
    return this.view(order);
  }

  async view(order: OrderRow): Promise<OrderView> {
    const parts = await this.repo.getParts(order.id);
    const intent = order.payment_intent_id ? await this.payments.getIntent(order.payment_intent_id) : null;
    const channels = await this.catalog.channels().catch(() => []);
    const outletName = (ch: string, code: string): string =>
      channels.find((c) => c.channel_code === ch)?.outlets.find((o) => o.outlet_code === code)?.public_name ?? code;
    const payment: OrderView['payment'] = { method: 'qris', status: intent?.status ?? 'none', expires_at: iso(intent?.expiresAt ?? order.expires_at) };
    if (order.status === 'awaiting_payment' && intent?.status === 'pending' && intent.qrString) payment.qr_string = intent.qrString;
    return {
      order_id: order.id,
      status: order.status,
      created_at: order.created_at.toISOString(),
      expires_at: order.expires_at.toISOString(),
      paid_at: iso(order.paid_at),
      amount_payable: order.amount_payable,
      currency: 'IDR',
      payment,
      parts: parts.map((p) => ({
        channel_code: p.channel_code,
        company_code: p.company_code,
        outlet_code: p.outlet_code,
        outlet_name: outletName(p.channel_code, p.outlet_code),
        status: p.status,
        sale_order_name: p.sale_order_name,
        lines: p.lines,
        tax_groups: p.tax_groups,
        amount_total: p.amount_total,
        amount_payable: p.amount_payable,
        refunded_amount: p.refunded_amount,
        ready_at: iso(p.ready_at),
        handed_over_at: iso(p.handed_over_at),
        cancel_reason: p.cancel_reason,
      })),
    };
  }

  // ───────────── batal (pelanggan) ─────────────
  async cancel(memberId: string, orderId: string): Promise<{ status: 'cancelled' }> {
    const order = await this.repo.getOrderOfMember(orderId, memberId);
    if (!order) throw Errors.notFound();
    if (order.status === 'cancelled' && order.close_reason === 'customer') return { status: 'cancelled' };
    if (order.status !== 'created' && order.status !== 'awaiting_payment') throw OnlineErrors.orderNotCancellable();
    if (order.payment_intent_id) {
      let st: PaymentStatus;
      try {
        st = await this.payments.cancelIntent(order.payment_intent_id);
      } catch (err) {
        if (err instanceof ApiError && err.getStatus() === 404) st = 'cancelled';
        else throw err;
      }
      if (st === 'paid') throw OnlineErrors.orderNotCancellable();
    }
    const result = await this.repo.tx(async (r) => {
      const o = await r.getOrder(orderId, true);
      if (!o) throw Errors.notFound();
      if (o.status === 'cancelled') return 'cancelled' as const;
      if (o.payment_intent_id) {
        const intent = await this.payments.getIntent(o.payment_intent_id);
        if (intent?.status === 'paid') return 'paid' as const; // webhook menang
      }
      if (!(await r.transitionOrder(orderId, ['created', 'awaiting_payment'], 'cancelled', { close_reason: 'customer', closed_at: new Date(this.now()) }))) {
        return 'paid' as const;
      }
      const m = unpaidClosedMessage(orderId, 'customer');
      await r.enqueue(QUEUES.ORDER_UNPAID_CLOSED, { ...m }, m.idempotency_key);
      return 'cancelled' as const;
    });
    if (result !== 'cancelled') throw OnlineErrors.orderNotCancellable();
    this.metrics.order('cancelled');
    return { status: 'cancelled' };
  }

  // ───────────── kode ambil ─────────────
  async pickupCodes(memberId: string, orderId: string): Promise<{ items: { channel_code: string; outlet_code: string; outlet_name: string; pickup_code: string }[] }> {
    const order = await this.repo.getOrderOfMember(orderId, memberId);
    if (!order) throw Errors.notFound();
    const parts = (await this.repo.getParts(orderId)).filter((p) => (p.status === 'confirmed' || p.status === 'ready') && p.pickup_code_enc);
    if (parts.length === 0) throw OnlineErrors.pickupCodeUnavailable();
    const channels = await this.catalog.channels().catch(() => []);
    return {
      items: parts.map((p) => ({
        channel_code: p.channel_code,
        outlet_code: p.outlet_code,
        outlet_name: channels.find((c) => c.channel_code === p.channel_code)?.outlets.find((o) => o.outlet_code === p.outlet_code)?.public_name ?? p.outlet_code,
        pickup_code: formatPickupCode(decryptPickupCode(this.cipher, p.pickup_code_enc as Buffer)),
      })),
    };
  }

  // ───────────── simulasi bayar (dev/staging) ─────────────
  async simulatePayment(memberId: string, orderId: string, status: Exclude<PaymentStatus, 'pending'>): Promise<OrderView> {
    if (this.config.isProductionDeployment) throw Errors.notFound();
    const order = await this.repo.getOrderOfMember(orderId, memberId);
    if (!order?.payment_intent_id) throw Errors.notFound();
    await this.payments.simulate(order.payment_intent_id, status);
    const fresh = await this.repo.getOrder(orderId);
    if (!fresh) throw Errors.notFound();
    return this.view(fresh);
  }
}

