import { Inject, Injectable, Logger, OnModuleDestroy, OnModuleInit } from '@nestjs/common';
import { AppConfig } from '../config/app-config';
import { CatalogService } from '../catalog/catalog.service';
import { DB, Db } from '../common/db/db';
import { OutboxService } from '../common/outbox/outbox.service';
import { KV_STORE, KvStore } from '../common/redis/kv-store';
import type { StockStatus } from '../odoo-rpc/odoo-online.types';
import { NOTIFY_PRICE_ALERT_QUEUE, NOTIFY_RESTOCK_QUEUE, PriceAlertMessage, RestockMessage, priceKey, restockKey } from './alert-message';

/** Prefix `ocat:` sudah ada di ACL Redis `efn-bff` (cache katalog). */
export const ALERT_WATCH_LOCK = 'ocat:alertwatch:lock';
const MAX_KEYS = 2000;
const STOCK_CHUNK = 50;

export interface WatchResult {
  restockChecked: number;
  restockPublished: number;
  priceChecked: number;
  pricePublished: number;
}

/**
 * Pendeteksi perubahan stok/harga untuk alert (publisher `notify.restock` / `notify.price_alert`, docs/04 §28.7).
 *
 * Kontrak menyebut publisher "BFF catalog-sync"; agar `src/catalog` tidak berubah, deteksi dilakukan di sini HANYA untuk
 * produk yang punya alert aktif (bukan seluruh katalog): stok lewat `CatalogService.stock` (cache Redis `ocat:stk:*`
 * 30 s, lalu Odoo), harga dari salinan katalog BFF (sinkron delta `CatalogSyncService`). State terakhir disimpan di
 * `platform.alert_stock_state` / `alert_price_state` (tanpa data member). Transisi `out → in_stock|low` menaikkan
 * `restock_seq`; penurunan harga menaikkan `price_version`; event ditulis lewat OUTBOX dalam transaksi yang sama dengan
 * state (konsistensi DB ↔ broker). Satu replika per siklus (lock Redis ber-TTL). Tidak pernah melempar.
 */
@Injectable()
export class AlertWatcherService implements OnModuleInit, OnModuleDestroy {
  private readonly logger = new Logger(AlertWatcherService.name);
  private timer: NodeJS.Timeout | null = null;
  private running = false;

  constructor(
    private readonly config: AppConfig,
    @Inject(DB) private readonly db: Db,
    private readonly catalog: CatalogService,
    private readonly outbox: OutboxService,
    @Inject(KV_STORE) private readonly kv: KvStore,
  ) {}

  onModuleInit(): void {
    if (!this.config.restockAlertEnabled && !this.config.priceAlertEnabled) return;
    this.timer = setInterval(() => void this.runOnce(), this.config.alertWatchIntervalSeconds * 1000);
    this.timer.unref();
  }

  onModuleDestroy(): void {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
  }

  async runOnce(): Promise<WatchResult> {
    const res: WatchResult = { restockChecked: 0, restockPublished: 0, priceChecked: 0, pricePublished: 0 };
    if (this.running) return res;
    this.running = true;
    try {
      // Lock lintas replika lewat Redis (advisory lock sesi PG tidak aman di pool: lock/unlock bisa beda koneksi).
      // TTL < interval → lock lepas sendiri bila replika mati di tengah siklus.
      const ttl = Math.max(30, this.config.alertWatchIntervalSeconds - 10);
      if (!(await this.kv.setNxEx(ALERT_WATCH_LOCK, '1', ttl))) return res;
      if (this.config.restockAlertEnabled) await this.watchRestock(res);
      if (this.config.priceAlertEnabled) await this.watchPrice(res);
      if (res.restockPublished + res.pricePublished > 0) {
        this.logger.log(`Alert watcher: ${res.restockPublished} restock, ${res.pricePublished} price alert diterbitkan`);
      }
    } catch (err) {
      this.logger.warn(`Alert watcher gagal: ${err instanceof Error ? err.message : 'db'}`);
    } finally {
      this.running = false;
    }
    return res;
  }

  private async watchRestock(res: WatchResult): Promise<void> {
    const rows = await this.db.query<{ channel_code: string; outlet_code: string; product_id: number }>(
      `SELECT DISTINCT channel_code, outlet_code, product_id FROM platform.restock_alert
        WHERE sent_at IS NULL AND expires_at > now() ORDER BY channel_code, outlet_code, product_id LIMIT $1`,
      [MAX_KEYS],
    );
    const groups = new Map<string, { ch: string; outlet: string; ids: number[] }>();
    for (const r of rows.rows) {
      const k = `${r.channel_code}\u0000${r.outlet_code}`;
      const g = groups.get(k) ?? { ch: r.channel_code, outlet: r.outlet_code, ids: [] };
      g.ids.push(Number(r.product_id));
      groups.set(k, g);
    }
    for (const g of groups.values()) {
      for (let i = 0; i < g.ids.length; i += STOCK_CHUNK) {
        const statuses = await this.stockOf(g.ch, g.outlet, g.ids.slice(i, i + STOCK_CHUNK));
        for (const [pid, status] of statuses) {
          res.restockChecked += 1;
          if (await this.applyStock(g.ch, g.outlet, pid, status)) res.restockPublished += 1;
        }
      }
    }
  }

