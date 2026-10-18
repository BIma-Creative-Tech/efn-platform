/**
 * Keranjang online (docs/04 §10.3.3, §10.3.4) — tipe + operasi MURNI (client, Route Handler, test).
 *
 * - Isi cart HANYA `{ channel_code, outlet_code, fulfilment, lines: [{ product_id, qty }] }` — tanpa harga.
 *   Harga, pajak, total selalu dari quote server (Odoo). Tidak ada harga/total yang dipercaya dari klien.
 * - Satu kanal (= satu PT/brand) = satu grup = satu SO di Odoo; UI wajib memperlihatkan pengelompokan per PT
 *   (ADR-019 Proposed, unified cart -> pesanan per PT).
 * - Keranjang tamu di `localStorage` (tanpa harga; `name` hanya untuk tampilan) lalu digabung ke server setelah login.
 */
import { isChannelCode, isOutletCode, isUuid, parsePositiveInt } from './catalog';

export const MAX_CART_LINES = 50;
export const DEFAULT_MAX_QTY = 99;
export const MAX_CART_BODY_BYTES = 16 * 1024;
export const GUEST_CART_KEY = 'efn_guest_cart_v1';

export type Fulfilment = 'pickup' | 'delivery';

export interface CartLine {
  product_id: number;
  qty: number;
}

export interface CartGroup {
  channel_code: string;
  outlet_code: string;
  fulfilment: Fulfilment;
  lines: CartLine[];
}

export interface Cart {
  version: number;
  groups: CartGroup[];
}

export interface CartWarning {
  product_id: number;
  code: string;
}

export interface CartPutResult extends Cart {
  warnings: CartWarning[];
}

export const EMPTY_CART: Cart = { version: 0, groups: [] };

function isObj(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v);
}

export function countLines(groups: CartGroup[]): number {
  return groups.reduce((n, g) => n + g.lines.length, 0);
}

export function countItems(groups: CartGroup[]): number {
  return groups.reduce((n, g) => n + g.lines.reduce((m, l) => m + l.qty, 0), 0);
}

/**
 * Validasi + allowlist body cart (dari browser atau dari BFF). Field lain (mis. harga yang disisipkan klien)
 * DIBUANG. Mengembalikan null bila bentuk tidak valid. Validasi bisnis final tetap di BFF.
 */
export function sanitizeCart(v: unknown, maxQty = 999): Cart | null {
  if (!isObj(v)) return null;
  const version = typeof v.version === 'number' && Number.isSafeInteger(v.version) && v.version >= 0 ? v.version : null;
  if (version === null || !Array.isArray(v.groups) || v.groups.length > MAX_CART_LINES) return null;
  const groups: CartGroup[] = [];
  const seenChannels = new Set<string>();
  for (const g of v.groups) {
    if (!isObj(g) || !isChannelCode(g.channel_code) || !isOutletCode(g.outlet_code)) return null;
    if (g.fulfilment !== 'pickup' && g.fulfilment !== 'delivery') return null;
    if (seenChannels.has(g.channel_code)) return null; // satu outlet per kanal
    seenChannels.add(g.channel_code);
    if (!Array.isArray(g.lines)) return null;
    const lines: CartLine[] = [];
    for (const l of g.lines) {
      if (!isObj(l)) return null;
      const id = parsePositiveInt(l.product_id);
      const qty = typeof l.qty === 'number' && Number.isInteger(l.qty) ? l.qty : null;
      if (id === null || qty === null || qty < 1 || qty > maxQty) return null;
      if (lines.some((x) => x.product_id === id)) return null;
      lines.push({ product_id: id, qty });
    }
    if (lines.length > 0) groups.push({ channel_code: g.channel_code, outlet_code: g.outlet_code, fulfilment: g.fulfilment, lines });
  }
  if (countLines(groups) > MAX_CART_LINES) return null;
  return { version, groups };
}

export function parseCartPutResult(v: unknown): CartPutResult | null {
  const cart = sanitizeCart(v);
  if (!cart || !isObj(v)) return null;
  const warnings: CartWarning[] = [];
  if (Array.isArray(v.warnings)) {
    for (const w of v.warnings) {
      if (!isObj(w)) continue;
      const id = parsePositiveInt(w.product_id);
      if (id !== null && typeof w.code === 'string') warnings.push({ product_id: id, code: w.code.slice(0, 40) });
    }
  }
  return { ...cart, warnings };
}

export interface AddItemInput {
  channel_code: string;
  outlet_code: string;
  product_id: number;
  qty: number;
  max_qty?: number;
}

export type CartOpResult = { ok: true; groups: CartGroup[]; outletChanged: boolean } | { ok: false; reason: 'too_many_lines' | 'invalid' };

