import { Inject, Injectable } from '@nestjs/common';
import { randomUUID } from 'node:crypto';
import { AppConfig } from '../config/app-config';
import { KV_STORE, KvStore } from '../common/redis/kv-store';
import { isUuid } from '../common/validation';
import { CatalogService } from '../catalog/catalog.service';
import { ODOO_ONLINE, OdooOnlineApi, OdooRpcError, QuoteIssue, QuoteResult, TaxGroup } from '../odoo-rpc/odoo-online.types';
import { OnlineErrors, mapOdooError } from '../odoo-rpc/online-errors';
import { OnlineMetrics } from '../odoo-rpc/online-metrics';
import { PartLine } from '../orders/online.model';
import { CartGroup } from './cart-store';
import { CartService } from './cart.service';

export interface QuoteGroupView {
  channel_code: string;
  company_code: string;
  outlet_code: string;
  fulfilment: 'pickup' | 'delivery';
  lines: (PartLine & { available: boolean })[];
  tax_groups: TaxGroup[];
  amount_total: number;
  amount_payable: number;
  issues: QuoteIssue[];
}
export interface QuoteView {
  quote_id: string;
  valid_until: string;
  cart_version: number;
  groups: QuoteGroupView[];
  amount_payable_total: number;
}
/** Disimpan di Redis `oq:<quote_id>` — `quote_hash` TIDAK pernah dikirim ke klien. */
export interface StoredQuote {
  member_id: string;
  cart_version: number;
  valid_until: string;
  groups: (QuoteGroupView & { quote_hash: string })[];
  amount_payable_total: number;
}

/** Bentuk publik grup quote: `quote_hash` dibuang (tidak pernah ke klien). */
export function toView(g: QuoteGroupView & { quote_hash: string }): QuoteGroupView {
  const view: QuoteGroupView & { quote_hash?: string } = { ...g };
  delete view.quote_hash;
  return view;
}

export function quoteKey(id: string): string {
  return `oq:${id}`;
}

/**
 * `POST /v1/cart/quote` (docs/04 §10.3.4): cart tersimpan → `efn_online_quote` per kanal (paralel, timeout 8 s). Harga,
 * pajak, label = apa adanya dari Odoo; BFF tidak menghitung. Satu kanal gagal → `503 online_unavailable` (quote parsial
 * ditunda). TTL = min(`valid_until` Odoo, `ONLINE_QUOTE_TTL_SECONDS`).
 */
@Injectable()
export class QuoteService {
  now: () => number = () => Date.now();

  constructor(
    private readonly config: AppConfig,
    private readonly cart: CartService,
    private readonly catalog: CatalogService,
    @Inject(ODOO_ONLINE) private readonly odoo: OdooOnlineApi,
    @Inject(KV_STORE) private readonly kv: KvStore,
    private readonly metrics: OnlineMetrics,
  ) {}

  async quote(memberId: string): Promise<QuoteView> {
    const cart = await this.cart.get(memberId);
    if (cart.groups.length === 0 || cart.groups.every((g) => g.lines.length === 0)) throw OnlineErrors.cartEmpty();
    for (const g of cart.groups) if (this.odoo.isCircuitOpen(g.channel_code)) throw OnlineErrors.unavailable();
    const results = await Promise.all(cart.groups.map((g) => this.quoteGroup(g)));
    const nowMs = this.now();
    let validUntil = nowMs + this.config.onlineQuoteTtlSeconds * 1000;
    for (const r of results) if (r.valid !== null) validUntil = Math.min(validUntil, r.valid);
    const ttl = Math.floor((validUntil - nowMs) / 1000);
    if (ttl < 5) throw OnlineErrors.unavailable();
    const groups = results.map((r) => r.group);
    const stored: StoredQuote = {
      member_id: memberId,
      cart_version: cart.version,
      valid_until: new Date(validUntil).toISOString(),
      groups,
      amount_payable_total: groups.reduce((s, g) => s + g.amount_payable, 0),
    };
    const id = randomUUID();
    await this.kv.setEx(quoteKey(id), JSON.stringify(stored), ttl);
    return {
      quote_id: id,
      valid_until: stored.valid_until,
      cart_version: cart.version,
      groups: groups.map(toView),
      amount_payable_total: stored.amount_payable_total,
    };
  }

  private async quoteGroup(g: CartGroup): Promise<{ group: QuoteGroupView & { quote_hash: string }; valid: number | null }> {
    const ch = await this.catalog.channel(g.channel_code);
    if (!ch) throw OnlineErrors.itemUnavailable();
    let q: QuoteResult;
    try {
      q = await this.odoo.quote(g.channel_code, g.outlet_code, g.fulfilment, g.lines);
    } catch (err) {
      throw mapOdooError(err, this.metrics, { method: 'quote', channel: g.channel_code });
    }
    // Baris quote harus persis baris cart (produk & qty); selain itu respons Odoo tidak dipercaya
    const want = new Map(g.lines.map((l) => [l.product_id, l.qty]));
    const issuesById = new Set(q.issues.map((i) => i.product_id));
    const seen = new Set<number>();
    for (const l of q.lines) {
      if (want.get(l.product_id) !== l.qty || seen.has(l.product_id)) {
        throw mapOdooError(new OdooRpcError('invalid_response', null, 'baris quote ≠ cart'), this.metrics, { method: 'quote', channel: g.channel_code });
      }
      seen.add(l.product_id);
    }
    for (const pid of want.keys()) {
      if (!seen.has(pid) && !issuesById.has(pid)) {
        throw mapOdooError(new OdooRpcError('invalid_response', null, 'baris quote hilang'), this.metrics, { method: 'quote', channel: g.channel_code });
      }
    }
    const names = await this.catalog.productsByIds(g.channel_code, q.lines.map((l) => l.product_id));
    if (q.company_code !== null && q.company_code !== ch.company_code) {
      throw mapOdooError(new OdooRpcError('invalid_response', null, 'company_code quote ≠ kanal'), this.metrics, { method: 'quote', channel: g.channel_code });
    }
    return {
      valid: q.valid_until ? q.valid_until.getTime() : null,
      group: {
        channel_code: g.channel_code,
        company_code: ch.company_code,
        outlet_code: g.outlet_code,
        fulfilment: g.fulfilment,
        lines: q.lines.map((l) => ({
          product_id: l.product_id,
          name: l.name ?? names.get(l.product_id)?.name ?? '',
          qty: l.qty,
          price_unit_incl: l.price_unit_incl,
          subtotal_incl: l.subtotal_incl,
          tax_labels: l.tax_labels,
          available: l.available,
        })),
        tax_groups: q.tax_groups,
        amount_total: q.amount_total,
        amount_payable: q.amount_payable,
        issues: q.issues,
        quote_hash: q.quote_hash,
      },
    };
  }

  /** Baca quote milik member; tidak ada / milik member lain → null (tidak dibedakan). */
  async load(quoteId: string, memberId: string): Promise<StoredQuote | null> {
    if (!isUuid(quoteId)) return null;
    const raw = await this.kv.get(quoteKey(quoteId));
    if (!raw) return null;
    try {
      const q = JSON.parse(raw) as StoredQuote;
      return q.member_id === memberId ? q : null;
    } catch {
      return null;
    }
  }

  async remove(quoteId: string): Promise<void> {
    await this.kv.del(quoteKey(quoteId));
  }
}
