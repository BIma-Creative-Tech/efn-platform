import { createHash, randomUUID } from 'node:crypto';
import sharp from 'sharp';
import {
  CancelReason,
  CancelResult,
  CatalogItem,
  CatalogPage,
  CounterRefundAuthorizeInput,
  CounterRefundAuthorizeResult,
  Fulfilment,
  ImageResult,
  InvoiceBuyerProfile,
  MarkPaidInput,
  MarkPaidResult,
  ItemAttribute,
  OdooOnlineApi,
  OdooRpcError,
  PlaceInput,
  PlaceResult,
  QuoteLineIn,
  QuoteResult,
  RefundInput,
  RefundResult,
  RefundSettledInput,
  SetInvoicePartyResult,
  StatusRow,
  StockRow,
} from './odoo-online.types';

/**
 * STUB json2 untuk DEV/TEST SAJA (`ODOO_ONLINE_MOCK=true`, L-7). `AppConfig.odooOnlineMock` menolak start di
 * staging/production (fail-closed). Meniru kontrak E-28 (docs/04 §10.4.3 + delta §10.4.4) secara in-memory per proses:
 * idempotensi place/cancel/refund, `absent` untuk cancel ref tak dikenal, `price_changed` bila `quote_hash` berbeda,
 * mark_paid → confirmed/stock_short/review/refund_required.
 *
 * SEMUA HARGA, PAJAK & BERAT DI SINI ADALAH DATA DEMO PLACEHOLDER — bukan aturan bisnis. Produk demo (semua kanal):
 *   11 Beras 5 kg (in_stock, 5 kg), 12 Minyak goreng 2 L (low, 1,9 kg), 13 "Uji stok-kurang" (lolos quote,
 *   `stock_short` saat mark_paid, 0,5 kg), 14 "Uji habis" (`out` → issue `out_of_stock` saat quote, berat kosong).
 *   Outlet `TOKO-01`.
 * Kode ambil: mock hanya menyimpan SHA-256-nya (meniru Odoo yang menyimpan hash KDF), tidak pernah teks asli.
 *
 * G20 (docs/04 §12.1): kanal = kode PT — `GL` (brand GL), `PB` (brand PB), `BCR` & `BCF` (brand BACHA). Kode lama
 * `RTL`/`FNB`/`RTL-GL`/… DIHAPUS. Mode bayar demo (§12.2, `paymentModes`): GL qris+counter, PB qris, BCR qris+counter,
 * BCF counter saja (pilot tanpa vendor). Batas berat demo (§12.3, `maxOrderWeightKg`): BCF 10 kg (`block`), kanal lain
 * mati. Bayar di kasir (L-56): `place` mode `counter` WAJIB membawa `order.counter_code` buatan BFF (stub menyimpan
 * hash-nya saja, tidak mengembalikannya); `payAtCounter(ref, code?)` meniru sync POS (E-45) → `confirmed` (+ event
 * `paid` yang dibangun test/simulasi).
 *
 * G19 (docs/04 §11): `expires_at` place = sekarang + 70 menit (default field Odoo §11.1). Promo demo (PLACEHOLDER):
 *   `DEMO5K`  — potongan tetap Rp5.000 (baris reward) bila subtotal ≥ Rp50.000, semua kanal;
 *   `DEMOGL10` — potongan 10 % hanya kanal brand `GL` (di kanal lain → `promo_rejected`, "bukan untuk brand ini").
 * Kode di `expiredPromos` (test) diperlakukan seperti kode tak dikenal (`promo_rejected`) — meniru Odoo yang memetakan
 * semua galat `_try_apply_code` ke `promo_rejected` tanpa pesan.
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
  cancelRefund: CancelResult['refund'];
  promoCodes: string[];
  /** G20 §12.2: mode bayar SO. */
  paymentMode: 'qris' | 'counter';
  /** L-56: SHA-256 kode bayar dari BFF (meniru Odoo yang hanya menyimpan hash); teks asli tidak pernah disimpan. */
  counterSha: string | null;
  /** G22 §14.C.3: porsi Poin EFN (Rupiah) saat place; 0 = tanpa poin. */
  pointsValue: number;
  /** = amountPayable − pointsValue. */
  amountDue: number;
  /** Σ points_value refund sebelumnya (alokasi proporsional, meniru Odoo). */
  pointsRefunded: number;
}

