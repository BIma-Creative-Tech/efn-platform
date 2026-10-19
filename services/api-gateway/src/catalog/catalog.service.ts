import { Inject, Injectable, Logger } from '@nestjs/common';
import { AppConfig } from '../config/app-config';
import { DB, Db } from '../common/db/db';
import { Errors } from '../common/errors/api-error';
import { KV_STORE, KvStore } from '../common/redis/kv-store';
import { COMPANY_CODES } from '../loyalty/loyalty.service';
import { CatalogChannelInfo, CatalogPrice, ChannelPaymentMode, ItemAttribute, ODOO_ONLINE, OdooOnlineApi, StockStatus } from '../odoo-rpc/odoo-online.types';
import { OnlineErrors, mapOdooError } from '../odoo-rpc/online-errors';
import { OnlineMetrics } from '../odoo-rpc/online-metrics';
import { BRAND_CODE_RE, IMAGE_SHA_RE, parsePaymentModes } from '../odoo-rpc/odoo-online.parse';
import { CATALOG_STORAGE, CatalogStorage, catalogImageKey } from './catalog-storage';

export interface OutletInfo {
  outlet_code: string;
  public_name: string;
  pickup_enabled: boolean;
  prep_minutes: number | null;
}
export interface ChannelInfo {
  channel_code: string;
  company_code: string;
  /** G19 §11.3 no. 2: brand kanal (`GL`, `BACHA`, `PB`) atau `null`. Tidak unik; hanya pembatasan UI (bukan otorisasi). */
  brand_code: string | null;
  name: string;
  fulfilment: { pickup: boolean; delivery: boolean };
  outlets: OutletInfo[];
  /**
   * G20 §12.2 / L-52 / L-55: metode bayar kanal (`qris|counter`) — (blok `channel` Odoo ?? `ONLINE_PAYMENT_METHODS`)
   * ∩ `ONLINE_PAYMENT_METHODS`. Opsional di tipe (fixture lama); `CatalogService` selalu mengisinya.
   */
  payment_modes?: ChannelPaymentMode[] | null;
  /** G22 §14.B B-2: `retail` | `fnb` (salinan lama tanpa field → `retail`). */
  business_type?: 'retail' | 'fnb';
  /**
   * G22 §14.C.1 no. 5: poin online efektif kanal = flag kanal Odoo ∧ kill switch BFF (`ONLINE_LOYALTY_EARN_ENABLED`,
   * `ONLINE_LOYALTY_REDEEM_ENABLED`).
   */
  loyalty?: { earn: boolean; redeem: boolean };
}
export interface ProductSummary {
  product_id: number;
  name: string;
  uom: string;
  category_id: number | null;
  image_sha: string | null;
  price_incl: number;
  price_excl: number;
  tax_labels: string[];
  max_qty: number;
  /** G20 §12.3 / L-52: berat produk (kg) atau `null`. Opsional di tipe (fixture lama); `toSummary` selalu mengisinya. */
  weight_kg?: number | null;
  /** G22 B-2 / L-78: atribut `no_variant` (format A.6); `[]` ritel. */
  attributes?: ItemAttribute[];
}
export interface ItemData {
  name: string;
  description: string;
  uom: string;
  prices: CatalogPrice[];
  max_qty: number;
  /** G20 §12.3: `null`/tidak ada (salinan pra-G20) = berat belum diketahui. */
  weight_kg?: number | null;
  /** G22 B-2: atribut (salinan pra-G22 tanpa field → []). */
  attributes?: ItemAttribute[];
}

/** G20 §12.1: kode PT yang diterima dari blok `channel` Odoo (sama dengan `COMPANY_CODES` loyalty). */
const COMPANY_CODE_SET: ReadonlySet<string> = new Set(COMPANY_CODES);
const CHANNEL_CACHE_MS = 30_000;
const STOCK_TTL_SECONDS = 30;

/** Cursor opak keyset `(sequence, product_id)` — base64url, divalidasi ketat saat dibaca. */
export function encodeCursor(seq: number, id: number): string {
  return Buffer.from(`${seq}:${id}`, 'utf8').toString('base64url');
}
export function decodeCursor(c: string): { seq: number; id: number } | null {
  if (!/^[A-Za-z0-9_-]{1,40}$/.test(c)) return null;
  const m = /^(-?\d{1,10}):(\d{1,10})$/.exec(Buffer.from(c, 'base64url').toString('utf8'));
  return m ? { seq: Number(m[1]), id: Number(m[2]) } : null;
}

