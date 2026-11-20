import { Inject, Injectable } from '@nestjs/common';
import { randomUUID } from 'node:crypto';
import { AppConfig } from '../config/app-config';
import { CatalogService } from '../catalog/catalog.service';
import { DB, Db, Queryable } from '../common/db/db';
import { ApiError, Errors } from '../common/errors/api-error';
import { RateLimiter } from '../common/rate-limit/rate-limiter';
import { AuthUser } from '../common/security/access-token.service';
import { AlertChannel, PriceAlertInput, RestockAlertInput, WishlistErrors, WishlistItemInput } from './dto/wishlist.dto';

export const WISHLIST_MAX_ITEMS = 200;
export const ALERT_MAX_ACTIVE = 50;
export const RESTOCK_TTL_DAYS = 30;
export const PRICE_ALERT_TTL_DAYS = 90;
/** Tulis per member per menit (anti penyalahgunaan; baca tidak dibatasi). */
const WRITE_PER_MIN = 60;

export interface WishlistView {
  items: { channel_code: string; product_id: number; added_at: string }[];
}
export interface RestockAlertView {
  id: string;
  channel_code: string;
  outlet_code: string;
  product_id: number;
  channels: AlertChannel[];
  created_at: string;
  expires_at: string;
}
export interface PriceAlertView {
  id: string;
  channel_code: string;
  product_id: number;
  target_price_idr: number;
  channels: AlertChannel[];
  created_at: string;
  expires_at: string;
}

const iso = (v: Date | string): string => (v instanceof Date ? v : new Date(v)).toISOString();

/**
 * Wishlist, notify-me (stok kembali), price alert (docs/04 §28.8, L-161). Data di PG platform (bukan Odoo), FK
 * `member_id` tanpa PII lain; ikut DSAR export & erasure (`wishlist-privacy.ts`). Fitur = pelaksanaan permintaan member
 * (tidak butuh consent `tracking`). Flag `WISHLIST_ENABLED`/`RESTOCK_ALERT_ENABLED`/`PRICE_ALERT_ENABLED` default false
 * → endpoint 404 (fitur disembunyikan). Kanal `email`/`wa_unofficial` hanya bisa dipilih bila flag kanal aktif;
 * consent `marketing` + kontak terverifikasi diperiksa notifier BFF-B saat kirim (Q-SM25 default).
 */
@Injectable()
export class WishlistService {
  constructor(
    private readonly config: AppConfig,
    @Inject(DB) private readonly db: Db,
    private readonly catalog: CatalogService,
    private readonly limiter: RateLimiter,
  ) {}

  private async writeLimit(user: AuthUser): Promise<void> {
    await this.limiter.enforce(`wl:member:${user.memberHash}`, WRITE_PER_MIN, 60);
  }

  private static lock(q: Queryable, memberId: string): Promise<unknown> {
    return q.query(`SELECT pg_advisory_xact_lock(hashtext('efn:wishlist:' || $1::text))`, [memberId]);
  }

  // ─────────────── wishlist ───────────────
  async list(user: AuthUser, channel: string | null): Promise<WishlistView> {
    if (!this.config.wishlistEnabled) throw Errors.notFound();
    const res = await this.db.query<{ channel_code: string; product_id: number; added_at: Date | string }>(
      `SELECT channel_code, product_id, added_at FROM platform.member_wishlist
        WHERE member_id = $1 AND ($2::text IS NULL OR channel_code = $2) ORDER BY added_at DESC LIMIT $3`,
      [user.memberId, channel, WISHLIST_MAX_ITEMS],
    );
    return { items: res.rows.map((r) => ({ channel_code: r.channel_code, product_id: Number(r.product_id), added_at: iso(r.added_at) })) };
  }

  async put(user: AuthUser, input: WishlistItemInput): Promise<void> {
    if (!this.config.wishlistEnabled) throw Errors.notFound();
    await this.writeLimit(user);
    const known = await this.catalog.productsByIds(input.channel_code, [input.product_id]);
    if (!known.has(input.product_id)) throw Errors.notFound();
    await this.db.tx(async (q) => {
      await WishlistService.lock(q, user.memberId);
      const exists = await q.query(
        `SELECT 1 FROM platform.member_wishlist WHERE member_id = $1 AND channel_code = $2 AND product_id = $3`,
        [user.memberId, input.channel_code, input.product_id],
      );
      if ((exists.rowCount ?? 0) > 0) return; // idempoten
      const n = await q.query<{ n: string | number }>(`SELECT count(*) AS n FROM platform.member_wishlist WHERE member_id = $1`, [user.memberId]);
      if (Number(n.rows[0]?.n ?? 0) >= WISHLIST_MAX_ITEMS) throw WishlistErrors.full();
      await q.query(
        `INSERT INTO platform.member_wishlist (member_id, channel_code, product_id) VALUES ($1, $2, $3) ON CONFLICT DO NOTHING`,
        [user.memberId, input.channel_code, input.product_id],
      );
    });
  }

