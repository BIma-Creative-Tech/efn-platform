import { Inject, Injectable, Logger, Optional } from '@nestjs/common';
import { MemberService } from '../common/member/member.service';
import { ONLINE_POINTS, OnlinePointsPort, QuotePoints } from '../orders/online-points';
import { randomUUID } from 'node:crypto';
import { AppConfig } from '../config/app-config';
import { KV_STORE, KvStore } from '../common/redis/kv-store';
import { redisKeyHash } from '../common/pii/pii';
import { cartKey, lineIdentity } from './cart-store';
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
  /** G23 §15.4: jadwal ambil pre-order terpilih (absen = secepatnya). */
  pickup_slot?: string;
  /** Baris produk cart + (G19 §11.2) baris reward Odoo `reward: true` (harga ≤ 0) apa adanya. */
  lines: (PartLine & { available: boolean })[];
  tax_groups: TaxGroup[];
  amount_total: number;
  amount_payable: number;
  issues: QuoteIssue[];
}
/** G19 §11.2 no. 4: `invalid` = kode yang tidak terpakai di grup MANA PUN (satu kategori untuk semua penyebab). */
export interface PromoView {
  applied: string[];
  invalid: string[];
}
export interface QuoteView {
  quote_id: string;
  valid_until: string;
  cart_version: number;
  groups: QuoteGroupView[];
  amount_payable_total: number;
  promo: PromoView;
  /** G22 §14.C.2: blok informatif poin (tidak mengikat). */
  points?: QuotePoints;
}
export type StoredQuoteGroup = QuoteGroupView & {
  quote_hash: string;
  /** §11.2 no. 5: kode terpakai di grup ini — dikirim saat place (bukan dari klien). Quote lama (pra-G19) tanpa field = []. */
  promo_applied?: string[];
};
/** Disimpan di Redis `oq:<quote_id>` — `quote_hash` & `promo_applied` per grup TIDAK pernah dikirim ke klien. */
export interface StoredQuote {
  member_id: string;
  cart_version: number;
  valid_until: string;
  groups: StoredQuoteGroup[];
  amount_payable_total: number;
  /** §11.2 no. 5: kode hasil normalisasi yang diminta klien. */
  promo_codes?: string[];
}

/** Bentuk publik grup quote: `quote_hash` & `promo_applied` dibuang (tidak pernah ke klien). */
export function toView(g: StoredQuoteGroup): QuoteGroupView {
  const view: QuoteGroupView & { quote_hash?: string; promo_applied?: string[] } = { ...g };
  delete view.quote_hash;
  delete view.promo_applied;
  return view;
}

/** Baris yang dikirim ke `efn_online_place`: hanya produk cart (baris reward dihitung ulang Odoo dari kode). */
export function placeLines(g: Pick<QuoteGroupView, 'lines'>): { product_id: number; qty: number; attribute_value_ids?: number[] }[] {
  // G22 B-4: PTAV ikut HANYA bila ada (payload & payload_sha256 Odoo baris tanpa atribut sama dengan pra-G22)
  return g.lines
    .filter((l) => l.reward !== true)
    .map((l) => (l.attribute_value_ids && l.attribute_value_ids.length > 0 ? { product_id: l.product_id, qty: l.qty, attribute_value_ids: l.attribute_value_ids } : { product_id: l.product_id, qty: l.qty }));
}

/** Batas jumlah baris reward per grup (pengaman bentuk respons Odoo). */
const MAX_REWARD_LINES = 20;

export function quoteKey(id: string): string {
  return `oq:${id}`;
}

/**
 * Indeks quote per member (untuk erasure): `oq:m:<HMAC(member_id)>` (pola key Redis ber-HMAC §8.16, pepper
 * `efn:redis_key:v1`) berisi daftar quote_id terakhir; TTL mengikuti TTL quote maksimum.
 */
export function quoteIndexKey(pepper: Buffer, memberId: string): string {
  return `oq:m:${redisKeyHash(pepper, memberId)}`;
}
const QUOTE_INDEX_MAX = 20;

/**
 * Erasure (DSAR delete, §10.8.3): hapus cart `cart:<member_id>`, semua quote `oq:<id>` yang terindeks milik member, dan
 * indeksnya. Best effort (TTL tetap membersihkan: cart 7 hari, quote ≤ 10 menit). Mengembalikan jumlah key terhapus.
 */
export async function purgeMemberOnlineRedis(kv: KvStore, pepper: Buffer, memberId: string): Promise<number> {
  let n = 0;
  const idx = quoteIndexKey(pepper, memberId);
  const raw = await kv.get(idx);
  let ids: string[] = [];
  try {
    ids = raw ? (JSON.parse(raw) as string[]).filter((x) => isUuid(x)) : [];
  } catch {
    ids = [];
  }
  for (const id of ids) n += await kv.del(quoteKey(id));
  n += await kv.del(idx);
  n += await kv.del(cartKey(memberId));
  return n;
}

