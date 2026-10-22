import { Inject, Injectable } from '@nestjs/common';
import { AppConfig, ONLINE_CHANNEL_RE } from '../config/app-config';
import { Errors } from '../common/errors/api-error';
import { asRecord } from '../common/validation';
import { CatalogService } from '../catalog/catalog.service';
import { OnlineErrors } from '../odoo-rpc/online-errors';
import { CART_STORE, Cart, CartGroup, CartLine, CartStore, lineIdentity } from './cart-store';

const OUTLET_RE = /^[A-Z0-9][A-Z0-9-]{1,23}$/;
/** G23 §15.4: ISO UTC detik nol (bentuk yang dikembalikan `GET /v1/catalog/slots`). */
const SLOT_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:00Z$/;
export const CART_MAX_LINES = 50;
export const CART_MAX_GROUPS = 10;
const QTY_HARD_MAX = 999;

export interface CartWarning {
  product_id: number;
  code: 'item_unavailable';
}

/** Parse body `PUT /v1/cart` (bentuk saja; validasi katalog di service). Harga dari klien TIDAK diterima. */
export function parseCartPut(body: unknown): { version: number; groups: CartGroup[] } {
  const o = asRecord(body);
  const allowedTop = new Set(['version', 'groups']);
  if (Object.keys(o).some((k) => !allowedTop.has(k))) throw Errors.invalidRequest();
  const version = o.version;
  if (typeof version !== 'number' || !Number.isSafeInteger(version) || version < 0) throw Errors.invalidRequest();
  if (!Array.isArray(o.groups) || o.groups.length > CART_MAX_GROUPS) throw Errors.invalidRequest();
  let total = 0;
  const seen = new Set<string>();
  const groups = o.groups.map((g): CartGroup => {
    const r = asRecord(g);
    if (Object.keys(r).some((k) => !['channel_code', 'outlet_code', 'fulfilment', 'lines', 'pickup_slot'].includes(k))) throw Errors.invalidRequest();
    const slot = r.pickup_slot === undefined || r.pickup_slot === null ? undefined : r.pickup_slot;
    if (slot !== undefined && (typeof slot !== 'string' || !SLOT_RE.test(slot) || Number.isNaN(Date.parse(slot)) || r.fulfilment !== 'pickup')) {
      throw Errors.invalidRequest();
    }
    if (typeof r.channel_code !== 'string' || !ONLINE_CHANNEL_RE.test(r.channel_code)) throw Errors.invalidRequest();
    if (typeof r.outlet_code !== 'string' || !OUTLET_RE.test(r.outlet_code)) throw Errors.invalidRequest();
    if (r.fulfilment !== 'pickup' && r.fulfilment !== 'delivery') throw Errors.invalidRequest();
    if (seen.has(r.channel_code)) throw Errors.invalidRequest('Satu outlet per kanal.');
    seen.add(r.channel_code);
    if (!Array.isArray(r.lines)) throw Errors.invalidRequest();
    // G22 B-4: baris digabung per identitas (product_id, PTAV terurut) — produk sama dengan modifier berbeda = dua baris
    const merged = new Map<string, CartLine>();
    for (const l of r.lines) {
      const lr = asRecord(l);
      if (Object.keys(lr).some((k) => k !== 'product_id' && k !== 'qty' && k !== 'attribute_value_ids')) throw Errors.invalidRequest();
      const pid = lr.product_id;
      const qty = lr.qty;
      if (typeof pid !== 'number' || !Number.isSafeInteger(pid) || pid < 1 || pid > 2_147_483_647) throw Errors.invalidRequest();
      if (typeof qty !== 'number' || !Number.isSafeInteger(qty) || qty < 1 || qty > QTY_HARD_MAX) throw Errors.invalidRequest();
      const ptavRaw = lr.attribute_value_ids === undefined || lr.attribute_value_ids === null ? [] : lr.attribute_value_ids;
      if (!Array.isArray(ptavRaw) || ptavRaw.length > 10) throw Errors.invalidRequest();
      const ptav = ptavRaw.map((x) => {
        if (typeof x !== 'number' || !Number.isSafeInteger(x) || x < 1 || x > 2_147_483_647) throw Errors.invalidRequest();
        return x;
      });
      if (new Set(ptav).size !== ptav.length) throw Errors.invalidRequest();
      const sorted = [...ptav].sort((a, b) => a - b);
      const line: CartLine = sorted.length > 0 ? { product_id: pid, qty, attribute_value_ids: sorted } : { product_id: pid, qty };
      const key = lineIdentity(line);
      const prev = merged.get(key);
      if (prev) prev.qty += qty;
      else merged.set(key, line);
    }
    total += merged.size;
    return {
      channel_code: r.channel_code,
      outlet_code: r.outlet_code,
      fulfilment: r.fulfilment,
      lines: [...merged.values()],
      ...(slot !== undefined ? { pickup_slot: slot as string } : {}),
    };
  });
  if (total > CART_MAX_LINES) throw Errors.invalidRequest(`Maksimal ${CART_MAX_LINES} baris di keranjang.`);
  return { version, groups };
}

