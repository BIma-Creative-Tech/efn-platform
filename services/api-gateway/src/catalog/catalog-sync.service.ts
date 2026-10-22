import { Inject, Injectable, Logger, OnModuleDestroy, OnModuleInit } from '@nestjs/common';
import { AppConfig } from '../config/app-config';
import { DB, Db, Queryable } from '../common/db/db';
import { CatalogItem, ODOO_ONLINE, OdooOnlineApi, OdooRpcError } from '../odoo-rpc/odoo-online.types';
import { OnlineMetrics } from '../odoo-rpc/online-metrics';
import { CATALOG_STORAGE, CatalogImageRejected, CatalogStorage, catalogImageKey, normalizeCatalogImage } from './catalog-storage';
import { CatalogService, ItemData } from './catalog.service';

export const CATALOG_SYNC_LOCK_KEY = 0x45464e43; // 'EFNC'
const PAGE_LIMIT = 200;
const MAX_PAGES = 200;
const IMAGE_BATCH = 25;
const PURGE_DAYS = 30;

export interface SyncResult {
  channel: string;
  upserted: number;
  deleted: number;
  images: number;
  ok: boolean;
  skipped?: boolean;
}

/**
 * Job pull delta katalog (docs/04 §10.8.2, ADR-034 keputusan 3): setiap `ONLINE_CATALOG_SYNC_SECONDS` per kanal
 * `ONLINE_CHANNELS` → `efn_online_catalog(since, cursor)` sampai `next_cursor=null`; `deleted` → `deleted_at`;
 * gambar baru → `efn_online_image` (512 & 1024) → re-encode sharp → bucket `catalog`. Satu replika per kanal per siklus
 * (advisory lock). Gagal → `last_error_code`, katalog lama tetap dilayani. Item terhapus > 30 hari dibersihkan
 * beserta gambarnya.
 */
@Injectable()
export class CatalogSyncService implements OnModuleInit, OnModuleDestroy {
  private readonly logger = new Logger(CatalogSyncService.name);
  private timer: NodeJS.Timeout | null = null;
  private running = false;
  now: () => number = () => Date.now();

  constructor(
    private readonly config: AppConfig,
    @Inject(DB) private readonly db: Db,
    @Inject(ODOO_ONLINE) private readonly odoo: OdooOnlineApi,
    @Inject(CATALOG_STORAGE) private readonly storage: CatalogStorage,
    private readonly catalog: CatalogService,
    private readonly metrics: OnlineMetrics,
  ) {}

  onModuleInit(): void {
    const s = this.config.onlineCatalogSyncSeconds;
    if (s > 0 && (this.config.onlineCatalogEnabled || this.config.onlineEnabled || this.config.fnbQrEnabled)) {
      this.timer = setInterval(() => void this.runOnce(), s * 1000);
      this.timer.unref();
      setTimeout(() => void this.runOnce(), 5_000).unref();
    }
  }

  onModuleDestroy(): void {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
  }

  /** Tidak pernah melempar. */
  async runOnce(): Promise<SyncResult[]> {
    if (this.running) return [];
    this.running = true;
    const out: SyncResult[] = [];
    try {
      // G22 §14.A.6: + kanal PT QR meja (salinan katalog untuk nama/gambar menu QR)
      for (const ch of this.config.catalogSyncChannels) out.push(await this.syncChannel(ch));
      this.catalog.invalidateChannels();
      await this.purge().catch((err: unknown) => this.logger.warn(`Purge katalog gagal: ${err instanceof Error ? err.message : 'db'}`));
    } finally {
      this.running = false;
    }
    return out;
  }