/**
 * `POST /v1/cart/quote` (docs/04 §10.3.4): cart tersimpan → `efn_online_quote` per kanal (paralel, timeout 8 s). Harga,
 * pajak, label = apa adanya dari Odoo; BFF tidak menghitung. Satu kanal gagal → `503 online_unavailable` (quote parsial
 * ditunda). TTL = min(`valid_until` Odoo, `ONLINE_QUOTE_TTL_SECONDS`).
 */
@Injectable()
export class QuoteService {
  private readonly logger = new Logger('OnlineQuote');
  now: () => number = () => Date.now();

  constructor(
    private readonly config: AppConfig,
    private readonly cart: CartService,
    private readonly catalog: CatalogService,
    @Inject(ODOO_ONLINE) private readonly odoo: OdooOnlineApi,
    @Inject(KV_STORE) private readonly kv: KvStore,
    private readonly metrics: OnlineMetrics,
    /** G22 §14.C.2 (opsional di test lama): blok poin quote. */
    @Optional() @Inject(ONLINE_POINTS) private readonly points: OnlinePointsPort | null = null,
    @Optional() @Inject(MemberService) private readonly members: MemberService | null = null,
  ) {}

  private async pointsBlock(memberId: string, groups: StoredQuoteGroup[]): Promise<QuotePoints | undefined> {
    if (!this.points) return undefined;
    try {
      const member = this.members ? await this.members.findActive(memberId) : null;
      const flags = await Promise.all(groups.map(async (g) => (await this.catalog.channel(g.channel_code))?.loyalty?.redeem === true));
      return await this.points.quoteBlock(
        memberId,
        member?.memberHash ?? null,
        groups.map((g, i) => ({ channel_code: g.channel_code, company_code: g.company_code, amount_payable: g.amount_payable, redeem: flags[i] })),
      );
    } catch (err) {
      // blok informatif: kegagalan tidak menggagalkan quote
      this.logger.warn(`Blok poin quote gagal: ${err instanceof Error ? err.name : 'error'}`);
      return undefined;
    }
  }

  /**
   * `promoCodes` = hasil `normalizePromoCodes` (controller). Kode yang sama dikirim ke SETIAP kanal (§11.2 no. 2);
   * Odoo hanya menerapkan program milik pricelist kanal itu. Respons `promo.invalid` = tidak terpakai di grup mana pun.
   */
  async quote(memberId: string, promoCodes: readonly string[] = []): Promise<QuoteView> {
    const cart = await this.cart.get(memberId);
    if (cart.groups.length === 0 || cart.groups.every((g) => g.lines.length === 0)) throw OnlineErrors.cartEmpty();
    for (const g of cart.groups) if (this.odoo.isCircuitOpen(g.channel_code)) throw OnlineErrors.unavailable();
    const codes = [...promoCodes];
    const results = await Promise.all(cart.groups.map((g) => this.quoteGroup(g, codes)));
    const nowMs = this.now();
    let validUntil = nowMs + this.config.onlineQuoteTtlSeconds * 1000;
    for (const r of results) if (r.valid !== null) validUntil = Math.min(validUntil, r.valid);
    const ttl = Math.floor((validUntil - nowMs) / 1000);
    if (ttl < 5) throw OnlineErrors.unavailable();
    const groups = results.map((r) => r.group);
    const promo: PromoView = {
      applied: codes.filter((c) => groups.some((g) => (g.promo_applied ?? []).includes(c))),
      invalid: codes.filter((c) => !groups.some((g) => (g.promo_applied ?? []).includes(c))),
    };
    if (codes.length > 0) {
      this.metrics.promo('applied', promo.applied.length);
      this.metrics.promo('invalid', promo.invalid.length);
      // §11.2 no. 6 + SEC-132 (L-42): kode TIDAK PERNAH di-log, juga tidak hash/tag-nya — hanya jumlah
      this.logger.debug(`Quote promo: terpakai ${promo.applied.length}, tidak terpakai ${promo.invalid.length}`);
    }
    const stored: StoredQuote = {
      member_id: memberId,
      cart_version: cart.version,
      valid_until: new Date(validUntil).toISOString(),
      groups,
      amount_payable_total: groups.reduce((s, g) => s + g.amount_payable, 0),
      promo_codes: codes,
    };
    const id = randomUUID();
    await this.kv.setEx(quoteKey(id), JSON.stringify(stored), ttl);
    // indeks per member (key ber-HMAC) agar erasure bisa menghapus quote yang masih hidup
    const idx = quoteIndexKey(this.config.redisKeyPepper, memberId);
    let prev: string[] = [];
    try {
      prev = JSON.parse((await this.kv.get(idx)) ?? '[]') as string[];
    } catch {
      prev = [];
    }
    await this.kv.setEx(idx, JSON.stringify([...prev.filter((x) => isUuid(x)), id].slice(-QUOTE_INDEX_MAX)), this.config.onlineQuoteTtlSeconds);
    const points = await this.pointsBlock(memberId, groups);
    return {
      quote_id: id,
      valid_until: stored.valid_until,
      cart_version: cart.version,
      groups: groups.map(toView),
      amount_payable_total: stored.amount_payable_total,
      promo,
      ...(points ? { points } : {}),
    };
  }

