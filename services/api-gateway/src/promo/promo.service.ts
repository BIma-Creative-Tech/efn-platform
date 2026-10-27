import { Inject, Injectable, Logger, OnModuleDestroy, OnModuleInit, Optional } from '@nestjs/common';
import { createHmac, randomUUID } from 'node:crypto';
import sharp from 'sharp';
import { AppConfig } from '../config/app-config';
import { DB, Db } from '../common/db/db';
import { Errors } from '../common/errors/api-error';
import { PII_AAD, PiiCipher } from '../common/pii/crypto';
import { CATALOG_STORAGE, CatalogImageRejected, CatalogStorage } from '../catalog/catalog-storage';
import { detectImageKind } from '../market-scout/image/magic-bytes';
import { ODOO_PROMO, OdooPromoApi, OdooPromoError, PromoFeedItem, PromoLink, PromoPush } from './odoo-promo';

/**
 * Promo Super-App (docs/04 §18.2). Feed Odoo (#22) ditarik berkala ke `platform.promo_item` per PT; gambar (#23)
 * di-re-encode (sharp, metadata dibuang) ke bucket `catalog` key `promo/<sha>/<w>.jpg`. Klien membaca
 * `GET /v1/promo?surface=web|app` (publik) — hanya item dalam periode tayang & gambarnya sudah siap.
 *
 * Push (#24): popup `queued` diklaim lalu dikirim lewat Expo Push API HANYA ke member dengan consent `marketing`
 * aktif (baris consent terbaru `granted=true`) dan token aktif. Kegagalan push tidak memengaruhi pop-up in-app.
 */

export const PROMO_WIDTHS = { banner: 1600, popup: 1024 } as const;
export type PromoKind = keyof typeof PROMO_WIDTHS;
export type PromoSurface = 'web' | 'app';

export interface PromoView {
  id: string;
  title: string;
  subtitle: string;
  cta_label: string;
  link: PromoLink;
  image_sha: string | null;
  frequency?: 'once' | 'daily' | 'every_open';
}
export interface PromoResponse {
  banners: PromoView[];
  popups: PromoView[];
  server_time: string;
}

export function promoImageKey(sha: string, w: number): string {
  return `promo/${sha}/${w}.jpg`;
}

/** Re-encode gambar promo: magic bytes, decode penuh, ≤ w px (tanpa upscale), JPEG q85, tanpa metadata. */
export async function normalizePromoImage(input: Buffer, w: number): Promise<Buffer> {
  if (input.length === 0 || input.length > 10 * 1024 * 1024) throw new CatalogImageRejected('ukuran gambar tidak valid');
  if (!detectImageKind(input)) throw new CatalogImageRejected('format gambar tidak didukung');
  try {
    return await sharp(input, { failOn: 'error', limitInputPixels: 50_000_000 })
      .rotate()
      .resize({ width: w, height: w, fit: 'inside', withoutEnlargement: true })
      .jpeg({ quality: 85 })
      .toBuffer();
  } catch {
    throw new CatalogImageRejected('gambar tidak dapat di-decode');
  }
}

const EXPO_TOKEN_RE = /^(ExponentPushToken|ExpoPushToken)\[[A-Za-z0-9_-]{10,200}\]$/;
export function isExpoPushToken(v: unknown): v is string {
  return typeof v === 'string' && v.length <= 256 && EXPO_TOKEN_RE.test(v);
}

/** Pengirim push (Expo). Dipisah agar test tidak memanggil jaringan; dev tanpa EAS → console. */
export interface PushSender {
  send(messages: { to: string; title: string; body: string; data: Record<string, unknown> }[]): Promise<{ ok: number; failed: number; invalidTokens: string[] }>;
}
export const PUSH_SENDER = Symbol('PUSH_SENDER');

@Injectable()
export class PromoService implements OnModuleInit, OnModuleDestroy {
  private readonly logger = new Logger(PromoService.name);
  private timer: NodeJS.Timeout | null = null;
  private running = false;
  now: () => number = () => Date.now();

