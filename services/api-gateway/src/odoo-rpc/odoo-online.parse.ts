import {
  CancelResult,
  CatalogChannelInfo,
  CatalogItem,
  CatalogPage,
  CatalogPrice,
  ImageResult,
  MarkPaidResult,
  OdooRpcError,
  PlaceResult,
  QuoteIssue,
  QuoteLine,
  QuoteResult,
  RefundResult,
  StatusRow,
  StockRow,
  TaxGroup,
} from './odoo-online.types';
import { PROMO_CODE_RE } from './promo-codes';

/**
 * Validasi ketat hasil json2 (docs/04 §10.4.3). Odoo dipercaya untuk harga/pajak, tetapi BENTUK respons tetap diperiksa:
 * respons tak sesuai kontrak → `OdooRpcError('invalid_response')` (503 ke pelanggan + alert `online_config_error`), bukan
 * nilai setengah jadi yang tersimpan di DB. Nominal `amount_payable` wajib bilangan bulat Rupiah (§10.2).
 */
function bad(what: string): never {
  throw new OdooRpcError('invalid_response', null, `respons Odoo tidak valid: ${what}`);
}

function obj(v: unknown, what: string): Record<string, unknown> {
  if (typeof v !== 'object' || v === null || Array.isArray(v)) bad(what);
  return v as Record<string, unknown>;
}
function arr(v: unknown, what: string, max = 10_000): unknown[] {
  if (!Array.isArray(v) || v.length > max) bad(what);
  return v as unknown[];
}
function str(v: unknown, what: string, max = 512): string {
  if (typeof v !== 'string' || v.length > max) bad(what);
  return v as string;
}
function optStr(v: unknown, what: string, max = 512): string | null {
  if (v === null || v === undefined || v === false) return null; // Odoo mengirim `false` untuk Char kosong
  return str(v, what, max);
}
function int(v: unknown, what: string, min = 0): number {
  if (typeof v !== 'number' || !Number.isSafeInteger(v) || v < min) bad(what);
  return v as number;
}
function num(v: unknown, what: string): number {
  if (typeof v !== 'number' || !Number.isFinite(v) || v < 0 || v > 1e12) bad(what);
  return v as number;
}
/** Nominal bertanda (baris reward boleh negatif, §11.2 no. 3). */
function signedNum(v: unknown, what: string): number {
  if (typeof v !== 'number' || !Number.isFinite(v) || Math.abs(v) > 1e12) bad(what);
  return v as number;
}
/** Daftar kode promo hasil Odoo: string berbentuk kode, dinormalisasi huruf besar, unik. Kosong/`false` → []. */
function promoList(v: unknown, what: string): string[] {
  if (v === undefined || v === null || v === false) return [];
  const out: string[] = [];
  for (const x of arr(v, what, 20)) {
    const s = str(x, what, 40);
    if (!PROMO_CODE_RE.test(s)) bad(what);
    const u = s.toUpperCase();
    if (!out.includes(u)) out.push(u);
  }
  return out;
}
/** `amount_payable` / porsi / refund: bilangan bulat Rupiah. */
export function rupiah(v: unknown, what: string): number {
  if (typeof v !== 'number' || !Number.isSafeInteger(v) || v < 0) bad(`${what} bukan bilangan bulat Rupiah`);
  return v as number;
}
function bool(v: unknown, what: string): boolean {
  if (typeof v !== 'boolean') bad(what);
  return v as boolean;
}
function labels(v: unknown, what: string): string[] {
  if (v === undefined || v === null || v === false) return [];
  return arr(v, what, 20).map((x) => str(x, what, 64));
}
function date(v: unknown, what: string): Date | null {
  if (v === null || v === undefined || v === false) return null;
  const s = str(v, what, 64);
  // Odoo Datetime tanpa zona = UTC ("2026-10-09 03:00:00")
  const iso = /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(s) ? `${s.replace(' ', 'T')}Z` : s;
  const ms = Date.parse(iso);
  if (Number.isNaN(ms)) bad(what);
  return new Date(ms);
}

/** L-9 (§10.17): hex huruf kecil 40 (SHA-1 lampiran Odoo) atau 64 (SHA-256). */
export const IMAGE_SHA_RE = /^(?:[0-9a-f]{40}|[0-9a-f]{64})$/;
/** G19 §11.3 no. 2 (E-37a): `efn.online.channel.brand_code`. */
export const BRAND_CODE_RE = /^[A-Z][A-Z0-9]{1,9}$/;

