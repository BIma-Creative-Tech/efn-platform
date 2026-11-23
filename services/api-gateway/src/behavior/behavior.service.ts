import { Inject, Injectable, Logger } from '@nestjs/common';
import { randomUUID } from 'node:crypto';
import { AppConfig } from '../config/app-config';
import { AmqpService } from '../common/amqp/amqp.service';
import { DB, Db } from '../common/db/db';
import { ApiError, Errors } from '../common/errors/api-error';
import { redisKeyHash } from '../common/pii/pii';
import { RateLimitResult, RateLimiter } from '../common/rate-limit/rate-limiter';
import { KV_STORE, KvStore } from '../common/redis/kv-store';
import { AuthUser } from '../common/security/access-token.service';
import { UNKNOWN_TEXT_SHA256 } from '../privacy/consent.service';
import { BEHAVIOR_EVENT_QUEUE, BehaviorEventMessage, BehaviorQueueEvent, behaviorIdempotencyKey } from './behavior-message';
import { ERASURE_CONSENT_VERSION, memberConsentGranted } from './behavior-privacy';
import { resolveChannelCompanies } from './channel-company';
import { MAX_BODY_BYTES, MEMBER_ONLY_EVENT_TYPES, ParsedEvent, RejectCode, parseDeviceConsent, parseEventBatch } from './dto/behavior.dto';
import { anonSubjectKey, deviceKey, memberSubjectKey } from './subject-key';

/** V-01: `consented` = subject ber-consent `tracking` (perilaku lama); `anonymous` = log kunjungan tanpa consent. */
export type IngestMode = 'consented' | 'anonymous';

export interface IngestResponse {
  accepted: number;
  duplicates?: number;
  rejected?: { index: number; code: RejectCode }[];
  disabled?: true;
  mode?: IngestMode;
}

interface Subject {
  key: string;
  kind: 'device' | 'member';
  consented: boolean;
}

/** Batas pencatatan consent device per IP per menit (anti spam baris append-only). */
const CONSENT_IP_PER_MIN = 30;

interface CatalogPriceRow {
  outlet_code: string | null;
  price_incl: number;
}

/** 403 consent (§28.6). Bentuk body mengikuti filter kontrak: `{ error: { code: "tracking_consent_required", ... } }`. */
export function trackingConsentRequired(): ApiError {
  return ApiError.of(403, 'tracking_consent_required', 'Izinkan pelacakan aktivitas terlebih dahulu.');
}

/**
 * Event perilaku visitor (docs/04 §28.6, ADR-047 §3.1–§3.2). Tanpa PII: device ID mentah tidak pernah disimpan
 * (`device_key` HMAC), tanpa IP/user agent di payload maupun tabel (IP hanya sebagai kunci rate limit ter-hash).
 *
 * Alur `POST /v1/events`: flag → batas ukuran → validasi → rate limit (event/menit per device & per IP) → consent gate
 * (akun `tracking` bila Bearer; selain itu consent device; V-01: tanpa consent + `BEHAVIOR_ANON_VISIT_ENABLED` → subject
 * anonim `consented=false`, tipe fitur member ditolak `invalid`) → pengayaan server (harga salinan katalog, status stok cache
 * `ocat:stk:*`, tanpa memanggil Odoo) → publish `behavior.event` (confirm channel) → 202.
 */
@Injectable()
export class BehaviorService {
  private readonly logger = new Logger(BehaviorService.name);
  now: () => number = () => Date.now();

  constructor(
    private readonly config: AppConfig,
    @Inject(DB) private readonly db: Db,
    @Inject(KV_STORE) private readonly kv: KvStore,
    private readonly limiter: RateLimiter,
    private readonly amqp: AmqpService,
  ) {}