/** Kanal demo G20: kode kanal = kode PT (§12.1), brand (§11.3). */
const MOCK_CHANNELS: Readonly<Record<string, { company: string; brand: string | null; name: string }>> = {
  GL: { company: 'GL', brand: 'GL', name: 'Grand Lucky (demo)' },
  PB: { company: 'PB', brand: 'PB', name: 'Paris Baguette (demo)' },
  BCR: { company: 'BCR', brand: 'BACHA', name: 'Bacha Coffee Butik (demo)' },
  BCF: { company: 'BCF', brand: 'BACHA', name: 'Bacha Coffee Kafe (demo)' },
};
/** Mode bayar default kanal demo (§12.2 no. 1) — PLACEHOLDER. */
const MOCK_PAYMENT_MODES: Readonly<Record<string, ('qris' | 'counter')[]>> = {
  GL: ['qris', 'counter'],
  PB: ['qris'],
  BCR: ['qris', 'counter'],
  BCF: ['counter'],
};
/** Produk reward Odoo (discount line) demo — id di luar katalog. */
const REWARD_PRODUCT_ID = 9001;
/** `payment_timeout_minutes` default field Odoo G19 (§11.1). */
export const MOCK_PAYMENT_TIMEOUT_MINUTES = 70;
/** SEC-131: batas qty per baris kanal demo (PLACEHOLDER; checklist kanal ritel 10–20). */
export const MOCK_MAX_QTY_PER_LINE = 20;