function parsePrice(v: unknown): CatalogPrice {
  const o = obj(v, 'price');
  return {
    outlet_code: optStr(o.outlet_code, 'price.outlet_code', 24),
    price_excl: num(o.price_excl, 'price_excl'),
    price_incl: num(o.price_incl, 'price_incl'),
    tax_labels: labels(o.tax_labels, 'tax_labels'),
  };
}

function parseItem(v: unknown): CatalogItem {
  const o = obj(v, 'item');
  const imageSha = optStr(o.image_sha, 'image_sha', 128);
  let categ: CatalogItem['categ'] = null;
  if (o.categ !== null && o.categ !== undefined && o.categ !== false) {
    const c = obj(o.categ, 'categ');
    categ = {
      id: int(c.id, 'categ.id', 1),
      name: str(c.name, 'categ.name', 128),
      parent_id: c.parent_id === null || c.parent_id === undefined || c.parent_id === false ? null : int(c.parent_id, 'categ.parent_id', 1),
      sequence: c.sequence === undefined ? 0 : int(c.sequence, 'categ.sequence'),
    };
  }
  return {
    product_id: int(o.product_id, 'product_id', 1),
    tmpl_id: int(o.tmpl_id, 'tmpl_id', 1),
    name: str(o.name, 'name', 256),
    description: (optStr(o.description, 'description', 4000) ?? '').slice(0, 2000),
    categ,
    uom: optStr(o.uom, 'uom', 32) ?? '',
    image_sha: imageSha !== null && IMAGE_SHA_RE.test(imageSha) ? imageSha : null,
    sequence: o.sequence === undefined ? 0 : int(o.sequence, 'sequence'),
    prices: arr(o.prices, 'prices', 200).map(parsePrice),
    max_qty: o.max_qty === undefined ? 99 : Math.min(999, int(o.max_qty, 'max_qty', 1)),
  };
}

function parseChannel(v: unknown): CatalogChannelInfo | null {
  if (v === null || v === undefined || v === false) return null;
  const o = obj(v, 'channel');
  const outlets = arr(o.outlets ?? [], 'channel.outlets', 200).map((x) => {
    const ot = obj(x, 'outlet');
    // L-11: kunci `code`; `outlet_code` tetap diterima (toleran)
    const code = str(ot.code ?? ot.outlet_code, 'outlet.code', 24);
    if (!/^[A-Z0-9][A-Z0-9-]{1,23}$/.test(code)) bad('outlet.code');
    return {
      outlet_code: code,
      public_name: str(ot.public_name, 'public_name', 128),
      pickup_enabled: ot.pickup_enabled === undefined ? true : bool(ot.pickup_enabled, 'pickup_enabled'),
      prep_minutes: ot.prep_minutes === undefined || ot.prep_minutes === null || ot.prep_minutes === false ? null : int(ot.prep_minutes, 'prep_minutes'),
      active: ot.active === undefined ? true : bool(ot.active, 'outlet.active'),
    };
  });
  const f = o.fulfilment === undefined || o.fulfilment === null ? null : obj(o.fulfilment, 'channel.fulfilment');
  const code = str(o.code, 'channel.code', 16);
  if (!/^[A-Z][A-Z0-9-]{1,15}$/.test(code)) bad('channel.code');
  // §11.3 no. 2: brand_code opsional (Char kosong Odoo = false); bila ada wajib cocok regex (constraint Odoo)
  const brand = optStr(o.brand_code, 'channel.brand_code', 10);
  if (brand !== null && brand !== '' && !BRAND_CODE_RE.test(brand)) bad('channel.brand_code');
  return {
    code,
    company_code: str(o.company_code, 'channel.company_code', 8),
    brand_code: brand === null || brand === '' ? null : brand,
    name: str(o.name, 'channel.name', 128),
    currency: str(o.currency, 'channel.currency', 8),
    active: o.active === undefined ? true : bool(o.active, 'channel.active'),
    // pilot: pickup bila ada outlet aktif yang menerima ambil; delivery hanya bila Odoo menyatakannya eksplisit
    fulfilment: f ? { pickup: f.pickup === true, delivery: f.delivery === true } : { pickup: outlets.some((x) => x.active && x.pickup_enabled), delivery: false },
    outlets,
  };
}