  async remove(user: AuthUser, channel: string, productId: number): Promise<void> {
    if (!this.config.wishlistEnabled) throw Errors.notFound();
    await this.writeLimit(user);
    await this.db.query(`DELETE FROM platform.member_wishlist WHERE member_id = $1 AND channel_code = $2 AND product_id = $3`, [
      user.memberId,
      channel,
      productId,
    ]);
  }

  private assertChannels(channels: readonly AlertChannel[]): void {
    for (const c of channels) {
      if (c === 'email' && !this.config.emailEnabled) throw WishlistErrors.channelUnavailable();
      if (c === 'wa_unofficial' && !this.config.waUnofficialEnabled) throw WishlistErrors.channelUnavailable();
    }
  }

  // ─────────────── restock alert ───────────────
  async listRestock(user: AuthUser): Promise<{ items: RestockAlertView[] }> {
    if (!this.config.restockAlertEnabled) throw Errors.notFound();
    const res = await this.db.query<{
      id: string;
      channel_code: string;
      outlet_code: string;
      product_id: number;
      channels: string[];
      created_at: Date | string;
      expires_at: Date | string;
    }>(
      `SELECT id::text AS id, channel_code, outlet_code, product_id, channels, created_at, expires_at FROM platform.restock_alert
        WHERE member_id = $1 AND sent_at IS NULL AND expires_at > now() ORDER BY created_at DESC LIMIT $2`,
      [user.memberId, ALERT_MAX_ACTIVE],
    );
    return {
      items: res.rows.map((r) => ({
        id: r.id,
        channel_code: r.channel_code,
        outlet_code: r.outlet_code,
        product_id: Number(r.product_id),
        channels: r.channels as AlertChannel[],
        created_at: iso(r.created_at),
        expires_at: iso(r.expires_at),
      })),
    };
  }

  async createRestock(user: AuthUser, input: RestockAlertInput): Promise<{ id: string }> {
    if (!this.config.restockAlertEnabled) throw Errors.notFound();
    this.assertChannels(input.channels);
    await this.writeLimit(user);
    // §28.8: produk harus `out` di outlet itu. Stok live (cache 30 s) — produk/outlet tidak dikenal → 404.
    const stock = await this.catalog.stock(input.channel_code, input.outlet_code, [input.product_id]);
    const status = stock.items.find((i) => i.product_id === input.product_id)?.status;
    if (status === 'in_stock' || status === 'low') throw WishlistErrors.alreadyInStock();
    if (status !== 'out') throw Errors.notFound();
    return this.db.tx(async (q) => {
      await WishlistService.lock(q, user.memberId);
      await q.query(`DELETE FROM platform.restock_alert WHERE member_id = $1 AND sent_at IS NULL AND expires_at <= now()`, [user.memberId]);
      const existing = await q.query<{ id: string }>(
        `SELECT id::text AS id FROM platform.restock_alert
          WHERE member_id = $1 AND channel_code = $2 AND outlet_code = $3 AND product_id = $4 AND sent_at IS NULL`,
        [user.memberId, input.channel_code, input.outlet_code, input.product_id],
      );
      if (existing.rows[0]) {
        await q.query(`UPDATE platform.restock_alert SET channels = $2::text[] WHERE id = $1`, [existing.rows[0].id, input.channels]);
        return { id: existing.rows[0].id };
      }
      const n = await q.query<{ n: string | number }>(
        `SELECT count(*) AS n FROM platform.restock_alert WHERE member_id = $1 AND sent_at IS NULL`,
        [user.memberId],
      );
      if (Number(n.rows[0]?.n ?? 0) >= ALERT_MAX_ACTIVE) throw WishlistErrors.alertLimit();
      const id = randomUUID();
      await q.query(
        `INSERT INTO platform.restock_alert (id, member_id, channel_code, outlet_code, product_id, channels, expires_at)
         VALUES ($1, $2, $3, $4, $5, $6::text[], now() + make_interval(days => $7::int))`,
        [id, user.memberId, input.channel_code, input.outlet_code, input.product_id, input.channels, RESTOCK_TTL_DAYS],
      );
      // Status `out` teramati sekarang → transisi berikutnya ke in_stock/low memicu `notify.restock`
      await q.query(
        `INSERT INTO platform.alert_stock_state (channel_code, outlet_code, product_id, status) VALUES ($1, $2, $3, 'out')
         ON CONFLICT (channel_code, outlet_code, product_id) DO UPDATE SET status = 'out', updated_at = now()`,
        [input.channel_code, input.outlet_code, input.product_id],
      );
      return { id };
    });
  }