  constructor(
    private readonly config: AppConfig,
    @Inject(DB) private readonly db: Db,
    @Inject(ODOO_PROMO) private readonly odoo: OdooPromoApi,
    @Inject(CATALOG_STORAGE) private readonly storage: CatalogStorage,
    private readonly cipher: PiiCipher,
    @Optional() @Inject(PUSH_SENDER) private readonly push: PushSender | null = null,
  ) {}

  onModuleInit(): void {
    if (!this.config.promoEnabled) return;
    this.timer = setInterval(() => void this.runOnce(), this.config.promoSyncSeconds * 1000);
    this.timer.unref();
    setTimeout(() => void this.runOnce(), 7_000).unref();
  }

  onModuleDestroy(): void {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
  }

  /** Satu putaran sinkron (+ push bila aktif). Tidak pernah melempar. */
  async runOnce(): Promise<void> {
    if (this.running) return;
    this.running = true;
    try {
      for (const company of this.config.promoCompanies) {
        await this.syncCompany(company);
        if (this.config.promoPushEnabled) await this.dispatchPush(company).catch((err: unknown) => this.logger.warn(`Push promo ${company} gagal: ${err instanceof Error ? err.message : 'error'}`));
      }
    } finally {
      this.running = false;
    }
  }

  async syncCompany(company: string): Promise<{ items: number; images: number } | null> {
    try {
      const feed = await this.odoo.feed(company);
      const rows: [PromoKind, PromoFeedItem][] = [...feed.banners.map((b) => ['banner', b] as [PromoKind, PromoFeedItem]), ...feed.popups.map((p) => ['popup', p] as [PromoKind, PromoFeedItem])];
      await this.db.tx(async (q) => {
        // Salinan = feed terkini persis: item yang hilang dari feed (dihapus/diarsip/berakhir) ikut dihapus.
        const keep = rows.map(([k, it]) => `${k}:${it.id}`);
        await q.query(`DELETE FROM platform.promo_item WHERE company_code = $1 AND NOT ((kind || ':' || odoo_id) = ANY($2::text[]))`, [company, keep]);
        for (const [kind, it] of rows) {
          const payload = { title: it.title, subtitle: it.subtitle, cta_label: it.cta_label, link: it.link, ...(kind === 'popup' ? { frequency: it.frequency ?? 'once' } : {}) };
          await q.query(
            `INSERT INTO platform.promo_item (kind, company_code, odoo_id, payload, image_sha, image_ready, web, app, sequence, date_start, date_end, synced_at)
             VALUES ($1, $2, $3, $4::jsonb, $5, false, $6, $7, $8, $9, $10, now())
             ON CONFLICT (kind, company_code, odoo_id) DO UPDATE SET
               payload = EXCLUDED.payload, web = EXCLUDED.web, app = EXCLUDED.app, sequence = EXCLUDED.sequence,
               date_start = EXCLUDED.date_start, date_end = EXCLUDED.date_end, synced_at = now(),
               image_ready = CASE WHEN platform.promo_item.image_sha IS NOT DISTINCT FROM EXCLUDED.image_sha THEN platform.promo_item.image_ready ELSE false END,
               image_sha = EXCLUDED.image_sha`,
            [kind, company, it.id, JSON.stringify(payload), it.image_sha, it.web, it.app, it.sequence, it.date_start, it.date_end],
          );
        }
        await q.query(
          `INSERT INTO platform.promo_sync (company_code, last_ok_at, last_attempt_at, last_error_code) VALUES ($1, now(), now(), NULL)
           ON CONFLICT (company_code) DO UPDATE SET last_ok_at = now(), last_attempt_at = now(), last_error_code = NULL`,
          [company],
        );
      });
      const images = await this.syncImages(company);
      return { items: rows.length, images };
    } catch (err) {
      const code = err instanceof OdooPromoError ? `odoo_${err.kind}` : 'db_error';
      this.logger.warn(`Sinkron promo PT ${company} gagal: ${code}`);
      await this.db
        .query(
          `INSERT INTO platform.promo_sync (company_code, last_attempt_at, last_error_code) VALUES ($1, now(), $2)
           ON CONFLICT (company_code) DO UPDATE SET last_attempt_at = now(), last_error_code = $2`,
          [company, code],
        )
        .catch(() => undefined);
      return null;
    }
  }