/**
 * Cart per member di Redis (docs/04 §10.3.3). Validasi server: kanal aktif & di `ONLINE_CHANNELS`, outlet milik kanal &
 * `pickup_enabled`, fulfilment diizinkan, produk ada di salinan katalog (yang hilang dibuang + `warnings`), qty ≤
 * `max_qty` produk. Isi cart tidak pernah dipercaya — quote selalu memvalidasi ulang di Odoo.
 */
@Injectable()
export class CartService {
  constructor(
    private readonly config: AppConfig,
    @Inject(CART_STORE) private readonly store: CartStore,
    private readonly catalog: CatalogService,
  ) {}

  get(memberId: string): Promise<Cart> {
    return this.store.load(memberId);
  }

  async put(memberId: string, input: { version: number; groups: CartGroup[] }): Promise<Cart & { warnings: CartWarning[] }> {
    const warnings: CartWarning[] = [];
    const groups: CartGroup[] = [];
    for (const g of input.groups) {
      const ch = await this.catalog.channel(g.channel_code);
      if (!ch) throw OnlineErrors.itemUnavailable();
      if (!ch.fulfilment[g.fulfilment]) throw OnlineErrors.fulfilmentUnavailable();
      const outlet = ch.outlets.find((o) => o.outlet_code === g.outlet_code);
      if (!outlet || (g.fulfilment === 'pickup' && !outlet.pickup_enabled)) throw OnlineErrors.itemUnavailable();
      // G23 §15.4: slot hanya untuk outlet ber-slot (kill switch mati → outlet tidak pernah slot_enabled)
      if (g.pickup_slot !== undefined && outlet.slot_enabled !== true) throw OnlineErrors.fulfilmentUnavailable();
      const products = await this.catalog.productsByIds(g.channel_code, g.lines.map((l) => l.product_id));
      const lines: CartLine[] = [];
      for (const l of g.lines) {
        const p = products.get(l.product_id);
        if (!p) {
          warnings.push({ product_id: l.product_id, code: 'item_unavailable' });
          continue;
        }
        if (l.qty > p.max_qty) throw Errors.invalidRequest(`Jumlah maksimal ${p.max_qty} per barang.`);
        lines.push(l);
      }
      if (lines.length > 0) groups.push({ ...g, lines });
    }
    if (!(await this.store.save(memberId, input.version, groups))) throw OnlineErrors.cartVersionConflict();
    return { version: input.version + 1, groups, warnings };
  }

  clear(memberId: string): Promise<void> {
    return this.store.clear(memberId);
  }

  clearIfVersion(memberId: string, version: number): Promise<void> {
    return this.store.clearIfVersion(memberId, version);
  }
}