function cloneGroups(groups: CartGroup[]): CartGroup[] {
  return groups.map((g) => ({ ...g, lines: g.lines.map((l) => ({ ...l })) }));
}

/**
 * Tambah barang (qty dijumlahkan, dibatasi `max_qty`). Kanal hanya boleh satu outlet: bila outlet berbeda,
 * seluruh grup kanal itu pindah ke outlet baru (`outletChanged` -> UI memberi tahu pengguna).
 * Pilot hanya `pickup` (§10.1).
 */
export function addItem(groups: CartGroup[], input: AddItemInput): CartOpResult {
  if (!isChannelCode(input.channel_code) || !isOutletCode(input.outlet_code)) return { ok: false, reason: 'invalid' };
  const max = Math.min(input.max_qty ?? DEFAULT_MAX_QTY, 999);
  if (!Number.isInteger(input.qty) || input.qty < 1 || parsePositiveInt(input.product_id) === null) {
    return { ok: false, reason: 'invalid' };
  }
  const next = cloneGroups(groups);
  let group = next.find((g) => g.channel_code === input.channel_code);
  let outletChanged = false;
  if (!group) {
    group = { channel_code: input.channel_code, outlet_code: input.outlet_code, fulfilment: 'pickup', lines: [] };
    next.push(group);
  } else if (group.outlet_code !== input.outlet_code) {
    group.outlet_code = input.outlet_code;
    outletChanged = true;
  }
  const line = group.lines.find((l) => l.product_id === input.product_id);
  if (line) {
    line.qty = Math.min(line.qty + input.qty, max);
  } else {
    if (countLines(next) >= MAX_CART_LINES) return { ok: false, reason: 'too_many_lines' };
    group.lines.push({ product_id: input.product_id, qty: Math.min(input.qty, max) });
  }
  return { ok: true, groups: next, outletChanged };
}

/** Ubah qty; qty ≤ 0 menghapus baris; grup kosong dihapus. */
export function setQty(groups: CartGroup[], channel: string, productId: number, qty: number, maxQty = DEFAULT_MAX_QTY): CartGroup[] {
  const next = cloneGroups(groups);
  const group = next.find((g) => g.channel_code === channel);
  if (!group) return next;
  if (!Number.isFinite(qty) || qty <= 0) {
    group.lines = group.lines.filter((l) => l.product_id !== productId);
  } else {
    const line = group.lines.find((l) => l.product_id === productId);
    if (line) line.qty = Math.min(Math.floor(qty), maxQty);
  }
  return next.filter((g) => g.lines.length > 0);
}

export function setOutlet(groups: CartGroup[], channel: string, outlet: string): CartGroup[] {
  if (!isOutletCode(outlet)) return cloneGroups(groups);
  return cloneGroups(groups).map((g) => (g.channel_code === channel ? { ...g, outlet_code: outlet } : g));
}

/** Gabungkan keranjang tamu ke keranjang server (qty dijumlahkan; outlet server menang bila sudah ada grup). */
export function mergeGroups(server: CartGroup[], guest: CartGroup[]): CartGroup[] {
  let merged = cloneGroups(server);
  for (const g of guest) {
    const existing = merged.find((m) => m.channel_code === g.channel_code);
    const outlet = existing ? existing.outlet_code : g.outlet_code;
    for (const l of g.lines) {
      const r = addItem(merged, { channel_code: g.channel_code, outlet_code: outlet, product_id: l.product_id, qty: l.qty });
      if (r.ok) merged = r.groups;
    }
  }
  return merged;
}

// ---------- keranjang tamu (localStorage) ----------

export interface GuestCart {
  groups: CartGroup[];
  /** Nama produk untuk tampilan saja (bukan harga). */
  names: Record<string, string>;
}

export function parseGuestCart(raw: string | null): GuestCart {
  if (!raw || raw.length > MAX_CART_BODY_BYTES * 2) return { groups: [], names: {} };
  try {
    const v: unknown = JSON.parse(raw);
    if (!isObj(v)) return { groups: [], names: {} };
    const cart = sanitizeCart({ version: 0, groups: v.groups });
    const names: Record<string, string> = {};
    if (isObj(v.names)) {
      for (const [k, n] of Object.entries(v.names)) {
        if (parsePositiveInt(k) !== null && typeof n === 'string') names[k] = n.slice(0, 200);
      }
    }
    return { groups: cart?.groups ?? [], names };
  } catch {
    return { groups: [], names: {} };
  }
}

export function serializeGuestCart(cart: GuestCart): string {
  return JSON.stringify({ groups: cart.groups, names: cart.names });
}

// ---------- quote (§10.3.4) ----------