  private async syncImages(company: string): Promise<number> {
    const res = await this.db.query<{ kind: PromoKind; odoo_id: number; image_sha: string }>(
      `SELECT kind, odoo_id, image_sha FROM platform.promo_item
        WHERE company_code = $1 AND image_sha IS NOT NULL AND image_ready = false AND date_end > now() LIMIT 10`,
      [company],
    );
    let n = 0;
    for (const r of res.rows) {
      try {
        const img = await this.odoo.image(company, r.kind, Number(r.odoo_id));
        if (img.sha !== r.image_sha) continue; // gambar berubah sejak feed: tunggu feed berikutnya
        const w = PROMO_WIDTHS[r.kind];
        await this.storage.put(promoImageKey(r.image_sha, w), await normalizePromoImage(img.data, w));
        await this.db.query(`UPDATE platform.promo_item SET image_ready = true WHERE kind = $1 AND company_code = $2 AND odoo_id = $3 AND image_sha = $4`, [
          r.kind,
          company,
          r.odoo_id,
          r.image_sha,
        ]);
        n += 1;
      } catch (err) {
        const why = err instanceof CatalogImageRejected ? 'rejected' : err instanceof OdooPromoError ? `odoo_${err.kind}` : 'storage';
        this.logger.warn(`Gambar promo ${r.kind} #${r.odoo_id} PT ${company} gagal (${why})`);
      }
    }
    return n;
  }

  /** `GET /v1/promo`: item dalam periode tayang, permukaan cocok, gambar siap (atau tanpa gambar). */
  async list(surface: PromoSurface): Promise<PromoResponse> {
    const empty = { banners: [], popups: [], server_time: new Date(this.now()).toISOString() };
    if (!this.config.promoEnabled) return empty;
    const res = await this.db.query<{ kind: PromoKind; company_code: string; odoo_id: number; payload: Record<string, unknown>; image_sha: string | null }>(
      `SELECT kind, company_code, odoo_id, payload, image_sha FROM platform.promo_item
        WHERE date_start <= now() AND date_end > now() AND (CASE WHEN $1 = 'web' THEN web ELSE app END)
          AND (image_sha IS NULL OR image_ready)
        ORDER BY sequence, date_start DESC, odoo_id DESC`,
      [surface],
    );
    const out: PromoResponse = { ...empty, banners: [], popups: [] };
    for (const r of res.rows) {
      const p = r.payload ?? {};
      const view: PromoView = {
        id: `${r.company_code}-${r.odoo_id}`,
        title: typeof p.title === 'string' ? p.title : '',
        subtitle: typeof p.subtitle === 'string' ? p.subtitle : '',
        cta_label: typeof p.cta_label === 'string' ? p.cta_label : '',
        link: (p.link as PromoLink) ?? null,
        image_sha: r.image_sha,
      };
      if (r.kind === 'banner') {
        if (out.banners.length < 10) out.banners.push(view);
      } else if (out.popups.length < 5) {
        out.popups.push({ ...view, frequency: p.frequency === 'daily' || p.frequency === 'every_open' ? p.frequency : 'once' });
      }
    }
    return out;
  }

  /** Gambar promo: hanya sha yang dirujuk item tayang (bukan bucket umum). */
  async image(sha: string, w: number): Promise<Buffer> {
    if (!this.config.promoEnabled || !/^[0-9a-f]{64}$/.test(sha)) throw Errors.notFound();
    const ok = await this.db.query(
      `SELECT 1 FROM platform.promo_item WHERE image_sha = $1 AND image_ready AND date_end > now()
          AND ((kind = 'banner' AND $2 = 1600) OR (kind = 'popup' AND $2 = 1024)) LIMIT 1`,
      [sha, w],
    );
    if (ok.rows.length === 0) throw Errors.notFound();
    const data = await this.storage.get(promoImageKey(sha, w));
    if (!data) throw Errors.notFound();
    return data;
  }

