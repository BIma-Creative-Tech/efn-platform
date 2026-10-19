import { Inject, Injectable, Logger, Optional } from '@nestjs/common';
import { createHash, randomUUID } from 'node:crypto';
import { AppConfig } from '../config/app-config';
import { ApiError, Errors } from '../common/errors/api-error';
import { QUEUES } from '../common/amqp/queues';
import { PiiCipher } from '../common/pii/crypto';
import { CatalogService } from '../catalog/catalog.service';
import { CartService } from '../cart/cart.service';
import { QuoteService, StoredQuote, placeLines } from '../cart/quote.service';
import { INVOICE_COMPANIES } from './invoice-request';
import { InvoiceBuyerProfile, ODOO_ONLINE, OdooOnlineApi, OdooRpcError, PlaceResult, isOdooBusiness } from '../odoo-rpc/odoo-online.types';
import { OnlineErrors, mapOdooError } from '../odoo-rpc/online-errors';
import { OnlineMetrics } from '../odoo-rpc/online-metrics';
import { PaymentStatus } from '../payments/payment-gateway';
import { ONLINE_PAYMENTS, OnlineIntent, OnlinePaymentsPort } from '../payments/online-payments';
import { OrderRow, OrderStatus, PartRow, PaymentMethod, UNPAID_OPEN, unpaidClosedMessage } from './online.model';
import { ONLINE_REPO, OnlineRepo } from './online.repository';
import { OnlineSagaService } from './online-saga.service';
import {
  decryptCounterCode,
  decryptPickupCode,
  encryptCounterCode,
  encryptPickupCode,
  formatPickupCode,
  generatePickupCode,
} from './pickup-code';

/** L-8 (§10.17): key opak dari route handler Next.js (`chk1_…`, 48 karakter); BFF hanya memeriksa bentuk. */
export const IDEMPOTENCY_KEY_RE = /^[A-Za-z0-9_-]{16,128}$/;
/**
 * Selisih minimum `expires_at` SO Odoo terhadap batas bayar BFF (§10.3.5 no. 5, tetap berlaku di G19 §11.1).
 * Default G19: BFF 3600 s + Odoo `payment_timeout_minutes` 70 → selisih ±10 menit (≥ 5). Odoo masih 25 sementara BFF
 * sudah 3600 → checkout ditolak `503 online_unavailable` + anomali `odoo_expiry_too_short` (urutan aktivasi salah).
 */
export const ODOO_EXPIRY_MARGIN_MS = 5 * 60_000;

export interface CheckoutInput {
  quoteId: string;
  acceptTotal: number;
  /** G20 §12.2 no. 2: default `qris`. */
  paymentMethod?: PaymentMethod;
  /**
   * G21 §13.5: profil pembeli faktur pajak (PII). Hanya di memori selama request; diteruskan sekali ke Odoo
   * `efn_online_set_invoice_party`; TIDAK masuk DB/outbox/Redis/log (K-36-7, L-63).
   */
  invoiceRequest?: InvoiceBuyerProfile;
}

/**
 * G21 §13.5 (aditif, hanya di respons checkout — tidak disimpan): `applied` = partner faktur terpasang di semua bagian;
 * `pending_cs` = sebagian/seluruh pemasangan gagal (anomali) → faktur atas nama dibuat CS.
 */
export interface InvoiceRequestView {
  status: 'applied' | 'pending_cs';
}

/** QRIS (gateway) — bentuk pra-G20, tidak berubah. */
export interface QrisPaymentView {
  method: 'qris';
  status: PaymentStatus | 'none';
  qr_string?: string;
  expires_at: string | null;
}
/**
 * G20 §12.2 no. 4 / L-52: bayar di kasir — PERSIS `{ method, counter_code, expires_at }` selama order
 * `awaiting_counter_payment`; status lain `{ method, expires_at }` (tanpa `counter_code`). `counter_code` tampil
 * `ABCD-EFGH` (kode BAYAR, bukan kode ambil).
 */
export interface CounterPaymentView {
  method: 'counter';
  counter_code?: string;
  expires_at: string;
}