export interface QuoteLine {
  product_id: number;
  name: string;
  qty: number;
  price_unit_incl: number;
  subtotal_incl: number;
  tax_labels: string[];
  available: boolean;
  /** Baris reward promo (§11.2): nilai negatif, dari Odoo, tidak pernah dari keranjang/klien. Ditampilkan sebagai diskon. */
  reward: boolean;
}

export interface TaxGroup {
  label: string;
  base: number;
  amount: number;
}

export interface QuoteIssue {
  product_id: number | null;
  code: string;
}

export interface QuoteGroup {
  channel_code: string;
  company_code: string;
  outlet_code: string;
  fulfilment: Fulfilment;
  lines: QuoteLine[];
  tax_groups: TaxGroup[];
  amount_total: number;
  amount_payable: number;
  issues: QuoteIssue[];
}

export interface Quote {
  quote_id: string;
  valid_until: string | null;
  cart_version: number;
  groups: QuoteGroup[];
  amount_payable_total: number;
  /** Hasil kode promo (§11.2 no. 4). `invalid` tidak dibedakan penyebabnya dan tidak memblokir checkout. */
  promo: PromoResult;
}

export interface PromoResult {
  applied: string[];
  invalid: string[];
}

// ---------- kode promo (docs/04 §11.2) ----------

export const MAX_PROMO_CODES = 3;
export const PROMO_CODE_RE = /^[A-Za-z0-9_-]{1,40}$/;
/** Satu pesan untuk SEMUA penyebab (anti-enumerasi kupon, §11.2 no. 4). Jangan dibedakan. */
export const PROMO_INVALID_MESSAGE = 'Kode promo tidak dapat digunakan';
export const PROMO_RATE_LIMIT_MESSAGE = 'Terlalu banyak percobaan kode promo. Coba lagi nanti.';

/** Normalisasi satu kode (trim + huruf besar); null bila format salah. Validasi final tetap di BFF. */
export function normalizePromoCode(raw: unknown): string | null {
  if (typeof raw !== 'string') return null;
  const t = raw.trim();
  return PROMO_CODE_RE.test(t) ? t.toUpperCase() : null;
}

/** Daftar kode -> dinormalisasi, duplikat dibuang. null bila ada kode salah format atau lebih dari 3. */
export function sanitizePromoCodes(v: unknown): string[] | null {
  if (v === undefined || v === null) return [];
  if (!Array.isArray(v) || v.length > MAX_PROMO_CODES) return null;
  const out: string[] = [];
  for (const c of v) {
    const n = normalizePromoCode(c);
    if (n === null) return null;
    if (!out.includes(n)) out.push(n);
  }
  return out;
}

function parsePromo(v: unknown): PromoResult {
  const pick = (x: unknown): string[] => {
    if (!Array.isArray(x)) return [];
    const out: string[] = [];
    for (const c of x) {
      const n = normalizePromoCode(c);
      if (n !== null && !out.includes(n) && out.length < MAX_PROMO_CODES) out.push(n);
    }
    return out;
  };
  return isObj(v) ? { applied: pick(v.applied), invalid: pick(v.invalid) } : { applied: [], invalid: [] };
}

function num(v: unknown): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? v : null;
}

function text(v: unknown, max: number): string {
  return typeof v === 'string' ? v.slice(0, max) : '';
}

function parseTaxGroups(v: unknown): TaxGroup[] {
  if (!Array.isArray(v)) return [];
  const out: TaxGroup[] = [];
  for (const t of v) {
    if (!isObj(t)) continue;
    const base = num(t.base);
    const amount = num(t.amount);
    if (typeof t.label !== 'string' || base === null || amount === null) continue;
    out.push({ label: t.label.slice(0, 40), base, amount });
  }
  return out;
}

/** Allowlist kode issue dari BFF. Kode tak dikenal tetap menjadi issue (tetap memblokir checkout) tetapi dinormalkan. */
export const KNOWN_ISSUE_CODES = [
  'out_of_stock',
  'item_unavailable',
  'qty_exceeds_max',
  'qty_limit',
  'price_changed',
  // §12.3 (W-33): `weight_limit` = tingkat grup/PT (product_id null); `weight_missing` = per produk.
  'weight_limit',
  'weight_missing',
] as const;
export const UNKNOWN_ISSUE_CODE = 'unknown_issue';

export function parseIssues(v: unknown): QuoteIssue[] {
  if (!Array.isArray(v)) return [];
  const out: QuoteIssue[] = [];
  for (const i of v) {
    if (!isObj(i) || typeof i.code !== 'string') continue;
    const code = (KNOWN_ISSUE_CODES as readonly string[]).includes(i.code) ? i.code : UNKNOWN_ISSUE_CODE;
    out.push({ product_id: parsePositiveInt(i.product_id), code });
  }
  return out;
}