/** Berat demo (kg, PLACEHOLDER) — `null` = belum diisi di Odoo. */
const DEFAULT_WEIGHTS: readonly [number, number | null][] = [
  [11, 5],
  [12, 1.9],
  [13, 0.5],
  [14, null],
];

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
  readonly channels = new Set(Object.keys(MOCK_CHANNELS));
  /** Test: kode promo yang "kedaluwarsa/kuota habis" (ditolak seperti kode tak dikenal). */
  readonly expiredPromos = new Set<string>();
  /** Test: menit `expires_at` place (default 70; set < 65 untuk mensimulasikan kanal Odoo yang belum dinaikkan). */
  paymentTimeoutMinutes = MOCK_PAYMENT_TIMEOUT_MINUTES;
  /** SEC-131: `max_qty_per_line` kanal demo (default 20 = `max_qty` katalog stub); qty baris > nilai ini → issue `qty_limit`. */
  maxQtyPerLine = MOCK_MAX_QTY_PER_LINE;
  /** G20 §12.2: `payment_modes` per kanal (test boleh mengubah). */
  readonly paymentModes = new Map<string, ('qris' | 'counter')[]>(Object.entries(MOCK_PAYMENT_MODES).map(([k, v]) => [k, [...v]]));
  /** G20 §12.3: `max_order_weight_kg` per kanal (0/absen = mati). */
  readonly maxOrderWeightKg = new Map<string, number>([['BCF', 10]]);
  /** G20 §12.3: `weight_missing_policy` per kanal (default `block` bila batas aktif). */
  readonly weightMissingPolicy = new Map<string, 'block' | 'ignore'>();
  /** G20 §12.3: `product.weight` demo (test boleh mengubah). */
  readonly weightKg = new Map<number, number | null>(DEFAULT_WEIGHTS);
  /**
   * G22 B-2/B-4 (PLACEHOLDER demo): atribut `no_variant` per produk — default kosong (test/dev mengisi). PTAV di luar
   * atribut produk → issue `attribute_invalid` (quote) / `invalid_input` (place).
   */
  readonly attributes = new Map<number, ItemAttribute[]>();
  /** G22 B-2: flag poin kanal (default earn & redeem aktif di stub; kill switch BFF tetap menentukan). */
  readonly loyaltyFlags = new Map<string, { earn: boolean; redeem: boolean }>();
  /** G22 §14.C.3: kebijakan stub — nilai 1 poin & persen maksimum dari amount_payable (PLACEHOLDER). */
  pointValueIdr = 100;
  redeemMaxPct = 50;

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
            max_qty: MOCK_MAX_QTY_PER_LINE,
            weight_kg: this.weightKg.get(p.id) ?? null,
            attributes: this.attributes.get(p.id) ?? [],
          };
        });
    return {
      items,
      deleted: [],
      next_cursor: null,
      server_time: new Date(this.now()).toISOString(),
      channel: {
        code: channel,
        company_code: MOCK_CHANNELS[channel]?.company ?? 'GL',
        brand_code: MOCK_CHANNELS[channel]?.brand ?? null,
        name: MOCK_CHANNELS[channel]?.name ?? 'EFN (demo)',
        currency: 'IDR',
        active: true,
        fulfilment: { pickup: true, delivery: false },
        outlets: [{ outlet_code: 'TOKO-01', public_name: 'Toko Demo 01', pickup_enabled: true, prep_minutes: 30, active: true }],
        payment_modes: [...(this.paymentModes.get(channel) ?? ['qris'])],
        business_type: channel === 'PB' || channel === 'BCF' ? 'fnb' : 'retail',
        loyalty: { ...(this.loyaltyFlags.get(channel) ?? { earn: true, redeem: true }) },
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

  /** Promo demo (PLACEHOLDER): nilai potongan (Rupiah, termasuk pajak) atau `null` bila kode tidak berlaku di kanal ini. */
  private promoDiscount(channel: string, code: string, subtotal: number): number | null {
    if (this.expiredPromos.has(code)) return null;
    if (code === 'DEMO5K') return subtotal >= 50_000 ? 5_000 : null;
    if (code === 'DEMOGL10') return MOCK_CHANNELS[channel]?.brand === 'GL' ? Math.round(subtotal * 0.1) : null;
    return null;
  }

  private compute(channel: string, outlet: string, lines: QuoteLineIn[], promoCodes: readonly string[] = []): QuoteResult {
    if (outlet !== 'TOKO-01') throw new OdooRpcError('business', 'not_found');
    if (lines.length === 0 || lines.length > 50) throw new OdooRpcError('business', 'invalid_input');
    if (promoCodes.length > 3) throw new OdooRpcError('business', 'invalid_input');
    // G22 B-4: identitas baris (product_id, PTAV terurut) — ganda → invalid_input
    const identities = lines.map((l) => `${l.product_id}|${[...(l.attribute_value_ids ?? [])].sort((a, b) => a - b).join(',')}`);
    if (new Set(identities).size !== identities.length) throw new OdooRpcError('business', 'invalid_input');
    const attrIssues: QuoteResult['issues'] = [];
    const out: QuoteResult['lines'] = lines.map((l) => {
      const p = PRODUCTS.find((x) => x.id === l.product_id);
      if (!p) throw new OdooRpcError('business', 'not_found');
      const ptav = [...(l.attribute_value_ids ?? [])].sort((a, b) => a - b);
      const attrs = this.attributes.get(p.id) ?? [];
      const chosen = attrs.flatMap((a) => a.values.filter((v) => ptav.includes(v.id)).map((v) => ({ a: a.name, v })));
      if (chosen.length !== ptav.length) attrIssues.push({ product_id: p.id, code: 'attribute_invalid' });
      // Seperti Odoo: pajak dihitung atas (harga excl + Σ price_extra excl), lalu dibulatkan SEKALI ke Rupiah. Faktor
      // excl→incl = (TAX_DEN + TAX_NUM) / TAX_DEN — sama dengan konversi `price_excl` katalog stub (bukan 1,11).
      const base = this.price(p.id);
      const extraExcl = chosen.reduce((t, x) => t + x.v.price_extra_excl, 0);
      const unit = extraExcl === 0 ? base : Math.round((((base * TAX_DEN) / (TAX_DEN + TAX_NUM) + extraExcl) * (TAX_DEN + TAX_NUM)) / TAX_DEN);
      return {
        product_id: p.id,
        name: p.name,
        qty: l.qty,
        price_unit_incl: unit,
        subtotal_incl: unit * l.qty,
        tax_labels: [TAX_LABEL],
        available: p.stock !== 'out',
        reward: false,
        attribute_value_ids: ptav,
        attributes_text: chosen.length > 0 ? chosen.map((x) => `${x.a}: ${x.v.name}`).join(', ') : null,
      };
    });
    const subtotal = out.reduce((s, l) => s + l.subtotal_incl, 0);
    const applied: string[] = [];
    const rejected: string[] = [];
    let remaining = subtotal;
    for (const code of promoCodes) {
      const d = this.promoDiscount(channel, code, subtotal);
      if (d === null || d <= 0 || applied.includes(code)) {
        if (!applied.includes(code) && !rejected.includes(code)) rejected.push(code);
        continue;
      }
      const amount = Math.min(d, remaining); // reward tidak membuat total negatif
      remaining -= amount;
      applied.push(code);
      out.push({
        product_id: REWARD_PRODUCT_ID,
        name: `Promo ${code} (demo)`,
        qty: 1,
        price_unit_incl: -amount,
        subtotal_incl: -amount,
        tax_labels: [TAX_LABEL],
        available: true,
        reward: true,
        attribute_value_ids: [],
        attributes_text: null,
      });
    }
    const total = out.reduce((s, l) => s + l.subtotal_incl, 0);
    const tax = round2((total * TAX_NUM) / (TAX_DEN + TAX_NUM));
    // quote_hash memuat kode terpakai (urut) + id reward (§11.2 no. 3)
    const issues: QuoteResult['issues'] = [...attrIssues];
    for (const l of out) {
      if (l.reward) continue;
      if (!l.available) issues.push({ product_id: l.product_id, code: 'out_of_stock' });
      // SEC-131: qty baris > batas per baris kanal → issue `qty_limit` (baris tetap dengan qty yang diminta)
      if (l.qty > this.maxQtyPerLine) issues.push({ product_id: l.product_id, code: 'qty_limit' });
    }
    // G20 §12.3: Σ product.weight × qty (rumus delivery.carrier._match_weight) vs batas kanal; masuk quote_hash
    const limit = this.maxOrderWeightKg.get(channel) ?? 0;
    let weightTag = '';
    if (limit > 0) {
      const policy = this.weightMissingPolicy.get(channel) ?? 'block';
      let weight = 0;
      for (const l of out) {
        if (l.reward) continue;
        const w = this.weightKg.get(l.product_id) ?? null;
        if (w === null) {
          if (policy === 'block') issues.push({ product_id: l.product_id, code: 'weight_missing' });
          continue;
        }
        weight += w * l.qty;
      }
      if (weight > limit) issues.push({ product_id: null, code: 'weight_limit' });
      weightTag = `|w${issues.filter((i) => i.code.startsWith('weight_')).map((i) => `${i.product_id ?? 'o'}:${i.code}`).join(',')}`;
    }
    const hash = sha(`${channel}|${outlet}|${JSON.stringify(lines)}|v${this.priceVersion}|${[...applied].sort().join(',')}|r${REWARD_PRODUCT_ID}${weightTag}`);
    const amountPayable = Math.round(total);
    // Konsisten dengan parser (L-35 / QA G19): tagihan < Rp1 tanpa issue tidak pernah keluar dari stub
    if (amountPayable < 1 && issues.length === 0) throw new OdooRpcError('invalid_response', null, 'respons Odoo tidak valid: amount_payable < 1');
    return {
      company_code: MOCK_CHANNELS[channel]?.company ?? 'GL',
      lines: out,
      tax_groups: [{ label: TAX_LABEL, base: round2(total - tax), amount: tax }],
      amount_total: total,
      amount_payable: amountPayable,
      quote_hash: hash,
      valid_until: new Date(this.now() + 15 * 60_000),
      issues,
      promo_applied: applied,
      promo_rejected: rejected,
    };
  }

  async quote(channel: string, outlet: string, _fulfilment: Fulfilment, lines: QuoteLineIn[], promoCodes: string[] = []): Promise<QuoteResult> {
    this.track('quote', channel);
    this.quoteArgs.push({ channel, promoCodes: [...promoCodes] });
    return this.compute(channel, outlet, lines, promoCodes);
  }
  /** Test: argumen promo per panggilan quote (memastikan kode yang sama dikirim ke setiap kanal). */
  readonly quoteArgs: { channel: string; promoCodes: string[] }[] = [];

  async place(channel: string, order: PlaceInput): Promise<PlaceResult> {
    this.track('place', channel);
    // Rahasia (kode ambil & kode bayar) hanya masuk sidik sebagai hash, seperti Odoo
    const payloadSha = sha(
      JSON.stringify({ ...order, pickup_code: sha(order.pickup_code), ...(order.counter_code !== undefined ? { counter_code: sha(order.counter_code) } : {}) }),
    );
    const existing = [...this.orders.values()].find((o) => o.key === order.idempotency_key);
    if (existing) {
      if (existing.payloadSha !== payloadSha) throw new OdooRpcError('business', 'idempotency_conflict');
      return this.placeResult(existing);
    }
    const q = this.compute(channel, order.outlet_code, order.lines, order.promo_codes ?? []);
    // kode yang dikirim place harus semuanya masih berlaku; bila tidak, hash beda → price_changed (§11.2 no. 5)
    if (q.quote_hash !== order.quote_hash || q.promo_rejected.length > 0) throw new OdooRpcError('business', 'price_changed');
    // ASUMSI: Odoo menolak place dengan qty > batas baris sebagai `invalid_input` (BFF memblokir lebih dulu: cart_has_issues)
    if (q.issues.some((i) => i.code === 'qty_limit' || i.code.startsWith('weight_'))) throw new OdooRpcError('business', 'invalid_input');
    if (q.issues.length > 0) throw new OdooRpcError('business', 'out_of_stock');
    if (q.amount_payable < 1) throw new OdooRpcError('invalid_response', null, 'respons Odoo tidak valid: amount_payable < 1'); // = parsePlace
    // G20 §12.2 no. 1/3: mode bayar harus diizinkan kanal (ASUMSI: Odoo menolak `invalid_input`)
    const mode = order.payment_mode === 'counter' ? 'counter' : 'qris';
    if (!(this.paymentModes.get(channel) ?? ['qris']).includes(mode)) throw new OdooRpcError('business', 'invalid_input');
    // L-56: kode bayar dari BFF wajib untuk counter (alfabet §10.2), dilarang untuk qris; ASUMSI Odoo → invalid_input
    const counterOk = order.counter_code !== undefined && /^[ABCDEFGHJKMNPQRSTUVWXYZ23456789]{8}$/.test(order.counter_code);
    if (mode === 'counter' ? !counterOk : order.counter_code !== undefined) throw new OdooRpcError('business', 'invalid_input');
    // G22 §14.C.3 (meniru E-28): kanal loyalty_redeem, value = points × nilai tukar, ≤ pct × amount_payable, tunai ≥ Rp1
    let pointsValue = 0;
    if (order.points_payment) {
      const pp = order.points_payment;
      const flags = this.loyaltyFlags.get(channel) ?? { earn: true, redeem: true };
      const okValue = pp.points > 0 && pp.value_idr === pp.points * this.pointValueIdr;
      const okPct = pp.value_idr <= Math.floor((q.amount_payable * this.redeemMaxPct) / 100) && q.amount_payable - pp.value_idr >= 1;
      if (!flags.redeem || !okValue || !okPct) throw new OdooRpcError('business', 'points_invalid');
      pointsValue = pp.value_idr;
    }
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
      expiresAt: new Date(this.now() + this.paymentTimeoutMinutes * 60_000),
      pickupSha: sha(order.pickup_code),
      paymentRef: null,
      refunded: 0,
      readyAt: null,
      pickedUpAt: null,
      cancelRefund: null,
      promoCodes: [...(order.promo_codes ?? [])],
      paymentMode: mode,
      counterSha: mode === 'counter' && order.counter_code ? sha(order.counter_code) : null,
      pointsValue,
      amountDue: q.amount_payable - pointsValue,
      pointsRefunded: 0,
    };
    this.orders.set(o.ref, o);
    return this.placeResult(o);
  }

  private placeResult(o: MockOrder): PlaceResult {
    // L-56: kode bayar TIDAK dikembalikan (Odoo hanya menyimpan hash)
    return {
      order_state: o.state,
      sale_order_name: o.saleOrderName,
      amount_total: o.amountTotal,
      amount_payable: o.amountPayable,
      expires_at: o.expiresAt,
      amount_due: o.amountDue,
    };
  }

  /** G22 §14.C.4 (meniru E-28): alokasi poin refund = round(min(ΣR, T) × V ÷ T) − Σ sebelumnya. */
  private refundPoints(o: MockOrder, amount: number): { points_value: number; cash_amount: number } {
    if (o.pointsValue <= 0) return { points_value: 0, cash_amount: amount };
    const cumulative = Math.min(o.amountPayable, o.refunded + amount);
    const pv = Math.min(o.pointsValue - o.pointsRefunded, Math.max(0, Math.round((cumulative * o.pointsValue) / o.amountPayable) - o.pointsRefunded));
    o.pointsRefunded += pv;
    return { points_value: pv, cash_amount: amount - pv };
  }

  /**
   * Dev/test (§12.2 no. 5–6): kasir mengetik kode bayar → pelunasan POS → validasi server (bagian `placed`, belum
   * kedaluwarsa, nominal = amount_payable) → jalur internal `mark_paid` (`method: counter`) → `confirmed`
   * (produk 13 → `stock_short`, sama dengan QRIS). Mengembalikan seq event `paid` yang akan dikirim Odoo.
   */
  payAtCounter(ref: string, code?: string): MockOrder {
    const o = this.get(ref);
    if (o.paymentMode !== 'counter' || o.state !== 'placed') throw new OdooRpcError('business', 'invalid_state');
    if (o.expiresAt.getTime() <= this.now()) throw new OdooRpcError('business', 'invalid_state');
    if (code !== undefined && sha(code.replace(/-/g, '').toUpperCase()) !== o.counterSha) throw new OdooRpcError('business', 'not_found');
    o.paymentRef = `pos:${randomUUID()}`;
    o.state = o.lines.some((l) => l.product_id === STOCK_SHORT_PRODUCT) ? 'stock_short' : 'confirmed';
    o.seq += 1;
    return o;
  }

  private get(ref: string): MockOrder {
    const o = this.orders.get(ref);
    if (!o) throw new OdooRpcError('business', 'not_found');
    return o;
  }

  async markPaid(channel: string, ref: string, payment: MarkPaidInput): Promise<MarkPaidResult> {
    this.track('markPaid', channel);
    const o = this.get(ref);
    // G20 §12.2 no. 2: order bayar di kasir TIDAK PERNAH dilunasi lewat jalur gateway BFF
    if (o.paymentMode === 'counter') throw new OdooRpcError('business', 'invalid_state');
    if (o.paymentRef !== null && o.paymentRef !== payment.payment_ref) {
      o.state = 'review';
      return { order_state: 'review', short_lines: null, refund_required: null };
    }
    if (o.state === 'cancelled') return { order_state: 'cancelled', short_lines: null, refund_required: 'full' };
    if (o.state !== 'placed') return { order_state: o.state, short_lines: null, refund_required: null };
    o.paymentRef = payment.payment_ref;
    // G22: tunai dibanding amount_due; points_value harus = nilai saat place
    if (payment.amount_paid !== o.amountDue || (payment.points_value ?? 0) !== o.pointsValue) {
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
      if (invoiced) {
        const amount = o.amountPayable - o.refunded;
        o.cancelRefund = { refund_ref: `RINV/${randomUUID().slice(0, 8)}`, amount, ...(o.pointsValue > 0 ? this.refundPoints(o, amount) : {}) };
      }
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
    const points = o.pointsValue > 0 ? this.refundPoints(o, amount) : null;
    o.refunded += amount;
    const r: RefundResult = {
      refund_ref: `RINV/${randomUUID().slice(0, 8)}`,
      amount,
      credit_note: `RINV/2026/${this.refundKeys.size + 1}`,
      ...(points ? { ...points, eligible_amount_refunded: Math.floor((amount * TAX_DEN) / (TAX_DEN + TAX_NUM)) } : {}),
    };
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

  /** L-60: otorisasi refund POS per idempotency_key (meniru Odoo: idempoten, payload beda → idempotency_conflict). */
  readonly counterRefundAuths = new Map<string, { ref: string; refundId: string; refundRef: string | null; amount: number }>();

  async counterRefundAuthorize(channel: string, ref: string, a: CounterRefundAuthorizeInput): Promise<CounterRefundAuthorizeResult> {
    this.track('counterRefundAuthorize', channel);
    const prev = this.counterRefundAuths.get(a.idempotency_key);
    if (prev) {
      if (prev.ref !== ref || prev.refundId !== a.refund_id || prev.refundRef !== a.refund_ref || prev.amount !== a.amount) {
        throw new OdooRpcError('business', 'idempotency_conflict');
      }
      return { authorized: true, state: 'authorized', amount: prev.amount };
    }
    const o = this.get(ref);
    if (o.paymentMode !== 'counter' || o.paymentRef === null) throw new OdooRpcError('business', 'invalid_state');
    if (!Number.isSafeInteger(a.amount) || a.amount < 1 || a.amount > o.amountPayable) throw new OdooRpcError('business', 'amount_mismatch');
    this.counterRefundAuths.set(a.idempotency_key, { ref, refundId: a.refund_id, refundRef: a.refund_ref, amount: a.amount });
    return { authorized: true, state: 'authorized', amount: a.amount };
  }

  /**
   * G21 §13.5: `efn_pii` aktif di Odoo stub (false → `invoice_not_available`, meniru L-63). Panggilan dicatat TANPA PII:
   * hanya jenis identitas + SHA-256 profil (test memverifikasi profil yang sama sampai ke Odoo).
   */
  invoicePiiActive = true;
  readonly invoiceCalls: { ref: string; channel: string; key: string; buyerType: string; profileSha: string }[] = [];

  async setInvoiceParty(channel: string, ref: string, profile: InvoiceBuyerProfile, idempotencyKey: string): Promise<SetInvoicePartyResult> {
    this.track('setInvoiceParty', channel);
    const profileSha = sha(JSON.stringify(profile));
    const prev = this.invoiceCalls.find((c) => c.key === idempotencyKey);
    if (prev) {
      if (prev.ref !== ref || prev.profileSha !== profileSha) throw new OdooRpcError('business', 'idempotency_conflict');
      return { state: 'applied' };
    }
    const o = this.get(ref);
    // Hanya PT PPN (GL, BCR) dan hanya bila efn_pii aktif (L-63)
    if (!this.invoicePiiActive || !['GL', 'BCR'].includes(MOCK_CHANNELS[o.channel]?.company ?? '')) {
      throw new OdooRpcError('business', 'invoice_not_available');
    }
    if (o.state !== 'placed') throw new OdooRpcError('business', 'invalid_state');
    this.invoiceCalls.push({ ref, channel, key: idempotencyKey, buyerType: profile.buyer_type, profileSha });
    return { state: 'applied' };
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
