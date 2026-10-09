import { createHash, randomUUID } from 'node:crypto';
import sharp from 'sharp';
import {
  CancelReason,
  CancelResult,
  CatalogItem,
  CatalogPage,
  Fulfilment,
  ImageResult,
  MarkPaidInput,
  MarkPaidResult,
  OdooOnlineApi,
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

/**
 * STUB json2 untuk DEV/TEST SAJA (`ODOO_ONLINE_MOCK=true`, L-7). `AppConfig.odooOnlineMock` menolak start di
 * staging/production (fail-closed). Meniru kontrak E-28 (docs/04 §10.4.3 + delta §10.4.4) secara in-memory per proses:
 * idempotensi place/cancel/refund, `absent` untuk cancel ref tak dikenal, `price_changed` bila `quote_hash` berbeda,
 * mark_paid → confirmed/stock_short/review/refund_required.
 *
 * SEMUA HARGA & PAJAK DI SINI ADALAH DATA DEMO PLACEHOLDER — bukan aturan bisnis. Produk demo kanal `RTL`:
 *   11 Beras 5 kg (in_stock), 12 Minyak goreng 2 L (low), 13 "Uji stok-kurang" (lolos quote, `stock_short` saat
 *   mark_paid), 14 "Uji habis" (`out` → issue `out_of_stock` saat quote). Outlet `TOKO-01`.
 * Kode ambil: mock hanya menyimpan SHA-256-nya (meniru Odoo yang menyimpan hash KDF), tidak pernah teks asli.
 */
interface MockOrder {
  ref: string;
  channel: string;
  key: string;
  payloadSha: string;
  state: string;
  seq: number;
  lines: QuoteLineIn[];
  amountTotal: number;
  amountPayable: number;
  saleOrderName: string;
  expiresAt: Date;
  pickupSha: string;
  paymentRef: string | null;
  refunded: number;
  readyAt: string | null;
  pickedUpAt: string | null;
  cancelRefund: { refund_ref: string; amount: number } | null;
}

const PRODUCTS: { id: number; tmpl: number; name: string; priceIncl: number; categ: number; stock: 'in_stock' | 'low' | 'out' }[] = [
  { id: 11, tmpl: 101, name: 'Beras Premium 5 kg', priceIncl: 79_900, categ: 1, stock: 'in_stock' },
  { id: 12, tmpl: 102, name: 'Minyak Goreng 2 L', priceIncl: 38_500, categ: 1, stock: 'low' },
  { id: 13, tmpl: 103, name: 'Produk Uji Stok-Kurang (mock)', priceIncl: 12_000, categ: 2, stock: 'in_stock' },
  { id: 14, tmpl: 104, name: 'Produk Uji Habis (mock)', priceIncl: 15_000, categ: 2, stock: 'out' },
];
const STOCK_SHORT_PRODUCT = 13;
/** Hanya untuk demo: label pajak tampilan; tarif sungguhan selalu dari Odoo. */
const TAX_LABEL = 'PPN (demo)';
const TAX_NUM = 11;
const TAX_DEN = 111;

function sha(s: string): string {
  return createHash('sha256').update(s, 'utf8').digest('hex');
}
function round2(n: number): number {
  return Math.round(n * 100) / 100;
}

export class MockOdooOnline implements OdooOnlineApi {
  readonly orders = new Map<string, MockOrder>();
  private readonly refundKeys = new Map<string, RefundResult>();
  private readonly cancelKeys = new Set<string>();
  /** Naikkan untuk mensimulasikan perubahan pricelist (quote lama → `price_changed`). */
  priceVersion = 1;
  /** Panggilan per metode (untuk test). */
  readonly calls: { method: string; channel: string }[] = [];
  /** Kegagalan terjadwal untuk test: metode → error yang dilempar sekali. */
  readonly failNext = new Map<string, OdooRpcError>();
  now: () => number = () => Date.now();
  readonly channels = new Set(['RTL']);

  private track(method: string, channel: string): void {
    this.calls.push({ method, channel });
    const f = this.failNext.get(method);
    if (f) {
      this.failNext.delete(method);
      throw f;
    }
    if (!this.channels.has(channel)) throw new OdooRpcError('business', 'channel_inactive');
  }

  isCircuitOpen(): boolean {
    return false;
  }

  private price(id: number): number {
    const p = PRODUCTS.find((x) => x.id === id);
    if (!p) throw new OdooRpcError('business', 'not_found');
    return p.priceIncl + (this.priceVersion - 1) * 100;
  }

  async catalog(channel: string, since: string | null): Promise<CatalogPage> {
    this.track('catalog', channel);
    const items: CatalogItem[] = since
      ? []
      : PRODUCTS.map((p, i) => {
          const incl = this.price(p.id);
          return {
            product_id: p.id,
            tmpl_id: p.tmpl,
            name: p.name,
            description: `Deskripsi demo ${p.name}.`,
            categ: { id: p.categ, name: p.categ === 1 ? 'Sembako' : 'Uji', parent_id: null, sequence: p.categ },
            uom: 'Unit',
            image_sha: sha(`mock-image-${p.tmpl}`).slice(0, 40),
            sequence: i + 1,
            prices: [{ outlet_code: null, price_incl: incl, price_excl: round2((incl * TAX_DEN) / (TAX_DEN + TAX_NUM)), tax_labels: [TAX_LABEL] }],
            max_qty: 20,
          };
        });
    return {
      items,
      deleted: [],
      next_cursor: null,
      server_time: new Date(this.now()).toISOString(),
      channel: {
        code: channel,
        company_code: 'RTL',
        name: 'EFN Retail (demo)',
        currency: 'IDR',
        active: true,
        fulfilment: { pickup: true, delivery: false },
        outlets: [{ outlet_code: 'TOKO-01', public_name: 'Toko Demo 01', pickup_enabled: true, prep_minutes: 30, active: true }],
      },
    };
  }

  async stock(channel: string, outlet: string, productIds: number[]): Promise<StockRow[]> {
    this.track('stock', channel);
    if (outlet !== 'TOKO-01') throw new OdooRpcError('business', 'not_found');
    return PRODUCTS.filter((p) => productIds.includes(p.id)).map((p) => ({ product_id: p.id, status: p.stock }));
  }

  async image(channel: string, tmplId: number, size: 512 | 1024): Promise<ImageResult> {
    this.track('image', channel);
    if (!PRODUCTS.some((p) => p.tmpl === tmplId)) throw new OdooRpcError('business', 'not_found');
    const png = await sharp({ create: { width: Math.min(size, 64), height: Math.min(size, 64), channels: 3, background: { r: 200, g: 120, b: (tmplId * 37) % 255 } } })
      .png()
      .toBuffer();
    return { sha: sha(`mock-image-${tmplId}`).slice(0, 40), mimetype: 'image/png', data: png };
  }

  private compute(channel: string, outlet: string, lines: QuoteLineIn[]): QuoteResult {
    if (outlet !== 'TOKO-01') throw new OdooRpcError('business', 'not_found');
    if (lines.length === 0 || lines.length > 50) throw new OdooRpcError('business', 'invalid_input');
    const out = lines.map((l) => {
      const p = PRODUCTS.find((x) => x.id === l.product_id);
      if (!p) throw new OdooRpcError('business', 'not_found');
      const unit = this.price(p.id);
      return { product_id: p.id, name: p.name, qty: l.qty, price_unit_incl: unit, subtotal_incl: unit * l.qty, tax_labels: [TAX_LABEL], available: p.stock !== 'out' };
    });
    const total = out.reduce((s, l) => s + l.subtotal_incl, 0);
    const tax = round2((total * TAX_NUM) / (TAX_DEN + TAX_NUM));
    const hash = sha(`${channel}|${outlet}|${JSON.stringify(lines)}|v${this.priceVersion}`);
    return {
      company_code: 'RTL',
      lines: out,
      tax_groups: [{ label: TAX_LABEL, base: round2(total - tax), amount: tax }],
      amount_total: total,
      amount_payable: Math.round(total),
      quote_hash: hash,
      valid_until: new Date(this.now() + 15 * 60_000),
      issues: out.filter((l) => !l.available).map((l) => ({ product_id: l.product_id, code: 'out_of_stock' })),
    };
  }

  async quote(channel: string, outlet: string, _fulfilment: Fulfilment, lines: QuoteLineIn[]): Promise<QuoteResult> {
    this.track('quote', channel);
    return this.compute(channel, outlet, lines);
  }

  async place(channel: string, order: PlaceInput): Promise<PlaceResult> {
    this.track('place', channel);
    const payloadSha = sha(JSON.stringify({ ...order, pickup_code: sha(order.pickup_code) }));
    const existing = [...this.orders.values()].find((o) => o.key === order.idempotency_key);
    if (existing) {
      if (existing.payloadSha !== payloadSha) throw new OdooRpcError('business', 'idempotency_conflict');
      return this.placeResult(existing);
    }
    const q = this.compute(channel, order.outlet_code, order.lines);
    if (q.quote_hash !== order.quote_hash) throw new OdooRpcError('business', 'price_changed');
    if (q.issues.length > 0) throw new OdooRpcError('business', 'out_of_stock');
    const o: MockOrder = {
      ref: order.platform_order_ref,
      channel,
      key: order.idempotency_key,
      payloadSha,
      state: 'placed',
      seq: 1,
      lines: order.lines,
      amountTotal: q.amount_total,
      amountPayable: q.amount_payable,
      saleOrderName: `S${String(this.orders.size + 1).padStart(5, '0')}`,
      expiresAt: new Date(this.now() + 25 * 60_000),
      pickupSha: sha(order.pickup_code),
      paymentRef: null,
      refunded: 0,
      readyAt: null,
      pickedUpAt: null,
      cancelRefund: null,
    };
    this.orders.set(o.ref, o);
    return this.placeResult(o);
  }

  private placeResult(o: MockOrder): PlaceResult {
    return { order_state: o.state, sale_order_name: o.saleOrderName, amount_total: o.amountTotal, amount_payable: o.amountPayable, expires_at: o.expiresAt };
  }

  private get(ref: string): MockOrder {
    const o = this.orders.get(ref);
    if (!o) throw new OdooRpcError('business', 'not_found');
    return o;
  }

  async markPaid(channel: string, ref: string, payment: MarkPaidInput): Promise<MarkPaidResult> {
    this.track('markPaid', channel);
    const o = this.get(ref);
    if (o.paymentRef !== null && o.paymentRef !== payment.payment_ref) {
      o.state = 'review';
      return { order_state: 'review', short_lines: null, refund_required: null };
    }
    if (o.state === 'cancelled') return { order_state: 'cancelled', short_lines: null, refund_required: 'full' };
    if (o.state !== 'placed') return { order_state: o.state, short_lines: null, refund_required: null };
    o.paymentRef = payment.payment_ref;
    if (payment.amount_paid !== o.amountPayable) {
      o.state = 'review';
    } else if (o.lines.some((l) => l.product_id === STOCK_SHORT_PRODUCT)) {
      o.state = 'stock_short';
    } else {
      o.state = 'confirmed';
    }
    o.seq += 1;
    const short = o.state === 'stock_short' ? o.lines.filter((l) => l.product_id === STOCK_SHORT_PRODUCT) : null;
    return { order_state: o.state, short_lines: short, refund_required: null };
  }

  async cancel(channel: string, ref: string, _reason: CancelReason, idempotencyKey: string): Promise<CancelResult> {
    this.track('cancel', channel);
    const o = this.orders.get(ref);
    if (!o) return { order_state: 'absent', refund: null }; // delta §10.4.4 no. 3
    this.cancelKeys.add(idempotencyKey);
    if (o.state === 'picked_up') throw new OdooRpcError('business', 'invalid_state');
    if (o.state !== 'cancelled') {
      const invoiced = o.state === 'confirmed' || o.state === 'ready';
      o.state = 'cancelled';
      o.seq += 1;
      if (invoiced) o.cancelRefund = { refund_ref: `RINV/${randomUUID().slice(0, 8)}`, amount: o.amountPayable - o.refunded };
    }
    return { order_state: 'cancelled', refund: o.cancelRefund };
  }

  async refund(channel: string, ref: string, refund: RefundInput): Promise<RefundResult> {
    this.track('refund', channel);
    const prev = this.refundKeys.get(refund.idempotency_key);
    if (prev) return prev;
    const o = this.get(ref);
    if (!['confirmed', 'ready', 'picked_up'].includes(o.state)) throw new OdooRpcError('business', 'invalid_state');
    const amount = refund.full
      ? o.amountPayable - o.refunded
      : (refund.lines ?? []).reduce((s, l) => {
          const orig = o.lines.find((x) => x.product_id === l.product_id);
          if (!orig || l.qty > orig.qty) throw new OdooRpcError('business', 'invalid_input');
          return s + this.price(l.product_id) * l.qty;
        }, 0);
    if (amount <= 0 || o.refunded + amount > o.amountPayable) throw new OdooRpcError('business', 'amount_mismatch');
    o.refunded += amount;
    const r: RefundResult = { refund_ref: `RINV/${randomUUID().slice(0, 8)}`, amount, credit_note: `RINV/2026/${this.refundKeys.size + 1}` };
    this.refundKeys.set(refund.idempotency_key, r);
    return r;
  }

  async refundSettled(channel: string, _refundRef: string, settlement: RefundSettledInput): Promise<{ state: string }> {
    this.track('refundSettled', channel);
    return { state: settlement.ok ? 'settled' : 'failed' };
  }

  async status(channel: string, refs: string[]): Promise<StatusRow[]> {
    this.track('status', channel);
    return refs
      .map((r) => this.orders.get(r))
      .filter((o): o is MockOrder => o !== undefined)
      .map((o) => ({ platform_order_ref: o.ref, state: o.state, seq: o.seq, ready_at: o.readyAt, picked_up_at: o.pickedUpAt }));
  }

  /** Test/dev: transisi yang di Odoo dilakukan staf outlet. */
  advance(ref: string, state: 'ready' | 'picked_up'): MockOrder {
    const o = this.get(ref);
    o.state = state;
    o.seq += 1;
    if (state === 'ready') o.readyAt = new Date(this.now()).toISOString();
    else o.pickedUpAt = new Date(this.now()).toISOString();
    return o;
  }
}