  async deleteRestock(user: AuthUser, id: string): Promise<void> {
    if (!this.config.restockAlertEnabled) throw Errors.notFound();
    await this.writeLimit(user);
    const res = await this.db.query(`DELETE FROM platform.restock_alert WHERE id = $1 AND member_id = $2`, [id, user.memberId]);
    if ((res.rowCount ?? 0) === 0) throw Errors.notFound();
  }

  // ─────────────── price alert ───────────────
  async listPrice(user: AuthUser): Promise<{ items: PriceAlertView[] }> {
    if (!this.config.priceAlertEnabled) throw Errors.notFound();
    const res = await this.db.query<{
      id: string;
      channel_code: string;
      product_id: number;
      target_price_idr: string | number;
      channels: string[];
      created_at: Date | string;
      expires_at: Date | string;
    }>(
      `SELECT id::text AS id, channel_code, product_id, target_price_idr, channels, created_at, expires_at FROM platform.price_alert
        WHERE member_id = $1 AND triggered_at IS NULL AND expires_at > now() ORDER BY created_at DESC LIMIT $2`,
      [user.memberId, ALERT_MAX_ACTIVE],
    );
    return {
      items: res.rows.map((r) => ({
        id: r.id,
        channel_code: r.channel_code,
        product_id: Number(r.product_id),
        target_price_idr: Number(r.target_price_idr),
        channels: r.channels as AlertChannel[],
        created_at: iso(r.created_at),
        expires_at: iso(r.expires_at),
      })),
    };
  }

  async createPrice(user: AuthUser, input: PriceAlertInput): Promise<{ id: string }> {
    if (!this.config.priceAlertEnabled) throw Errors.notFound();
    this.assertChannels(input.channels);
    await this.writeLimit(user);
    let price: number;
    try {
      price = Math.round((await this.catalog.product(input.channel_code, input.product_id)).price_incl);
    } catch (err) {
      if (err instanceof ApiError) throw err;
      throw Errors.serviceUnavailable();
    }
    // §28.8: integer > 0 dan < harga saat ini
    if (!(price > 0) || input.target_price_idr >= price) throw WishlistErrors.targetNotBelowPrice();
    return this.db.tx(async (q) => {
      await WishlistService.lock(q, user.memberId);
      await q.query(`DELETE FROM platform.price_alert WHERE member_id = $1 AND triggered_at IS NULL AND expires_at <= now()`, [user.memberId]);
      const existing = await q.query<{ id: string }>(
        `SELECT id::text AS id FROM platform.price_alert WHERE member_id = $1 AND channel_code = $2 AND product_id = $3 AND triggered_at IS NULL`,
        [user.memberId, input.channel_code, input.product_id],
      );
      let id: string;
      if (existing.rows[0]) {
        id = existing.rows[0].id;
        await q.query(
          `UPDATE platform.price_alert SET target_price_idr = $2, channels = $4::text[], expires_at = now() + make_interval(days => $3::int)
            WHERE id = $1`,
          [id, input.target_price_idr, PRICE_ALERT_TTL_DAYS, input.channels],
        );
      } else {
        const n = await q.query<{ n: string | number }>(
          `SELECT count(*) AS n FROM platform.price_alert WHERE member_id = $1 AND triggered_at IS NULL`,
          [user.memberId],
        );
        if (Number(n.rows[0]?.n ?? 0) >= ALERT_MAX_ACTIVE) throw WishlistErrors.alertLimit();
        id = randomUUID();
        await q.query(
          `INSERT INTO platform.price_alert (id, member_id, channel_code, product_id, target_price_idr, channels, expires_at)
           VALUES ($1, $2, $3, $4, $5, $7::text[], now() + make_interval(days => $6::int))`,
          [id, user.memberId, input.channel_code, input.product_id, input.target_price_idr, PRICE_ALERT_TTL_DAYS, input.channels],
        );
      }
      // Harga teramati sekarang = dasar deteksi penurunan berikutnya (tidak menimpa state yang sudah ada)
      await q.query(
        `INSERT INTO platform.alert_price_state (channel_code, product_id, price_idr) VALUES ($1, $2, $3)
         ON CONFLICT (channel_code, product_id) DO NOTHING`,
        [input.channel_code, input.product_id, price],
      );
      return { id };
    });
  }

  async deletePrice(user: AuthUser, id: string): Promise<void> {
    if (!this.config.priceAlertEnabled) throw Errors.notFound();
    await this.writeLimit(user);
    const res = await this.db.query(`DELETE FROM platform.price_alert WHERE id = $1 AND member_id = $2`, [id, user.memberId]);
    if ((res.rowCount ?? 0) === 0) throw Errors.notFound();
  }
}