  async syncChannel(channel: string): Promise<SyncResult> {
    const res: SyncResult = { channel, upserted: 0, deleted: 0, images: 0, ok: false };
    try {
      const done = await this.db.tx(async (q) => {
        const lock = await q.query<{ ok: boolean }>('SELECT pg_try_advisory_xact_lock($1, hashtext($2)) AS ok', [CATALOG_SYNC_LOCK_KEY, channel]);
        if (!lock.rows[0]?.ok) return false;
        await q.query(
          `INSERT INTO platform.online_catalog_sync (channel_code) VALUES ($1) ON CONFLICT (channel_code) DO NOTHING`,
          [channel],
        );
        const st = await q.query<{ since: string | null; cursor: string | null }>(
          `SELECT since, cursor FROM platform.online_catalog_sync WHERE channel_code = $1 FOR UPDATE`,
          [channel],
        );
        const since = st.rows[0]?.since ?? null;
        let cursor = st.rows[0]?.cursor ?? null;
        let nextSince: string | null = null;
        for (let page = 0; page < MAX_PAGES; page += 1) {
          const p = await this.odoo.catalog(channel, since, cursor, PAGE_LIMIT);
          if (nextSince === null) nextSince = p.server_time;
          for (const item of p.items) {
            await this.upsertItem(q, channel, item);
            res.upserted += 1;
          }
          if (p.deleted.length > 0) {
            const del = await q.query(
              `UPDATE platform.online_catalog_item SET deleted_at = now(), updated_at = now()
                WHERE channel_code = $1 AND product_id = ANY($2::int[]) AND deleted_at IS NULL`,
              [channel, p.deleted],
            );
            res.deleted += del.rowCount ?? 0;
          }
          if (p.channel) {
            await q.query(`UPDATE platform.online_catalog_sync SET channel_data = $2::jsonb WHERE channel_code = $1`, [channel, JSON.stringify(p.channel)]);
          }
          cursor = p.next_cursor;
          if (!cursor) break;
        }
        await q.query(
          `UPDATE platform.online_catalog_sync
              SET since = $2, cursor = NULL, last_ok_at = now(), last_attempt_at = now(), last_error_code = NULL
            WHERE channel_code = $1`,
          [channel, nextSince ?? since],
        );
        return true;
      });
      if (!done) return { ...res, skipped: true };
      res.ok = true;
      res.images = await this.syncImages(channel);
      this.metrics.setCatalogAge(channel, 0);
      this.logger.log(`Katalog ${channel}: ${res.upserted} item diperbarui, ${res.deleted} dihapus, ${res.images} gambar`);
    } catch (err) {
      const code = err instanceof OdooRpcError ? `odoo_${err.kind}` : 'db_error';
      this.logger.warn(`Sinkron katalog ${channel} gagal: ${code}`);
      await this.db
        .query(
          `INSERT INTO platform.online_catalog_sync (channel_code, last_attempt_at, last_error_code) VALUES ($1, now(), $2)
           ON CONFLICT (channel_code) DO UPDATE SET last_attempt_at = now(), last_error_code = $2`,
          [channel, code],
        )
        .catch(() => undefined);
      await this.updateAge(channel);
    }
    return res;
  }

  private async updateAge(channel: string): Promise<void> {
    const r = await this.db
      .query<{ last_ok_at: Date | string | null }>(`SELECT last_ok_at FROM platform.online_catalog_sync WHERE channel_code = $1`, [channel])
      .catch(() => ({ rows: [] as { last_ok_at: Date | string | null }[] }));
    const at = r.rows[0]?.last_ok_at;
    this.metrics.setCatalogAge(channel, at ? (this.now() - new Date(at).getTime()) / 1000 : 1e9);
  }