export function parseCatalogPage(v: unknown): CatalogPage {
  const o = obj(v, 'catalog');
  return {
    items: arr(o.items, 'items', 1000).map(parseItem),
    deleted: arr(o.deleted ?? [], 'deleted', 10_000).map((x) => int(x, 'deleted', 1)),
    next_cursor: optStr(o.next_cursor, 'next_cursor', 512),
    server_time: str(o.server_time, 'server_time', 64),
    channel: parseChannel(o.channel),
  };
}

export function parseStock(v: unknown): StockRow[] {
  return arr(v, 'stock', 200).map((x) => {
    const o = obj(x, 'stock row');
    const status = o.status;
    if (status !== 'in_stock' && status !== 'low' && status !== 'out') bad('stock.status');
    return { product_id: int(o.product_id, 'stock.product_id', 1), status }; // qty sengaja dibuang (§10.3.2)
  });
}

export function parseImage(v: unknown): ImageResult {
  const o = obj(v, 'image');
  const b64 = str(o.data_b64, 'data_b64', 12 * 1024 * 1024);
  if (!/^[A-Za-z0-9+/]*={0,2}$/.test(b64)) bad('data_b64');
  return { sha: str(o.sha, 'image.sha', 128), mimetype: str(o.mimetype, 'mimetype', 64), data: Buffer.from(b64, 'base64') };
}

function parseTaxGroups(v: unknown): TaxGroup[] {
  if (v === undefined || v === null || v === false) return [];
  return arr(v, 'tax_groups', 50).map((x) => {
    const o = obj(x, 'tax_group');
    return { label: str(o.label, 'tax_group.label', 64), base: num(o.base, 'tax_group.base'), amount: num(o.amount, 'tax_group.amount') };
  });
}

/**
 * Kode isu quote tingkat produk yang dikenal BFF (diteruskan apa adanya ke klien di `groups[].issues`):
 * - `out_of_stock` — stok tidak cukup;
 * - `qty_limit` — G19 SEC-131: qty baris melebihi batas per baris kanal (`max_qty_per_line`).
 * Kode lain (`^[a-z_]{1,40}$`, mis. isu tingkat order `order_limit` dengan `product_id: null`) tetap diterima dan
 * diteruskan; semua isu memblokir checkout (`409 cart_has_issues`, `orders.service`).
 */
export const PRODUCT_ISSUE_CODES = ['out_of_stock', 'qty_limit'] as const;

/**
 * Hasil `efn_online_quote` (docs/08 §8.3 C no. 4). Bila Odoo tidak mengirim `price_unit_incl`, dihitung dari
 * `subtotal_incl / qty` (2 desimal, hanya tampilan); bila tidak mengirim `issues`, baris `available=false` → `out_of_stock`.
 */