export interface OrderView {
  order_id: string;
  status: OrderStatus;
  created_at: string;
  expires_at: string;
  paid_at: string | null;
  amount_payable: number;
  currency: 'IDR';
  payment: QrisPaymentView | CounterPaymentView;
  /** G21 §13.5: hanya pada respons checkout pertama yang membawa `invoice_request`. */
  invoice_request?: InvoiceRequestView;
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

/**
 * L-14: status yang dilihat pelanggan. Bila order `paid|fulfilling` punya bagian `review`, pelanggan melihat `review`
 * ("sedang diperiksa"). Hanya tampilan — status DB tetap diturunkan dari bagian (§10.6.1) agar pulih otomatis saat
 * ops menyelesaikan pemeriksaan.
 */
export function customerStatus(order: OrderRow, parts: readonly PartRow[]): OrderStatus {
  if ((order.status === 'paid' || order.status === 'fulfilling') && parts.some((p) => p.status === 'review')) return 'review';
  // L-57: pesanan counter yang dilunasi di kasir setelah batal/kedaluwarsa → "sedang diperiksa" (status DB tetap ditutup)
  if (order.payment_method === 'counter' && (order.status === 'cancelled' || order.status === 'expired' || order.status === 'failed') && parts.some((p) => p.status === 'review')) {
    return 'review';
  }
  return order.status;
}

/**
 * Sidik body checkout untuk idempotensi. G20: `payment_method` ikut HANYA bila `counter` (sidik order QRIS sama dengan
 * pra-G20) → key sama dengan metode berbeda = `409 idempotency_conflict`.
 */
export function requestSha256(input: CheckoutInput): string {
  const body: Record<string, unknown> = { quote_id: input.quoteId, accept_total: input.acceptTotal };
  if (input.paymentMethod === 'counter') body.payment_method = 'counter';
  // G21 §13.5: HANYA penanda ada/tidaknya permintaan faktur — isi profil (PII, NPWP/NIK berentropi rendah) tidak pernah
  // ikut di-hash/disimpan. Sidik order tanpa faktur sama dengan pra-G21.
  if (input.invoiceRequest) body.invoice_request = true;
  return createHash('sha256').update(JSON.stringify(body), 'utf8').digest('hex');
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
    /** G20: hanya untuk simulasi bayar di kasir (dev, stub Odoo). Opsional agar test lama tetap jalan. */
    @Optional() private readonly saga: OnlineSagaService | null = null,
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
    const method: PaymentMethod = input.paymentMethod ?? 'qris';
    // G20 §12.2 no. 2: `counter` hanya satu bagian (satu PT, L-50); metode harus diizinkan SETIAP kanal
    // L-55: metode di luar ONLINE_PAYMENT_METHODS → 422 (gerbang deploy pilot; `counter` tidak butuh provider)
    if (!this.config.onlinePaymentMethods.includes(method)) throw OnlineErrors.paymentMethodUnavailable();
    if (method === 'counter' && quote.groups.length > 1) throw OnlineErrors.counterPaymentSinglePartOnly();
    // G21 §13.5: faktur atas nama hanya bila SEMUA bagian kanal PT PPN (GL, BCR); F&B (PB1) → 422 sebelum order dibuat
    if (input.invoiceRequest && quote.groups.some((g) => !INVOICE_COMPANIES.includes(g.company_code))) {
      throw OnlineErrors.invoiceNotAvailable();
    }
    for (const g of quote.groups) {
      const ch = await this.catalog.channel(g.channel_code);
      if (!ch) throw OnlineErrors.itemUnavailable();
      if (!(ch.payment_modes ?? this.config.onlinePaymentMethods).includes(method)) throw OnlineErrors.paymentMethodUnavailable();
    }
    for (const g of quote.groups) if (this.odoo.isCircuitOpen(g.channel_code)) throw OnlineErrors.unavailable();

    const orderId = randomUUID();
    const expiresAt = new Date(this.now() + this.config.onlinePaymentTimeoutSeconds * 1000);
    const codes = new Map<string, string>();
    /** L-56: kode bayar dibuat BFF (pola kode ambil) dan disimpan terenkripsi di transaksi yang sama, SEBELUM place. */
    const counterCodes = new Map<string, string>();
    const created = await this.repo.tx(async (r) => {
      await r.lockMember(memberId);
      const again = await r.findOrderByKey(memberId, key);
      if (again) return again;
      if (await r.hasActiveDelete(memberId)) throw OnlineErrors.accountDeleting();
      if ((await r.countOpenOrders(memberId)) >= this.config.onlineMaxOpenOrdersPerMember) throw OnlineErrors.tooManyOpenOrders();
      if (method === 'counter' && (await r.countOpenOrders(memberId, 'counter')) >= this.config.onlineMaxOpenCounterOrdersPerMember) {
        throw OnlineErrors.tooManyOpenCounterOrders();
      }
      await r.insertOrder({
        id: orderId,
        member_id: memberId,
        idempotency_key: key,
        request_sha256: sha,
        quote_id: input.quoteId,
        status: 'created',
        amount_payable: quote.amount_payable_total,
        currency: 'IDR',
        payment_method: method,
        expires_at: expiresAt,
      });
      for (const g of quote.groups) {
        const partId = randomUUID();
        const code = generatePickupCode();
        codes.set(partId, code);
        let counterCode: string | null = null;
        if (method === 'counter') {
          // kode bayar ≠ kode ambil (L-50): ulang sampai berbeda (peluang sama ≈ 1/31^8)
          let next = generatePickupCode();
          while (next === code) next = generatePickupCode();
          counterCode = next;
          counterCodes.set(partId, next);
        }
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
            ...(l.reward === true ? { reward: true as const } : {}), // G19 §11.2: baris reward tampil di status order
          })),
          tax_groups: g.tax_groups,
          amount_total: g.amount_total,
          amount_payable: g.amount_payable,
          quote_hash: g.quote_hash,
          pickup_code_enc: encryptPickupCode(this.cipher, code, partId),
          counter_code_enc: counterCode ? encryptCounterCode(this.cipher, counterCode, partId) : null,
        });
      }
      return null;
    });
    if (created) return this.replay(created, sha);
    this.metrics.order('created');
    return this.place(memberId, orderId, input.quoteId, quote, codes, input.acceptTotal, expiresAt, method, counterCodes, input.invoiceRequest ?? null);
  }

  /**
   * G21 §13.5: setelah SEMUA bagian ter-place (dan total/expires_at lolos), pasang partner faktur pembeli per bagian
   * (`efn_online_set_invoice_party`, idempoten `online:invoice:<part_id>`). `invoice_not_available` dari Odoo (efn_pii
   * belum aktif, L-63) → kegagalan checkout (order dikompensasi, 422 diteruskan). Gagal lain (transport/circuit/auth/
   * respons) → anomali `invoice_party_failed` + lanjut (partner generik; faktur atas nama dibuat CS). Profil tidak pernah
   * di-log: anomali hanya order/part/kode.
   */
  private async applyInvoiceParty(
    orderId: string,
    parts: readonly PartRow[],
    profile: InvoiceBuyerProfile,
  ): Promise<{ failure: ApiError | null; view: InvoiceRequestView }> {
    let pending = false;
    for (const part of parts) {
      try {
        await this.odoo.setInvoiceParty(part.channel_code, part.id, profile, `online:invoice:${part.id}`);
      } catch (err) {
        if (isOdooBusiness(err, 'invoice_not_available')) return { failure: OnlineErrors.invoiceNotAvailable(), view: { status: 'pending_cs' } };
        pending = true;
        const code = err instanceof OdooRpcError ? (err.code ?? err.kind) : 'error';
        this.metrics.anomaly('invoice_party_failed', 'warning', { order_id: orderId, part_id: part.id, channel: part.channel_code, code });
      }
    }
    return { failure: null, view: { status: pending ? 'pending_cs' : 'applied' } };
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
    method: PaymentMethod,
    counterCodes: Map<string, string>,
    invoice: InvoiceBuyerProfile | null = null,
  ): Promise<OrderView> {
    const parts = await this.repo.getParts(orderId);
    const results = new Map<string, PlaceResult>();
    let failure: ApiError | null = null;
    for (const part of parts) {
      const g = quote.groups.find((x) => x.channel_code === part.channel_code);
      const code = codes.get(part.id);
      const counterCode = counterCodes.get(part.id);
      if (!g || !code || (method === 'counter' && !counterCode)) {
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
          lines: placeLines(g), // hanya produk cart; baris reward dihitung ulang Odoo dari promo_codes
          quote_hash: g.quote_hash,
          pickup_code: code,
          // G19 §11.2 no. 5: kode yang TERPAKAI di grup ini menurut quote tersimpan (bukan dari klien)
          promo_codes: [...(g.promo_applied ?? [])],
          // G20 §12.2 no. 3 + L-56: hanya untuk bayar di kasir (payload QRIS tidak berubah); kode bayar rahasia, tidak di-log
          ...(method === 'counter' && counterCode ? { payment_mode: 'counter' as const, counter_code: counterCode } : {}),
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
    counterCodes.clear();
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
    // G21 §13.5: faktur atas nama SEBELUM payment intent (invoice_not_available → order dikompensasi tanpa intent)
    let invoiceView: InvoiceRequestView | null = null;
    if (!failure && invoice) {
      const res = await this.applyInvoiceParty(orderId, parts, invoice);
      failure = res.failure;
      invoiceView = res.view;
    }
    const withInvoice = (v: OrderView): OrderView => (invoiceView ? { ...v, invoice_request: invoiceView } : v);
    if (!failure && method === 'counter') {
      // G20 §12.2 no. 2: TANPA payment intent — pelanggan membayar di kasir dengan kode bayar
      await this.repo.tx(async (r) => {
        if (await r.transitionOrder(orderId, ['created'], 'awaiting_counter_payment')) this.metrics.order('awaiting_counter_payment');
      });
      await this.cart.clearIfVersion(memberId, quote.cart_version);
      await this.quotes.remove(quoteId).catch(() => undefined);
      const order = await this.repo.getOrder(orderId);
      if (!order) throw OnlineErrors.unavailable();
      this.logger.log(`Order online ${orderId} menunggu bayar di kasir (${parts.length} bagian)`);
      return withInvoice(await this.view(order));
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
    return withInvoice(await this.view(order));
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
            counter_code_enc: null,
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
        status: customerStatus(o, parts.filter((p) => p.order_id === o.id)),
        created_at: o.created_at.toISOString(),
        amount_payable: o.amount_payable,
        payment_method: o.payment_method, // G20 (aditif): `qris` | `counter`
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
    let payment: OrderView['payment'];
    if (order.payment_method === 'counter') {
      payment = this.counterPayment(order, parts);
    } else {
      const qris: QrisPaymentView = { method: 'qris', status: intent?.status ?? 'none', expires_at: iso(intent?.expiresAt ?? order.expires_at) };
      if (order.status === 'awaiting_payment' && intent?.status === 'pending' && intent.qrString) qris.qr_string = intent.qrString;
      payment = qris;
    }
    return {
      order_id: order.id,
      status: customerStatus(order, parts),
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

  /**
   * L-52 no. 1: `counter_code` didekripsi HANYA saat order `awaiting_counter_payment` (pemanggil `view` selalu sudah
   * memastikan order milik member: `get`/`checkout`/`simulate`). Gagal dekripsi → kode tidak ditampilkan + anomali
   * (bukan 500), pelanggan bisa membatalkan dan memesan ulang.
   */
  private counterPayment(order: OrderRow, parts: readonly PartRow[]): CounterPaymentView {
    const out: CounterPaymentView = { method: 'counter', expires_at: order.expires_at.toISOString() };
    if (order.status !== 'awaiting_counter_payment') return out;
    const part = parts.find((p) => p.status === 'placed' && p.counter_code_enc);
    if (!part?.counter_code_enc) return out;
    try {
      out.counter_code = formatPickupCode(decryptCounterCode(this.cipher, part.counter_code_enc, part.id));
    } catch {
      this.metrics.anomaly('counter_code_unreadable', 'critical', { order_id: order.id, part_id: part.id });
    }
    return out;
  }

  // ───────────── batal (pelanggan) ─────────────
  async cancel(memberId: string, orderId: string): Promise<{ status: 'cancelled' }> {
    const order = await this.repo.getOrderOfMember(orderId, memberId);
    if (!order) throw Errors.notFound();
    if (order.status === 'cancelled' && order.close_reason === 'customer') return { status: 'cancelled' };
    // G20 L-52 no. 3: `awaiting_counter_payment` boleh dibatalkan (alur batal tanpa refund — belum ada uang masuk)
    if (!UNPAID_OPEN.includes(order.status)) throw OnlineErrors.orderNotCancellable();
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
      if (!(await r.transitionOrder(orderId, UNPAID_OPEN, 'cancelled', { close_reason: 'customer', closed_at: new Date(this.now()) }))) {
        return 'paid' as const; // termasuk event `paid` bayar di kasir yang menang lebih dulu
      }
      // Kode bayar tidak lagi berlaku → hapus segera (retensi juga membersihkan)
      for (const p of await r.getParts(orderId)) if (p.counter_code_enc) await r.updatePart(p.id, { counter_code_enc: null });
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
    const all = await this.repo.getParts(orderId);
    // L-14: selama ada bagian `review`, kode ambil tidak ditampilkan sama sekali
    if (all.some((p) => p.status === 'review')) throw OnlineErrors.orderInReview();
    const parts = all.filter((p) => (p.status === 'confirmed' || p.status === 'ready') && p.pickup_code_enc);
    if (parts.length === 0) throw OnlineErrors.pickupCodeUnavailable();
    const channels = await this.catalog.channels().catch(() => []);
    const items = [];
    for (const p of parts) {
      const dec = decryptPickupCode(this.cipher, p.pickup_code_enc as Buffer, p.id);
      // SEC-102: baris lama (AAD v1 per kolom) ditulis ulang ke AAD v2 terikat part_id (best effort, optimistik)
      if (dec.legacy) await this.repo.updatePart(p.id, { pickup_code_enc: encryptPickupCode(this.cipher, dec.code, p.id) }).catch(() => undefined);
      items.push({
        channel_code: p.channel_code,
        outlet_code: p.outlet_code,
        outlet_name: channels.find((c) => c.channel_code === p.channel_code)?.outlets.find((o) => o.outlet_code === p.outlet_code)?.public_name ?? p.outlet_code,
        pickup_code: formatPickupCode(dec.code),
      });
    }
    return { items };
  }

  // ───────────── simulasi bayar (dev/staging) ─────────────
  async simulatePayment(memberId: string, orderId: string, status: Exclude<PaymentStatus, 'pending'>): Promise<OrderView> {
    if (this.config.isProductionDeployment) throw Errors.notFound();
    const order = await this.repo.getOrderOfMember(orderId, memberId);
    if (order?.payment_method === 'counter') return this.simulateCounter(order, status);
    if (!order?.payment_intent_id) throw Errors.notFound();
    await this.payments.simulate(order.payment_intent_id, status);
    const fresh = await this.repo.getOrder(orderId);
    if (!fresh) throw Errors.notFound();
    return this.view(fresh);
  }

  /**
   * DEV SAJA (G20): bayar di kasir hanya bisa disimulasikan dengan stub Odoo (`ODOO_ONLINE_MOCK`, fail-closed di
   * staging/production) — meniru sync POS (`payAtCounter`) + event `paid` (`method: counter`). Odoo sungguhan → 404
   * (pelunasan nyata hanya lewat POS). Hanya `status: "paid"`; kedaluwarsa = job BFF biasa.
   */
  private async simulateCounter(order: OrderRow, status: Exclude<PaymentStatus, 'pending'>): Promise<OrderView> {
    const stub = this.odoo as unknown as { payAtCounter?: (ref: string) => { state: string; seq: number } };
    if (!this.saga || typeof stub.payAtCounter !== 'function') throw Errors.notFound();
    if (status !== 'paid') throw Errors.invalidRequest();
    if (order.status !== 'awaiting_counter_payment') throw OnlineErrors.invalidState();
    for (const part of (await this.repo.getParts(order.id)).filter((p) => p.status === 'placed')) {
      let res: { state: string; seq: number };
      try {
        res = stub.payAtCounter(part.id);
      } catch {
        throw OnlineErrors.invalidState();
      }
      const saga = this.saga;
      await this.repo.tx(async (r) => {
        const o = await r.getOrder(order.id, true);
        const p = await r.getPart(part.id, true);
        if (o && p) await saga.applyCounterPaid(r, o, p, { source: 'bff', odooState: res.state, seq: res.seq, paidAt: null });
      });
    }
    const fresh = await this.repo.getOrder(order.id);
    if (!fresh) throw Errors.notFound();
    return this.view(fresh);
  }
}