/** Harga tampilan default (outlet `null`); harga tagihan SELALU dari quote Odoo (§10.3.2). */
export function defaultPrice(prices: readonly CatalogPrice[]): CatalogPrice | null {
  return prices.find((p) => p.outlet_code === null) ?? prices[0] ?? null;
}

/**
 * Katalog publik dari salinan BFF (docs/04 §10.3.2, §10.8.2). Tidak memanggil Odoo kecuali stok live (cache Redis
 * 30 s). Tidak pernah ada URL Odoo di respons; gambar dilayani BFF dari bucket `catalog` hanya untuk hash yang
 * dirujuk item aktif dan sudah di-re-encode.
 */
@Injectable()
export class CatalogService {
  private readonly logger = new Logger(CatalogService.name);
  private channelCache: { at: number; items: ChannelInfo[] } | null = null;
  now: () => number = () => Date.now();

  constructor(
    private readonly config: AppConfig,
    @Inject(DB) private readonly db: Db,
    @Inject(KV_STORE) private readonly kv: KvStore,
    @Inject(ODOO_ONLINE) private readonly odoo: OdooOnlineApi,
    @Inject(CATALOG_STORAGE) private readonly storage: CatalogStorage,
    private readonly metrics: OnlineMetrics,
  ) {}

  invalidateChannels(): void {
    this.channelCache = null;
  }

  /**
   * Kanal aktif yang juga ada di `ONLINE_CHANNELS`, fulfilment dipotong `ONLINE_FULFILMENTS`. `brand` (sudah
   * divalidasi controller, §11.3 no. 3) menyaring kanal ber-`brand_code` sama; `undefined`/`null` = semua kanal.
   */
  async channels(brand?: string | null): Promise<ChannelInfo[]> {
    const all = await this.allChannels();
    return brand === undefined || brand === null ? all : all.filter((c) => c.brand_code === brand);
  }

  private async allChannels(): Promise<ChannelInfo[]> {
    if (this.channelCache && this.now() - this.channelCache.at < CHANNEL_CACHE_MS) return this.channelCache.items;
    const allowed = this.config.onlineChannels;
    const res = await this.db.query<{ channel_code: string; channel_data: CatalogChannelInfo | null; last_ok_at: Date | null }>(
      `SELECT channel_code, channel_data, last_ok_at FROM platform.online_catalog_sync WHERE channel_code = ANY($1::text[]) ORDER BY channel_code`,
      [allowed],
    );
    const fulfil = new Set(this.config.onlineFulfilments);
    const items: ChannelInfo[] = [];
    for (const row of res.rows) {
      if (!row.last_ok_at) continue;
      // L-11: tanpa blok `channel` dari Odoo kanal TIDAK ditampilkan (fail-closed; tidak menebak PT/outlet)
      const info = row.channel_data ? this.fromOdoo(row.channel_code, row.channel_data) : null;
      if (!info) {
        this.logger.warn(`Kanal ${row.channel_code} disembunyikan: blok channel Odoo tidak ada/tidak valid (L-11)`);
        continue;
      }
      info.fulfilment = { pickup: info.fulfilment.pickup && fulfil.has('pickup'), delivery: info.fulfilment.delivery && fulfil.has('delivery') };
      items.push(info);
    }
    this.channelCache = { at: this.now(), items };
    return items;
  }

  private fromOdoo(code: string, c: CatalogChannelInfo): ChannelInfo | null {
    if (c.code !== code || !c.active || !COMPANY_CODE_SET.has(c.company_code) || c.currency !== 'IDR') return null;
    // `channel_data` hasil sinkron sebelum G19 tidak memuat brand_code → null (kanal tetap tampil tanpa brand)
    const brand: unknown = (c as { brand_code?: unknown }).brand_code;
    // L-52: payment_modes dari Odoo bila ada (juga salinan lama tanpa field → config BFF); divalidasi ulang (jsonb)
    let modes: ChannelPaymentMode[] | null = null;
    try {
      modes = parsePaymentModes((c as { payment_modes?: unknown }).payment_modes);
    } catch {
      modes = null;
    }
    return {
      channel_code: c.code,
      company_code: c.company_code,
      brand_code: typeof brand === 'string' && BRAND_CODE_RE.test(brand) ? brand : null,
      name: c.name,
      fulfilment: { ...c.fulfilment },
      outlets: c.outlets
        .filter((o) => o.active)
        .map((o) => ({ outlet_code: o.outlet_code, public_name: o.public_name, pickup_enabled: o.pickup_enabled, prep_minutes: o.prep_minutes })),
      // L-55: yang ditampilkan hanya mode yang juga diizinkan BFF (`ONLINE_PAYMENT_METHODS`)
      payment_modes: (modes ?? this.config.onlinePaymentMethods).filter((m) => this.config.onlinePaymentMethods.includes(m)),
      business_type: (c as { business_type?: unknown }).business_type === 'fnb' ? 'fnb' : 'retail',
      loyalty: {
        earn: (c as { loyalty?: { earn?: unknown } }).loyalty?.earn === true && this.config.onlineLoyaltyEarnEnabled,
        redeem: (c as { loyalty?: { redeem?: unknown } }).loyalty?.redeem === true && this.config.onlineLoyaltyRedeemEnabled,
      },
    };
  }