  /** Status stok per produk; produk yang hilang dari katalog/Odoo gagal tidak menggagalkan produk lain. */
  private async stockOf(ch: string, outlet: string, ids: number[]): Promise<Map<number, StockStatus>> {
    const out = new Map<number, StockStatus>();
    try {
      const r = await this.catalog.stock(ch, outlet, ids);
      for (const it of r.items) out.set(it.product_id, it.status);
      return out;
    } catch {
      if (ids.length === 1) return out;
    }
    for (const id of ids) {
      try {
        const r = await this.catalog.stock(ch, outlet, [id]);
        for (const it of r.items) out.set(it.product_id, it.status);
      } catch {
        // produk terhapus dari katalog / Odoo tidak tersedia → dicoba siklus berikut
      }
    }
    return out;
  }

  private applyStock(ch: string, outlet: string, pid: number, status: StockStatus): Promise<boolean> {
    return this.db.tx(async (q) => {
      const cur = await q.query<{ status: StockStatus; restock_seq: number }>(
        `SELECT status, restock_seq FROM platform.alert_stock_state WHERE channel_code = $1 AND outlet_code = $2 AND product_id = $3 FOR UPDATE`,
        [ch, outlet, pid],
      );
      // Tanpa state: alert aktif dibuat saat produk `out` → anggap sebelumnya `out`
      const prev = cur.rows[0]?.status ?? 'out';
      const seq = Number(cur.rows[0]?.restock_seq ?? 0);
      const restocked = prev === 'out' && (status === 'in_stock' || status === 'low');
      const nextSeq = restocked ? seq + 1 : seq;
      await q.query(
        `INSERT INTO platform.alert_stock_state (channel_code, outlet_code, product_id, status, restock_seq) VALUES ($1, $2, $3, $4, $5)
         ON CONFLICT (channel_code, outlet_code, product_id) DO UPDATE SET status = EXCLUDED.status, restock_seq = EXCLUDED.restock_seq, updated_at = now()`,
        [ch, outlet, pid, status, nextSeq],
      );
      if (!restocked) return false;
      const key = restockKey(ch, outlet, pid, nextSeq);
      const msg: RestockMessage = {
        schema_version: 1,
        idempotency_key: key,
        channel_code: ch,
        outlet_code: outlet,
        product_id: pid,
        detected_at: new Date().toISOString(),
      };
      await this.outbox.enqueue(q, NOTIFY_RESTOCK_QUEUE, { ...msg }, key);
      return true;
    });
  }

  private async watchPrice(res: WatchResult): Promise<void> {
    const rows = await this.db.query<{ channel_code: string; product_id: number }>(
      `SELECT DISTINCT channel_code, product_id FROM platform.price_alert
        WHERE triggered_at IS NULL AND expires_at > now() ORDER BY channel_code, product_id LIMIT $1`,
      [MAX_KEYS],
    );
    const byChannel = new Map<string, number[]>();
    for (const r of rows.rows) byChannel.set(r.channel_code, [...(byChannel.get(r.channel_code) ?? []), Number(r.product_id)]);
    for (const [ch, ids] of byChannel) {
      let products: Map<number, { price_incl: number }>;
      try {
        products = await this.catalog.productsByIds(ch, ids);
      } catch {
        continue;
      }
      for (const [pid, p] of products) {
        res.priceChecked += 1;
        if (await this.applyPrice(ch, pid, Math.max(0, Math.round(p.price_incl)))) res.pricePublished += 1;
      }
    }
  }

  private applyPrice(ch: string, pid: number, price: number): Promise<boolean> {
    return this.db.tx(async (q) => {
      const cur = await q.query<{ price_idr: string | number; price_version: number }>(
        `SELECT price_idr, price_version FROM platform.alert_price_state WHERE channel_code = $1 AND product_id = $2 FOR UPDATE`,
        [ch, pid],
      );
      const row = cur.rows[0];
      const dropped = row !== undefined && price > 0 && price < Number(row.price_idr);
      const version = Number(row?.price_version ?? 0) + (dropped ? 1 : 0);
      await q.query(
        `INSERT INTO platform.alert_price_state (channel_code, product_id, price_idr, price_version) VALUES ($1, $2, $3, $4)
         ON CONFLICT (channel_code, product_id) DO UPDATE SET price_idr = EXCLUDED.price_idr, price_version = EXCLUDED.price_version, updated_at = now()`,
        [ch, pid, price, version],
      );
      if (!dropped) return false;
      const key = priceKey(ch, pid, version);
      const msg: PriceAlertMessage = { schema_version: 1, idempotency_key: key, channel_code: ch, product_id: pid, new_price_idr: price, detected_at: new Date().toISOString() };
      await this.outbox.enqueue(q, NOTIFY_PRICE_ALERT_QUEUE, { ...msg }, key);
      return true;
    });
  }
}