export function parseQuote(v: unknown): QuoteResult {
  const o = obj(v, 'quote');
  const lines: QuoteLine[] = arr(o.lines, 'quote.lines', 200).map((x) => {
    const l = obj(x, 'quote line');
    const qty = int(l.qty, 'quote.qty', 1);
    // §11.2 no. 3: baris reward (`reward: true`) boleh bernilai negatif, tetapi tidak pernah menambah tagihan (≤ 0)
    const reward = l.reward === undefined || l.reward === null || l.reward === false ? false : bool(l.reward, 'reward');
    const money = reward ? signedNum : num;
    const subtotal = money(l.subtotal_incl, 'subtotal_incl');
    if (reward && subtotal > 0) bad('reward.subtotal_incl > 0');
    return {
      product_id: int(l.product_id, 'quote.product_id', 1),
      name: optStr(l.name, 'quote.name', 256),
      qty,
      price_unit_incl: l.price_unit_incl === undefined ? Math.round((subtotal / qty) * 100) / 100 : money(l.price_unit_incl, 'price_unit_incl'),
      subtotal_incl: subtotal,
      tax_labels: labels(l.tax_labels, 'quote.tax_labels'),
      available: l.available === undefined ? true : bool(l.available, 'available'),
      reward,
    };
  });
  let issues: QuoteIssue[];
  if (Array.isArray(o.issues)) {
    issues = arr(o.issues, 'issues', 200).map((x) => {
      const i = obj(x, 'issue');
      const code = str(i.code, 'issue.code', 40);
      if (!/^[a-z_]{1,40}$/.test(code)) bad('issue.code');
      const productId = i.product_id === null || i.product_id === undefined || i.product_id === false ? null : int(i.product_id, 'issue.product_id', 1);
      // Isu tingkat PRODUK (out_of_stock, qty_limit SEC-131) wajib menyebut product_id; selain itu klien tidak tahu baris mana
      if (productId === null && (PRODUCT_ISSUE_CODES as readonly string[]).includes(code)) bad(`issue.${code} tanpa product_id`);
      return { product_id: productId, code };
    });
  } else {
    issues = lines.filter((l) => !l.reward && !l.available).map((l) => ({ product_id: l.product_id, code: 'out_of_stock' }));
  }
  const promoApplied = promoList(o.promo_applied, 'promo_applied');
  const promoRejected = promoList(o.promo_rejected, 'promo_rejected');
  if (promoApplied.some((c) => promoRejected.includes(c))) bad('promo_applied ∩ promo_rejected');
  const amountPayable = rupiah(o.amount_payable, 'amount_payable');
  // L-35 / QA G19: tagihan < Rp1 tanpa issue = konfigurasi promo otomatis salah (QRIS tidak bisa Rp0) -> 503 + alert.
  if (amountPayable < 1 && issues.length === 0) bad('amount_payable < 1');
  return {
    company_code: optStr(o.company_code, 'company_code', 8),
    lines,
    tax_groups: parseTaxGroups(o.tax_groups),
    amount_total: num(o.amount_total, 'amount_total'),
    amount_payable: amountPayable,
    quote_hash: str(o.quote_hash, 'quote_hash', 128),
    valid_until: date(o.valid_until, 'valid_until'),
    issues,
    promo_applied: promoApplied,
    promo_rejected: promoRejected,
  };
}

export function parsePlace(v: unknown): PlaceResult {
  const o = obj(v, 'place');
  const amountPayable = rupiah(o.amount_payable, 'amount_payable');
  if (amountPayable < 1) bad('amount_payable < 1'); // L-35: SO Rp0 tidak pernah dibuat payment intent
  return {
    order_state: str(o.order_state, 'order_state', 32),
    sale_order_name: optStr(o.sale_order_name, 'sale_order_name', 64),
    amount_total: num(o.amount_total, 'amount_total'),
    amount_payable: amountPayable,
    expires_at: date(o.expires_at, 'expires_at'),
  };
}

export function parseMarkPaid(v: unknown): MarkPaidResult {
  const o = obj(v, 'mark_paid');
  return {
    order_state: str(o.order_state, 'order_state', 32),
    short_lines: o.short_lines === undefined || o.short_lines === null || o.short_lines === false ? null : arr(o.short_lines, 'short_lines', 200),
    refund_required: optStr(o.refund_required, 'refund_required', 16),
  };
}

export function parseCancel(v: unknown): CancelResult {
  const o = obj(v, 'cancel');
  let refund: CancelResult['refund'] = null;
  if (o.refund !== undefined && o.refund !== null && o.refund !== false) {
    const r = obj(o.refund, 'cancel.refund');
    refund = { refund_ref: str(r.refund_ref, 'refund_ref', 128), amount: rupiah(r.amount, 'refund.amount') };
  }
  return { order_state: str(o.order_state, 'order_state', 32), refund };
}

export function parseRefund(v: unknown): RefundResult {
  const o = obj(v, 'refund');
  return {
    refund_ref: str(o.refund_ref, 'refund_ref', 128),
    amount: rupiah(o.amount, 'refund.amount'),
    credit_note: optStr(o.credit_note, 'credit_note', 64),
  };
}

export function parseRefundSettled(v: unknown): { state: string } {
  const o = obj(v, 'refund_settled');
  return { state: str(o.state, 'state', 32) };
}

export function parseStatus(v: unknown): StatusRow[] {
  return arr(v, 'status', 100).map((x) => {
    const o = obj(x, 'status row');
    return {
      platform_order_ref: str(o.platform_order_ref, 'platform_order_ref', 64),
      state: str(o.state, 'state', 32),
      seq: o.seq === undefined || o.seq === null || o.seq === false ? null : int(o.seq, 'seq'),
      ready_at: optStr(o.ready_at, 'ready_at', 64),
      picked_up_at: optStr(o.picked_up_at, 'picked_up_at', 64),
    };
  });
}