  async channel(code: string): Promise<ChannelInfo | null> {
    return (await this.allChannels()).find((c) => c.channel_code === code) ?? null;
  }

  private async requireChannel(code: string): Promise<ChannelInfo> {
    const c = await this.channel(code);
    if (!c) throw Errors.notFound();
    return c;
  }

  async categories(channel: string): Promise<{ items: { category_id: number; name: string; parent_id: number | null; sequence: number; image_sha: null }[] }> {
    await this.requireChannel(channel);
    const res = await this.db.query<{ category_id: number; data: { name: string; parent_id: number | null; sequence: number } }>(
      `SELECT category_id, data FROM platform.online_catalog_category
        WHERE channel_code = $1 AND deleted_at IS NULL ORDER BY (data->>'sequence')::int NULLS LAST, category_id LIMIT 500`,
      [channel],
    );
    return {
      items: res.rows.map((r) => ({
        category_id: Number(r.category_id),
        name: String(r.data.name),
        parent_id: r.data.parent_id ?? null,
        sequence: Number(r.data.sequence ?? 0),
        image_sha: null,
      })),
    };
  }

  async products(
    channel: string,
    opts: { category: number | null; q: string | null; cursor: string | null; limit: number },
  ): Promise<{ items: ProductSummary[]; next_cursor: string | null; catalog_synced_at: string | null }> {
    await this.requireChannel(channel);
    const params: unknown[] = [channel];
    const where = ['channel_code = $1', 'deleted_at IS NULL'];
    if (opts.category !== null) {
      params.push(opts.category);
      where.push(`category_id = $${params.length}`);
    }
    if (opts.q) {
      params.push(`%${opts.q.toLowerCase().replace(/[\\%_]/g, (m) => `\\${m}`)}%`);
      where.push(`search_text LIKE $${params.length}`);
    }
    if (opts.cursor) {
      const c = decodeCursor(opts.cursor);
      if (!c) throw Errors.invalidRequest();
      params.push(c.seq, c.id);
      where.push(`(sequence, product_id) > ($${params.length - 1}, $${params.length})`);
    }
    params.push(opts.limit + 1);
    const res = await this.db.query<ItemRow>(
      `SELECT product_id, data, image_sha, image_ready_sha, category_id, sequence FROM platform.online_catalog_item
        WHERE ${where.join(' AND ')} ORDER BY sequence, product_id LIMIT $${params.length}`,
      params,
    );
    const rows = res.rows.slice(0, opts.limit);
    const last = rows[rows.length - 1];
    const sync = await this.db.query<{ last_ok_at: Date | string | null }>(
      `SELECT last_ok_at FROM platform.online_catalog_sync WHERE channel_code = $1`,
      [channel],
    );
    const at = sync.rows[0]?.last_ok_at;
    return {
      items: rows.map(toSummary),
      next_cursor: res.rows.length > opts.limit && last ? encodeCursor(Number(last.sequence), Number(last.product_id)) : null,
      catalog_synced_at: at ? new Date(at).toISOString() : null,
    };
  }

  async product(channel: string, productId: number): Promise<ProductSummary & { description: string }> {
    await this.requireChannel(channel);
    const res = await this.db.query<ItemRow>(
      `SELECT product_id, data, image_sha, image_ready_sha, category_id, sequence FROM platform.online_catalog_item
        WHERE channel_code = $1 AND product_id = $2 AND deleted_at IS NULL`,
      [channel, productId],
    );
    const row = res.rows[0];
    if (!row) throw Errors.notFound();
    return { ...toSummary(row), description: String(row.data.description ?? '').slice(0, 2000) };
  }

  /** Produk aktif kanal (validasi cart & nama baris quote). */
  async productsByIds(channel: string, ids: readonly number[]): Promise<Map<number, ProductSummary>> {
    if (ids.length === 0) return new Map();
    const res = await this.db.query<ItemRow>(
      `SELECT product_id, data, image_sha, image_ready_sha, category_id, sequence FROM platform.online_catalog_item
        WHERE channel_code = $1 AND product_id = ANY($2::int[]) AND deleted_at IS NULL`,
      [channel, [...ids]],
    );
    return new Map(res.rows.map((r) => [Number(r.product_id), toSummary(r)]));
  }