  /** `POST /v1/events/consent` — baris append-only `behavior.device_consent`. */
  async recordDeviceConsent(body: unknown, ip: string | null): Promise<{ recorded: boolean; granted?: boolean; disabled?: true }> {
    if (!this.config.behaviorTrackingEnabled) return { recorded: false, disabled: true };
    const input = parseDeviceConsent(body);
    await this.enforce(`beh:consent:ip:${redisKeyHash(this.config.redisKeyPepper, ip ?? 'unknown')}`, 1, CONSENT_IP_PER_MIN);
    const active = this.config.consentActiveText('tracking');
    let textSha256: string;
    if (input.granted) {
      if (!active) throw ApiError.of(503, 'consent_unavailable', 'Pengaturan persetujuan belum tersedia. Coba lagi nanti.');
      if (input.version !== active.version) throw Errors.conflict('stale_text', 'Teks persetujuan sudah diperbarui. Muat ulang lalu baca versi terbaru.');
      textSha256 = active.textSha256;
    } else {
      textSha256 = active?.textSha256 ?? UNKNOWN_TEXT_SHA256;
    }
    const key = deviceKey(this.config.behaviorDevicePepper, input.deviceId);
    await this.db.tx(async (q) => {
      await q.query(`INSERT INTO behavior.device_consent (device_key, granted, version, text_sha256) VALUES ($1, $2, $3, $4)`, [
        key,
        input.granted,
        input.version,
        textSha256,
      ]);
      // Penarikan consent device → perangkat tidak lagi dikaitkan ke akun mana pun
      if (!input.granted) await q.query(`DELETE FROM behavior.device_link WHERE device_key = $1`, [key]);
    });
    return { recorded: true, granted: input.granted };
  }

  /** `POST /v1/events`. `user` = member dari Bearer (opsional, sudah diverifikasi controller). */
  async ingest(body: unknown, rawBytes: number, ip: string | null, user: AuthUser | null): Promise<IngestResponse> {
    if (!this.config.behaviorTrackingEnabled) return { accepted: 0, disabled: true };
    if (rawBytes > MAX_BODY_BYTES) throw Errors.payloadTooLarge();
    const nowMs = this.now();
    const batch = parseEventBatch(body, nowMs);
    const dKey = deviceKey(this.config.behaviorDevicePepper, batch.deviceId);
    const weight = Math.max(1, batch.events.length);
    const devKey = `beh:dev:${dKey.slice(0, 32)}`;
    const ipKey = `beh:ip:${redisKeyHash(this.config.redisKeyPepper, ip ?? 'unknown')}`;
    await this.enforce(devKey, weight, this.config.behaviorRateDevicePerMinute);
    try {
      await this.enforce(ipKey, weight, this.config.behaviorRateIpPerMinute);
    } catch (err) {
      await this.limiter.release(devKey, weight, 60).catch(() => undefined);
      throw err;
    }

    const subject = await this.resolveSubject(batch.deviceId, dKey, user);
    const rejected = [...batch.rejected];
    let events = batch.events;
    if (!subject.consented) {
      // V-01: fitur member (wishlist, notify-me, price alert) tidak diterima dari subject anonim
      events = [];
      for (const e of batch.events) {
        if (MEMBER_ONLY_EVENT_TYPES.has(e.event.type)) rejected.push({ index: e.index, code: 'invalid' });
        else events.push(e);
      }
    }
    const enriched = await this.enrich(events, rejected);
    rejected.sort((a, b) => a.index - b.index);
    const mode: IngestMode = subject.consented ? 'consented' : 'anonymous';
    const res: IngestResponse = { accepted: enriched.length, duplicates: batch.duplicates, rejected, mode };
    if (enriched.length === 0) return res;

    const batchId = randomUUID();
    const msg: BehaviorEventMessage = {
      schema_version: 1,
      batch_id: batchId,
      received_at: new Date(nowMs).toISOString(),
      subject_key: subject.key,
      subject_kind: subject.kind,
      consented: subject.consented,
      channel: batch.channel,
      events: enriched,
    };
    try {
      await this.amqp.publish(BEHAVIOR_EVENT_QUEUE, msg, behaviorIdempotencyKey(batchId));
    } catch (err) {
      this.logger.warn(`Publish behavior.event gagal: ${err instanceof Error ? err.message : 'broker'}`);
      throw Errors.serviceUnavailable();
    }
    return res;
  }