  private async upsertItem(q: Queryable, channel: string, item: CatalogItem): Promise<void> {
    const data: ItemData & { description: string } = {
      name: item.name,
      description: item.description,
      uom: item.uom,
      prices: item.prices,
      max_qty: item.max_qty,
      weight_kg: item.weight_kg, // G20 §12.3 (nullable)
      attributes: item.attributes ?? [], // G22 B-2
    };
    const search = `${item.name} ${item.categ?.name ?? ''}`.toLowerCase().slice(0, 512);
    await q.query(
      `INSERT INTO platform.online_catalog_item (channel_code, product_id, tmpl_id, data, image_sha, category_id, sequence, search_text, updated_at, deleted_at)
       VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7, $8, now(), NULL)
       ON CONFLICT (channel_code, product_id) DO UPDATE
         SET tmpl_id = EXCLUDED.tmpl_id, data = EXCLUDED.data, image_sha = EXCLUDED.image_sha, category_id = EXCLUDED.category_id,
             sequence = EXCLUDED.sequence, search_text = EXCLUDED.search_text, updated_at = now(), deleted_at = NULL`,
      [channel, item.product_id, item.tmpl_id, JSON.stringify(data), item.image_sha, item.categ?.id ?? null, item.sequence, search],
    );
    if (item.categ) {
      await q.query(
        `INSERT INTO platform.online_catalog_category (channel_code, category_id, data, updated_at, deleted_at)
         VALUES ($1, $2, $3::jsonb, now(), NULL)
         ON CONFLICT (channel_code, category_id) DO UPDATE SET data = EXCLUDED.data, updated_at = now(), deleted_at = NULL`,
        [channel, item.categ.id, JSON.stringify({ name: item.categ.name, parent_id: item.categ.parent_id, sequence: item.categ.sequence })],
      );
    }
  }

  /** Gambar baru/berubah → tarik 512 & 1024, re-encode, simpan; gagal → dicoba siklus berikut. */
  async syncImages(channel: string): Promise<number> {
    const res = await this.db.query<{ product_id: number; tmpl_id: number; image_sha: string; image_ready_sha: string | null }>(
      `SELECT product_id, tmpl_id, image_sha, image_ready_sha FROM platform.online_catalog_item
        WHERE channel_code = $1 AND deleted_at IS NULL AND image_sha IS NOT NULL AND image_ready_sha IS DISTINCT FROM image_sha
        LIMIT $2`,
      [channel, IMAGE_BATCH],
    );
    let n = 0;
    for (const r of res.rows) {
      try {
        for (const size of [512, 1024] as const) {
          const img = await this.odoo.image(channel, Number(r.tmpl_id), size);
          const jpeg = await normalizeCatalogImage(img.data, size);
          await this.storage.put(catalogImageKey(channel, r.image_sha, size), jpeg);
        }
        await this.db.query(
          `UPDATE platform.online_catalog_item SET image_ready_sha = $3 WHERE channel_code = $1 AND product_id = $2 AND image_sha = $3`,
          [channel, r.product_id, r.image_sha],
        );
        n += 1;
      } catch (err) {
        const why = err instanceof CatalogImageRejected ? 'rejected' : err instanceof OdooRpcError ? `odoo_${err.kind}` : 'storage';
        this.logger.warn(`Gambar produk ${r.product_id} kanal ${channel} gagal (${why})`);
      }
    }
    return n;
  }

  /** Retensi §10.8.3: item terhapus > 30 hari → hapus baris + objek gambar (best effort). */
  async purge(): Promise<number> {
    const res = await this.db.query<{ channel_code: string; image_ready_sha: string | null }>(
      `DELETE FROM platform.online_catalog_item
        WHERE deleted_at IS NOT NULL AND deleted_at < now() - make_interval(days => $1::int)
        RETURNING channel_code, image_ready_sha`,
      [PURGE_DAYS],
    );
    for (const r of res.rows) {
      if (!r.image_ready_sha) continue;
      const still = await this.db.query(`SELECT 1 FROM platform.online_catalog_item WHERE image_ready_sha = $1 LIMIT 1`, [r.image_ready_sha]);
      if ((still.rowCount ?? 0) > 0) continue;
      for (const size of [512, 1024] as const) {
        await this.storage.remove(catalogImageKey(r.channel_code, r.image_ready_sha, size)).catch(() => undefined);
      }
    }
    return res.rowCount ?? 0;
  }
}