  private async quoteGroup(g: CartGroup, codes: string[]): Promise<{ group: StoredQuoteGroup; valid: number | null }> {
    const ch = await this.catalog.channel(g.channel_code);
    if (!ch) throw OnlineErrors.itemUnavailable();
    let q: QuoteResult;
    try {
      q = await this.odoo.quote(g.channel_code, g.outlet_code, g.fulfilment, g.lines, codes, g.pickup_slot ?? null);
    } catch (err) {
      throw mapOdooError(err, this.metrics, { method: 'quote', channel: g.channel_code });
    }
    const invalid = (why: string): never => {
      throw mapOdooError(new OdooRpcError('invalid_response', null, why), this.metrics, { method: 'quote', channel: g.channel_code });
    };
    // Baris PRODUK quote harus persis baris cart (produk & qty); selain itu respons Odoo tidak dipercaya.
    // Baris reward (§11.2 no. 3) dinilai terpisah: boleh memakai produk yang sama (mis. buy_x_get_y), harga ≤ 0 (parser).
    const productLines = q.lines.filter((l) => !l.reward);
    const rewardLines = q.lines.filter((l) => l.reward);
    if (rewardLines.length > MAX_REWARD_LINES) invalid('baris reward terlalu banyak');
    // G22 B-4 / L-78: identitas baris = (product_id, PTAV terurut) — produk sama dengan modifier berbeda = dua baris
    const want = new Map(g.lines.map((l) => [lineIdentity(l), l]));
    const issuesById = new Set(q.issues.map((i) => i.product_id));
    // SEC-131: produk ber-issue `qty_limit` boleh dikembalikan dengan qty dipangkas (1..qty cart); quote tetap terblokir
    // (`409 cart_has_issues` saat checkout) sehingga qty itu tidak pernah dipakai untuk place.
    const qtyLimited = new Set(q.issues.filter((i) => i.code === 'qty_limit').map((i) => i.product_id));
    const seen = new Set<string>();
    for (const l of productLines) {
      const id = lineIdentity(l);
      const cartQty = want.get(id)?.qty;
      const qtyOk = cartQty === l.qty || (cartQty !== undefined && qtyLimited.has(l.product_id) && l.qty < cartQty);
      if (!qtyOk || seen.has(id)) invalid('baris quote ≠ cart');
      seen.add(id);
    }
    for (const [id, l] of want) {
      if (!seen.has(id) && !issuesById.has(l.product_id)) invalid('baris quote hilang');
    }
    // §11.2 no. 3/5: Odoo hanya boleh melaporkan kode yang DIMINTA (kode lain = bug/konfigurasi, fail-closed)
    if (q.promo_applied.some((c) => !codes.includes(c)) || q.promo_rejected.some((c) => !codes.includes(c))) invalid('promo di luar permintaan');
    const names = await this.catalog.productsByIds(g.channel_code, productLines.map((l) => l.product_id));
    if (q.company_code !== null && q.company_code !== ch.company_code) invalid('company_code quote ≠ kanal');
    return {
      valid: q.valid_until ? q.valid_until.getTime() : null,
      group: {
        channel_code: g.channel_code,
        company_code: ch.company_code,
        outlet_code: g.outlet_code,
        fulfilment: g.fulfilment,
        ...(g.pickup_slot ? { pickup_slot: g.pickup_slot } : {}),
        lines: q.lines.map((l) => ({
          product_id: l.product_id,
          name: l.reward ? (l.name ?? 'Promo') : (l.name ?? names.get(l.product_id)?.name ?? ''),
          qty: l.qty,
          price_unit_incl: l.price_unit_incl,
          subtotal_incl: l.subtotal_incl,
          tax_labels: l.tax_labels,
          available: l.available,
          ...(l.reward ? { reward: true as const } : {}),
          // G22 L-78: hanya bila baris beratribut (bentuk baris ritel tidak berubah)
          ...((l.attribute_value_ids ?? []).length > 0 ? { attribute_value_ids: l.attribute_value_ids, attributes_text: l.attributes_text ?? null } : {}),
        })),
        tax_groups: q.tax_groups,
        amount_total: q.amount_total,
        amount_payable: q.amount_payable,
        issues: q.issues,
        quote_hash: q.quote_hash,
        // urutan mengikuti permintaan klien (deterministik untuk payload place)
        promo_applied: codes.filter((c) => q.promo_applied.includes(c)),
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