  private async enforce(key: string, weight: number, limit: number): Promise<void> {
    let r: RateLimitResult;
    try {
      r = await this.limiter.consume(key, weight, limit, 60);
    } catch {
      throw Errors.serviceUnavailable(); // Redis mati → fail closed (endpoint publik)
    }
    if (!r.allowed) throw Errors.rateLimited(r.retryAfterSeconds);
  }

  /**
   * Consent gate + pemilihan subject (ADR-047 §3.3):
   * - Bearer: consent `tracking` AKUN wajib aktif (consent device tidak otomatis menjadi consent akun) → subject member;
   *   tautan device ↔ member + pemetaan subject dicatat.
   * - Tanpa Bearer: consent device terbaru wajib `granted`; bila device tertaut ke member yang consent `tracking`-nya
   *   masih aktif → subject member, selain itu subject device.
   * - V-01 (keputusan pemilik proyek 23 Nov 2026): tanpa consent di atas (termasuk Bearer tanpa consent akun) dan
   *   `behaviorVisitLogEnabled` → subject ANONIM `anonSubjectKey` (`consented=false`): Bearer diabaikan untuk identitas,
   *   tidak ada `device_link`/`subject_member`. Penarikan consent device (baris `granted=false` biasa) tetap boleh anonim
   *   (penarikan = berhenti penautan/profiling), KECUALI baris erasure akun (`version='erasure'`, SEC-G50-07) → 403.
   *   Flag mati → 403 `tracking_consent_required` seperti sebelumnya.
   */
  private async resolveSubject(deviceId: string, dKey: string, user: AuthUser | null): Promise<Subject> {
    if (user && (await memberConsentGranted(this.db, user.memberId, 'tracking'))) {
      const sKey = memberSubjectKey(this.config.behaviorSubjectPepper, user.memberId);
      await this.db.tx(async (q) => {
        await q.query(
          `INSERT INTO behavior.device_link (member_id, device_key) VALUES ($1, $2)
           ON CONFLICT (member_id, device_key) DO UPDATE SET last_seen_at = now()`,
          [user.memberId, dKey],
        );
        await q.query(`INSERT INTO behavior.subject_member (subject_key, member_id) VALUES ($1, $2) ON CONFLICT DO NOTHING`, [sKey, user.memberId]);
      });
      return { key: sKey, kind: 'member', consented: true };
    }
    // Bearer tanpa consent akun: perilaku lama (403) bila log kunjungan mati; bila aktif, consent device dibaca hanya untuk
    // cek baris erasure sebelum dicatat anonim (consent device TIDAK menjadi subject saat Bearer ada, ADR-047 §3.3)
    if (user && !this.config.behaviorVisitLogEnabled) throw trackingConsentRequired();
    const consent = await this.db.query<{ granted: boolean; version: string }>(
      `SELECT granted, version FROM behavior.device_consent WHERE device_key = $1 ORDER BY created_at DESC, id DESC LIMIT 1`,
      [dKey],
    );
    const latest = consent.rows[0];
    // SEC-V01-01: consent device hanya berlaku untuk versi teks consent `tracking` yang AKTIF. Versi lama atau teks aktif
    // tidak dikonfigurasi → tidak ada subject consented/penautan member (anonim bila visit_log aktif, selain itu 403).
    const activeVersion = this.activeTrackingVersion();
    const deviceConsented = latest?.granted === true && activeVersion !== null && latest.version === activeVersion;
    if (user || !deviceConsented) {
      if (!this.config.behaviorVisitLogEnabled || latest?.version === ERASURE_CONSENT_VERSION) throw trackingConsentRequired();
      return { key: anonSubjectKey(this.config.behaviorDevicePepper, deviceId), kind: 'device', consented: false };
    }
    const link = await this.db.query<{ member_id: string }>(
      `SELECT l.member_id::text AS member_id FROM behavior.device_link l
         JOIN platform.member m ON m.id = l.member_id AND m.deleted_at IS NULL
        WHERE l.device_key = $1 ORDER BY l.last_seen_at DESC LIMIT 1`,
      [dKey],
    );
    const memberId = link.rows[0]?.member_id;
    if (memberId && (await memberConsentGranted(this.db, memberId, 'tracking'))) {
      await this.db.query(`UPDATE behavior.device_link SET last_seen_at = now() WHERE member_id = $1 AND device_key = $2`, [memberId, dKey]);
      return { key: memberSubjectKey(this.config.behaviorSubjectPepper, memberId), kind: 'member', consented: true };
    }
    return { key: dKey, kind: 'device', consented: true };
  }