/**
 * Kode promo yang dipertahankan setelah hitung ulang (murni). Bila quote gagal/jatuh ke fallback tanpa kode
 * (429/400), daftar lama TETAP dipakai untuk percobaan berikutnya; selain itu ikuti `applied` dari server.
 */
export function retainPromoCodes(current: string[], applied: string[] | null, fellBack: boolean): string[] {
  if (fellBack || applied === null) return [...current];
  return [...applied];
}

export function parseQuoteLines(v: unknown): QuoteLine[] {
  if (!Array.isArray(v)) return [];
  const out: QuoteLine[] = [];
  for (const l of v) {
    if (!isObj(l)) continue;
    const id = parsePositiveInt(l.product_id);
    const qty = num(l.qty);
    const unit = num(l.price_unit_incl);
    const sub = num(l.subtotal_incl);
    if (id === null || qty === null || unit === null || sub === null) continue;
    out.push({
      product_id: id,
      name: text(l.name, 200) || `Produk #${id}`,
      qty,
      price_unit_incl: unit,
      subtotal_incl: sub,
      tax_labels: Array.isArray(l.tax_labels) ? l.tax_labels.filter((x): x is string => typeof x === 'string').slice(0, 8) : [],
      available: l.available !== false,
      reward: l.reward === true,
    });
  }
  return out;
}

/**
 * Allowlist respons quote. Field di luar kontrak (mis. `quote_hash`, bila BFF keliru mengirimnya) TIDAK
 * diteruskan ke browser. Nominal payable harus bilangan bulat Rupiah (§10.2) — selain itu quote ditolak.
 */
export function parseQuote(v: unknown): Quote | null {
  if (!isObj(v) || !isUuid(v.quote_id) || !Array.isArray(v.groups)) return null;
  const total = num(v.amount_payable_total);
  if (total === null || !Number.isInteger(total) || total < 0) return null;
  const groups: QuoteGroup[] = [];
  for (const g of v.groups) {
    if (!isObj(g) || !isChannelCode(g.channel_code)) return null;
    const payable = num(g.amount_payable);
    if (payable === null || !Number.isInteger(payable)) return null;
    groups.push({
      channel_code: g.channel_code,
      company_code: text(g.company_code, 16) || g.channel_code,
      outlet_code: isOutletCode(g.outlet_code) ? g.outlet_code : '',
      fulfilment: g.fulfilment === 'delivery' ? 'delivery' : 'pickup',
      lines: parseQuoteLines(g.lines),
      tax_groups: parseTaxGroups(g.tax_groups),
      amount_total: num(g.amount_total) ?? payable,
      amount_payable: payable,
      issues: parseIssues(g.issues),
    });
  }
  return {
    quote_id: v.quote_id,
    valid_until: typeof v.valid_until === 'string' ? v.valid_until.slice(0, 40) : null,
    cart_version: typeof v.cart_version === 'number' && Number.isSafeInteger(v.cart_version) ? v.cart_version : 0,
    groups,
    amount_payable_total: total,
    promo: parsePromo(v.promo),
  };
}

export function quoteHasIssues(q: Quote): boolean {
  return q.groups.some((g) => g.issues.length > 0 || g.lines.some((l) => !l.reward && !l.available));
}

export const ISSUE_LABEL: Record<string, string> = {
  out_of_stock: 'Stok habis',
  item_unavailable: 'Tidak tersedia',
  qty_exceeds_max: 'Melebihi jumlah maksimum',
  qty_limit: 'Jumlah melebihi batas pembelian. Kurangi jumlah barang ini.',
  price_changed: 'Harga berubah',
  weight_limit: 'Total berat pesanan melebihi batas untuk PT ini. Kurangi jumlah atau hapus beberapa barang, lalu coba lagi.',
  weight_missing: 'Berat barang ini belum tercatat sehingga belum bisa dipesan online. Hapus barang ini atau beli langsung di toko.',
};

export function issueLabel(code: string): string {
  return ISSUE_LABEL[code] ?? 'Tidak dapat dipesan';
}

/** Issue tingkat grup/PT (tanpa `product_id`), mis. `weight_limit`. Tidak menempel ke baris produk mana pun. */
export function groupLevelIssues(g: Pick<QuoteGroup, 'issues'>): QuoteIssue[] {
  return g.issues.filter((i) => i.product_id === null);
}

/** Issue yang menempel pada satu produk (mis. `weight_missing`, `out_of_stock`). */
export function productIssue(g: Pick<QuoteGroup, 'issues'>, productId: number): QuoteIssue | undefined {
  return g.issues.find((i) => i.product_id === productId);
}