  /** Gambar: hanya hash yang dirujuk item aktif dan sudah tersimpan (selain itu 404). */
  async image(sha: string, size: 512 | 1024): Promise<Buffer> {
    if (!IMAGE_SHA_RE.test(sha)) throw Errors.notFound();
    const res = await this.db.query<{ channel_code: string }>(
      `SELECT channel_code FROM platform.online_catalog_item
        WHERE image_sha = $1 AND image_ready_sha = $1 AND deleted_at IS NULL AND channel_code = ANY($2::text[]) LIMIT 1`,
      [sha, this.config.catalogSyncChannels], // G22: gambar menu QR lewat endpoint yang sama
    );
    const ch = res.rows[0]?.channel_code;
    if (!ch) throw Errors.notFound();
    let data: Buffer | null;
    try {
      data = await this.storage.get(catalogImageKey(ch, sha, size));
    } catch (err) {
      this.logger.warn(`Gambar katalog gagal dibaca: ${err instanceof Error ? err.message : 'storage'}`);
      throw Errors.serviceUnavailable();
    }
    if (!data) throw Errors.notFound();
    return data;
  }

  /** Stok live per outlet (`efn_online_stock`), cache Redis `ocat:stk:<channel>:<outlet>:<product_id>` 30 s. */
  async stock(channel: string, outlet: string, ids: number[]): Promise<{ items: { product_id: number; status: StockStatus }[]; checked_at: string }> {
    const info = await this.requireChannel(channel);
    if (!info.outlets.some((o) => o.outlet_code === outlet)) throw Errors.notFound();
    const uniq = [...new Set(ids)];
    // SEC-96: hanya produk aktif katalog kanal; id lain → 404 TANPA memanggil Odoo
    const known = await this.productsByIds(channel, uniq);
    if (uniq.some((id) => !known.has(id))) throw Errors.notFound();
    const out = new Map<number, StockStatus>();
    const missing: number[] = [];
    for (const id of uniq) {
      const v = await this.kv.get(`ocat:stk:${channel}:${outlet}:${id}`);
      if (v === 'in_stock' || v === 'low' || v === 'out') out.set(id, v);
      else if (v !== 'none') missing.push(id); // 'none' = cache negatif (Odoo tidak mengembalikan status)
    }
    if (missing.length > 0) {
      if (this.odoo.isCircuitOpen(channel)) throw OnlineErrors.unavailable();
      let rows;
      try {
        rows = await this.odoo.stock(channel, outlet, missing);
      } catch (err) {
        throw mapOdooError(err, this.metrics, { method: 'stock', channel });
      }
      for (const r of rows) {
        if (!missing.includes(r.product_id)) continue;
        out.set(r.product_id, r.status);
        await this.kv.setEx(`ocat:stk:${channel}:${outlet}:${r.product_id}`, r.status, STOCK_TTL_SECONDS);
      }
      for (const id of missing) {
        if (!out.has(id)) await this.kv.setEx(`ocat:stk:${channel}:${outlet}:${id}`, 'none', STOCK_TTL_SECONDS);
      }
    }
    return {
      items: uniq.filter((id) => out.has(id)).map((id) => ({ product_id: id, status: out.get(id) as StockStatus })),
      checked_at: new Date(this.now()).toISOString(),
    };
  }
}

interface ItemRow {
  product_id: number;
  data: ItemData & { description?: string };
  image_sha: string | null;
  image_ready_sha: string | null;
  category_id: number | null;
  sequence: number;
}

function toSummary(r: ItemRow): ProductSummary {
  const price = defaultPrice(r.data.prices ?? []);
  return {
    product_id: Number(r.product_id),
    name: String(r.data.name),
    uom: String(r.data.uom ?? ''),
    category_id: r.category_id === null ? null : Number(r.category_id),
    image_sha: r.image_sha !== null && r.image_sha === r.image_ready_sha ? r.image_sha : null,
    price_incl: price?.price_incl ?? 0,
    price_excl: price?.price_excl ?? 0,
    tax_labels: price?.tax_labels ?? [],
    max_qty: Number(r.data.max_qty ?? 99),
    weight_kg: typeof r.data.weight_kg === 'number' && Number.isFinite(r.data.weight_kg) ? r.data.weight_kg : null,
    attributes: Array.isArray(r.data.attributes) ? r.data.attributes : [],
  };
}