  /** Versi teks consent `tracking` aktif; null bila belum/salah dikonfigurasi (tidak melempar di jalur ingest). */
  private activeTrackingVersion(): string | null {
    try {
      return this.config.consentActiveText('tracking')?.version ?? null;
    } catch {
      return null;
    }
  }

  /** Pengayaan server (tidak dipercaya dari klien): harga dari salinan katalog, status stok dari cache Redis. */
  private async enrich(events: { index: number; event: ParsedEvent }[], rejected: { index: number; code: RejectCode }[]): Promise<BehaviorQueueEvent[]> {
    const byChannel = new Map<string, Set<number>>();
    for (const { event } of events) {
      if (event.product_id === null) continue;
      const set = byChannel.get(event.channel_code) ?? new Set<number>();
      set.add(event.product_id);
      byChannel.set(event.channel_code, set);
    }
    const prices = new Map<string, CatalogPriceRow[]>();
    for (const [channel, ids] of byChannel) {
      const res = await this.db.query<{ product_id: number; data: { prices?: unknown } }>(
        `SELECT product_id, data FROM platform.online_catalog_item
          WHERE channel_code = $1 AND product_id = ANY($2::int[]) AND deleted_at IS NULL`,
        [channel, [...ids]],
      );
      for (const r of res.rows) prices.set(`${channel}:${Number(r.product_id)}`, priceRows(r.data?.prices));
    }
    // Revisi G50-A: setiap event wajib punya PT (kolom `event_raw.company_code`, mart ETL per PT)
    const companies = await resolveChannelCompanies(this.db, events.map((e) => e.event.channel_code));
    const out: BehaviorQueueEvent[] = [];
    for (const { index, event } of events) {
      const company = companies.get(event.channel_code);
      if (!company) {
        rejected.push({ index, code: 'unknown_channel' });
        continue;
      }
      let price: number | null = null;
      let stock: BehaviorQueueEvent['stock_status'] = null;
      if (event.product_id !== null) {
        const rows = prices.get(`${event.channel_code}:${event.product_id}`);
        if (!rows) {
          rejected.push({ index, code: 'unknown_product' });
          continue;
        }
        const p = rows.find((x) => x.outlet_code === event.outlet_code) ?? rows.find((x) => x.outlet_code === null) ?? rows[0];
        price = p ? Math.max(0, Math.round(p.price_incl)) : null;
        if (event.outlet_code) {
          const v = await this.kv.get(`ocat:stk:${event.channel_code}:${event.outlet_code}:${event.product_id}`).catch(() => null);
          stock = v === 'in_stock' || v === 'low' || v === 'out' ? v : null;
        }
      }
      out.push({
        event_id: event.event_id,
        type: event.type as BehaviorQueueEvent['type'],
        client_ts: event.client_ts,
        channel_code: event.channel_code,
        company_code: company,
        outlet_code: event.outlet_code,
        product_id: event.product_id,
        duration_s: event.duration_s,
        qty: event.qty,
        query: event.query,
        result_count: event.result_count,
        stock_status: stock,
        price_idr: price,
        page: event.page,
      });
    }
    return out;
  }
}

function priceRows(raw: unknown): CatalogPriceRow[] {
  if (!Array.isArray(raw)) return [];
  const out: CatalogPriceRow[] = [];
  for (const p of raw) {
    if (typeof p !== 'object' || p === null) continue;
    const o = p as Record<string, unknown>;
    if (typeof o.price_incl !== 'number' || !Number.isFinite(o.price_incl)) continue;
    out.push({ outlet_code: typeof o.outlet_code === 'string' ? o.outlet_code : null, price_incl: o.price_incl });
  }
  return out;
}