  // ─────────────────────────────── push token ───────────────────────────────
  private tokenHash(token: string): string {
    return createHmac('sha256', this.config.redisKeyPepper).update(`efn:push_token:${token}`, 'utf8').digest('hex');
  }

  async registerToken(memberId: string, token: unknown, platform: unknown): Promise<void> {
    if (!isExpoPushToken(token) || (platform !== 'android' && platform !== 'ios')) throw Errors.invalidRequest();
    const hash = this.tokenHash(token);
    const enc = this.cipher.encrypt(token, PII_AAD.PUSH_TOKEN);
    // Satu perangkat = satu member: token yang pindah akun (login akun lain) dipindahkan, bukan diduplikasi.
    await this.db.query(
      `INSERT INTO platform.push_token (id, member_id, platform, token_enc, token_hash, created_at, last_seen_at, disabled_at)
       VALUES ($1, $2, $3, $4, $5, now(), now(), NULL)
       ON CONFLICT (token_hash) DO UPDATE SET member_id = EXCLUDED.member_id, platform = EXCLUDED.platform,
         token_enc = EXCLUDED.token_enc, last_seen_at = now(), disabled_at = NULL`,
      [randomUUID(), memberId, platform, enc, hash],
    );
  }

  async removeTokens(memberId: string, token?: unknown): Promise<void> {
    if (token !== undefined && token !== null) {
      if (!isExpoPushToken(token)) throw Errors.invalidRequest();
      await this.db.query(`DELETE FROM platform.push_token WHERE member_id = $1 AND token_hash = $2`, [memberId, this.tokenHash(token)]);
      return;
    }
    await this.db.query(`DELETE FROM platform.push_token WHERE member_id = $1`, [memberId]);
  }

  /** Klaim push antre (#24) lalu kirim ke member ber-consent marketing. */
  async dispatchPush(company: string): Promise<{ claimed: number; sent: number }> {
    if (!this.config.promoPushEnabled || !this.push) return { claimed: 0, sent: 0 };
    const items: PromoPush[] = await this.odoo.pushClaim(company, 5);
    let sent = 0;
    for (const item of items) sent += await this.sendToConsented(item);
    return { claimed: items.length, sent };
  }

  async sendToConsented(item: PromoPush): Promise<number> {
    if (!this.push) return 0;
    const res = await this.db.query<{ token_enc: Buffer }>(
      `SELECT t.token_enc FROM platform.push_token t
         JOIN LATERAL (SELECT granted FROM platform.consent c WHERE c.member_id = t.member_id AND c.purpose = 'marketing'
                        ORDER BY c.created_at DESC, c.id DESC LIMIT 1) c ON c.granted
        WHERE t.disabled_at IS NULL`,
    );
    const tokens: string[] = [];
    for (const r of res.rows) {
      try {
        tokens.push(this.cipher.decrypt(r.token_enc, PII_AAD.PUSH_TOKEN));
      } catch {
        // kunci pensiun / baris rusak: lewati (tidak pernah log token)
      }
    }
    let ok = 0;
    for (let i = 0; i < tokens.length; i += 100) {
      const batch = tokens.slice(i, i + 100);
      const r = await this.push.send(batch.map((to) => ({ to, title: item.title, body: item.body, data: { promo_id: item.id, link: item.link } })));
      ok += r.ok;
      if (r.invalidTokens.length) {
        await this.db.query(`UPDATE platform.push_token SET disabled_at = now() WHERE token_hash = ANY($1::text[])`, [r.invalidTokens.map((t) => this.tokenHash(t))]);
      }
    }
    this.logger.log(`Push promo #${item.id}: ${ok}/${tokens.length} terkirim (member ber-consent marketing)`);
    return ok;
  }
}
